from __future__ import annotations

from agent.plugin_agent import PluginAgentRunResult
import plugins.workflow.entitlement as entitlement_module
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.entitlement import DeterministicAgentRunner
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

        def run(self, request, **_kwargs):
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


def test_deterministic_entitlement_never_reads_or_replaces_real_persistent_session(
    tmp_path, monkeypatch, workflow_writer
):
    class TrackingRegistry(NodeSessionRegistry):
        def __init__(self, hermes_home):
            super().__init__(hermes_home)
            self.get_calls = 0
            self.compare_and_set_calls = 0

        def get(self, key):
            self.get_calls += 1
            return super().get(key)

        def compare_and_set(
            self,
            key,
            expected_generation,
            session_id,
            cache_fingerprint,
        ):
            self.compare_and_set_calls += 1
            return super().compare_and_set(
                key,
                expected_generation,
                session_id,
                cache_fingerprint,
            )

    class Runner:
        def __init__(self):
            self.requests = []

        def run(self, request, **_kwargs):
            self.requests.append(request)
            return PluginAgentRunResult(
                final_response="ok",
                session_id=(
                    "original-real-session"
                    if len(self.requests) == 1
                    else "next-real-session"
                ),
                provider="fake",
                model="fake",
                status="completed",
                pending_interaction=None,
                usage={},
                audit={},
            )

    class CapturingDeterministicRunner(DeterministicAgentRunner):
        def __init__(self):
            self.requests = []

        def run(self, request, *, is_cancelled=None):
            self.requests.append(request)
            return super().run(request, is_cancelled=is_cancelled)

    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="persistent-entitlement",
            persist_sessions=True,
            nodes=[{"id": "analyze", "prompt": "Analyze"}],
        )
    )
    store = RunStore(tmp_path / "home")
    registry = TrackingRegistry(tmp_path / "home")
    runner = Runner()
    deterministic_runner = CapturingDeterministicRunner()
    monkeypatch.setattr(
        entitlement_module,
        "_DETERMINISTIC_AGENT_RUNNER",
        deterministic_runner,
    )

    def run_once(key, metadata=None):
        prepared = store.prepare_run_snapshot(package)
        admitted = store.start_run(
            RunAdmissionRequest(
                workflow_name=package.definition.name,
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="cli",
                idempotency_key=key,
                concurrency_key=package.definition.name,
                run_metadata=metadata or {},
            ),
            immutable_snapshot=prepared,
        )
        return RunScheduler(
            store,
            agent_runner=runner,
            session_registry=registry,
        ).advance(admitted.run_id)

    seeded = run_once("seed-real")
    assert seeded["status"] == "succeeded"
    key = NodeSessionKey(
        "persistent-entitlement",
        "analyze",
        "local",
        "default",
        "default",
    )
    original_record = NodeSessionRegistry.get(registry, key)
    assert original_record is not None
    assert original_record.session_id == "original-real-session"
    assert original_record.generation == 1

    registry.get_calls = 0
    registry.compare_and_set_calls = 0
    deterministic = run_once(
        "deterministic",
        {"ai_entitlement": "deterministic"},
    )

    assert deterministic["status"] == "succeeded"
    assert deterministic_runner.requests[0].context_mode == "fresh"
    assert deterministic_runner.requests[0].session_id is None
    assert registry.get_calls == 0
    assert registry.compare_and_set_calls == 0
    assert NodeSessionRegistry.get(registry, key) == original_record
    assert len(runner.requests) == 1

    ordinary = run_once("resume-real")

    assert ordinary["status"] == "succeeded"
    assert runner.requests[1].context_mode == "shared"
    assert runner.requests[1].session_id == "original-real-session"
    assert all(
        request.session_id != "workflow-deterministic" for request in runner.requests
    )
