from __future__ import annotations

from agent.plugin_agent import PluginAgentRunResult
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.sessions import NodeSessionKey, NodeSessionRegistry
from plugins.workflow.store import RunStore


def test_session_registry_uses_generation_compare_and_set(tmp_path):
    registry = NodeSessionRegistry(tmp_path / "home")
    key = NodeSessionKey("demo", "analyze", "scope-a", "provider", "default")

    first = registry.compare_and_set(
        key,
        expected_generation=0,
        session_id="session-1",
        cache_fingerprint="fingerprint-1",
    )
    stale = registry.compare_and_set(
        key,
        expected_generation=0,
        session_id="stale-session",
        cache_fingerprint="fingerprint-1",
    )
    current = registry.get(key)

    assert first is True
    assert stale is False
    assert current.session_id == "session-1"
    assert current.generation == 1


def test_session_registry_is_scope_filtered_and_reset_is_guarded(tmp_path):
    registry = NodeSessionRegistry(tmp_path / "home")
    first = NodeSessionKey("demo", "analyze", "scope-a", "provider", "default")
    second = NodeSessionKey("demo", "analyze", "scope-b", "provider", "default")
    registry.compare_and_set(first, 0, "a", "fp")
    registry.compare_and_set(second, 0, "b", "fp")

    removed = registry.reset("demo", scope="scope-a", node_id="analyze")

    assert removed == 1
    assert registry.get(first) is None
    assert registry.get(second).session_id == "b"


def test_persisted_session_resumes_across_runs_but_explicit_fresh_wins(
    tmp_path, workflow_writer
):
    class Runner:
        def __init__(self):
            self.requests = []

        def run(self, request):
            self.requests.append(request)
            return PluginAgentRunResult(
                final_response="ok",
                session_id=f"session-{len(self.requests)}",
                provider="fake",
                model="fake",
                status="completed",
                pending_interaction=None,
                usage={},
                audit={},
            )

    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="persistent",
            persist_sessions=True,
            nodes=[{"id": "analyze", "prompt": "Analyze"}],
        )
    )
    store = RunStore(tmp_path / "home")
    registry = NodeSessionRegistry(tmp_path / "home")
    runner = Runner()

    def run_once(key):
        prepared = store.prepare_run_snapshot(package)
        admitted = store.start_run(
            RunAdmissionRequest(
                workflow_name="persistent",
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="cli",
                idempotency_key=key,
                concurrency_key="persistent",
            ),
            immutable_snapshot=prepared,
        )
        RunScheduler(store, agent_runner=runner, session_registry=registry).advance(
            admitted.run_id
        )

    run_once("first")
    run_once("second")

    assert runner.requests[0].context_mode == "fresh"
    assert runner.requests[1].context_mode == "shared"
    assert runner.requests[1].session_id == "session-1"

    fresh_package = load_workflow(
        workflow_writer(
            tmp_path / "fresh-package",
            name="persistent",
            persist_sessions=True,
            nodes=[{"id": "analyze", "prompt": "Analyze", "context": "fresh"}],
        )
    )
    package = fresh_package
    run_once("third")
    assert runner.requests[2].context_mode == "fresh"
