"""Isolated, host-owned Hermes agents for trusted plugins.

The public facade serializes policy rather than credentials. Every run starts a
fresh Python worker, so tool registry/cache/callback/environment mutations are
contained and cannot alter a long-lived parent conversation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping

from tools.managed_process import (
    ManagedProcessTree,
    ProcessResourceLimits,
    TerminationPolicy,
)


_PROTOCOL_VERSION = 1
_MAX_REQUEST_BYTES = 1_000_000
_MAX_FRAME_BYTES = 4_000_000
_MAX_QUEUED_FRAMES = 8
_MAX_PROMPT_CHARS = 500_000
_MAX_POLICY_NAMES = 256


class _PluginAgentCancelled(RuntimeError):
    """Internal control flow for caller-requested worker cancellation."""


class _PluginAgentResourceExceeded(RuntimeError):
    """Internal control flow for bounded worker-tree enforcement."""


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
    hooks: tuple[Mapping[str, Any], ...] = ()
    mcp_servers: Mapping[str, Mapping[str, Any]] | None = None
    inline_agents: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    reasoning_config: Mapping[str, Any] | None = None
    fallback_model: str | None = None
    ephemeral_system_prompt: str | None = None
    request_overrides: Mapping[str, Any] = field(default_factory=dict)
    max_budget_usd: float | None = None
    sandbox_policy: Mapping[str, Any] | None = None
    approved_action_digest: str | None = None
    workdir: Path | None = None
    max_iterations: int = 90
    max_api_attempts: int = 3
    idle_timeout_seconds: float = 300.0
    wall_timeout_seconds: float = 1800.0
    provider_request_timeout_seconds: float = 300.0
    max_process_tree_rss_bytes: int = 2048 * 1024 * 1024
    max_process_tree_cpu_seconds: float = 900.0
    max_descendants: int = 32
    cooperative_shutdown_seconds: float = 5.0
    term_grace_seconds: float = 5.0
    kill_reap_grace_seconds: float = 2.0


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
                self,
                "pending_interaction",
                MappingProxyType(dict(self.pending_interaction)),
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
    if (
        not isinstance(request.max_api_attempts, int)
        or isinstance(request.max_api_attempts, bool)
        or not 1 <= request.max_api_attempts <= 5
    ):
        raise ValueError("max API attempts must be between 1 and 5")
    for label, value in (("provider", request.provider), ("model", request.model)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{label} must be a non-empty string or None")
    if request.approved_action_digest is not None and not re.fullmatch(
        r"[0-9a-f]{64}", request.approved_action_digest
    ):
        raise ValueError(
            "approved action digest must be 64 lowercase hexadecimal characters"
        )
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
    for label, value in (
        ("cooperative shutdown", request.cooperative_shutdown_seconds),
        ("TERM grace", request.term_grace_seconds),
        ("KILL/reap grace", request.kill_reap_grace_seconds),
    ):
        if (
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{label} must be finite and positive")
    try:
        ProcessResourceLimits(
            max_rss_bytes=request.max_process_tree_rss_bytes,
            max_cpu_seconds=request.max_process_tree_cpu_seconds,
            max_descendants=request.max_descendants,
        )
    except ValueError as exc:
        raise ValueError(f"plugin-agent resource limits are invalid: {exc}") from exc
    for label, values in (
        ("enabled_toolsets", request.enabled_toolsets),
        ("allowed_tools", request.allowed_tools),
        ("denied_tools", request.denied_tools),
        ("skills", request.skills),
    ):
        _validate_name_list(label, values)
    if not isinstance(request.hooks, tuple) or len(request.hooks) > 128:
        raise ValueError("hooks must be a tuple with at most 128 entries")
    for hook in request.hooks:
        if not isinstance(hook, Mapping):
            raise ValueError("every hook must be a mapping")
    try:
        if len(json.dumps(request.hooks, default=str).encode("utf-8")) > 256_000:
            raise ValueError("hooks exceed the plugin-agent size limit")
    except (TypeError, ValueError) as exc:
        raise ValueError("hooks must be JSON serializable") from exc
    if request.mcp_servers is not None:
        if (
            not isinstance(request.mcp_servers, Mapping)
            or len(request.mcp_servers) > 32
        ):
            raise ValueError("mcp_servers must contain at most 32 server mappings")
        if any(
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(config, Mapping)
            for name, config in request.mcp_servers.items()
        ):
            raise ValueError("mcp_servers must map non-empty names to mappings")
        try:
            encoded_mcp = json.dumps(request.mcp_servers, default=str).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("mcp_servers must be JSON serializable") from exc
        if len(encoded_mcp) > 256_000:
            raise ValueError("mcp_servers exceed the plugin-agent size limit")
    if (
        not isinstance(request.inline_agents, Mapping)
        or len(request.inline_agents) > 16
    ):
        raise ValueError("inline_agents must contain at most 16 definitions")
    for agent_id, definition in request.inline_agents.items():
        if not isinstance(agent_id, str) or not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", agent_id
        ):
            raise ValueError("inline agent ids must be kebab-case")
        if not isinstance(definition, Mapping):
            raise ValueError(f"inline agent {agent_id} must be a mapping")
        turns = definition.get("max_iterations", 90)
        if (
            isinstance(turns, bool)
            or not isinstance(turns, int)
            or not 1 <= turns <= 90
        ):
            raise ValueError("inline agent max_iterations must be between 1 and 90")
    for label, value in (
        ("reasoning_config", request.reasoning_config),
        ("request_overrides", request.request_overrides),
        ("sandbox_policy", request.sandbox_policy),
    ):
        if value is not None and not isinstance(value, Mapping):
            raise ValueError(f"{label} must be a mapping or None")
    if request.fallback_model is not None and (
        not isinstance(request.fallback_model, str)
        or not request.fallback_model.strip()
    ):
        raise ValueError("fallback_model must be a non-empty string or None")
    if request.ephemeral_system_prompt is not None and not isinstance(
        request.ephemeral_system_prompt, str
    ):
        raise ValueError("ephemeral_system_prompt must be text or None")
    if request.max_budget_usd is not None and (
        isinstance(request.max_budget_usd, bool)
        or not isinstance(request.max_budget_usd, int | float)
        or not math.isfinite(request.max_budget_usd)
        or request.max_budget_usd <= 0
    ):
        raise ValueError("max_budget_usd must be finite and positive")
    if request.workdir is not None:
        path = Path(request.workdir).expanduser()
        if not path.is_dir():
            raise ValueError("workdir must be an existing directory")


def _agent_override_allowed(
    plugin_id: str,
    kind: str,
    value: str | None,
    *,
    config: Mapping[str, Any] | None = None,
) -> bool:
    if not value:
        return True
    try:
        if config is None:
            from hermes_cli.config import load_config

            config = load_config() or {}
        entry = (((config or {}).get("plugins") or {}).get("entries") or {}).get(
            plugin_id, {}
        )
        policy = entry.get("agent") if isinstance(entry, dict) else None
        if not isinstance(policy, dict) or not policy.get(
            f"allow_{kind}_override", False
        ):
            return False
        allowed = policy.get(f"allowed_{kind}s")
        return not isinstance(allowed, list) or "*" in allowed or value in allowed
    except Exception:
        return False


def _request_payload(plugin_id: str, request: PluginAgentRunRequest) -> dict[str, Any]:
    body = asdict(request)
    body["workdir"] = (
        str(Path(request.workdir).expanduser().resolve()) if request.workdir else None
    )
    return {
        "protocol_version": _PROTOCOL_VERSION,
        "type": "run",
        "plugin_id": plugin_id,
        "request": body,
    }


def _read_stream(
    stream,
    events: queue.Queue,
    label: str,
    *,
    stopped: threading.Event,
) -> None:
    try:
        while not stopped.is_set():
            line = stream.readline(_MAX_FRAME_BYTES + 1)
            if line == "":
                break
            while not stopped.is_set():
                try:
                    events.put((label, line), timeout=0.05)
                    break
                except queue.Full:
                    continue
    except (OSError, ValueError):
        pass
    finally:
        if not stopped.is_set():
            try:
                events.put((f"{label}_eof", ""), timeout=0.05)
            except queue.Full:
                pass


def _exchange_worker(
    payload: dict[str, Any],
    *,
    workdir: Path | None,
    idle_timeout_seconds: float,
    wall_timeout_seconds: float,
    worker_argv: list[str] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    resource_limits: ProcessResourceLimits | None = None,
    termination_policy: TerminationPolicy | None = None,
) -> dict[str, Any]:
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > _MAX_REQUEST_BYTES:
        raise ValueError("plugin-agent request frame is too large")

    package_root = Path(__file__).resolve().parent.parent
    default_worker_argv = [
        sys.executable,
        "-c",
        (
            "import runpy,sys;sys.path.insert(0,sys.argv[1]);"
            "runpy.run_module('agent.plugin_agent_worker',run_name='__main__')"
        ),
        str(package_root),
    ]
    tree = ManagedProcessTree.spawn(
        worker_argv or default_worker_argv,
        policy=termination_policy
        or TerminationPolicy(
            cooperative_grace_seconds=5.0,
            term_grace_seconds=5.0,
            kill_grace_seconds=2.0,
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
    events: queue.Queue = queue.Queue(maxsize=_MAX_QUEUED_FRAMES)
    stopped = threading.Event()
    stdout_reader = threading.Thread(
        target=_read_stream,
        args=(tree.process.stdout, events, "stdout"),
        kwargs={"stopped": stopped},
        daemon=True,
    )
    stderr_reader = threading.Thread(
        target=_read_stream,
        args=(tree.process.stderr, events, "stderr"),
        kwargs={"stopped": stopped},
        daemon=True,
    )
    stdout_reader.start()
    stderr_reader.start()
    tree.process.stdin.write(encoded + "\n")
    tree.process.stdin.flush()

    started = last_activity = time.monotonic()
    stderr_tail = ""
    limits = resource_limits or ProcessResourceLimits()
    try:
        while True:
            now = time.monotonic()
            if is_cancelled is not None and is_cancelled():
                raise _PluginAgentCancelled("plugin-agent run cancelled")
            violation = tree.resource_violation(limits)
            if violation is not None:
                raise _PluginAgentResourceExceeded(violation)
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
                continue
            if label != "stdout":
                continue
            if len(line.encode("utf-8")) > _MAX_FRAME_BYTES:
                raise RuntimeError("plugin-agent response frame is too large")
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
            last_activity = time.monotonic()
    finally:
        stopped.set()
        try:
            tree.process.stdin.close()
        except Exception:
            pass
        tree.close()
        stdout_reader.join(timeout=1.0)
        stderr_reader.join(timeout=1.0)


class PluginAgentRunner:
    """Plugin-bound facade that never exposes a live agent or credentials."""

    starts_request_mcp = True

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
                workdir=Path(request.workdir).expanduser().resolve()
                if request.workdir
                else None,
                idle_timeout_seconds=request.idle_timeout_seconds,
                wall_timeout_seconds=request.wall_timeout_seconds,
                is_cancelled=is_cancelled,
                resource_limits=ProcessResourceLimits(
                    max_rss_bytes=request.max_process_tree_rss_bytes,
                    max_cpu_seconds=request.max_process_tree_cpu_seconds,
                    max_descendants=request.max_descendants,
                ),
                termination_policy=TerminationPolicy(
                    cooperative_grace_seconds=request.cooperative_shutdown_seconds,
                    term_grace_seconds=request.term_grace_seconds,
                    kill_grace_seconds=request.kill_reap_grace_seconds,
                    wait_timeout_seconds=request.kill_reap_grace_seconds,
                ),
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
        except _PluginAgentResourceExceeded as exc:
            return PluginAgentRunResult(
                final_response="",
                session_id=request.session_id or "",
                provider=request.provider or "",
                model=request.model or "",
                status="failed",
                pending_interaction=None,
                usage={},
                audit={
                    "plugin_id": self.plugin_id,
                    "failure_kind": "resource_limit",
                    "resource_code": str(exc),
                },
            )


__all__ = ["PluginAgentRunRequest", "PluginAgentRunResult", "PluginAgentRunner"]
