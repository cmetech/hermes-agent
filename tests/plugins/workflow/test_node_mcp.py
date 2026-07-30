from __future__ import annotations

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import stat
import sys
import threading
import time
from urllib.parse import quote

import psutil
import pytest
import yaml

from agent.plugin_agent import (
    PluginAgentRunRequest,
    PluginAgentRunResult,
    PluginAgentRunner,
    _validate_request,
)
from agent.plugin_agent_worker import (
    PackageMCPUnavailable,
    _finalize_authenticated_mcp_config,
    _supported_network_url,
)
from plugins.workflow.resources import (
    AuthenticatedExecutionMaterializer,
    ResourceResolver,
)
from plugins.workflow.executors.ai import AgentNodeExecutor
from tools.registry import registry
from tests.plugins.workflow.test_ai_executor import (
    FakeAgentRunner,
    _archon_context,
    _context,
    _node,
)
from tools.mcp_tool import _interpolate_env_vars


FIXTURE = Path(__file__).parent / "fixtures" / "mcp" / "echo_server.py"


def _nested_percent_encode(value: str, passes: int) -> str:
    for _ in range(passes):
        value = quote(value, safe="")
    return value


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
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
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
        yaml.safe_dump({
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
        }),
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
    (root / "artifacts" / "echo").write_text("command: shadow\n", encoding="utf-8")
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
    (run / "mcp" / "echo.yaml").write_text("command: sealed\n", encoding="utf-8")
    runner = FakeAgentRunner("done")
    context = _context(
        tmp_path,
        _node("mcp-node", "work", mcp="echo"),
        sealed_resource_paths=frozenset({"mcp/echo.yaml"}),
    )

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "succeeded"
    assert runner.requests[0].mcp_servers["echo"]["command"] == "sealed"


@pytest.mark.parametrize("mutation", ["delete", "rename", "replace"])
def test_node_executor_uses_authenticated_mcp_bytes_without_reopening_source(
    tmp_path, mutation
):
    run = tmp_path / "run"
    (run / "mcp").mkdir(parents=True)
    definition = run / "mcp" / "echo.yaml"
    authenticated = (
        f"command: {json.dumps(sys.executable)}\nargs: [servers/echo.py]\n"
    ).encode()
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
    if mutation == "delete":
        definition.unlink()
    elif mutation == "rename":
        definition.rename(definition.with_suffix(".gone"))
    else:
        definition.write_text(
            "command: forged\nargs: [servers/echo.py]\n", encoding="utf-8"
        )

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "succeeded"
    assert runner.requests[0].mcp_servers["echo"]["command"] == sys.executable


def test_sealed_mcp_classifies_local_references_only_after_env_interpolation(
    tmp_path, monkeypatch
):
    run = tmp_path / "run"
    authenticated = {
        "mcp/echo.yaml": (
            "command: '${COMMAND}'\n"
            "args: ['${ENTRY}', '--config=${CONFIG}']\n"
            "env: {SERVER_DATA: '${DATA}'}\n"
        ).encode(),
        "servers/echo.py": b"print('sealed')\n",
        "config/settings.json": b"{}\n",
        "data/value.txt": b"sealed\n",
    }
    for relative, data in authenticated.items():
        path = run / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    materializer = AuthenticatedExecutionMaterializer()
    try:
        server = ResourceResolver(
            run,
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)["echo"]

        authority = server["__hermes_authenticated_local_mcp"]
        assert "files" not in authority
        assert "entry" not in authority
        monkeypatch.setenv("COMMAND", sys.executable)
        monkeypatch.setenv("ENTRY", "servers/echo.py")
        monkeypatch.setenv("CONFIG", "config/settings.json")
        monkeypatch.setenv("DATA", "data/value.txt")

        finalized = _finalize_authenticated_mcp_config({
            "echo": _interpolate_env_vars(server)
        })["echo"]
        root = Path(finalized["__hermes_private_mcp_cwd"])

        assert finalized["command"] == sys.executable
        assert finalized["args"][3:5] == [str(root), "servers/echo.py"]
        assert finalized["args"][5] == f"--config={root / 'config/settings.json'}"
        assert finalized["env"]["SERVER_DATA"] == str(root / "data/value.txt")
    finally:
        materializer.cleanup()


@pytest.mark.parametrize(
    "server",
    [
        {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-fetch"]},
        {"command": "uvx", "args": ["mcp-server-fetch"]},
        {"url": "https://mcp.example.test/sse"},
        {"command": sys.executable, "args": ["-c", "print('inline')"]},
    ],
)
def test_sealed_nonlocal_mcp_configs_preserve_behavior_and_isolate_inline_python(
    tmp_path, server
):
    run = tmp_path / "run"
    definition = yaml.safe_dump({"echo": server}).encode()
    authenticated = {
        "mcp/echo.yaml": definition,
        "unrelated/package-resource.txt": b"sealed\n",
    }
    materializer = AuthenticatedExecutionMaterializer()
    try:
        resolved = ResourceResolver(
            run,
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)["echo"]
        assert "__hermes_authenticated_local_mcp" in resolved

        finalized = _finalize_authenticated_mcp_config({"echo": resolved})["echo"]

        if server.get("args", [None])[0] == "-c":
            assert finalized["command"] == server["command"]
            assert finalized["args"] == ["-I", *server["args"]]
            assert Path(finalized["__hermes_private_mcp_cwd"]).name == "payload"
        else:
            assert finalized == server
    finally:
        materializer.cleanup()


def test_post_interpolation_local_mcp_with_unsupported_command_fails_closed(
    tmp_path, monkeypatch
):
    run = tmp_path / "run"
    authenticated = {
        "mcp/echo.yaml": b"command: '${COMMAND}'\nargs: ['${ENTRY}']\n",
        "servers/echo.py": b"print('sealed')\n",
    }
    materializer = AuthenticatedExecutionMaterializer()
    try:
        server = ResourceResolver(
            run,
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)["echo"]
        monkeypatch.setenv("COMMAND", "node")
        monkeypatch.setenv("ENTRY", "servers/echo.py")

        with pytest.raises(
            PackageMCPUnavailable, match="runtime closure cannot be proven"
        ):
            _finalize_authenticated_mcp_config({"echo": _interpolate_env_vars(server)})
    finally:
        materializer.cleanup()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command", "./bin/missing-server"),
        ("command", "bin\\missing-server.exe"),
        ("entry", "servers/missing.py"),
        ("entry", "./missing.py"),
        ("entry", "..\\missing.py"),
        ("arg", "--config=config/missing.json"),
        ("arg", "settings.yaml"),
        ("env", "data/missing.txt"),
        ("env", "..\\secrets.json"),
        ("runtime_files", "helpers/missing.py"),
    ],
)
def test_sealed_mcp_rejects_undeclared_path_like_values_after_interpolation(
    tmp_path, field, value
):
    run = tmp_path / "run"
    definition = {
        "command": sys.executable,
        "args": ["servers/echo.py"],
    }
    if field == "command":
        definition["command"] = value
        definition["args"] = []
    elif field == "entry":
        definition["args"] = [value]
    elif field == "arg":
        definition["args"].append(value)
    elif field == "env":
        definition["env"] = {"WORKFLOW_DATA": value}
    else:
        definition["runtime_files"] = [value]
    authenticated = {
        "mcp/echo.yaml": yaml.safe_dump({"echo": definition}).encode(),
        "servers/echo.py": b"print('sealed')\n",
    }
    materializer = AuthenticatedExecutionMaterializer()
    try:
        server = ResourceResolver(
            run,
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)["echo"]

        with pytest.raises(
            PackageMCPUnavailable, match="runtime closure cannot be proven"
        ):
            _finalize_authenticated_mcp_config({"echo": server})
    finally:
        materializer.cleanup()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command", "file:///tmp/mutable-server"),
        ("arg", "FiLe:///tmp/mutable.txt"),
        ("arg", "  FILE://localhost/tmp/mutable.txt  "),
        ("arg", "file:%2f%2f%2ftmp%2fmutable.txt"),
        ("arg", "%66ile%3A%252F%252Ftmp%252Fmutable.txt"),
        ("arg", _nested_percent_encode("file:///tmp/mutable.txt", 6)),
        ("arg", _nested_percent_encode("file:///tmp/mutable.txt", 65)),
        ("arg", "sqlite:///tmp/mutable.db"),
        ("arg", "unix:///tmp/mutable.sock"),
        ("arg", "jar:file:///tmp/mutable.jar!/server.py"),
        ("arg", "data:text/plain;base64,c2VhbGVk"),
        ("arg", "data:text/plain;base64,UQ=="),
        ("arg", "vscode://file/tmp/mutable.py"),
        ("arg", "unknown+local://host/tmp/mutable"),
        ("arg", "prefixhttps://network.example.test/a/%25"),
        ("arg", "file:https://network.example.test/a/%25"),
        ("arg", "https://network.example.test/a/%ZZ"),
        ("arg", "--config=SQLITE%3A%2F%2F%2Ftmp%2Fmutable.db"),
        ("arg", "--endpoint%3Dfile%3A%2F%2F%2Ftmp%2Fmutable.txt"),
        ("arg", "--endpoint%3Dfile%253Ahttps%253A%252F%252Fnetwork.example.test"),
        ("arg", "--endpoint=%20https://network.example.test/a/%25"),
        ("arg", "junk%3Dhttps://network.example.test/a/%25"),
        ("arg", _nested_percent_encode("--config=file:///tmp/mutable.txt", 6)),
        ("arg", "--config%3Dfile%2"),
        ("arg", "file%3"),
        ("env", "uNiX:%2F%2F%2Ftmp%2Fmutable.sock"),
        ("env", _nested_percent_encode("unix:///tmp/mutable.sock", 6)),
        ("env", "%FF"),
        ("runtime_files", "file:///tmp/mutable.txt"),
        ("runtime_files", _nested_percent_encode("file:///tmp/mutable.txt", 6)),
        ("runtime_files", "%25"),
    ],
)
def test_sealed_mcp_rejects_local_embed_and_unknown_uri_schemes(tmp_path, field, value):
    run = tmp_path / "run"
    definition = {
        "command": sys.executable,
        "args": ["servers/echo.py"],
    }
    if field == "command":
        definition["command"] = value
        definition["args"] = []
    elif field == "arg":
        definition["args"].append(value)
    elif field == "env":
        definition["env"] = {"WORKFLOW_DEPENDENCY": value}
    else:
        definition["runtime_files"] = [value]
    authenticated = {
        "mcp/echo.yaml": yaml.safe_dump({"echo": definition}).encode(),
        "servers/echo.py": b"print('sealed')\n",
    }
    materializer = AuthenticatedExecutionMaterializer()
    try:
        server = ResourceResolver(
            run,
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)["echo"]

        with pytest.raises(
            PackageMCPUnavailable, match="runtime closure cannot be proven"
        ):
            _finalize_authenticated_mcp_config({"echo": server})
    finally:
        materializer.cleanup()


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/mutable.sock",
        "SQLITE%3A%2F%2F%2Ftmp%2Fmutable.db",
        "data:text/plain,embedded",
        "vscode://file/tmp/mutable.py",
        "unknown://host/resource",
        "https:///missing-authority",
        _nested_percent_encode("file:///tmp/mutable.sock", 6),
        "https%3A%2F%2Fnetwork.example.test%2F%ZZ",
    ],
)
def test_sealed_remote_config_rejects_non_network_uri_schemes(tmp_path, url):
    run = tmp_path / "run"
    server = {"url": url}
    authenticated = {"mcp/echo.yaml": yaml.safe_dump({"echo": server}).encode()}
    materializer = AuthenticatedExecutionMaterializer()
    try:
        resolved = ResourceResolver(
            run,
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)["echo"]

        with pytest.raises(
            PackageMCPUnavailable, match="runtime closure cannot be proven"
        ):
            _finalize_authenticated_mcp_config({"echo": resolved})
    finally:
        materializer.cleanup()


@pytest.mark.parametrize("scheme", ["http", "https", "ws", "wss"])
def test_sealed_mcp_preserves_explicit_network_urls(tmp_path, scheme):
    run = tmp_path / "run"
    server = {
        "command": "mcp-server-fetch",
        "args": [
            f"{scheme}://network.example.test/a/%25literal?ratio=100%25",
            f"--endpoint={scheme.upper()}://network.example.test/c/%25?ratio=100%25",
        ],
        "env": {
            "REMOTE_ENDPOINT": f"{scheme}://network.example.test/e/%25?ratio=100%25"
        },
    }
    authenticated = {"mcp/echo.yaml": yaml.safe_dump({"echo": server}).encode()}
    materializer = AuthenticatedExecutionMaterializer()
    try:
        resolved = ResourceResolver(
            run,
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)["echo"]

        assert _finalize_authenticated_mcp_config({"echo": resolved})["echo"] == server
    finally:
        materializer.cleanup()


@pytest.mark.parametrize(
    ("field", "url"),
    [
        ("arg", "https://network.example.test/a/%25?q=100%25#fragment%25"),
        ("compound", "http://127.0.0.1:8080/a/%25"),
        ("env", "wss://[2001:db8::1]:8443/socket?ratio=100%25"),
        ("url", "https://例え.テスト/パス?q=%25#部分%25"),
        ("arg", "https://user:password@network.example.test:443/a/%25"),
        ("compound", "ws://localhost:65535/events/%25"),
        ("url", "https://network.example.test:0080/a/%25"),
        ("env", "https://[2001:db8::1]:0080/a/%25"),
        (
            "env",
            "https://cafe\u0301.example.test/a/\U0001f680?q=nai\u0308ve#re\u0301sume\u0301",
        ),
    ],
)
def test_sealed_mcp_preserves_strict_supported_network_url_forms(
    tmp_path, field, url
):
    if field == "url":
        server = {"url": url}
    else:
        server = {"command": "mcp-server-fetch", "args": []}
        if field == "arg":
            server["args"] = [url]
        elif field == "compound":
            server["args"] = [f"--endpoint={url}"]
        else:
            server["env"] = {"REMOTE_ENDPOINT": url}
    authenticated = {"mcp/echo.yaml": yaml.safe_dump({"echo": server}).encode()}
    materializer = AuthenticatedExecutionMaterializer()
    try:
        resolved = ResourceResolver(
            tmp_path / "run",
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)["echo"]

        assert _finalize_authenticated_mcp_config({"echo": resolved})["echo"] == server
    finally:
        materializer.cleanup()


@pytest.mark.parametrize("location", ["hostname", "path", "query", "fragment"])
def test_supported_network_url_rejects_surrogate_without_parser_error(location):
    surrogate = chr(0xD800)
    candidate = {
        "hostname": f"https://network{surrogate}.example.test/path",
        "path": f"https://network.example.test/a{surrogate}b",
        "query": f"https://network.example.test/path?q=a{surrogate}b",
        "fragment": f"https://network.example.test/path#a{surrogate}b",
    }[location]

    assert _supported_network_url(candidate) is False


@pytest.mark.parametrize(
    ("field", "location"),
    [
        ("arg", "path"),
        ("compound", "query"),
        ("env", "fragment"),
        ("url", "hostname"),
    ],
)
def test_sealed_mcp_rejects_surrogate_network_urls_without_unsafe_encoding(
    tmp_path, field, location
):
    surrogate = chr(0xD800)
    url = {
        "hostname": f"https://network{surrogate}.example.test/path",
        "path": f"https://network.example.test/a{surrogate}b",
        "query": f"https://network.example.test/path?q=a{surrogate}b",
        "fragment": f"https://network.example.test/path#a{surrogate}b",
    }[location]
    if field == "url":
        server = {"url": url}
    else:
        server = {"command": "mcp-server-fetch", "args": []}
        if field == "arg":
            server["args"] = [url]
        elif field == "compound":
            server["args"] = [f"--endpoint={url}"]
        else:
            server["env"] = {"REMOTE_ENDPOINT": url}
    authenticated = {
        "mcp/echo.yaml": yaml.safe_dump({"echo": server}).encode("ascii")
    }
    materializer = AuthenticatedExecutionMaterializer()
    try:
        resolved = ResourceResolver(
            tmp_path / "run",
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)["echo"]

        with pytest.raises(
            PackageMCPUnavailable, match="runtime closure cannot be proven"
        ):
            _finalize_authenticated_mcp_config({"echo": resolved})
    finally:
        materializer.cleanup()


@pytest.mark.parametrize(
    ("field", "url"),
    [
        ("arg", "https:///missing-authority"),
        ("compound", "https://:443/missing-host"),
        ("env", "https://network.example.test/a b"),
        ("url", "https://network.example.test/a%20b"),
        ("arg", "https://network.example.test/a\tb"),
        ("compound", "https://network.example.test/a%09b"),
        ("env", "https://network.example.test/a%250db"),
        ("url", "https://network.example.test/a%c2%80b"),
        ("arg", "https://network.example.test/a%c2%a0b"),
        ("arg", "https://network\u202e.example.test/path"),
        ("compound", "https://network.example.test/a%e2%80%8bb"),
        ("env", "https://network.example.test/path?q=a\ufeffb"),
        ("url", "https://network.example.test/path#a%c2%adb"),
        ("url", "https://network%e2%80%ae.example.test/path"),
        ("arg", "https://network.example.test/a\u200bb"),
        ("compound", "https://network.example.test/path?q=a%ef%bb%bfb"),
        ("env", "https://network.example.test/path#a\u00adb"),
        ("url", "https://network.example.test/a%ed%a0%80b"),
        ("url", "https://network.example.test:invalid/path"),
        ("arg", "https://network.example.test:65536/path"),
        ("compound", "https://network.example.test:/path"),
        ("env", "https://[2001:db8::1/path"),
        ("url", "https://[not-ipv6]/path"),
        ("arg", "https://2001:db8::1/path"),
        ("compound", "https://network.example.test\\mutable"),
        ("env", "https:\\network.example.test\\mutable"),
        ("url", "https://network.example.test/%5cmutable"),
        ("arg", "https://network.example.test/%255cmutable"),
        ("compound", "//network.example.test/scheme-relative"),
        ("env", "prefix=https://network.example.test/path"),
        ("url", "https://user@@network.example.test/path"),
        ("arg", "https://@network.example.test/path"),
        ("compound", "https://999.999.999.999/path"),
    ],
)
def test_sealed_mcp_rejects_invalid_or_ambiguous_network_url_forms(
    tmp_path, field, url
):
    if field == "url":
        server = {"url": url}
    else:
        server = {"command": "mcp-server-fetch", "args": []}
        if field == "arg":
            server["args"] = [url]
        elif field == "compound":
            server["args"] = [f"--endpoint={url}"]
        else:
            server["env"] = {"REMOTE_ENDPOINT": url}
    authenticated = {"mcp/echo.yaml": yaml.safe_dump({"echo": server}).encode()}
    materializer = AuthenticatedExecutionMaterializer()
    try:
        resolved = ResourceResolver(
            tmp_path / "run",
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)["echo"]

        with pytest.raises(
            PackageMCPUnavailable, match="runtime closure cannot be proven"
        ):
            _finalize_authenticated_mcp_config({"echo": resolved})
    finally:
        materializer.cleanup()


def test_sealed_mcp_preserves_encoded_network_and_ordinary_literal_values(tmp_path):
    server = {
        "command": "mcp-server-fetch",
        "args": [
            _nested_percent_encode(
                "https://network.example.test/a/%25?ratio=100%25", 6
            ),
            _nested_percent_encode(
                "--endpoint=wss://network.example.test/%25?ratio=100%25", 6
            ),
            "ordinary%20encoded%20literal",
        ],
        "env": {
            "REMOTE_ENDPOINT": _nested_percent_encode(
                "ws://network.example.test/%25?ratio=100%25", 6
            )
        },
    }
    authenticated = {"mcp/echo.yaml": yaml.safe_dump({"echo": server}).encode()}
    materializer = AuthenticatedExecutionMaterializer()
    try:
        resolved = ResourceResolver(
            tmp_path / "run",
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)["echo"]

        assert _finalize_authenticated_mcp_config({"echo": resolved})["echo"] == server
    finally:
        materializer.cleanup()


@pytest.mark.parametrize(
    ("server", "python_mode"),
    [
        (
            {"command": "npx", "args": ["-y", "@scope/package", "--mode=stdio"]},
            None,
        ),
        (
            {
                "command": "npx",
                "args": [
                    "--package%3D%40scope%2Fpackage",
                    "-p=ordinary-package@1.2.3",
                    "--mode=stdio",
                ],
            },
            None,
        ),
        ({"command": "uvx", "args": ["mcp-server-fetch", "--quiet"]}, None),
        (
            {
                "command": "uvx",
                "args": [
                    "--from%3Dprovider-package",
                    "--with=dependency-package%3E%3D1.2",
                ],
            },
            None,
        ),
        (
            {
                "url": _nested_percent_encode(
                    "https://mcp.example.test/%25?ratio=100%25", 6
                ),
                "headers": {"Authorization": "Bearer ordinary-token"},
            },
            None,
        ),
        (
            {
                "command": "mcp-server-fetch",
                "args": [
                    "--mode=stdio",
                    "ordinary-literal",
                    "ordinary=value",
                    "https://example.test/a/b",
                ],
            },
            None,
        ),
        ({"command": sys.executable, "args": ["-c", "print('a/b')"]}, "-c"),
        ({"command": sys.executable, "args": ["-m", "installed.module"]}, "-m"),
    ],
)
def test_sealed_mcp_compatibility_table_preserves_nonpath_forms_without_mutable_cwd(
    tmp_path, server, python_mode
):
    run = tmp_path / "run"
    authenticated = {"mcp/echo.yaml": yaml.safe_dump({"echo": server}).encode()}
    materializer = AuthenticatedExecutionMaterializer()
    try:
        resolved = ResourceResolver(
            run,
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)["echo"]

        finalized = _finalize_authenticated_mcp_config({"echo": resolved})["echo"]

        if python_mode is None:
            assert finalized == server
        else:
            assert finalized["command"] == server["command"]
            assert finalized["args"] == ["-I", *server["args"]]
            assert Path(finalized["__hermes_private_mcp_cwd"]).name == "payload"
    finally:
        materializer.cleanup()


@pytest.mark.parametrize(
    ("command", "value"),
    [
        ("npx", "--config=@scope/package"),
        ("npx", "--package=../mutable-package"),
        ("npx", "--package=file:///tmp/mutable-package"),
        ("uvx", "--package=@scope/package"),
        ("npx", "--with=@scope/package"),
        ("mcp-server-fetch", "--package=@scope/package"),
        ("npx", "--package=@scope/package/extra"),
        ("npx", "--config%3D%40scope%2Fpackage"),
    ],
)
def test_sealed_mcp_package_option_exemption_is_context_bounded(
    tmp_path, command, value
):
    server = {"command": command, "args": [value]}
    authenticated = {"mcp/echo.yaml": yaml.safe_dump({"echo": server}).encode()}
    materializer = AuthenticatedExecutionMaterializer()
    try:
        resolved = ResourceResolver(
            tmp_path / "run",
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)["echo"]

        with pytest.raises(
            PackageMCPUnavailable, match="runtime closure cannot be proven"
        ):
            _finalize_authenticated_mcp_config({"echo": resolved})
    finally:
        materializer.cleanup()


@pytest.mark.parametrize(
    "missing_field",
    ["command", "entry", "compound_config", "env", "runtime_files"],
)
def test_real_worker_rejects_interpolated_undeclared_paths_before_mcp_spawn(
    tmp_path, monkeypatch, missing_field
):
    _write_provider_config("http://127.0.0.1:1/v1", api_mode="chat_completions")
    run = tmp_path / "run"
    run.mkdir()
    pid_file = tmp_path / f"{missing_field}.pid"
    mutable_server = run / "mutable-server.py"
    mutable_server.write_text(
        f"from pathlib import Path\nPath({str(pid_file)!r}).write_text('spawned')\n",
        encoding="utf-8",
    )
    mutable_server.chmod(0o700)
    sealed_server = (
        f"from pathlib import Path\nPath({str(pid_file)!r}).write_text('spawned')\n"
    ).encode()
    server = {
        "command": sys.executable,
        "args": ["servers/sealed.py"],
        "connect_timeout": 1,
    }
    if missing_field == "command":
        server = {"command": "${MISSING_COMMAND}", "connect_timeout": 1}
        monkeypatch.setenv("MISSING_COMMAND", "./mutable-server.py")
    elif missing_field == "entry":
        server["args"] = ["${MISSING_ENTRY}"]
        monkeypatch.setenv("MISSING_ENTRY", "mutable-server.py")
    elif missing_field == "compound_config":
        server["args"].append("--config=${MISSING_CONFIG}")
        monkeypatch.setenv("MISSING_CONFIG", "mutable/settings.json")
    elif missing_field == "env":
        server["env"] = {"WORKFLOW_DATA": "${MISSING_DATA}"}
        monkeypatch.setenv("MISSING_DATA", "mutable/data.txt")
    else:
        server["runtime_files"] = ["${MISSING_RUNTIME_FILE}"]
        monkeypatch.setenv("MISSING_RUNTIME_FILE", "mutable/helper.py")
    definition = yaml.safe_dump({"node_echo": server}).encode()
    authenticated = {
        "mcp/echo.yaml": definition,
        "servers/sealed.py": sealed_server,
    }
    materializer = AuthenticatedExecutionMaterializer()
    try:
        resolved = ResourceResolver(
            run,
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)
        result = PluginAgentRunner("workflow/test").run(
            PluginAgentRunRequest(
                prompt="must fail before MCP spawn",
                allowed_tools=("mcp__node_echo__missing",),
                mcp_servers=resolved,
                workdir=run,
                max_api_attempts=1,
                idle_timeout_seconds=10,
                wall_timeout_seconds=20,
                provider_request_timeout_seconds=5,
            )
        )
    finally:
        materializer.cleanup()

    assert result.status == "failed"
    assert result.audit["failure_kind"] == "package_mcp_unavailable"
    assert "runtime closure cannot be proven" in str(result.audit["error"])
    assert not pid_file.exists()


@pytest.mark.parametrize(
    ("uri_field", "value"),
    [
        ("exact", "FiLe:///tmp/mutable.txt"),
        ("compound", "SQLITE://localhost/tmp/mutable.db"),
        ("compound_encoded", "file%3A%2F%2F%2Ftmp%2Fmutable.txt"),
        ("env", "  fIlE://localhost/tmp/mutable.txt  "),
        ("encoded", "%66ile%3A%252F%252Ftmp%252Fmutable.txt"),
        ("deep_encoded", _nested_percent_encode("file:///tmp/mutable.txt", 6)),
        ("malformed", "file%3"),
    ],
)
def test_real_worker_rejects_local_uri_before_mcp_spawn(
    tmp_path, monkeypatch, uri_field, value
):
    _write_provider_config("http://127.0.0.1:1/v1", api_mode="chat_completions")
    run = tmp_path / "run"
    run.mkdir()
    pid_file = tmp_path / f"{uri_field}.pid"
    sealed_server = (
        f"from pathlib import Path\nPath({str(pid_file)!r}).write_text('spawned')\n"
    ).encode()
    server = {
        "command": sys.executable,
        "args": ["servers/sealed.py"],
        "connect_timeout": 1,
    }
    monkeypatch.setenv("LOCAL_URI", value)
    if uri_field == "compound":
        server["args"].append("--config=${LOCAL_URI}")
    elif uri_field == "compound_encoded":
        server["args"].append("--config%3D${LOCAL_URI}")
    elif uri_field == "env":
        server["env"] = {"WORKFLOW_DEPENDENCY": "${LOCAL_URI}"}
    else:
        server["args"].append("${LOCAL_URI}")
    definition = yaml.safe_dump({"node_echo": server}).encode()
    authenticated = {
        "mcp/echo.yaml": definition,
        "servers/sealed.py": sealed_server,
    }
    materializer = AuthenticatedExecutionMaterializer()
    try:
        resolved = ResourceResolver(
            run,
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)
        result = PluginAgentRunner("workflow/test").run(
            PluginAgentRunRequest(
                prompt="must fail before MCP spawn",
                allowed_tools=("mcp__node_echo__missing",),
                mcp_servers=resolved,
                workdir=run,
                max_api_attempts=1,
                idle_timeout_seconds=10,
                wall_timeout_seconds=20,
                provider_request_timeout_seconds=5,
            )
        )
    finally:
        materializer.cleanup()

    assert result.status == "failed"
    assert result.audit["failure_kind"] == "package_mcp_unavailable"
    assert "runtime closure cannot be proven" in str(result.audit["error"])
    assert not pid_file.exists()


def test_maximum_valid_mcp_workflow_authority_fits_plugin_agent_request_limit(
    tmp_path,
):
    run = tmp_path / "run"
    runtime_files = [f"resources/{index:03d}-{'x' * 230}.json" for index in range(430)]
    server = {
        "command": sys.executable,
        "args": ["servers/echo.py"],
        "runtime_files": runtime_files,
    }
    definition = yaml.safe_dump({"echo": server}).encode()
    assert len(definition) < 256_000
    authenticated = {
        "mcp/echo.yaml": definition,
        "servers/echo.py": b"print('sealed')\n",
        **{relative: b"{}\n" for relative in runtime_files},
    }
    materializer = AuthenticatedExecutionMaterializer()
    try:
        resolved = ResourceResolver(
            run,
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)
        request = PluginAgentRunRequest(prompt="run", mcp_servers=resolved)

        _validate_request(request)

        encoded = json.dumps(request.mcp_servers, default=str).encode()
        assert len(authenticated) == 432
        assert len(encoded) <= 256_000
    finally:
        materializer.cleanup()


def test_compact_authority_manifest_is_private_bounded_and_digest_revalidated(
    tmp_path,
):
    run = tmp_path / "run"
    secret = b"UNRELATED_RAW_AUTHORITY_SECRET\n"
    authenticated = {
        "mcp/echo.yaml": (
            f"command: {json.dumps(sys.executable)}\nargs: [servers/echo.py]\n"
        ).encode(),
        "servers/echo.py": b"print('sealed')\n",
        "private/unrelated.txt": secret,
    }
    materializer = AuthenticatedExecutionMaterializer()
    try:
        server = ResourceResolver(
            run,
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)["echo"]
        authority = server["__hermes_authenticated_local_mcp"]
        root = Path(authority["root"])
        manifest = root / authority["manifest"]
        serialized = json.dumps(authority, sort_keys=True)

        assert not root.is_relative_to(run)
        assert len(serialized.encode()) < 2048
        assert secret.decode().strip() not in serialized
        if os.name != "nt":
            assert stat.S_IMODE(root.stat().st_mode) == 0o700
            assert stat.S_IMODE((root / authority["payload"]).stat().st_mode) == 0o700
            assert stat.S_IMODE(manifest.parent.stat().st_mode) == 0o700
            assert stat.S_IMODE(manifest.stat().st_mode) == 0o400

        manifest.chmod(0o600)
        manifest.write_bytes(manifest.read_bytes() + b" ")
        with pytest.raises(
            PackageMCPUnavailable, match="authenticated MCP authority changed"
        ):
            _finalize_authenticated_mcp_config({"echo": server})
    finally:
        materializer.cleanup()


def test_authority_control_manifest_cannot_collide_with_authenticated_resources(
    tmp_path,
):
    run = tmp_path / "run"
    manifest_name = ".hermes-authority-manifest-v1.json"
    authenticated = {
        "mcp/echo.yaml": (
            f"command: {json.dumps(sys.executable)}\nargs: [servers/echo.py]\n"
        ).encode(),
        "servers/echo.py": b"print('sealed')\n",
        manifest_name: b"authenticated top-level resource\n",
        f"nested/{manifest_name}": b"authenticated nested resource\n",
    }
    materializer = AuthenticatedExecutionMaterializer()
    try:
        server = ResourceResolver(
            run,
            sealed_paths=authenticated,
            sealed_bytes=authenticated,
        ).mcp_servers("echo", materializer=materializer)["echo"]
        authority = server["__hermes_authenticated_local_mcp"]
        outer_root = Path(authority["root"])
        payload_root = outer_root / authority["payload"]
        control_manifest = outer_root / authority["manifest"]

        assert control_manifest.parent == outer_root / "control"
        assert payload_root.name == "payload"
        assert (payload_root / manifest_name).read_bytes() == authenticated[manifest_name]
        assert (payload_root / "nested" / manifest_name).read_bytes() == authenticated[
            f"nested/{manifest_name}"
        ]
        finalized = _finalize_authenticated_mcp_config({"echo": server})["echo"]
        assert finalized["__hermes_private_mcp_cwd"] == str(payload_root)
    finally:
        materializer.cleanup()


def test_genuinely_oversized_mcp_config_still_fails_plugin_agent_contract():
    request = PluginAgentRunRequest(
        prompt="run",
        mcp_servers={"echo": {"command": "npx", "opaque": "x" * 256_001}},
    )

    with pytest.raises(ValueError, match="mcp_servers exceed"):
        _validate_request(request)


def test_mcp_child_opens_materialized_authenticated_server_after_original_race(
    tmp_path,
):
    run = tmp_path / "run"
    (run / "mcp").mkdir(parents=True)
    (run / "servers").mkdir()
    definition = run / "mcp" / "echo.yaml"
    definition_bytes = (
        f"command: {json.dumps(sys.executable)}\nargs: [servers/echo.py]\n"
    ).encode()
    definition.write_bytes(definition_bytes)
    server = run / "servers" / "echo.py"
    authenticated_server = (
        b"import sys\n"
        b"print('authenticated:' + sys.stdin.readline().strip(), flush=True)\n"
    )
    forged_server = (
        b"import sys\nprint('forged:' + sys.stdin.readline().strip(), flush=True)\n"
    )
    server.write_bytes(authenticated_server)

    class RacingRunner:
        def __init__(self) -> None:
            self.materialized_paths: list[Path] = []

        def run(self, request, **_kwargs):
            config = _finalize_authenticated_mcp_config({
                "echo": dict(request.mcp_servers["echo"])
            })["echo"]
            args = [str(item) for item in config.get("args", ())]
            self.materialized_paths = [Path(config["__hermes_private_mcp_cwd"])]
            server.write_bytes(forged_server)
            child = subprocess.run(
                [str(config["command"]), *args],
                input="protocol-line\n",
                text=True,
                capture_output=True,
                check=True,
                cwd=config["__hermes_private_mcp_cwd"],
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


def test_real_mcp_worker_uses_complete_private_runtime_closure_after_ipc(
    tmp_path,
    monkeypatch,
):
    provider, base_url = _start_mock_provider()
    _write_provider_config(base_url, api_mode="chat_completions")
    run = tmp_path / "run"
    for directory in ("mcp", "servers", "config", "data"):
        (run / directory).mkdir(parents=True, exist_ok=True)
    definition_bytes = (
        "mcp_servers:\n"
        "  node_echo:\n"
        "    command: '${WORKFLOW_MCP_COMMAND}'\n"
        "    args: ['${WORKFLOW_MCP_ENTRY}', '--config=${WORKFLOW_MCP_CONFIG}']\n"
        "    runtime_files: [servers/helper.py, data/value.txt]\n"
        "    env: {WORKFLOW_DATA_PATH: '${WORKFLOW_MCP_DATA}'}\n"
        "    connect_timeout: 10\n"
    ).encode()
    server_bytes = f"""\
from pathlib import Path
import os
import sys
from mcp.server.fastmcp import FastMCP
from helper import VALUE

ORIGINAL_ROOT = Path({str(run)!r})

config_path = next(value.split('=', 1)[1] for value in sys.argv[1:] if value.startswith('--config='))
config = Path(config_path).read_text(encoding='utf-8').strip()
data = Path(os.environ['WORKFLOW_DATA_PATH']).read_text(encoding='utf-8').strip()
try:
    import injected
except ImportError:
    injected_value = 'isolated'
else:
    injected_value = injected.VALUE
server = FastMCP('authority-echo')

@server.tool()
def echo(text: str) -> str:
    private = (
        'hermes-workflow-authority-' in Path.cwd().as_posix()
        and Path(__file__).resolve().is_relative_to(Path.cwd().resolve())
        and all(str(ORIGINAL_ROOT) not in item for item in sys.path)
    )
    return '|'.join((VALUE, config, data, injected_value, text, str(private)))

if __name__ == '__main__':
    server.run(transport='stdio')
""".encode()
    authenticated = {
        "mcp/echo.yaml": definition_bytes,
        "servers/echo.py": server_bytes,
        "servers/helper.py": b"VALUE = 'authenticated-helper'\n",
        "config/settings.json": b"authenticated-config\n",
        "data/value.txt": b"authenticated-data\n",
    }
    for relative, data in authenticated.items():
        path = run / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    monkeypatch.setenv("WORKFLOW_MCP_COMMAND", sys.executable)
    monkeypatch.setenv("WORKFLOW_MCP_ENTRY", "servers/echo.py")
    monkeypatch.setenv("WORKFLOW_MCP_CONFIG", "config/settings.json")
    monkeypatch.setenv("WORKFLOW_MCP_DATA", "data/value.txt")

    class MutatingRealRunner:
        starts_request_mcp = True

        def run(self, request, **kwargs):
            (run / "servers" / "helper.py").write_text(
                "VALUE = 'forged-helper'\n", encoding="utf-8"
            )
            (run / "config" / "settings.json").write_text(
                "forged-config\n", encoding="utf-8"
            )
            (run / "data" / "value.txt").write_text("forged-data\n", encoding="utf-8")
            (run / "injected.py").write_text(
                "VALUE = 'forged-injected'\n", encoding="utf-8"
            )
            return PluginAgentRunner("workflow/test").run(
                replace(request, provider=None, model=None), **kwargs
            )

    context = _context(
        tmp_path,
        _node(
            "mcp-node",
            "Call the echo tool once and then finish.",
            mcp="echo",
            allowed_tools=["mcp__node_echo__echo"],
        ),
        sealed_resource_paths=frozenset(authenticated),
        sealed_resource_bytes=authenticated,
    )
    try:
        result = AgentNodeExecutor(MutatingRealRunner()).execute(context)
    finally:
        provider.shutdown()
        provider.server_close()

    assert result.status == "succeeded", (result.error_message, result.metadata)
    provider_request = json.dumps(_MockProviderHandler.requests)
    for expected in (
        "authenticated-helper",
        "authenticated-config",
        "authenticated-data",
        "isolated",
        "hello",
        "True",
    ):
        assert expected in provider_request, provider_request
    assert "forged-helper" not in provider_request
    assert "forged-config" not in provider_request
    assert "forged-data" not in provider_request
    assert "forged-injected" not in provider_request


def test_mcp_authority_materialization_is_idempotent_for_duplicate_entry(
    tmp_path,
):
    materializer = AuthenticatedExecutionMaterializer()
    try:
        first = materializer.materialize("servers/echo.py", b"print('sealed')\n")
        second = materializer.materialize("servers/echo.py", b"print('sealed')\n")
    finally:
        materializer.cleanup()

    assert first == second
    assert first.name == "echo.py"
    assert first.parent.name == "servers"


def test_mcp_authority_cleanup_retries_portable_sharing_failure(
    monkeypatch,
):
    import plugins.workflow.resources as resources_module

    materializer = AuthenticatedExecutionMaterializer()
    materializer.materialize("servers/echo.py", b"print('sealed')\n")
    real_rmtree = resources_module.shutil.rmtree
    calls = 0

    def sharing_then_remove(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("simulated Windows sharing violation")
        return real_rmtree(path)

    monkeypatch.setattr(resources_module.shutil, "rmtree", sharing_then_remove)

    materializer.cleanup()

    assert calls == 2
    assert not materializer.root.exists()


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
            authority = request.mcp_servers["echo"]["__hermes_authenticated_local_mcp"]
            root = Path(str(authority["root"]))
            manifest = root / str(authority["manifest"])
            self.materialized_paths = [
                path
                for path in root.rglob("*")
                if path.is_file() and path != manifest
            ]
            assert all(path.exists() for path in self.materialized_paths)
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


def test_structured_repair_does_not_restart_original_mcp_servers(tmp_path):
    runner = FakeAgentRunner("not json", '{"answer":"fixed"}')
    node = _node(
        "mcp-repair",
        "work",
        mcp="echo",
        output_format={
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        },
    )
    relative = "mcp/echo.yaml"
    context = _archon_context(
        tmp_path,
        node,
        sealed_resource_paths=frozenset({relative}),
        sealed_resource_bytes={
            relative: b"node_echo:\n  command: echo\n  args: [ok]\n"
        },
    )

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "succeeded"
    assert set(runner.requests[0].mcp_servers) == {"node_echo"}
    assert runner.requests[1].mcp_servers is None


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
            yaml.safe_dump({
                "model": {
                    "default": "workflow-mock-model",
                    "provider": "custom:missing-worker-provider",
                }
            }),
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

    with pytest.raises(worker.PackageMCPUnavailable, match="package_mcp_unavailable"):
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
