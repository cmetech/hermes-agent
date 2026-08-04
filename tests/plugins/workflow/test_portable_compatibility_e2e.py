from __future__ import annotations

from datetime import datetime, timezone
import os
import shlex
import sys

import pytest

from agent.plugin_agent import PluginAgentRunResult, PluginAgentSessionMissingError
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.bash_rendering import BashRenderingError, render_v3_bash
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.language_schema import NODE_TYPES, workflow_authoring_contract
from plugins.workflow.models import WorkflowLanguageProfile
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.sessions import NodeSessionRegistry
from plugins.workflow.showcase import load_showcase_catalog, run_showcase
from plugins.workflow.store import RunStore


def _run_package(package, home, key, **scheduler_kwargs):
    store = RunStore(home)
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=key,
        ),
        immutable_snapshot=prepared,
    )
    clock = [datetime.now(timezone.utc)]
    scheduler = RunScheduler(store, **scheduler_kwargs, utcnow=lambda: clock[0])
    try:
        result = scheduler.advance(admitted.run_id)
        if result["status"] == "waiting_retry":
            clock[0] = min(
                datetime.fromisoformat(node["next_attempt_at"])
                for node in result["nodes"].values()
                if node.get("next_attempt_at")
            )
            result = scheduler.advance(admitted.run_id)
    finally:
        scheduler.shutdown(deadline_seconds=2)
    return store, admitted.run_id, result


def test_archon_shape_and_installed_offline_showcases_need_no_yaml_rewrite(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    shutdown_deadlines: list[float] = []
    original_shutdown = RunScheduler.shutdown

    def tracking_shutdown(self, deadline_seconds=10):
        shutdown_deadlines.append(deadline_seconds)
        return original_shutdown(self, deadline_seconds=deadline_seconds)

    monkeypatch.setattr(RunScheduler, "shutdown", tracking_shutdown)
    root = tmp_path / "portable"
    # Adapted from Archon's official node/DAG authoring and `$node.output` material:
    # https://archon.diy/guides/authoring-workflows/
    # https://archon.diy/book/quick-reference/
    workflow = workflow_writer(
        root,
        name="portable-contract",
        persist_sessions=True,
        nodes=[
            {
                "id": "prepare",
                "bash": (
                    'marker="$ARTIFACTS_DIR/retry-marker"; '
                    'if [ ! -f "$marker" ]; then : > "$marker"; exit 1; fi; '
                    "printf 2"
                ),
                "timeout": 120_000,
                "retry": {"max_attempts": 1, "delay_ms": 1_000, "on_error": "all"},
            },
            {
                "id": "consume",
                "bash": "printf consumed",
                "depends_on": ["prepare"],
                "when": "$prepare.output >= 2",
            },
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    report = assess_compatibility(package, available_tools=frozenset())

    assert package.definition.name == "portable-contract"
    assert (
        package.language.effective_profile
        is WorkflowLanguageProfile.ARCHON_2026_07
    )
    assert package.language.normalizer_version == 3
    prepare = package.definition.nodes[0]
    assert prepare.options["timeout"] == 120_000
    assert prepare.options["retry"]["max_attempts"] == 1
    assert report.runnable
    assert [node.node_type for node in package.definition.nodes] == ["bash", "bash"]
    _store, _run_id, executed = _run_package(
        package, tmp_path / "portable-home", "portable-execution"
    )
    assert shutdown_deadlines == [2]
    assert executed["status"] == "succeeded"
    assert len(executed["nodes"]["prepare"]["attempts"]) == 2
    assert executed["nodes"]["consume"]["state"] == "succeeded"
    showcase_catalog = load_showcase_catalog()
    assert set(showcase_catalog) == {
        "ai-extensions",
        "approval-gate",
        "laptop-diagnostic",
        "resilience",
        "scheduling",
    }
    assert all(
        showcase_catalog[name].verified_bundled_provenance
        for name in ("approval-gate", "laptop-diagnostic", "resilience")
    )
    offline = run_showcase("resilience", hermes_home=tmp_path / "home", symptom="retry")
    assert offline["status"] == "succeeded"


def test_official_archon_bash_boundary_executes_inline_and_spilled_values(
    tmp_path,
    workflow_writer,
) -> None:
    """Exercise representative v3 boundary behavior from the public contract."""
    template = 'printf \'%s\' "$producer.output"'
    start = template.index("$producer.output")
    end = start + len("$producer.output")

    inline = render_v3_bash(
        template,
        [(start, end, "x" * 32_768)],
        spill_directory=tmp_path / "inline",
    )
    spilled = None
    try:
        assert inline.spill_count == 0
        if os.name == "nt":
            with pytest.raises(BashRenderingError) as exc:
                render_v3_bash(
                    template,
                    [(start, end, "x" * 32_769)],
                    spill_directory=tmp_path / "spilled",
                )
            assert exc.value.code == "bash_spill_integrity"
        else:
            spilled = render_v3_bash(
                template,
                [(start, end, "x" * 32_769)],
                spill_directory=tmp_path / "spilled",
            )
            assert spilled.spill_count == 1
            assert spilled.spill_total_bytes == 32_769
    finally:
        inline.close()
        if spilled is not None:
            spilled.close()

    executable = shlex.quote(sys.executable)
    boundary_cases = ((32_768, 0), (32_769, 1)) if os.name != "nt" else ()
    for size, expected_spills in boundary_cases:
        workflow = workflow_writer(
            tmp_path / f"boundary-{size}",
            name=f"official-boundary-{size}",
            nodes=[
                {
                    "id": "producer",
                    "bash": (
                        f"{executable} -c \"import sys; "
                        f"sys.stdout.write('x'*{size})\""
                    ),
                },
                {
                    "id": "consumer",
                    "bash": 'printf \'%s\' "$producer.output"',
                    "depends_on": ["producer"],
                },
            ],
        )
        workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
            "language_compatibility: archon-2026-07\n", encoding="utf-8"
        )
        package = load_workflow(workflow)
        _store, _run_id, result = _run_package(
            package, tmp_path / f"boundary-home-{size}", f"boundary-{size}"
        )
        assert result["status"] == "succeeded"
        metadata = result["nodes"]["consumer"]["attempts"][0]["metadata"]["bash"]
        assert metadata["spill_count"] == expected_spills
        assert metadata["spill_total_bytes"] == (size if expected_spills else 0)

    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)
    bash = contract["definition_schema"]["properties"]["nodes"]["items"][
        "properties"
    ]["bash"]
    assert bash["x-hermes-semantics"]["inline_utf8_bytes"] == 32_768


class _MissingSessionRunner:
    def __init__(self):
        self.requests = []
        self.missing = False

    def run(self, request, **_kwargs):
        self.requests.append(request)
        if self.missing and request.context_mode == "shared":
            raise PluginAgentSessionMissingError("confirmed absent")
        return PluginAgentRunResult(
            final_response="ok",
            session_id=f"session-{len(self.requests)}",
            provider="fixture-provider",
            model="fixture-model",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={"provider_attempts": 1},
        )


def test_official_archon_confirmed_missing_cross_run_session_executes_fresh_once(
    tmp_path, workflow_writer
) -> None:
    workflow = workflow_writer(
        tmp_path / "recovery",
        name="official-recovery",
        persist_sessions=True,
        provider="fixture-provider",
        model="fixture-model",
        nodes=[{"id": "analyze", "prompt": "Analyze the direct input"}],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    home = tmp_path / "recovery-home"
    runner = _MissingSessionRunner()
    registry = NodeSessionRegistry(home)

    _run_package(
        package,
        home,
        "seed",
        agent_runner=runner,
        session_registry=registry,
    )
    runner.missing = True
    store, run_id, recovered = _run_package(
        package,
        home,
        "recover",
        agent_runner=runner,
        session_registry=registry,
    )

    assert recovered["status"] == "succeeded"
    assert [request.context_mode for request in runner.requests] == [
        "fresh",
        "shared",
        "fresh",
    ]
    assert sum(
        event["event_type"] == "persistent_session_missing_fresh_start"
        for event in store.tail_events(run_id)
    ) == 1


def test_mcp_and_skills_stay_ai_node_options_in_the_archon_contract(
    tmp_path, workflow_writer
) -> None:
    workflow = workflow_writer(
        tmp_path / "extensions",
        name="archon-extension-shape",
        nodes=[
            {
                "id": "prompt-node",
                "prompt": "inspect",
                "mcp": "echo.yaml",
                "skills": ["ascii-art"],
            },
            {
                "id": "command-node",
                "command": "inspect",
                "mcp": "echo.yaml",
                "skills": ["ascii-art"],
                "depends_on": ["prompt-node"],
            },
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )

    package = load_workflow(workflow)

    assert [node.node_type for node in package.definition.nodes] == [
        "prompt",
        "command",
    ]
    for node in package.definition.nodes:
        assert node.options["mcp"] == "echo.yaml"
        assert node.options["skills"] == ("ascii-art",)
    assert {"mcp", "skills"}.isdisjoint(NODE_TYPES)
