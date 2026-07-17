"""Child entry point for :mod:`agent.plugin_agent`; not a public API."""

from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import sys
import threading
from typing import Any


_PROTOCOL_VERSION = 1
_MAX_REQUEST_BYTES = 1_000_000
_MAX_FRAME_BYTES = 4_000_000
_protocol_stdout = sys.stdout
_emit_lock = threading.Lock()
_cancel_event = threading.Event()
_active_agent: Any = None


def _emit(frame_type: str, **payload: Any) -> None:
    frame = {"protocol_version": _PROTOCOL_VERSION, "type": frame_type, **payload}
    encoded = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _MAX_FRAME_BYTES:
        raise RuntimeError("plugin-agent output frame exceeds protocol limit")
    with _emit_lock:
        _protocol_stdout.write(encoded + "\n")
        _protocol_stdout.flush()


def _sanitize(value: Any, limit: int = 2000) -> str:
    text = str(value or "")[:limit]
    try:
        from agent.redact import redact_sensitive_text

        return redact_sensitive_text(text)
    except Exception:
        return text


def _interaction(kind: str, payload: dict[str, Any]) -> dict[str, str]:
    safe = {key: _sanitize(value, 1000) for key, value in payload.items()}
    digest = hashlib.sha256(
        json.dumps([kind, safe], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    descriptor = {"kind": kind, "action_digest": digest, **safe}
    _emit("interaction", interaction=descriptor)
    return descriptor


def _configured_model(requested: str | None) -> str:
    if requested:
        return requested
    from hermes_cli.config import load_config

    configured = (load_config() or {}).get("model")
    if isinstance(configured, dict):
        return str(configured.get("default") or configured.get("model") or "").strip()
    if isinstance(configured, str):
        return configured.strip()
    return ""


def _tool_name(schema: dict[str, Any]) -> str:
    function = schema.get("function")
    return str(function.get("name", "")) if isinstance(function, dict) else ""


def _run(payload: dict[str, Any]) -> dict[str, Any]:
    global _active_agent
    from agent.plugin_agent import PluginAgentRunRequest, _validate_request

    request_data = payload.get("request")
    if not isinstance(request_data, dict):
        raise ValueError("request payload is missing")
    request_data = dict(request_data)
    if request_data.get("workdir"):
        request_data["workdir"] = Path(request_data["workdir"])
    for name in ("enabled_toolsets", "allowed_tools", "denied_tools", "skills"):
        if request_data.get(name) is not None:
            request_data[name] = tuple(request_data[name])
    request = PluginAgentRunRequest(**request_data)
    _validate_request(request)
    plugin_id = str(payload.get("plugin_id") or "")
    if not plugin_id:
        raise ValueError("plugin_id is missing")

    # Bound provider calls inside this isolated process without introducing a
    # parent-visible config/env mutation or a new AIAgent constructor surface.
    import hermes_cli.timeouts as timeout_mod

    configured_timeout = timeout_mod.get_provider_request_timeout
    timeout_mod.get_provider_request_timeout = lambda provider, model: min(
        float(request.provider_request_timeout_seconds),
        float(configured_timeout(provider, model)),
    )

    from tools.registry import registry

    allowed = None if request.allowed_tools is None else set(request.allowed_tools)
    denied = set(request.denied_tools)
    pending: list[dict[str, str]] = []
    with registry.scoped_names(allowed_names=allowed, denied_names=denied):
        from run_agent import AIAgent
        from hermes_cli.runtime_provider import resolve_runtime_provider
        from hermes_state import SessionDB

        known = set(registry._tools)
        unknown = sorted((set(request.allowed_tools or ()) | denied) - known)
        if unknown:
            raise ValueError(f"unknown tool name(s): {', '.join(unknown)}")

        model = _configured_model(request.model)
        runtime = resolve_runtime_provider(
            requested=request.provider, target_model=model or None
        )
        session_db = SessionDB()
        history = None
        if request.context_mode == "shared":
            if session_db.get_session(request.session_id) is None:
                raise ValueError("session_id does not identify an existing session")
            history = session_db.get_messages_as_conversation(request.session_id)

        prompt = request.prompt
        if request.skills:
            from agent.skill_commands import build_preloaded_skills_prompt

            skill_text, _loaded, missing = build_preloaded_skills_prompt(
                list(request.skills), task_id=request.session_id
            )
            if missing:
                raise ValueError(f"unknown skill(s): {', '.join(missing)}")
            if skill_text:
                # Skill content is part of the new user turn, never a system
                # prompt mutation, preserving cache and role alternation.
                prompt = f"{skill_text}\n\n{request.prompt}"

        def pause(descriptor: dict[str, str]) -> None:
            pending.append(descriptor)
            active = _active_agent
            if active is not None:
                active._interrupt_requested = True

        def approval(command, description, **_kwargs):
            pause(
                _interaction(
                    "approval",
                    {"command": command, "description": description},
                )
            )
            return "deny"

        def clarify(question, choices=None):
            pause(
                _interaction(
                    "clarification",
                    {"question": question, "choices": choices or []},
                )
            )
            return "Interaction paused for a host-provided answer."

        def sudo():
            pause(_interaction("sudo", {"message": "sudo credential required"}))
            return ""

        def secret(name, prompt_text, metadata):
            pause(
                _interaction(
                    "secret",
                    {"name": name, "prompt": prompt_text, "skill": metadata.get("skill_name", "")},
                )
            )
            return {"success": False, "stored_as": name, "validated": False}

        from tools.terminal_tool import set_approval_callback, set_sudo_password_callback
        from tools.skills_tool import set_secret_capture_callback

        set_approval_callback(approval)
        set_sudo_password_callback(sudo)
        set_secret_capture_callback(secret)
        try:
            agent = AIAgent(
                model=model,
                max_iterations=request.max_iterations,
                provider=runtime.get("provider"),
                base_url=runtime.get("base_url"),
                api_key=runtime.get("api_key"),
                api_mode=runtime.get("api_mode"),
                acp_command=runtime.get("command"),
                acp_args=runtime.get("args"),
                credential_pool=runtime.get("credential_pool"),
                enabled_toolsets=(
                    list(request.enabled_toolsets)
                    if request.enabled_toolsets is not None
                    else None
                ),
                quiet_mode=True,
                platform="plugin-agent",
                session_id=request.session_id,
                session_db=session_db,
                clarify_callback=clarify,
            )
            agent._api_max_retries = request.max_api_attempts
            _active_agent = agent
            if _cancel_event.is_set():
                agent._interrupt_requested = True
            visible = {
                name for name in registry._tools
                if (allowed is None or name in allowed) and name not in denied
            }
            agent.tools = [tool for tool in (agent.tools or []) if _tool_name(tool) in visible]
            agent.valid_tool_names = {_tool_name(tool) for tool in agent.tools}
            if not agent.valid_tool_names <= visible:
                raise RuntimeError("agent tool scope verification failed")

            _emit("progress", phase="running", session_id=agent.session_id)
            response = agent.run_conversation(prompt, conversation_history=history)
            usage = {
                "input_tokens": int(getattr(agent, "session_input_tokens", 0) or 0),
                "output_tokens": int(getattr(agent, "session_output_tokens", 0) or 0),
                "cache_read_tokens": int(getattr(agent, "session_cache_read_tokens", 0) or 0),
                "cache_write_tokens": int(getattr(agent, "session_cache_write_tokens", 0) or 0),
            }
            failed = bool(response.get("failed"))
            return {
                "final_response": _sanitize(response.get("final_response", ""), 500_000),
                "session_id": str(agent.session_id or ""),
                "provider": str(agent.provider or ""),
                "model": str(agent.model or ""),
                "status": "paused" if pending else ("failed" if failed else "completed"),
                "pending_interaction": pending[0] if pending else None,
                "usage": usage,
                "audit": {
                    "plugin_id": plugin_id,
                    "tool_names": sorted(agent.valid_tool_names),
                    "api_calls": int(response.get("api_calls", 0) or 0),
                },
            }
        finally:
            _active_agent = None
            set_approval_callback(None)
            set_sudo_password_callback(None)
            set_secret_capture_callback(None)
            close_db = getattr(session_db, "close", None)
            if callable(close_db):
                close_db()


def main() -> int:
    raw = sys.stdin.buffer.readline(_MAX_REQUEST_BYTES + 1)
    if not raw or len(raw) > _MAX_REQUEST_BYTES:
        return 2
    try:
        payload = json.loads(raw)
        if payload.get("protocol_version") != _PROTOCOL_VERSION or payload.get("type") != "run":
            raise ValueError("unsupported plugin-agent protocol frame")

        def watch_coordinator() -> None:
            # The parent keeps stdin open as a lifeline after the request. EOF
            # means it died or cancelled; interrupt the synchronous agent loop.
            sys.stdin.buffer.read(1)
            _cancel_event.set()
            agent = _active_agent
            if agent is not None:
                agent._interrupt_requested = True

        threading.Thread(
            target=watch_coordinator, name="plugin-agent-lifeline", daemon=True
        ).start()
        with redirect_stdout(sys.stderr):
            result = _run(payload)
    except BaseException as exc:
        plugin_id = ""
        try:
            plugin_id = str(payload.get("plugin_id") or "")
        except Exception:
            pass
        result = {
            "final_response": "",
            "session_id": "",
            "provider": "",
            "model": "",
            "status": "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
            "pending_interaction": None,
            "usage": {},
            "audit": {
                "plugin_id": plugin_id,
                "failure_kind": type(exc).__name__,
                "error": _sanitize(exc),
            },
        }
    _emit("result", result=result)
    # Keep the direct worker alive until the coordinator acknowledges receipt
    # by closing its stdin lifeline. This closes the tiny result/exit race in
    # which descendants could otherwise outlive an already-reaped parent.
    _cancel_event.wait(timeout=2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
