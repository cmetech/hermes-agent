from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import psutil
import yaml

from plugins.workflow.resources import ResourceResolver
from plugins.workflow.executors.ai import AgentNodeExecutor
from tools.registry import registry
from tests.plugins.workflow.test_ai_executor import FakeAgentRunner, _context, _node


FIXTURE = Path(__file__).parent / "fixtures" / "mcp" / "echo_server.py"


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
