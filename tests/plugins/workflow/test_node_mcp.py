from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import psutil
import pytest
import yaml

from agent.plugin_agent import (
    PluginAgentRunRequest,
    PluginAgentRunResult,
    PluginAgentRunner,
)
from plugins.workflow.resources import ResourceResolver
from plugins.workflow.executors.ai import AgentNodeExecutor
from tools.registry import registry
from tests.plugins.workflow.test_ai_executor import FakeAgentRunner, _context, _node


FIXTURE = Path(__file__).parent / "fixtures" / "mcp" / "echo_server.py"


class _MockProviderHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        type(self).requests.append(request)
        tool_result_seen = any(
            message.get("role") == "tool" for message in request.get("messages", [])
        )
        if tool_result_seen:
            chunks = [
                {
                    "id": "workflow-mcp-final",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "echo complete"},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "workflow-mcp-final",
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "stop"}
                    ],
                },
            ]
        else:
            chunks = [
                {
                    "id": "workflow-mcp-call",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_workflow_echo",
                                        "type": "function",
                                        "function": {
                                            "name": "mcp__node_echo__echo",
                                            "arguments": '{"text":"hello"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "workflow-mcp-call",
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
                    ],
                },
            ]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *_args: object) -> None:
        pass


def _start_mock_provider() -> tuple[ThreadingHTTPServer, str]:
    _MockProviderHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockProviderHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/v1"


def _wait_for_pid_exit(pid: int) -> None:
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    try:
        process.wait(timeout=5)
    except psutil.TimeoutExpired:
        raise AssertionError(f"MCP process {pid} survived worker cleanup") from None


def _write_provider_config(base_url: str, *, api_mode: str) -> None:
    hermes_home = Path(os.environ["HERMES_HOME"])
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": {
                    "default": "workflow-mock-model",
                    "provider": "custom:workflow-worker-test",
                },
                "custom_providers": [
                    {
                        "name": "workflow-worker-test",
                        "base_url": base_url,
                        "api_key": "local-test-key",
                        "api_mode": api_mode,
                        "model": "workflow-mock-model",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _request_mcp(pid_file: Path) -> dict[str, dict[str, object]]:
    return {
        "node_echo": {
            "command": sys.executable,
            "args": [str(FIXTURE)],
            "env": {"WORKFLOW_MCP_PID_FILE": str(pid_file)},
            "connect_timeout": 10,
        }
    }


def _wait_for_pid_file(pid_file: Path) -> int:
    deadline = time.monotonic() + 5
    while not pid_file.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pid_file.is_file(), "MCP subprocess did not publish its PID"
    return int(pid_file.read_text())


def test_snapshotted_mcp_definition_is_contained_and_keeps_env_references_raw(tmp_path):
    root = tmp_path / "run"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "echo.yaml").write_text(
        yaml.safe_dump({
            "echo": {
                "command": sys.executable,
                "args": [str(FIXTURE)],
                "env": {"API_TOKEN": "${WORKFLOW_TEST_TOKEN}"},
            }
        }),
        encoding="utf-8",
    )

    servers = ResourceResolver(root).mcp_servers("echo")

    assert servers["echo"]["env"]["API_TOKEN"] == "${WORKFLOW_TEST_TOKEN}"
    assert "secret-value" not in json.dumps(servers)


def test_mcp_resolver_ignores_resolver_visible_mutable_output(tmp_path):
    root = tmp_path / "run"
    (root / "artifacts").mkdir(parents=True)
    (root / "artifacts" / "echo").write_text(
        "command: shadow\n", encoding="utf-8"
    )
    sealed = root / "mcp" / "artifacts" / "echo.yaml"
    sealed.parent.mkdir(parents=True)
    sealed.write_text("command: sealed\n", encoding="utf-8")

    servers = ResourceResolver(
        root, sealed_paths={"mcp/artifacts/echo.yaml"}
    ).mcp_servers("artifacts/echo")

    assert servers["echo"]["command"] == "sealed"


def test_node_executor_resolves_only_scheduler_verified_mcp_resource(tmp_path):
    run = tmp_path / "run"
    (run / "mcp").mkdir(parents=True)
    (run / "mcp" / "echo").write_text("command: shadow\n", encoding="utf-8")
    (run / "mcp" / "echo.yaml").write_text(
        "command: sealed\n", encoding="utf-8"
    )
    runner = FakeAgentRunner("done")
    context = _context(
        tmp_path,
        _node("mcp-node", "work", mcp="echo"),
        sealed_resource_paths=frozenset({"mcp/echo.yaml"}),
    )

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "succeeded"
    assert runner.requests[0].mcp_servers["echo"]["command"] == "sealed"


def test_node_executor_rejects_post_authentication_mcp_substitution(tmp_path):
    run = tmp_path / "run"
    (run / "mcp").mkdir(parents=True)
    definition = run / "mcp" / "echo.yaml"
    authenticated = b"command: sealed\nargs: [servers/echo.py]\n"
    definition.write_bytes(authenticated)
    (run / "servers").mkdir()
    server = run / "servers" / "echo.py"
    server_bytes = b"print('sealed')\n"
    server.write_bytes(server_bytes)
    runner = FakeAgentRunner("unused")
    context = _context(
        tmp_path,
        _node("mcp-node", "work", mcp="echo"),
        sealed_resource_paths=frozenset({"mcp/echo.yaml", "servers/echo.py"}),
        sealed_resource_bytes={
            "mcp/echo.yaml": authenticated,
            "servers/echo.py": server_bytes,
        },
    )
    definition.write_text("command: forged\nargs: [servers/echo.py]\n", encoding="utf-8")

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "failed"
    assert result.error_code == "validation"
    assert not runner.requests


def test_mcp_child_opens_materialized_authenticated_server_after_original_race(
    tmp_path,
):
    run = tmp_path / "run"
    (run / "mcp").mkdir(parents=True)
    (run / "servers").mkdir()
    definition = run / "mcp" / "echo.yaml"
    definition_bytes = (
        f"command: {json.dumps(sys.executable)}\n"
        "args: [servers/echo.py]\n"
    ).encode()
    definition.write_bytes(definition_bytes)
    server = run / "servers" / "echo.py"
    authenticated_server = (
        b"import sys\n"
        b"print('authenticated:' + sys.stdin.readline().strip(), flush=True)\n"
    )
    forged_server = (
        b"import sys\n"
        b"print('forged:' + sys.stdin.readline().strip(), flush=True)\n"
    )
    server.write_bytes(authenticated_server)

    class RacingRunner:
        def __init__(self) -> None:
            self.materialized_paths: list[Path] = []

        def run(self, request, **_kwargs):
            config = dict(request.mcp_servers["echo"])
            args = [str(item) for item in config.get("args", ())]
            self.materialized_paths = [
                Path(item)
                for item in args
                if Path(item).is_absolute() and Path(item).exists()
            ]
            server.write_bytes(forged_server)
            child = subprocess.run(
                [str(config["command"]), *args],
                input="protocol-line\n",
                text=True,
                capture_output=True,
                check=True,
                cwd=run,
            )
            return PluginAgentRunResult(
                final_response=child.stdout.strip(),
                session_id="race-session",
                provider="fake",
                model="fake",
                status="completed",
                pending_interaction=None,
                usage={"input_tokens": 1, "output_tokens": 1},
                audit={},
            )

    runner = RacingRunner()
    context = _context(
        tmp_path,
        _node("mcp-node", "work", mcp="echo"),
        sealed_resource_paths=frozenset({"mcp/echo.yaml", "servers/echo.py"}),
        sealed_resource_bytes={
            "mcp/echo.yaml": definition_bytes,
            "servers/echo.py": authenticated_server,
        },
    )

    result = AgentNodeExecutor(runner).execute(context)

    assert server.read_bytes() == forged_server
    assert result.status == "succeeded"
    output = run / result.artifacts[0].relative_path
    assert output.read_text() == "authenticated:protocol-line"
    assert runner.materialized_paths
    assert all(not path.exists() for path in runner.materialized_paths)


@pytest.mark.parametrize("outcome", ["failure", "cancelled"])
def test_mcp_materialized_authority_is_cleaned_after_terminal_outcome(
    tmp_path, outcome
):
    run = tmp_path / "run"
    (run / "mcp").mkdir(parents=True)
    (run / "servers").mkdir()
    definition = run / "mcp" / "echo.yaml"
    definition_bytes = (
        f"command: {json.dumps(sys.executable)}\nargs: [servers/echo.py]\n"
    ).encode()
    definition.write_bytes(definition_bytes)
    server_bytes = b"print('authenticated')\n"
    (run / "servers" / "echo.py").write_bytes(server_bytes)

    class TerminalRunner:
        def __init__(self) -> None:
            self.materialized_paths: list[Path] = []

        def run(self, request, **_kwargs):
            args = request.mcp_servers["echo"]["args"]
            self.materialized_paths = [
                Path(str(item))
                for item in args
                if Path(str(item)).is_absolute() and Path(str(item)).exists()
            ]
            if outcome == "failure":
                raise RuntimeError("runner failed")
            return PluginAgentRunResult(
                final_response="",
                session_id="cancel-session",
                provider="fake",
                model="fake",
                status="cancelled",
                pending_interaction=None,
                usage={},
                audit={},
            )

    runner = TerminalRunner()
    context = _context(
        tmp_path,
        _node("mcp-node", "work", mcp="echo"),
        sealed_resource_paths=frozenset({"mcp/echo.yaml", "servers/echo.py"}),
        sealed_resource_bytes={
            "mcp/echo.yaml": definition_bytes,
            "servers/echo.py": server_bytes,
        },
    )

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == ("failed" if outcome == "failure" else "cancelled")
    assert runner.materialized_paths
    assert all(not path.exists() for path in runner.materialized_paths)


def test_node_executor_passes_only_its_snapshotted_mcp_mapping(tmp_path):
    run = tmp_path / "run"
    (run / "mcp").mkdir(parents=True)
    (run / "mcp" / "echo.yaml").write_text(
        yaml.safe_dump({
            "node_echo": {"command": sys.executable, "args": [str(FIXTURE)]}
        }),
        encoding="utf-8",
    )
    runner = FakeAgentRunner("done")
    context = _context(tmp_path, _node("mcp-node", "work", mcp="echo"))

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "succeeded"
    assert set(runner.requests[0].mcp_servers) == {"node_echo"}


def test_real_plugin_agent_runs_request_mcp_and_reaps_it(tmp_path):
    import model_tools
    from tools import mcp_tool

    provider, base_url = _start_mock_provider()
    pid_file = tmp_path / "worker-mcp.pid"
    _write_provider_config(base_url, api_mode="chat_completions")
    parent_loader = mcp_tool._load_mcp_config
    parent_tools = dict(registry._tools)
    parent_aliases = registry.get_registered_toolset_aliases()
    parent_generation = registry._generation
    parent_resolved_names = list(model_tools._last_resolved_tool_names)

    try:
        result = PluginAgentRunner("workflow/test").run(
            PluginAgentRunRequest(
                prompt="Call the echo tool once and then finish.",
                allowed_tools=("mcp__node_echo__echo",),
                mcp_servers=_request_mcp(pid_file),
                max_iterations=3,
                max_api_attempts=1,
                idle_timeout_seconds=20,
                wall_timeout_seconds=40,
                provider_request_timeout_seconds=10,
            )
        )
    finally:
        provider.shutdown()
        provider.server_close()

    assert result.status == "completed", result.audit
    assert result.final_response == "echo complete"
    assert len(_MockProviderHandler.requests) == 2
    assert any(
        tool.get("function", {}).get("name") == "mcp__node_echo__echo"
        for tool in _MockProviderHandler.requests[0].get("tools", [])
    )
    assert pid_file.is_file()
    _wait_for_pid_exit(_wait_for_pid_file(pid_file))
    assert mcp_tool._load_mcp_config is parent_loader
    assert registry._tools == parent_tools
    assert registry.get_registered_toolset_aliases() == parent_aliases
    assert registry._generation == parent_generation
    assert model_tools._last_resolved_tool_names == parent_resolved_names


def test_failed_request_mcp_is_typed_and_never_reaches_provider(tmp_path):
    provider, base_url = _start_mock_provider()
    pid_file = tmp_path / "failed-mcp.pid"
    _write_provider_config(base_url, api_mode="chat_completions")
    failing_server = {
        "node_echo": {
            "command": sys.executable,
            "args": [
                "-c",
                (
                    "import os; from pathlib import Path; "
                    "Path(os.environ['WORKFLOW_MCP_PID_FILE']).write_text(str(os.getpid()))"
                ),
            ],
            "env": {"WORKFLOW_MCP_PID_FILE": str(pid_file)},
            "connect_timeout": 2,
        }
    }

    try:
        result = PluginAgentRunner("workflow/test").run(
            PluginAgentRunRequest(
                prompt="This must not reach the provider.",
                allowed_tools=("mcp__node_echo__echo",),
                mcp_servers=failing_server,
                max_api_attempts=1,
                idle_timeout_seconds=10,
                wall_timeout_seconds=20,
                provider_request_timeout_seconds=5,
            )
        )
    finally:
        provider.shutdown()
        provider.server_close()

    assert result.status == "failed"
    assert result.audit["failure_kind"] == "package_mcp_unavailable"
    assert str(result.audit["error"]).startswith("package_mcp_unavailable:")
    assert _MockProviderHandler.requests == []
    _wait_for_pid_exit(_wait_for_pid_file(pid_file))


@pytest.mark.parametrize("failure_stage", ["resolution", "classification"])
def test_runtime_failure_precedes_request_mcp_start_and_provider(
    tmp_path, failure_stage
):
    provider, base_url = _start_mock_provider()
    pid_file = tmp_path / f"{failure_stage}-mcp.pid"
    if failure_stage == "classification":
        _write_provider_config(base_url, api_mode="codex_app_server")
    else:
        hermes_home = Path(os.environ["HERMES_HOME"])
        (hermes_home / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "model": {
                        "default": "workflow-mock-model",
                        "provider": "custom:missing-worker-provider",
                    }
                }
            ),
            encoding="utf-8",
        )

    try:
        result = PluginAgentRunner("workflow/test").run(
            PluginAgentRunRequest(
                prompt="This must fail before MCP startup.",
                allowed_tools=("mcp__node_echo__echo",),
                mcp_servers=_request_mcp(pid_file),
                max_api_attempts=1,
                idle_timeout_seconds=10,
                wall_timeout_seconds=20,
                provider_request_timeout_seconds=5,
            )
        )
    finally:
        provider.shutdown()
        provider.server_close()

    assert result.status == "failed"
    if failure_stage == "classification":
        assert result.audit["failure_kind"] == "package_mcp_unavailable"
    assert not pid_file.exists()
    assert _MockProviderHandler.requests == []


def test_incapable_runtime_fails_before_agent_construction_and_mcp_start(
    tmp_path, monkeypatch
):
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    import run_agent
    from agent.plugin_agent import _request_payload

    pid_file = tmp_path / "incapable-runtime.pid"
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: {
            "provider": "openai-codex",
            "model": "workflow-mock-model",
            "api_mode": "codex_app_server",
        },
    )

    def forbidden_agent(*_args, **_kwargs):
        raise AssertionError("incapable runtime constructed AIAgent")

    monkeypatch.setattr(run_agent, "AIAgent", forbidden_agent)

    with pytest.raises(
        worker.PackageMCPUnavailable, match="package_mcp_unavailable"
    ):
        worker._run(
            _request_payload(
                "workflow/test",
                PluginAgentRunRequest(
                    prompt="must fail at runtime classification",
                    allowed_tools=("mcp__node_echo__echo",),
                    mcp_servers=_request_mcp(pid_file),
                ),
            )
        )

    assert not pid_file.exists()


@pytest.mark.parametrize(
    ("failure_stage", "expected_error"),
    [
        ("tool_policy", ValueError),
        ("session_construction", RuntimeError),
        ("shared_session", ValueError),
        ("skill_validation", ValueError),
        ("agent_construction", RuntimeError),
        ("cancellation", KeyboardInterrupt),
        ("conversation", RuntimeError),
    ],
)
def test_request_mcp_cleanup_covers_every_post_start_failure(
    tmp_path, monkeypatch, failure_stage, expected_error
):
    import agent.plugin_agent_worker as worker
    import agent.skill_commands as skill_commands
    import hermes_cli.runtime_provider as runtime_provider
    import hermes_cli.timeouts as timeout_mod
    import hermes_state
    import run_agent
    from agent.plugin_agent import _request_payload
    from hermes_cli.plugins import get_plugin_manager
    from tools import mcp_tool, skills_tool, terminal_tool

    pid_file = tmp_path / f"{failure_stage}.pid"
    original_loader = mcp_tool._load_mcp_config
    original_timeout = timeout_mod.get_provider_request_timeout
    original_approval = terminal_tool._get_approval_callback()
    original_sudo = terminal_tool._get_sudo_password_callback()
    original_secret = skills_tool._secret_capture_callback
    original_tools = dict(registry._tools)
    original_aliases = registry.get_registered_toolset_aliases()
    original_generation = registry._generation
    plugin_manager = get_plugin_manager()
    original_hooks = {
        name: list(callbacks) for name, callbacks in plugin_manager._hooks.items()
    }
    original_middleware = {
        name: list(callbacks) for name, callbacks in plugin_manager._middleware.items()
    }
    session_dbs = []

    class FakeSessionDB:
        def __init__(self):
            if failure_stage == "session_construction":
                assert pid_file.is_file()
                raise RuntimeError("injected session construction failure")
            self.closed = False
            session_dbs.append(self)

        def get_session(self, _session_id):
            return None if failure_stage == "shared_session" else object()

        def get_messages_as_conversation(self, _session_id):
            return []

        def close(self):
            self.closed = True

    class FakeAgent:
        def __init__(self, **kwargs):
            assert pid_file.is_file()
            if failure_stage == "agent_construction":
                raise RuntimeError("injected agent construction failure")
            self.tools = []
            self.valid_tool_names = set()
            self.session_id = kwargs["session_id"]
            self.provider = kwargs["provider"]
            self.model = kwargs["model"]
            self.session_input_tokens = 0
            self.session_output_tokens = 0
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0
            self._interrupt_requested = False

        def run_conversation(self, _prompt, conversation_history=None):
            assert pid_file.is_file()
            if failure_stage == "cancellation":
                assert self._interrupt_requested is True
                raise KeyboardInterrupt("injected cancellation")
            raise RuntimeError("injected conversation failure")

    def fake_skill_prompt(_skills, *, task_id):
        assert pid_file.is_file()
        return "", [], ["missing-skill"]

    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: {
            "provider": "custom",
            "model": "workflow-mock-model",
            "base_url": "http://127.0.0.1:1/v1",
            "api_key": "local-test-key",
            "api_mode": "chat_completions",
        },
    )
    monkeypatch.setattr(hermes_state, "SessionDB", FakeSessionDB)
    monkeypatch.setattr(run_agent, "AIAgent", FakeAgent)
    monkeypatch.setattr(
        skill_commands, "build_preloaded_skills_prompt", fake_skill_prompt
    )
    worker._cancel_event.clear()
    if failure_stage == "cancellation":
        worker._cancel_event.set()
    request = PluginAgentRunRequest(
        prompt="exercise cleanup",
        context_mode="shared" if failure_stage == "shared_session" else "fresh",
        session_id="session-1" if failure_stage == "shared_session" else None,
        allowed_tools=(
            "mcp__node_echo__echo",
            *(("unknown_tool",) if failure_stage == "tool_policy" else ()),
        ),
        skills=("missing-skill",) if failure_stage == "skill_validation" else (),
        mcp_servers=_request_mcp(pid_file),
        inline_agents={"helper": {"prompt": "help"}},
    )

    try:
        with pytest.raises(expected_error):
            worker._run(_request_payload("workflow/test", request))
    finally:
        worker._cancel_event.clear()

    _wait_for_pid_exit(_wait_for_pid_file(pid_file))
    assert mcp_tool._load_mcp_config is original_loader
    assert timeout_mod.get_provider_request_timeout is original_timeout
    assert terminal_tool._get_approval_callback() is original_approval
    assert terminal_tool._get_sudo_password_callback() is original_sudo
    assert skills_tool._secret_capture_callback is original_secret
    assert registry._tools == original_tools
    assert registry.get_registered_toolset_aliases() == original_aliases
    assert registry._generation == original_generation
    assert registry.get_entry("workflow_agent") == original_tools.get("workflow_agent")
    assert plugin_manager._hooks == original_hooks
    assert plugin_manager._middleware == original_middleware
    assert all(session_db.closed for session_db in session_dbs)


def test_local_stdio_mcp_is_process_isolated_and_reaped_after_shutdown(tmp_path):
    pid_file = tmp_path / "mcp.pid"
    code = """
import json, os, sys
from tools.mcp_tool import register_mcp_servers, shutdown_mcp_servers
names = register_mcp_servers({
    'node_echo': {
        'command': sys.executable,
        'args': [sys.argv[1]],
        'env': {'WORKFLOW_MCP_PID_FILE': sys.argv[2]},
        'connect_timeout': 10,
    }
})
print(json.dumps(sorted(names)), flush=True)
shutdown_mcp_servers()
"""
    parent_names = set(registry.get_all_tool_names())
    completed = subprocess.run(
        [sys.executable, "-c", code, str(FIXTURE), str(pid_file)],
        cwd=Path(__file__).parents[3],
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )

    names = json.loads(completed.stdout.strip().splitlines()[-1])
    assert "mcp__node_echo__echo" in names
    assert all(name.startswith("mcp__node_echo__") for name in names)
    assert set(registry.get_all_tool_names()) == parent_names
    pid = int(pid_file.read_text())
    assert not psutil.pid_exists(pid)


def test_parallel_mcp_workers_cannot_see_each_others_tools(tmp_path):
    code = """
import json, sys
from tools.mcp_tool import register_mcp_servers, shutdown_mcp_servers
name = sys.argv[2]
names = register_mcp_servers({name: {'command': sys.executable, 'args': [sys.argv[1]], 'connect_timeout': 10}})
print(json.dumps(sorted(names)), flush=True)
shutdown_mcp_servers()
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(FIXTURE), name],
            cwd=Path(__file__).parents[3],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for name in ("left", "right")
    ]
    outputs = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr
        outputs.append(json.loads(stdout.strip().splitlines()[-1]))

    assert "mcp__left__echo" in outputs[0]
    assert "mcp__right__echo" in outputs[1]
    assert all(name.startswith("mcp__left__") for name in outputs[0])
    assert all(name.startswith("mcp__right__") for name in outputs[1])
