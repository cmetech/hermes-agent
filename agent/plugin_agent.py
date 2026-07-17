"""Isolated, host-owned Hermes agents for trusted plugins.

The public facade serializes policy rather than credentials. Every run starts a
fresh Python worker, so tool registry/cache/callback/environment mutations are
contained and cannot alter a long-lived parent conversation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping

from tools.managed_process import ManagedProcessTree, TerminationPolicy


_PROTOCOL_VERSION = 1
_MAX_REQUEST_BYTES = 1_000_000
_MAX_FRAME_BYTES = 4_000_000
_MAX_PROMPT_CHARS = 500_000
_MAX_POLICY_NAMES = 256


class _PluginAgentCancelled(RuntimeError):
    """Internal control flow for caller-requested worker cancellation."""


@dataclass(frozen=True)
class PluginAgentRunRequest:
    prompt: str
    provider: str | None = None
    model: str | None = None
    context_mode: Literal["fresh", "shared"] = "fresh"
    session_id: str | None = None
    enabled_toolsets: tuple[str, ...] | None = None
    allowed_tools: tuple[str, ...] | None = None
    denied_tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    workdir: Path | None = None
    max_iterations: int = 90
    idle_timeout_seconds: float = 300.0
    wall_timeout_seconds: float = 1800.0
    provider_request_timeout_seconds: float = 300.0


@dataclass(frozen=True)
class PluginAgentRunResult:
    final_response: str
    session_id: str
    provider: str
    model: str
    status: Literal["completed", "paused", "cancelled", "failed"]
    pending_interaction: Mapping[str, str] | None
    usage: Mapping[str, int | float | None]
    audit: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.pending_interaction is not None:
            object.__setattr__(
                self, "pending_interaction", MappingProxyType(dict(self.pending_interaction))
            )
        object.__setattr__(self, "usage", MappingProxyType(dict(self.usage)))
        object.__setattr__(self, "audit", MappingProxyType(dict(self.audit)))


def _validate_name_list(label: str, values: tuple[str, ...] | None) -> None:
    if values is None:
        return
    if not isinstance(values, tuple):
        raise TypeError(f"{label} must be a tuple or None")
    if len(values) > _MAX_POLICY_NAMES:
        raise ValueError(f"{label} exceeds {_MAX_POLICY_NAMES} names")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{label} must contain non-empty strings")


def _validate_request(request: PluginAgentRunRequest) -> None:
    if not isinstance(request, PluginAgentRunRequest):
        raise TypeError("request must be PluginAgentRunRequest")
    if not isinstance(request.prompt, str) or not request.prompt.strip():
        raise ValueError("prompt must be non-empty")
    if len(request.prompt) > _MAX_PROMPT_CHARS:
        raise ValueError("prompt exceeds the plugin-agent size limit")
    if request.context_mode not in {"fresh", "shared"}:
        raise ValueError("context_mode must be fresh or shared")
    if request.context_mode == "shared" and (
        not isinstance(request.session_id, str) or not request.session_id.strip()
    ):
        raise ValueError("shared context requires session_id")
    if (
        not isinstance(request.max_iterations, int)
        or isinstance(request.max_iterations, bool)
        or request.max_iterations <= 0
    ):
        raise ValueError("max_iterations must be a positive integer")
    for label, value in (("provider", request.provider), ("model", request.model)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{label} must be a non-empty string or None")
    for label, value in (
        ("idle_timeout_seconds", request.idle_timeout_seconds),
        ("wall_timeout_seconds", request.wall_timeout_seconds),
        ("provider_request_timeout_seconds", request.provider_request_timeout_seconds),
    ):
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{label} must be finite and positive")
    if request.idle_timeout_seconds > request.wall_timeout_seconds:
        raise ValueError("idle timeout cannot exceed wall timeout")
    if request.provider_request_timeout_seconds > request.wall_timeout_seconds:
        raise ValueError("provider request timeout cannot exceed wall timeout")
    for label, values in (
        ("enabled_toolsets", request.enabled_toolsets),
        ("allowed_tools", request.allowed_tools),
        ("denied_tools", request.denied_tools),
        ("skills", request.skills),
    ):
        _validate_name_list(label, values)
    if request.workdir is not None:
        path = Path(request.workdir).expanduser()
        if not path.is_dir():
            raise ValueError("workdir must be an existing directory")


def _agent_override_allowed(plugin_id: str, kind: str, value: str | None) -> bool:
    if not value:
        return True
    try:
        from hermes_cli.config import load_config

        entry = (((load_config() or {}).get("plugins") or {}).get("entries") or {}).get(
            plugin_id, {}
        )
        policy = entry.get("agent") if isinstance(entry, dict) else None
        if not isinstance(policy, dict) or not policy.get(f"allow_{kind}_override", False):
            return False
        allowed = policy.get(f"allowed_{kind}s")
        return not isinstance(allowed, list) or "*" in allowed or value in allowed
    except Exception:
        return False


def _request_payload(plugin_id: str, request: PluginAgentRunRequest) -> dict[str, Any]:
    body = asdict(request)
    body["workdir"] = str(Path(request.workdir).expanduser().resolve()) if request.workdir else None
    return {
        "protocol_version": _PROTOCOL_VERSION,
        "type": "run",
        "plugin_id": plugin_id,
        "request": body,
    }


def _read_stream(stream, events: queue.Queue, label: str) -> None:
    try:
        for line in iter(stream.readline, ""):
            events.put((label, line))
    finally:
        events.put((f"{label}_eof", ""))


def _exchange_worker(
    payload: dict[str, Any],
    *,
    workdir: Path | None,
    idle_timeout_seconds: float,
    wall_timeout_seconds: float,
    worker_argv: list[str] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > _MAX_REQUEST_BYTES:
        raise ValueError("plugin-agent request frame is too large")

    tree = ManagedProcessTree.spawn(
        worker_argv or [sys.executable, "-m", "agent.plugin_agent_worker"],
        policy=TerminationPolicy(
            cooperative_grace_seconds=0.2,
            term_grace_seconds=1.0,
            kill_grace_seconds=1.0,
            wait_timeout_seconds=2.0,
        ),
        cwd=str(workdir) if workdir else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert tree.process.stdin is not None
    assert tree.process.stdout is not None
    assert tree.process.stderr is not None
    events: queue.Queue = queue.Queue()
    stdout_reader = threading.Thread(
        target=_read_stream, args=(tree.process.stdout, events, "stdout"), daemon=True
    )
    stderr_reader = threading.Thread(
        target=_read_stream, args=(tree.process.stderr, events, "stderr"), daemon=True
    )
    stdout_reader.start()
    stderr_reader.start()
    tree.process.stdin.write(encoded + "\n")
    tree.process.stdin.flush()

    started = last_activity = time.monotonic()
    stderr_tail = ""
    try:
        while True:
            now = time.monotonic()
            if is_cancelled is not None and is_cancelled():
                raise _PluginAgentCancelled("plugin-agent run cancelled")
            if now - started >= wall_timeout_seconds:
                raise TimeoutError("plugin-agent wall timeout")
            if now - last_activity >= idle_timeout_seconds:
                raise TimeoutError("plugin-agent idle timeout")
            wait_for = min(
                0.2,
                wall_timeout_seconds - (now - started),
                idle_timeout_seconds - (now - last_activity),
            )
            try:
                label, line = events.get(timeout=max(wait_for, 0.01))
            except queue.Empty:
                if tree.process.poll() is not None and not stdout_reader.is_alive():
                    # stderr may contain provider diagnostics with credential
                    # fragments. Keep it bounded for host diagnostics, but do
                    # not surface it through the plugin-facing exception.
                    raise RuntimeError("plugin-agent worker exited without a result")
                continue
            if label == "stderr":
                stderr_tail = (stderr_tail + line)[-_MAX_FRAME_BYTES:]
                last_activity = time.monotonic()
                continue
            if label != "stdout":
                continue
            if len(line.encode("utf-8")) > _MAX_FRAME_BYTES:
                raise RuntimeError("plugin-agent response frame is too large")
            last_activity = time.monotonic()
            try:
                frame = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError("plugin-agent emitted invalid JSON") from exc
            if frame.get("protocol_version") != _PROTOCOL_VERSION:
                raise RuntimeError("plugin-agent protocol version mismatch")
            if frame.get("type") == "result":
                return frame
            if frame.get("type") not in {"progress", "interaction"}:
                raise RuntimeError("plugin-agent emitted an unknown frame type")
    finally:
        try:
            tree.process.stdin.close()
        except Exception:
            pass
        tree.close()


class PluginAgentRunner:
    """Plugin-bound facade that never exposes a live agent or credentials."""

    def __init__(self, plugin_id: str) -> None:
        if not isinstance(plugin_id, str) or not plugin_id.strip():
            raise ValueError("plugin_id must be non-empty")
        self.plugin_id = plugin_id.strip()

    def run(
        self,
        request: PluginAgentRunRequest,
        *,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> PluginAgentRunResult:
        _validate_request(request)
        if not _agent_override_allowed(self.plugin_id, "provider", request.provider):
            raise PermissionError(f"plugin {self.plugin_id!r} cannot override provider")
        if not _agent_override_allowed(self.plugin_id, "model", request.model):
            raise PermissionError(f"plugin {self.plugin_id!r} cannot override model")
        if request.context_mode == "shared":
            from hermes_state import SessionDB

            session_db = SessionDB()
            try:
                if session_db.get_session(request.session_id) is None:
                    raise ValueError(
                        "session_id does not identify an existing Hermes session"
                    )
            finally:
                session_db.close()

        payload = _request_payload(self.plugin_id, request)
        try:
            frame = _exchange_worker(
                payload,
                workdir=Path(request.workdir).expanduser().resolve() if request.workdir else None,
                idle_timeout_seconds=request.idle_timeout_seconds,
                wall_timeout_seconds=request.wall_timeout_seconds,
                is_cancelled=is_cancelled,
            )
            result = frame.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("plugin-agent result payload is missing")
            return PluginAgentRunResult(**result)
        except TimeoutError as exc:
            kind = "idle_timeout" if "idle" in str(exc) else "wall_timeout"
            return PluginAgentRunResult(
                final_response="",
                session_id=request.session_id or "",
                provider=request.provider or "",
                model=request.model or "",
                status="failed",
                pending_interaction=None,
                usage={},
                audit={"plugin_id": self.plugin_id, "failure_kind": kind},
            )
        except _PluginAgentCancelled:
            return PluginAgentRunResult(
                final_response="",
                session_id=request.session_id or "",
                provider=request.provider or "",
                model=request.model or "",
                status="cancelled",
                pending_interaction=None,
                usage={},
                audit={"plugin_id": self.plugin_id, "failure_kind": "cancelled"},
            )


__all__ = ["PluginAgentRunRequest", "PluginAgentRunResult", "PluginAgentRunner"]
