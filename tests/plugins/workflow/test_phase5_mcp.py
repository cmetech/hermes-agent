from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from agent.plugin_agent_worker import (
    PackageMCPUnavailable,
    _finalize_authenticated_mcp_config,
)
from agent.plugin_agent import PluginAgentRunRequest, _validate_request
from plugins.workflow.resources import (
    AuthenticatedExecutionMaterializer,
    ResourceResolver,
)
from plugins.workflow.admission_service import _phase5_mcp_execution_preconditions
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.schema import parse_workflow_source_bytes
from tests.plugins.workflow.test_phase5_provider_snapshot import _authority


def _sealed_server(tmp_path: Path, server: dict, files: dict[str, bytes] | None = None):
    definition = yaml.safe_dump({"server": server}).encode()
    authenticated = {"mcp/server.yaml": definition, **(files or {})}
    materializer = AuthenticatedExecutionMaterializer()
    resolved = ResourceResolver(
        tmp_path / "run",
        sealed_paths=authenticated,
        sealed_bytes=authenticated,
    ).mcp_servers("server", materializer=materializer)
    return materializer, resolved


def test_phase5_mcp_launches_only_exact_hermes_python_with_isolated_guard(tmp_path):
    materializer, resolved = _sealed_server(
        tmp_path,
        {"command": sys.executable, "args": ["servers/main.py"]},
        {
            "servers/main.py": b"from helper import VALUE\nprint(VALUE)\n",
            "servers/helper.py": b"VALUE = 'sealed-helper'\n",
        },
    )
    try:
        finalized = _finalize_authenticated_mcp_config(
            resolved, phase5=True
        )["server"]
        completed = subprocess.run(
            [finalized["command"], *finalized["args"]],
            cwd=finalized["__hermes_private_mcp_cwd"],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        materializer.cleanup()

    assert finalized["command"] == sys.executable
    assert finalized["args"][:3] == ["-I", "-S", "-c"]
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "sealed-helper"


def test_phase5_mcp_runtime_identity_round_trips_and_drift_blocks_worker(monkeypatch):
    import agent.plugin_agent_worker as worker

    request = PluginAgentRunRequest(
        prompt="run",
        intended_authority_digest="a" * 64,
        expected_mcp_runtime_identity_digest="b" * 64,
        allowed_tools=(),
    )
    decoded = PluginAgentRunRequest.from_wire(request.to_wire())
    _validate_request(decoded)
    monkeypatch.setattr(
        worker, "plugin_agent_python_runtime_identity", lambda: "c" * 64
    )
    monkeypatch.setattr(worker, "_emit", lambda *_args, **_kwargs: None)

    result = worker._run({"plugin_id": "workflow", "request": decoded.to_wire()})

    assert result["audit"]["failure_kind"] == "provider_capability_drift"
    assert result["audit"]["provider_attempts"] == 0


@pytest.mark.parametrize(
    "server",
    [
        {"url": "https://mcp.example.test/sse"},
        {"command": "npx", "args": ["server.js"]},
        {"command": "python", "args": ["servers/main.py"]},
        {"command": sys.executable, "args": ["-m", "server"]},
        {"command": sys.executable, "args": ["-c", "print('inline')"]},
    ],
)
def test_phase5_mcp_blocks_unsealed_or_remote_launch_forms(tmp_path, server):
    materializer, resolved = _sealed_server(
        tmp_path,
        server,
        {"servers/main.py": b"print('no')\n"},
    )
    try:
        with pytest.raises(PackageMCPUnavailable):
            _finalize_authenticated_mcp_config(resolved, phase5=True)
    finally:
        materializer.cleanup()


def test_phase5_mcp_import_guard_blocks_delayed_ambient_import(tmp_path):
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    (ambient / "injected.py").write_text("VALUE = 'ambient'\n", encoding="utf-8")
    script = (
        "import json,sys\n"
        f"sys.path.insert(0, {str(ambient)!r})\n"
        "try:\n import injected\n"
        "except ImportError:\n print(json.dumps({'blocked': True}))\n"
        "else:\n print(json.dumps({'blocked': False, 'value': injected.VALUE}))\n"
    ).encode()
    materializer, resolved = _sealed_server(
        tmp_path,
        {"command": sys.executable, "args": ["servers/main.py"]},
        {"servers/main.py": script},
    )
    try:
        finalized = _finalize_authenticated_mcp_config(resolved, phase5=True)[
            "server"
        ]
        completed = subprocess.run(
            [finalized["command"], *finalized["args"]],
            cwd=finalized["__hermes_private_mcp_cwd"],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        materializer.cleanup()

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"blocked": True}


def test_phase5_remote_mcp_is_an_unsupported_authority_obligation(
    tmp_path, workflow_writer
):
    root = tmp_path / "source/workflows"
    path = workflow_writer(
        root,
        name="remote-mcp",
        filename="remote-mcp.yaml",
        model="@primary",
        nodes=[{"id": "ask", "prompt": "hello", "mcp": "remote"}],
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    path.with_name("remote-mcp.hermes.yaml").write_bytes(sidecar)
    (root.parent / "mcp").mkdir()
    (root.parent / "mcp/remote.yaml").write_text(
        "url: https://mcp.example.test/sse\ntransport: sse\n",
        encoding="utf-8",
    )
    source = parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar,
        source="project",
        precedence=1,
    )
    compilation = compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=5,
    )

    facts = _phase5_mcp_execution_preconditions(compilation)
    authority = _authority(
        compilation.package,
        mcp_execution_preconditions=facts,
    )

    assert facts == {"ask": False}
    obligation = next(
        item for item in authority.obligations if item.decision.feature.value == "mcp"
    )
    assert obligation.decision.disposition.value == "unsupported"


def test_phase5_sealed_python_mcp_satisfies_authority_preconditions(
    tmp_path, workflow_writer
):
    root = tmp_path / "source/workflows"
    path = workflow_writer(
        root,
        name="local-mcp",
        filename="local-mcp.yaml",
        model="@primary",
        nodes=[{"id": "ask", "prompt": "hello", "mcp": "local"}],
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    path.with_name("local-mcp.hermes.yaml").write_bytes(sidecar)
    (root.parent / "mcp").mkdir()
    (root.parent / "servers").mkdir()
    (root.parent / "mcp/local.yaml").write_text(
        yaml.safe_dump({
            "command": sys.executable,
            "args": ["servers/main.py"],
        }),
        encoding="utf-8",
    )
    (root.parent / "servers/main.py").write_text("print('sealed')\n")
    source = parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar,
        source="project",
        precedence=1,
    )
    compilation = compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=5,
    )

    facts = _phase5_mcp_execution_preconditions(compilation)
    authority = _authority(
        compilation.package,
        mcp_execution_preconditions=facts,
    )

    assert facts == {"ask": True}
    obligation = next(
        item for item in authority.obligations if item.decision.feature.value == "mcp"
    )
    runtime_digest = obligation.decision.requested_semantics[
        "runtime_identity_digest"
    ]
    assert isinstance(runtime_digest, str) and len(runtime_digest) == 64
    assert obligation.decision.requested_semantics["import_policy_version"] == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"args": ["servers/main.py", 7]},
        {"args": ["servers/main.py", "configs/missing.json"]},
        {"args": ["servers/main.py", "https://example.test"]},
        {"args": ["servers/main.py", "https://"]},
        {"args": ["servers/main.py", "https://example.test:"]},
        {"args": ["servers/main.py", "https://[::1"]},
        {"args": ["servers/main.py", "https://example.test/\u200b"]},
        {"args": ["servers/main.py", "https://example.test/%5csecret"]},
        {"args": ["servers/main.py"], "env": []},
        {"args": ["servers/main.py"], "env": {"CONFIG_PATH": "missing.json"}},
    ],
)
def test_phase5_mcp_admission_rejects_shapes_the_worker_cannot_authenticate(
    tmp_path, workflow_writer, extra
):
    root = tmp_path / "source/workflows"
    path = workflow_writer(
        root,
        name="invalid-mcp-closure",
        filename="invalid-mcp-closure.yaml",
        model="@primary",
        nodes=[{"id": "ask", "prompt": "hello", "mcp": "local"}],
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    path.with_name("invalid-mcp-closure.hermes.yaml").write_bytes(sidecar)
    (root.parent / "mcp").mkdir()
    (root.parent / "servers").mkdir()
    document = {"command": sys.executable, **extra}
    (root.parent / "mcp/local.yaml").write_text(
        yaml.safe_dump(document), encoding="utf-8"
    )
    (root.parent / "servers/main.py").write_text("print('sealed')\n")
    source = parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar,
        source="project",
        precedence=1,
    )
    compilation = compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=5,
    )

    facts = _phase5_mcp_execution_preconditions(compilation)
    authority = _authority(
        compilation.package,
        mcp_execution_preconditions=facts,
    )
    obligation = next(
        item for item in authority.obligations if item.decision.feature.value == "mcp"
    )

    assert facts == {"ask": False}
    assert obligation.decision.disposition.value == "unsupported"
