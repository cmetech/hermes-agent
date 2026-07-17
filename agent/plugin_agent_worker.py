"""Child entry point for :mod:`agent.plugin_agent`; not a public API."""

from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import re
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

_ARCHON_HOOK_EVENT_MAP = {
    "PreToolUse": "pre_tool_call",
    "PostToolUse": "post_tool_call",
    "PostToolUseFailure": "post_tool_call",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "SessionStart": "on_session_start",
    "SessionEnd": "on_session_end",
    "UserPromptSubmit": "pre_llm_call",
    "PermissionRequest": "pre_approval_request",
    "Setup": "on_session_start",
    "Elicitation": "pre_approval_request",
    "ElicitationResult": "post_approval_response",
    "InstructionsLoaded": "pre_llm_call",
    "TaskCompleted": "subagent_stop",
}
_UNSUPPORTED_ARCHON_HOOK_EVENTS = {
    "Notification",
    "Stop",
    "PreCompact",
    "TeammateIdle",
    "ConfigChange",
    "WorktreeCreate",
    "WorktreeRemove",
}


def _translate_hook_response(event: str, response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict):
        raise ValueError(f"{event} hook response must be a mapping")
    specific = response.get("hookSpecificOutput")
    if specific is not None and not isinstance(specific, dict):
        raise ValueError(f"{event} hookSpecificOutput must be a mapping")
    specific = specific or {}
    declared = specific.get("hookEventName")
    if declared is not None and declared != event:
        raise ValueError(f"{event} hookEventName does not match {declared}")
    translated: dict[str, Any] = {}
    permission = specific.get("permissionDecision")
    reason = specific.get("permissionDecisionReason") or response.get("stopReason")
    if permission == "deny" or response.get("decision") == "block":
        translated.update({
            "action": "block",
            "message": str(reason or "blocked by node hook"),
        })
    elif permission == "ask":
        translated.update({
            "action": "approve",
            "message": str(reason or "approval requested by node hook"),
        })
    elif permission not in {None, "allow"}:
        raise ValueError(f"{event} permissionDecision is invalid")
    if response.get("continue") is False:
        translated.update({
            "action": "block",
            "message": str(reason or "node hook stopped execution"),
        })
    updated = specific.get("updatedInput")
    if updated is not None:
        if not isinstance(updated, dict):
            raise ValueError(f"{event} updatedInput must be a mapping")
        translated["args"] = dict(updated)
    context = specific.get("additionalContext") or response.get("systemMessage")
    if context:
        translated["context"] = str(context)
    if "updatedMCPToolOutput" in specific:
        translated["output"] = specific["updatedMCPToolOutput"]
    if "content" in specific:
        translated["content"] = specific["content"]
    if "action" in specific:
        translated["elicitation_action"] = specific["action"]
    return translated or None


def _compile_node_hook_resources(raw_hooks: Any) -> tuple[dict[str, Any], ...]:
    if raw_hooks is None:
        return ()
    if not isinstance(raw_hooks, (tuple, list)):
        raise ValueError("node hooks must be a list")
    compiled = []
    for index, raw in enumerate(raw_hooks):
        if not isinstance(raw, dict):
            raise ValueError(f"node hook {index} must be a mapping")
        unknown = set(raw) - {"event", "matcher", "response", "timeout"}
        if unknown:
            raise ValueError(
                f"node hook {index} has unknown field: {sorted(unknown)[0]}"
            )
        event = raw.get("event")
        if event in _UNSUPPORTED_ARCHON_HOOK_EVENTS:
            raise ValueError(f"unsupported node hook event: {event}")
        if event not in _ARCHON_HOOK_EVENT_MAP:
            raise ValueError(f"unknown node hook event: {event}")
        matcher = raw.get("matcher")
        try:
            compiled_matcher = re.compile(matcher) if matcher is not None else None
        except (TypeError, re.error) as exc:
            raise ValueError(f"node hook {index} matcher is invalid") from exc
        timeout = raw.get("timeout", 30)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not 0 < timeout <= 300
        ):
            raise ValueError(f"node hook {index} timeout must be between 0 and 300")
        response = raw.get("response")
        translated = _translate_hook_response(str(event), response)
        compiled.append({
            "event": event,
            "hermes_event": _ARCHON_HOOK_EVENT_MAP[event],
            "matcher": compiled_matcher,
            "timeout": float(timeout),
            "response": translated,
        })
    return tuple(compiled)


def _install_node_hooks(raw_hooks: Any) -> list[dict[str, str]]:
    resources = _compile_node_hook_resources(raw_hooks)
    if not resources:
        return []
    from hermes_cli.plugins import get_plugin_manager

    manager = get_plugin_manager()
    observed: list[dict[str, str]] = []

    def matches(resource: dict[str, Any], kwargs: dict[str, Any]) -> bool:
        matcher = resource["matcher"]
        return matcher is None or bool(
            matcher.search(str(kwargs.get("tool_name") or ""))
        )

    for resource in resources:
        event = str(resource["event"])
        hermes_event = str(resource["hermes_event"])
        response = resource["response"]

        def callback(
            _resource=resource,
            _event=event,
            _response=response,
            **kwargs,
        ):
            if not matches(_resource, kwargs):
                return None
            if _event == "PostToolUseFailure" and kwargs.get("status") not in {
                "failed",
                "error",
                "blocked",
            }:
                return None
            if _event == "PostToolUse" and kwargs.get("status") in {
                "failed",
                "error",
                "blocked",
            }:
                return None
            observed.append({
                "event": _event,
                "tool_name": str(kwargs.get("tool_name") or ""),
            })
            return dict(_response) if isinstance(_response, dict) else None

        manager._hooks.setdefault(hermes_event, []).append(callback)
        if event == "PreToolUse" and isinstance(response, dict) and "args" in response:

            def middleware(
                _resource=resource,
                _response=response,
                **kwargs,
            ):
                if not matches(_resource, kwargs):
                    return None
                observed.append({
                    "event": "PreToolUse",
                    "tool_name": str(kwargs.get("tool_name") or ""),
                })
                return {
                    "args": dict(_response["args"]),
                    "source": "workflow-node-hook",
                }

            manager._middleware.setdefault("tool_request", []).append(middleware)
    return observed


def _build_inline_agent_handler(
    *,
    plugin_id: str,
    definitions: dict[str, Any],
    workdir: Path,
    parent_request: Any,
    runner_factory,
    emit_progress,
    pause,
):
    """Build the synchronous worker-local ``workflow_agent`` dispatcher."""
    admission_lock = threading.Lock()
    total_started = 0
    maximum_children = max(0, min(64, getattr(parent_request, "max_descendants", 32)))

    def handler(args: dict[str, Any]) -> dict[str, Any]:
        nonlocal total_started
        task = args.get("task")
        if not isinstance(task, str) or not task.strip() or len(task) > 100_000:
            return {"error": "task must contain 1 to 100000 characters"}
        agent_id = args.get("agent_id")
        definition = definitions.get(agent_id)
        if not isinstance(definition, dict):
            return {"error": "unknown inline agent"}
        with admission_lock:
            if total_started >= maximum_children:
                return {"error": "inline agent descendant limit exhausted"}
            total_started += 1
        instructions = str(definition.get("instructions") or "").strip()
        prompt_parts = [str(definition["prompt"]).strip(), task.strip()]
        if instructions:
            prompt_parts.insert(0, instructions)
        prompt = "\n\n".join(prompt_parts)
        emit_progress(phase="inline_agent_started", agent_id=str(agent_id))
        from agent.plugin_agent import PluginAgentRunRequest

        parent = parent_request
        request = PluginAgentRunRequest(
            prompt=prompt,
            provider=getattr(parent, "provider", None),
            model=definition.get("model") or getattr(parent, "model", None),
            allowed_tools=(
                tuple(definition["allowed_tools"])
                if definition.get("allowed_tools") is not None
                else None
            ),
            denied_tools=tuple(definition.get("denied_tools", ())),
            workdir=workdir,
            max_iterations=int(definition.get("max_iterations", 90)),
            max_api_attempts=getattr(parent, "max_api_attempts", 1),
            idle_timeout_seconds=getattr(parent, "idle_timeout_seconds", 300.0),
            wall_timeout_seconds=getattr(parent, "wall_timeout_seconds", 1800.0),
            provider_request_timeout_seconds=getattr(
                parent, "provider_request_timeout_seconds", 300.0
            ),
            approved_action_digest=getattr(parent, "approved_action_digest", None),
            reasoning_config=getattr(parent, "reasoning_config", None),
            fallback_model=getattr(parent, "fallback_model", None),
            request_overrides=getattr(parent, "request_overrides", {}),
            max_budget_usd=getattr(parent, "max_budget_usd", None),
            sandbox_policy=getattr(parent, "sandbox_policy", None),
            max_process_tree_rss_bytes=getattr(
                parent, "max_process_tree_rss_bytes", 2048 * 1024 * 1024
            ),
            max_process_tree_cpu_seconds=getattr(
                parent, "max_process_tree_cpu_seconds", 900.0
            ),
            max_descendants=max(0, getattr(parent, "max_descendants", 32) - 1),
            cooperative_shutdown_seconds=getattr(
                parent, "cooperative_shutdown_seconds", 5.0
            ),
            term_grace_seconds=getattr(parent, "term_grace_seconds", 5.0),
            kill_reap_grace_seconds=getattr(parent, "kill_reap_grace_seconds", 2.0),
        )
        result = runner_factory(plugin_id).run(request)
        if result.status == "paused":
            descriptor = dict(result.pending_interaction or {})
            pause(descriptor)
            emit_progress(phase="inline_agent_paused", agent_id=str(agent_id))
            return {"status": "paused", "pending_interaction": descriptor}
        emit_progress(phase=f"inline_agent_{result.status}", agent_id=str(agent_id))
        if result.status != "completed":
            return {
                "status": result.status,
                "error": "isolated inline agent did not complete",
            }
        return {
            "status": "completed",
            "result": _sanitize(result.final_response, 64_000),
            "usage": dict(result.usage),
        }

    return handler


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


def _interaction_descriptor(kind: str, payload: dict[str, Any]) -> dict[str, str]:
    safe = {key: _sanitize(value, 1000) for key, value in payload.items()}
    digest = hashlib.sha256(
        json.dumps([kind, safe], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {"kind": kind, "action_digest": digest, **safe}


def _interaction(kind: str, payload: dict[str, Any]) -> dict[str, str]:
    descriptor = _interaction_descriptor(kind, payload)
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
    for name in (
        "enabled_toolsets",
        "allowed_tools",
        "denied_tools",
        "skills",
        "hooks",
    ):
        if request_data.get(name) is not None:
            request_data[name] = tuple(request_data[name])
    request = PluginAgentRunRequest(**request_data)
    _validate_request(request)
    plugin_id = str(payload.get("plugin_id") or "")
    if not plugin_id:
        raise ValueError("plugin_id is missing")

    # A node worker sees only the MCP definitions carried by its immutable
    # request. Environment placeholders resolve here, after IPC, and resolved
    # values are never returned to the plugin or parent process.
    from tools import mcp_tool as worker_mcp

    try:
        from hermes_cli.env_loader import load_hermes_dotenv

        load_hermes_dotenv()
    except Exception:
        pass
    raw_mcp = dict(request.mcp_servers or {})
    resolved_mcp = {
        str(name): worker_mcp._interpolate_env_vars(dict(config))
        for name, config in raw_mcp.items()
    }
    original_mcp_loader = worker_mcp._load_mcp_config
    worker_mcp._load_mcp_config = lambda: resolved_mcp

    # Bound provider calls inside this isolated process without introducing a
    # parent-visible config/env mutation or a new AIAgent constructor surface.
    import hermes_cli.timeouts as timeout_mod

    configured_timeout = timeout_mod.get_provider_request_timeout
    timeout_mod.get_provider_request_timeout = lambda provider, model: min(
        float(request.provider_request_timeout_seconds),
        float(configured_timeout(provider, model)),
    )

    # Importing the agent after replacing the loader ensures model-tool
    # discovery cannot connect to profile-global MCP servers.
    from run_agent import AIAgent
    from tools.registry import registry

    allowed = None if request.allowed_tools is None else set(request.allowed_tools)
    denied = set(request.denied_tools) | {"delegate_task"}
    if not request.inline_agents:
        denied.add("workflow_agent")
    pending: list[dict[str, str]] = []
    approved_action_consumed = False

    def pause(descriptor: dict[str, str]) -> None:
        pending.append(descriptor)
        active = _active_agent
        if active is not None:
            active._interrupt_requested = True

    inline_registered = False
    if request.inline_agents:
        from agent.plugin_agent import PluginAgentRunner

        inline_handler = _build_inline_agent_handler(
            plugin_id=plugin_id,
            definitions={
                str(name): dict(definition)
                for name, definition in request.inline_agents.items()
            },
            workdir=Path(request.workdir or Path.cwd()),
            parent_request=request,
            runner_factory=PluginAgentRunner,
            emit_progress=lambda **progress: _emit("progress", **progress),
            pause=pause,
        )
        registry.register(
            name="workflow_agent",
            toolset="workflow-node",
            schema={
                "name": "workflow_agent",
                "description": "Run one declared workflow-local inline agent synchronously.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_id": {
                            "type": "string",
                            "enum": sorted(request.inline_agents),
                        },
                        "task": {"type": "string", "maxLength": 100000},
                    },
                    "required": ["agent_id", "task"],
                    "additionalProperties": False,
                },
            },
            handler=lambda args, **_kwargs: json.dumps(
                inline_handler(args), ensure_ascii=False
            ),
        )
        inline_registered = True
    with registry.scoped_names(allowed_names=allowed, denied_names=denied):
        from hermes_cli.runtime_provider import resolve_runtime_provider
        from hermes_state import SessionDB

        known = set(registry._tools)
        unknown = sorted(
            (set(request.allowed_tools or ()) | set(request.denied_tools)) - known
        )
        if unknown:
            worker_mcp.shutdown_mcp_servers()
            worker_mcp._load_mcp_config = original_mcp_loader
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

        def approval(command, description, **_kwargs):
            nonlocal approved_action_consumed
            descriptor = _interaction_descriptor(
                "approval",
                {"command": command, "description": description},
            )
            if (
                not approved_action_consumed
                and request.approved_action_digest == descriptor["action_digest"]
            ):
                approved_action_consumed = True
                return "once"
            _emit("interaction", interaction=descriptor)
            pause(descriptor)
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
                    {
                        "name": name,
                        "prompt": prompt_text,
                        "skill": metadata.get("skill_name", ""),
                    },
                )
            )
            return {"success": False, "stored_as": name, "validated": False}

        from tools.terminal_tool import (
            set_approval_callback,
            set_sudo_password_callback,
        )
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
                ephemeral_system_prompt=request.ephemeral_system_prompt,
                reasoning_config=dict(request.reasoning_config or {}),
                fallback_model=(
                    {
                        "provider": runtime.get("provider"),
                        "model": request.fallback_model,
                    }
                    if request.fallback_model
                    else None
                ),
                request_overrides=dict(request.request_overrides),
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
            hook_events = _install_node_hooks(request.hooks)
            if _cancel_event.is_set():
                agent._interrupt_requested = True
            visible = {
                name
                for name in registry._tools
                if (allowed is None or name in allowed) and name not in denied
            }
            agent.tools = [
                tool for tool in (agent.tools or []) if _tool_name(tool) in visible
            ]
            agent.valid_tool_names = {_tool_name(tool) for tool in agent.tools}
            if not agent.valid_tool_names <= visible:
                raise RuntimeError("agent tool scope verification failed")

            _emit("progress", phase="running", session_id=agent.session_id)
            response = agent.run_conversation(prompt, conversation_history=history)
            usage = {
                "input_tokens": int(getattr(agent, "session_input_tokens", 0) or 0),
                "output_tokens": int(getattr(agent, "session_output_tokens", 0) or 0),
                "cache_read_tokens": int(
                    getattr(agent, "session_cache_read_tokens", 0) or 0
                ),
                "cache_write_tokens": int(
                    getattr(agent, "session_cache_write_tokens", 0) or 0
                ),
            }
            failed = bool(response.get("failed"))
            return {
                "final_response": _sanitize(
                    response.get("final_response", ""), 500_000
                ),
                "session_id": str(agent.session_id or ""),
                "provider": str(agent.provider or ""),
                "model": str(agent.model or ""),
                "status": "paused"
                if pending
                else ("failed" if failed else "completed"),
                "pending_interaction": pending[0] if pending else None,
                "usage": usage,
                "audit": {
                    "plugin_id": plugin_id,
                    "tool_names": sorted(agent.valid_tool_names),
                    "api_calls": int(response.get("api_calls", 0) or 0),
                    "hook_events": hook_events,
                    "max_budget_usd": request.max_budget_usd,
                    "sandbox_policy_declared": request.sandbox_policy is not None,
                },
            }
        finally:
            _active_agent = None
            set_approval_callback(None)
            set_sudo_password_callback(None)
            set_secret_capture_callback(None)
            worker_mcp.shutdown_mcp_servers()
            worker_mcp._load_mcp_config = original_mcp_loader
            if inline_registered:
                registry.deregister("workflow_agent")
            close_db = getattr(session_db, "close", None)
            if callable(close_db):
                close_db()


def main() -> int:
    raw = sys.stdin.buffer.readline(_MAX_REQUEST_BYTES + 1)
    if not raw or len(raw) > _MAX_REQUEST_BYTES:
        return 2
    try:
        payload = json.loads(raw)
        if (
            payload.get("protocol_version") != _PROTOCOL_VERSION
            or payload.get("type") != "run"
        ):
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
