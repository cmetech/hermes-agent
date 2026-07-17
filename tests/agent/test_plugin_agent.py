"""Contracts for isolated host-owned agents exposed to trusted plugins."""

from __future__ import annotations

import dataclasses
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import threading
import time

import pytest
import psutil

from agent.plugin_agent import (
    PluginAgentRunRequest,
    PluginAgentRunResult,
    PluginAgentRunner,
    _PluginAgentCancelled,
    _PluginAgentResourceExceeded,
    _exchange_worker,
)
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from tools.managed_process import ProcessResourceLimits, TerminationPolicy
from tools.registry import ToolRegistry


def _register(registry: ToolRegistry, name: str) -> None:
    registry.register(
        name=name,
        toolset="test",
        schema={"name": name, "description": name, "parameters": {"type": "object"}},
        handler=lambda args: name,
    )


def test_allowed_and_denied_tools_are_enforced_before_first_call() -> None:
    registry = ToolRegistry()
    _register(registry, "read_file")
    _register(registry, "terminal")

    with registry.scoped_names(
        allowed_names={"read_file", "terminal"},
        denied_names={"terminal"},
    ):
        assert registry.get_entry("read_file") is not None
        assert registry.get_entry("terminal") is None
        assert registry.get_all_tool_names() == ["read_file"]
        assert registry.dispatch("terminal", {}) == '{"error": "Unknown tool: terminal"}'

    assert registry.get_all_tool_names() == ["read_file", "terminal"]


def test_empty_allowlist_means_no_names_and_deny_is_applied_last() -> None:
    registry = ToolRegistry()
    _register(registry, "read_file")

    with registry.scoped_names(allowed_names=set()):
        assert registry.get_entry("read_file") is None
        assert registry.get_definitions({"read_file"}) == []

    with registry.scoped_names(
        allowed_names={"read_file"}, denied_names={"read_file"}
    ):
        assert registry.get_entry("read_file") is None


def test_scope_generation_changes_on_enter_and_exit_and_restores_after_error() -> None:
    registry = ToolRegistry()
    _register(registry, "read_file")
    before = registry._generation

    with pytest.raises(RuntimeError, match="boom"):
        with registry.scoped_names(allowed_names={"read_file"}):
            assert registry._generation == before + 1
            raise RuntimeError("boom")

    assert registry._generation == before + 2
    assert registry.get_entry("read_file") is not None


def test_incompatible_overlapping_scopes_are_rejected() -> None:
    registry = ToolRegistry()
    _register(registry, "read_file")
    _register(registry, "terminal")

    with registry.scoped_names(allowed_names={"read_file"}):
        with pytest.raises(RuntimeError, match="scope"):
            with registry.scoped_names(allowed_names={"terminal"}):
                pass


def test_deferred_registration_remains_hidden_from_queries_and_dispatch() -> None:
    registry = ToolRegistry()
    _register(registry, "read_file")

    with registry.scoped_names(allowed_names={"read_file"}):
        _register(registry, "deferred_tool")
        assert registry.get_entry("deferred_tool") is None
        assert "deferred_tool" not in registry.get_all_tool_names()
        assert registry.get_definitions({"deferred_tool"}) == []
        assert "Unknown tool" in registry.dispatch("deferred_tool", {})

    assert registry.get_entry("deferred_tool") is not None


def test_request_and_result_are_immutable() -> None:
    request = PluginAgentRunRequest(prompt="hello")
    result = PluginAgentRunResult(
        final_response="done",
        session_id="session-1",
        provider="test",
        model="fake",
        status="completed",
        pending_interaction=None,
        usage={"input_tokens": 1},
        audit={"plugin_id": "test-plugin"},
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        request.prompt = "changed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = "failed"  # type: ignore[misc]


def test_plugin_runner_returns_usage_without_exposing_credentials(monkeypatch) -> None:
    captured: dict = {}

    def fake_exchange(payload, **kwargs):
        captured.update(payload)
        return {
            "protocol_version": 1,
            "type": "result",
            "result": {
                "final_response": "used read_file",
                "session_id": "session-1",
                "provider": "fake",
                "model": "fake-model",
                "status": "completed",
                "pending_interaction": None,
                "usage": {"input_tokens": 3, "output_tokens": 2},
                "audit": {"plugin_id": "test-plugin", "tool_names": ["read_file"]},
            },
        }

    monkeypatch.setattr("agent.plugin_agent._exchange_worker", fake_exchange)
    result = PluginAgentRunner("test-plugin").run(
        PluginAgentRunRequest(prompt="Use read_file once", allowed_tools=("read_file",))
    )

    assert result.status == "completed"
    assert result.session_id
    assert result.usage["input_tokens"] == 3
    assert "api_key" not in result.audit
    assert "api_key" not in captured
    assert captured["plugin_id"] == "test-plugin"


@pytest.mark.parametrize(
    ("run_request", "message"),
    [
        (PluginAgentRunRequest(prompt=""), "prompt"),
        (PluginAgentRunRequest(prompt="x", max_iterations=0), "max_iterations"),
        (PluginAgentRunRequest(prompt="x", max_iterations=1.5), "max_iterations"),
        (PluginAgentRunRequest(prompt="x", max_api_attempts=0), "API attempts"),
        (
            PluginAgentRunRequest(prompt="x", cooperative_shutdown_seconds=0),
            "cooperative shutdown",
        ),
        (PluginAgentRunRequest(prompt="x", idle_timeout_seconds=0), "idle"),
        (PluginAgentRunRequest(prompt="x", idle_timeout_seconds=float("nan")), "idle"),
        (PluginAgentRunRequest(prompt="x", wall_timeout_seconds=float("inf")), "wall"),
        (PluginAgentRunRequest(prompt="x", wall_timeout_seconds=-1), "wall"),
        (PluginAgentRunRequest(prompt="x", max_descendants=-1), "descendants"),
        (
            PluginAgentRunRequest(
                prompt="x", idle_timeout_seconds=10, wall_timeout_seconds=5
            ),
            "idle",
        ),
        (
                PluginAgentRunRequest(
                    prompt="x",
                    idle_timeout_seconds=5,
                    provider_request_timeout_seconds=20,
                    wall_timeout_seconds=10,
                ),
            "provider",
        ),
    ],
)
def test_invalid_requests_fail_before_worker_start(
    monkeypatch, run_request, message
) -> None:
    started = False

    def should_not_start(*args, **kwargs):
        nonlocal started
        started = True
        raise AssertionError("worker started")

    monkeypatch.setattr("agent.plugin_agent._exchange_worker", should_not_start)

    with pytest.raises(ValueError, match=message):
        PluginAgentRunner("test-plugin").run(run_request)
    assert started is False


def test_invalid_workdir_and_shared_session_fail_before_worker_start(
    monkeypatch, tmp_path: Path
) -> None:
    file_path = tmp_path / "not-a-directory"
    file_path.write_text("x")
    monkeypatch.setattr(
        "agent.plugin_agent._exchange_worker",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("started")),
    )

    with pytest.raises(ValueError, match="workdir"):
        PluginAgentRunner("test-plugin").run(
            PluginAgentRunRequest(prompt="x", workdir=file_path)
        )
    with pytest.raises(ValueError, match="session_id"):
        PluginAgentRunner("test-plugin").run(
            PluginAgentRunRequest(prompt="x", context_mode="shared", session_id=None)
        )


@pytest.mark.parametrize("field", ["provider", "model"])
def test_provider_and_model_overrides_are_fail_closed_before_worker(
    monkeypatch, field: str
) -> None:
    monkeypatch.setattr(
        "agent.plugin_agent._exchange_worker",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("started")),
    )
    request = PluginAgentRunRequest(prompt="x", **{field: "untrusted-override"})
    with pytest.raises(PermissionError, match=field):
        PluginAgentRunner("test-plugin").run(request)


def test_parent_workdir_and_environment_are_unchanged(monkeypatch) -> None:
    cwd = os.getcwd()
    env = dict(os.environ)

    monkeypatch.setattr(
        "agent.plugin_agent._exchange_worker",
        lambda payload, **kwargs: {
            "protocol_version": 1,
            "type": "result",
            "result": {
                "final_response": "done",
                "session_id": "s",
                "provider": "fake",
                "model": "fake",
                "status": "completed",
                "pending_interaction": None,
                "usage": {},
                "audit": {},
            },
        },
    )

    PluginAgentRunner("test-plugin").run(PluginAgentRunRequest(prompt="x"))

    assert os.getcwd() == cwd
    assert dict(os.environ) == env


def test_plugin_context_agent_is_lazy_and_bound_to_manifest_key() -> None:
    manifest = PluginManifest(
        name="bare-name", source="test", key="workflow/test-plugin"
    )
    ctx = PluginContext(manifest, PluginManager())

    first = ctx.agent
    second = ctx.agent

    assert first is second
    assert isinstance(first, PluginAgentRunner)
    assert first.plugin_id == "workflow/test-plugin"


def test_real_workers_are_process_isolated_and_unknown_tools_fail_before_billing() -> None:
    import model_tools
    from tools.registry import registry

    cwd = os.getcwd()
    env = dict(os.environ)
    generation = registry._generation
    resolved_names = list(model_tools._last_resolved_tool_names)

    def run(name: str) -> PluginAgentRunResult:
        return PluginAgentRunner("test-plugin").run(
            PluginAgentRunRequest(
                prompt="must not reach a provider",
                allowed_tools=(name,),
                idle_timeout_seconds=15,
                wall_timeout_seconds=30,
                provider_request_timeout_seconds=10,
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("unknown_worker_a", "unknown_worker_b")))

    assert all(result.status == "failed" for result in results)
    assert all(result.audit["failure_kind"] == "ValueError" for result in results)
    assert all("unknown tool" in result.audit["error"] for result in results)
    assert registry._generation == generation
    assert model_tools._last_resolved_tool_names == resolved_names
    assert os.getcwd() == cwd
    assert dict(os.environ) == env


def test_worker_installs_fail_closed_dangerous_approval(monkeypatch) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    import hermes_state
    import run_agent
    from tools.terminal_tool import _get_approval_callback

    class FakeDB:
        pass

    class FakeAgent:
        def __init__(self, **kwargs):
            self.session_id = "worker-session"
            self.provider = "fake"
            self.model = "fake"
            self.tools = []
            self.valid_tool_names = set()
            self.session_input_tokens = 0
            self.session_output_tokens = 0
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0

        def run_conversation(self, prompt, conversation_history=None):
            assert self._api_max_retries == 2
            callback = _get_approval_callback()
            assert callback is not None
            assert callback("rm -rf /tmp/example", "dangerous") == "deny"
            return {"final_response": "denied", "api_calls": 0}

    monkeypatch.setattr(run_agent, "AIAgent", FakeAgent)
    monkeypatch.setattr(hermes_state, "SessionDB", FakeDB)
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **kwargs: {"provider": "fake", "base_url": "", "api_key": "secret"},
    )
    monkeypatch.setattr(worker, "_emit", lambda *args, **kwargs: None)

    result = worker._run({
        "plugin_id": "test-plugin",
        "request": dataclasses.asdict(
            PluginAgentRunRequest(
                prompt="attempt dangerous command",
                allowed_tools=(),
                max_api_attempts=2,
            )
        ),
    })

    assert result["status"] == "paused"
    assert result["pending_interaction"]["kind"] == "approval"
    assert "action_digest" in result["pending_interaction"]
    assert "api_key" not in result["audit"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descendant probe")
@pytest.mark.live_system_guard_bypass
def test_worker_timeout_terminates_descendants(tmp_path: Path) -> None:
    pid_file = tmp_path / "descendant.pid"
    code = (
        "import pathlib,subprocess,sys,time;"
        "sys.stdin.readline();"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid));"
        "time.sleep(60)"
    )
    with pytest.raises(TimeoutError, match="idle timeout"):
        _exchange_worker(
            {"protocol_version": 1, "type": "run"},
            workdir=tmp_path,
            idle_timeout_seconds=0.3,
            wall_timeout_seconds=3,
            worker_argv=[sys.executable, "-c", code],
        )

    deadline = time.monotonic() + 3
    descendant_pid = int(pid_file.read_text())
    while time.monotonic() < deadline:
        try:
            proc = psutil.Process(descendant_pid)
            if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.02)
    else:
        pytest.fail(f"worker descendant {descendant_pid} survived timeout cleanup")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descendant probe")
@pytest.mark.live_system_guard_bypass
def test_worker_cancellation_closes_lifeline_and_terminates_descendants(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "cancelled-descendant.pid"
    code = (
        "import pathlib,subprocess,sys,time;"
        "sys.stdin.readline();"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid));"
        "time.sleep(60)"
    )
    cancelled = threading.Event()

    def cancel_after_spawn() -> None:
        deadline = time.monotonic() + 3
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        cancelled.set()

    threading.Thread(target=cancel_after_spawn, daemon=True).start()
    with pytest.raises(_PluginAgentCancelled):
        _exchange_worker(
            {"protocol_version": 1, "type": "run"},
            workdir=tmp_path,
            idle_timeout_seconds=5,
            wall_timeout_seconds=10,
            worker_argv=[sys.executable, "-c", code],
            is_cancelled=cancelled.is_set,
        )

    descendant_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            proc = psutil.Process(descendant_pid)
            if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.02)
    else:
        pytest.fail(f"worker descendant {descendant_pid} survived cancellation")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descendant probe")
@pytest.mark.live_system_guard_bypass
def test_worker_resource_limit_terminates_descendants(tmp_path: Path) -> None:
    code = (
        "import subprocess,sys,time;sys.stdin.readline();"
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        "time.sleep(60)"
    )
    with pytest.raises(_PluginAgentResourceExceeded, match="descendant_limit"):
        _exchange_worker(
            {"protocol_version": 1, "type": "run"},
            workdir=tmp_path,
            idle_timeout_seconds=5,
            wall_timeout_seconds=10,
            worker_argv=[sys.executable, "-c", code],
            resource_limits=ProcessResourceLimits(max_descendants=0),
        )


def test_worker_stderr_is_never_exposed_to_plugin() -> None:
    secret = "sk-test-secret-should-not-escape"
    code = (
        "import sys;sys.stdin.readline();"
        f"sys.stderr.write({secret!r});sys.stderr.flush();raise SystemExit(1)"
    )
    with pytest.raises(RuntimeError) as exc_info:
        _exchange_worker(
            {"protocol_version": 1, "type": "run"},
            workdir=None,
            idle_timeout_seconds=2,
            wall_timeout_seconds=5,
            worker_argv=[sys.executable, "-c", code],
        )

    assert secret not in str(exc_info.value)


@pytest.mark.live_system_guard_bypass
def test_worker_stderr_does_not_reset_semantic_idle_deadline() -> None:
    code = (
        "import sys,time;sys.stdin.readline();"
        "\nwhile True:"
        "\n sys.stderr.write('diagnostic\\n');sys.stderr.flush();time.sleep(0.03)"
    )
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="idle timeout"):
        _exchange_worker(
            {"protocol_version": 1, "type": "run"},
            workdir=None,
            idle_timeout_seconds=0.2,
            wall_timeout_seconds=2,
            worker_argv=[sys.executable, "-c", code],
            termination_policy=TerminationPolicy(
                cooperative_grace_seconds=0.05,
                term_grace_seconds=0.1,
                kill_grace_seconds=0.2,
                wait_timeout_seconds=0.2,
            ),
        )
    assert time.monotonic() - started < 1
