from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest
import yaml
from agent.plugin_agent import PluginAgentRunResult
from hermes_cli.runtime_provider import ExecutionRuntimeCapabilities
from plugins.workflow.api_admission import ApiAdmissionAuthority, start_api_run
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.runner_binding import (
    RunnerCapabilities,
    WorkflowRunnerBinding,
    assess_package_execution,
    background_execution_context,
)
from plugins.workflow.models import ExecutionFence
from plugins.workflow.scheduler import RunScheduler
from hermes_cli.plugin_services import BackgroundServiceContext
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from plugins.workflow.trust import WorkflowTrustStore, compute_package_digest
import plugins.workflow.showcase as showcase_module
import plugins.workflow.scheduled_revalidation as scheduled_revalidation_module


UTC = timezone.utc


def _binding(
    *,
    runner_capable: bool = True,
    runtime_mode: str = "chat_completions",
    real_runner: object | None = None,
) -> WorkflowRunnerBinding:
    return WorkflowRunnerBinding(
        real_runner=real_runner or object(),
        deterministic_runner=object(),
        real_capabilities=RunnerCapabilities(
            starts_request_mcp=runner_capable,
        ),
        deterministic_capabilities=RunnerCapabilities(starts_request_mcp=False),
        runtime_capabilities=ExecutionRuntimeCapabilities(
            api_mode=runtime_mode,
            hermes_managed_tool_loop=runtime_mode != "codex_app_server",
        ),
    )


class _RecordingAIRunner:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def run(self, request, *, is_cancelled=None) -> PluginAgentRunResult:
        assert is_cancelled is None or not is_cancelled()
        self.requests.append(request)
        return PluginAgentRunResult(
            final_response=json.dumps({"summary": "recorded", "simulated": True}),
            session_id="scheduled-revalidation-session",
            provider="scheduled-revalidation-runner",
            model="offline-recording-model",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={"provider_attempts": 0},
        )


def _authority() -> ApiAdmissionAuthority:
    return ApiAdmissionAuthority(
        principal="schedule-revalidation-test",
        namespace="schedule-revalidation-test",
        operator_scope=None,
        source_instance="desktop:schedule-revalidation-test",
        assurance="local_admin_claim",
        trigger_source="desktop",
    )


def _healthy_coordinator(
    store: RunStore,
) -> tuple[CoordinatorStore, CoordinatorIdentity, int]:
    coordinator = CoordinatorStore(store.database)
    identity = CoordinatorIdentity(
        owner_id="schedule-revalidation-test",
        host_kind="web",
        host_instance_id="schedule-revalidation-test",
        pid=1,
        process_start_time=None,
    )
    result = coordinator.try_acquire(
        identity,
        now=datetime.now(UTC),
        lease_seconds=300,
    )
    assert result.is_leader
    return coordinator, identity, result.lease.epoch


def _trusted_profile_package(
    home: Path,
    workflow_writer,
    *,
    name: str,
    binding: WorkflowRunnerBinding,
):
    workflow_path = workflow_writer(
        home / "workflows",
        name=name,
        filename=f"{name}.yaml",
    )
    workflow_path.with_name(f"{name}.hermes.yaml").write_text(
        "limits:\n  max_parallel_nodes: 1\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow_path)
    context = background_execution_context(binding, requires_ai=None)
    _compatibility, risk = assess_package_execution(package, context)
    WorkflowTrustStore(home).trust(
        compute_package_digest(package).sha256,
        actor="schedule-revalidation-test",
        risk_digest=risk.risk_digest,
    )
    return package, context


def _admit_scheduled_user(
    home: Path,
    workflow_writer,
    *,
    name: str,
    binding: WorkflowRunnerBinding,
):
    package, context = _trusted_profile_package(
        home,
        workflow_writer,
        name=name,
        binding=binding,
    )
    store = RunStore(home)
    coordinator, identity, epoch = _healthy_coordinator(store)
    due = datetime.now(UTC) + timedelta(seconds=10)
    result = start_api_run(
        store,
        hermes_home=home,
        workdir=home.parent,
        user_home=home.parent,
        workflow_name=package.definition.name,
        values={},
        idempotency_key=name,
        concurrency_policy="queue",
        authority=_authority(),
        catalog_source="profile",
        runner_binding=binding,
        schedule_at=due.isoformat().replace("+00:00", "Z"),
        schedule_now_utc=due - timedelta(seconds=10),
    )
    return (
        store,
        package,
        str(result["run_id"]),
        due,
        coordinator,
        identity,
        epoch,
        context,
    )


def _advance_with_binding(
    store: RunStore,
    run_id: str,
    due: datetime,
    identity: CoordinatorIdentity,
    epoch: int,
    binding: WorkflowRunnerBinding,
) -> dict[str, object]:
    scheduler = RunScheduler(
        store,
        runner_binding=binding,
        execution_fence=ExecutionFence(identity.owner_id, epoch),
        utcnow=lambda: due,
    )
    try:
        return scheduler.advance(run_id)
    finally:
        scheduler.shutdown(deadline_seconds=2)


@contextmanager
def _test_bundle_path(root: Path):
    yield root.resolve()


def _restamp_showcase_copy(root: Path, showcase_id: str) -> None:
    catalog_path = root / "catalog.yaml"
    manifest_path = root / "digests.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["catalog_sha256"] = sha256(catalog_path.read_bytes()).hexdigest()
    manifest["packages"][showcase_id] = showcase_module._tree_digest(
        root / "packages" / showcase_id
    )
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _admit_scheduled_showcase(
    home: Path,
    *,
    showcase_id: str,
    binding: WorkflowRunnerBinding,
):
    store = RunStore(home)
    _coordinator, identity, epoch = _healthy_coordinator(store)
    due = datetime.now(UTC) + timedelta(seconds=10)
    result = start_api_run(
        store,
        hermes_home=home,
        workdir=home.parent,
        user_home=home.parent,
        workflow_name=showcase_id,
        values={},
        idempotency_key=f"scheduled-showcase-{showcase_id}",
        concurrency_policy="queue",
        authority=_authority(),
        catalog_source="showcase",
        runner_binding=binding,
        schedule_at=due.isoformat().replace("+00:00", "Z"),
        schedule_now_utc=due - timedelta(seconds=10),
    )
    return store, str(result["run_id"]), due, identity, epoch


def test_scheduled_admission_persists_exact_fire_time_identity(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    package, context = _trusted_profile_package(
        home,
        workflow_writer,
        name="scheduled-fire-time-identity",
        binding=binding,
    )
    store = RunStore(home)
    _healthy_coordinator(store)

    result = start_api_run(
        store,
        hermes_home=home,
        workdir=tmp_path,
        user_home=tmp_path,
        workflow_name=package.definition.name,
        values={},
        idempotency_key="scheduled-fire-time-identity",
        concurrency_policy="queue",
        authority=_authority(),
        catalog_source="profile",
        runner_binding=binding,
        schedule_at="2099-01-02T03:04:05Z",
        schedule_now_utc=datetime(2099, 1, 1, tzinfo=UTC),
    )

    run = store.load_run(str(result["run_id"]))
    metadata = run["run_metadata"]
    assert metadata["catalog_source"] == "profile"
    assert metadata["package_digest"] == run["definition_digest"]
    assert metadata["risk_digest"]
    assert metadata["execution_identity"]
    assert metadata["execution_identity"] == context.identity_digest
    assert "showcase_scenario_digest" not in metadata
    assert run["policy_digest"]
    assert run["input_manifest_digest"]


def test_execution_context_identity_changes_with_actual_runner_and_runtime() -> None:
    capable = background_execution_context(_binding(), requires_ai=True)
    runner_incapable = background_execution_context(
        _binding(runner_capable=False),
        requires_ai=True,
    )
    runtime_incapable = background_execution_context(
        _binding(runtime_mode="codex_app_server"),
        requires_ai=True,
    )

    assert capable.entitlement == AIEntitlementResolution("real")
    assert (
        len({
            capable.identity_digest,
            runner_incapable.identity_digest,
            runtime_incapable.identity_digest,
        })
        == 3
    )


def test_real_coordinator_revalidates_revoked_user_trust_before_any_claim(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    package, _context = _trusted_profile_package(
        home,
        workflow_writer,
        name="scheduled-trust-revoked",
        binding=binding,
    )
    store = RunStore(home)
    coordinator, identity, epoch = _healthy_coordinator(store)
    due = datetime.now(UTC) + timedelta(seconds=10)
    admitted = start_api_run(
        store,
        hermes_home=home,
        workdir=tmp_path,
        user_home=tmp_path,
        workflow_name=package.definition.name,
        values={},
        idempotency_key="scheduled-trust-revoked",
        concurrency_policy="queue",
        authority=_authority(),
        catalog_source="profile",
        runner_binding=binding,
        schedule_at=due.isoformat().replace("+00:00", "Z"),
        schedule_now_utc=due - timedelta(seconds=10),
    )
    run_id = str(admitted["run_id"])
    directory = store.run_directory(run_id)
    immutable_before = {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file() and path.name not in {"run.json", "events.jsonl", ".lock"}
    }
    WorkflowTrustStore(home).revoke(compute_package_digest(package).sha256)

    fence = ExecutionFence(identity.owner_id, epoch)
    scheduler = RunScheduler(
        store,
        runner_binding=binding,
        execution_fence=fence,
        utcnow=lambda: due,
    )
    service = WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind="web",
            host_instance_id="schedule-revalidation-test",
        ),
        hermes_home=home,
        utcnow=lambda: due,
        runner_binding=binding,
    )
    try:
        service._sweep_once(store, coordinator, identity, epoch, scheduler)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    failed = store.load_run(run_id)
    assert failed["status"] == "failed"
    assert failed["last_error"]["code"] == "schedule_revalidation_failed"
    assert {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file() and path.name not in {"run.json", "events.jsonl", ".lock"}
    } == immutable_before
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("trust_change", ["revoked", "risk-changed"])
def test_restarted_coordinator_requires_current_exact_user_trust(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
    trust_change: str,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, package, run_id, due, _coordinator, identity, epoch, _context = (
        _admit_scheduled_user(
            home,
            workflow_writer,
            name=f"scheduled-restart-{trust_change}",
            binding=binding,
        )
    )
    package_digest = compute_package_digest(package).sha256
    trust = WorkflowTrustStore(home)
    if trust_change == "revoked":
        assert trust.revoke(package_digest)
    else:
        trust.trust(
            package_digest,
            actor="schedule-revalidation-test",
            risk_digest="f" * 64,
        )

    restarted = RunStore(home)
    failed = _advance_with_binding(
        restarted,
        run_id,
        due,
        identity,
        epoch,
        binding,
    )

    assert failed["status"] == "failed"
    assert failed["last_error"]["code"] == "schedule_revalidation_failed"
    with restarted._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("mutation", ["delete", "replace"])
def test_scheduled_user_source_change_fails_closed_before_claim(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
    mutation: str,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, package, run_id, due, _coordinator, identity, epoch, _context = (
        _admit_scheduled_user(
            home,
            workflow_writer,
            name=f"scheduled-source-{mutation}",
            binding=binding,
        )
    )
    if mutation == "delete":
        package.workflow_path.unlink()
    else:
        package.workflow_path.write_text(
            package.workflow_path.read_text(encoding="utf-8").replace(
                "description:", "description: changed-"
            ),
            encoding="utf-8",
        )

    failed = _advance_with_binding(store, run_id, due, identity, epoch, binding)

    assert failed["status"] == "failed"
    assert failed["last_error"]["code"] == "schedule_revalidation_failed"
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "fire_binding",
    [
        pytest.param(_binding(runner_capable=False), id="runner-no-request-mcp"),
        pytest.param(_binding(runtime_mode="codex_app_server"), id="app-server"),
    ],
)
def test_actual_fire_time_context_change_fails_before_claim(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
    fire_binding: WorkflowRunnerBinding,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    admission_binding = _binding()
    store, _package, run_id, due, _coordinator, identity, epoch, _context = (
        _admit_scheduled_user(
            home,
            workflow_writer,
            name="scheduled-context-change",
            binding=admission_binding,
        )
    )

    failed = _advance_with_binding(
        store,
        run_id,
        due,
        identity,
        epoch,
        fire_binding,
    )

    assert failed["status"] == "failed"
    assert failed["last_error"]["code"] == "schedule_revalidation_failed"
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    ("runner_capable", "runtime_mode", "expected_status"),
    [
        pytest.param(False, "chat_completions", "failed", id="runner-incapable"),
        pytest.param(True, "codex_app_server", "failed", id="runtime-incapable"),
        pytest.param(True, "chat_completions", "succeeded", id="unchanged-capable"),
    ],
)
def test_scheduled_ai_uses_actual_fire_time_runner_and_runtime_context(
    tmp_path: Path,
    monkeypatch,
    runner_capable: bool,
    runtime_mode: str,
    expected_status: str,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda *_args, **_kwargs: ("authenticated ascii skill", ["ascii-art"], []),
    )
    runner = _RecordingAIRunner()
    admission_binding = _binding(real_runner=runner)
    store, run_id, due, identity, epoch = _admit_scheduled_showcase(
        home,
        showcase_id="ai-extensions",
        binding=admission_binding,
    )
    fire_binding = _binding(
        runner_capable=runner_capable,
        runtime_mode=runtime_mode,
        real_runner=runner,
    )

    result = _advance_with_binding(
        store,
        run_id,
        due,
        identity,
        epoch,
        fire_binding,
    )

    assert result["status"] == expected_status
    if expected_status == "failed":
        assert result["last_error"]["code"] == "schedule_revalidation_failed"
        assert runner.requests == []
        with store._connect() as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
                ).fetchone()[0]
                == 0
            )
    else:
        assert len(runner.requests) == 2
        assert (
            result["schedule_revalidation"]["execution_identity"]
            == (result["run_metadata"]["execution_identity"])
        )


def test_unchanged_scheduled_user_revalidates_and_runs_once(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, _package, run_id, due, _coordinator, identity, epoch, _context = (
        _admit_scheduled_user(
            home,
            workflow_writer,
            name="scheduled-unchanged",
            binding=binding,
        )
    )

    succeeded = _advance_with_binding(
        store,
        run_id,
        due,
        identity,
        epoch,
        binding,
    )

    assert succeeded["status"] == "succeeded"
    assert succeeded["schedule_revalidation"] == {
        "execution_identity": succeeded["run_metadata"]["execution_identity"],
        "admission_state_version": 1,
    }
    promoted = [
        event
        for event in store.tail_events(run_id)
        if event["event_type"] == "run_promoted"
    ]
    assert len(promoted) == 1


def test_unscheduled_admission_and_execution_do_not_gain_revalidation_state(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    package, _context = _trusted_profile_package(
        home,
        workflow_writer,
        name="unscheduled-identity-regression",
        binding=binding,
    )
    store = RunStore(home)
    _coordinator, identity, epoch = _healthy_coordinator(store)
    admitted = start_api_run(
        store,
        hermes_home=home,
        workdir=tmp_path,
        user_home=tmp_path,
        workflow_name=package.definition.name,
        values={},
        idempotency_key="unscheduled-identity-regression",
        concurrency_policy="queue",
        authority=_authority(),
        catalog_source="profile",
        runner_binding=binding,
    )
    run_id = str(admitted["run_id"])
    before = store.load_run(run_id)
    admission_identity = (
        before["definition_digest"],
        before["policy_digest"],
        before["input_manifest_digest"],
        before["run_metadata"],
    )

    result = _advance_with_binding(
        store,
        run_id,
        datetime.now(UTC),
        identity,
        epoch,
        _binding(runner_capable=False),
    )

    assert result["status"] == "succeeded"
    assert (
        result["definition_digest"],
        result["policy_digest"],
        result["input_manifest_digest"],
        result["run_metadata"],
    ) == admission_identity
    assert result["run_metadata"] == {}
    assert "schedule_revalidation" not in result
    assert result["last_error"] is None


@pytest.mark.parametrize(
    "mutation",
    ["tamper", "restamp-package", "restamp-scenario"],
)
def test_forced_showcase_revalidation_rejects_cached_identity_changes(
    tmp_path: Path,
    monkeypatch,
    mutation: str,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    copied = tmp_path / "showcases"
    shutil.copytree(Path(showcase_module.__file__).with_name("showcases"), copied)
    monkeypatch.setattr(
        showcase_module,
        "_bundle_path",
        lambda explicit=None: _test_bundle_path(copied),
    )
    showcase_module._clear_verified_showcase_cache_for_tests()
    binding = _binding()
    store, run_id, due, identity, epoch = _admit_scheduled_showcase(
        home,
        showcase_id="approval-gate",
        binding=binding,
    )
    assert showcase_module._VERIFIED_SHOWCASE_CACHE
    if mutation in {"tamper", "restamp-package"}:
        workflow = copied / "packages/approval-gate/workflows/approval-gate.yaml"
        workflow.write_text(
            workflow.read_text(encoding="utf-8").replace(
                "Pause for explicit", "Pause safely for explicit"
            ),
            encoding="utf-8",
        )
        if mutation == "restamp-package":
            catalog_path = copied / "catalog.yaml"
            catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
            scenario = next(
                item for item in catalog["scenarios"] if item["id"] == "approval-gate"
            )
            scenario["package_digest"] = showcase_module._tree_digest(
                copied / "packages/approval-gate"
            )
            catalog_path.write_text(
                yaml.safe_dump(catalog, sort_keys=False), encoding="utf-8"
            )
            _restamp_showcase_copy(copied, "approval-gate")
    else:
        catalog_path = copied / "catalog.yaml"
        catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
        scenario = next(
            item for item in catalog["scenarios"] if item["id"] == "approval-gate"
        )
        scenario["purpose"] = f"{scenario['purpose']} Restamped replacement."
        catalog_path.write_text(
            yaml.safe_dump(catalog, sort_keys=False), encoding="utf-8"
        )
        _restamp_showcase_copy(copied, "approval-gate")

    failed = _advance_with_binding(store, run_id, due, identity, epoch, binding)

    assert failed["status"] == "failed"
    assert failed["last_error"]["code"] == "schedule_revalidation_failed"
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 0
        )


def test_unchanged_showcase_force_revalidates_and_promotes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    copied = tmp_path / "showcases"
    shutil.copytree(Path(showcase_module.__file__).with_name("showcases"), copied)
    monkeypatch.setattr(
        showcase_module,
        "_bundle_path",
        lambda explicit=None: _test_bundle_path(copied),
    )
    showcase_module._clear_verified_showcase_cache_for_tests()
    binding = _binding()
    store, run_id, due, identity, epoch = _admit_scheduled_showcase(
        home,
        showcase_id="approval-gate",
        binding=binding,
    )
    admitted_metadata = dict(store.load_run(run_id)["run_metadata"])

    result = _advance_with_binding(store, run_id, due, identity, epoch, binding)

    assert result["status"] == "paused"
    assert result["schedule_revalidation"] == {
        "execution_identity": admitted_metadata["execution_identity"],
        "admission_state_version": 1,
    }
    assert result["run_metadata"]["showcase_scenario_digest"]


@pytest.mark.parametrize(
    ("relative_path", "mutation"),
    [
        pytest.param("definition.yaml", "corrupt", id="definition-corrupt"),
        pytest.param("policy.yaml", "corrupt", id="policy-corrupt"),
        pytest.param("resources.json", "corrupt", id="input-manifest-corrupt"),
        pytest.param("resources.json", "missing", id="input-manifest-missing"),
    ],
)
def test_sealed_snapshot_mismatch_is_terminal_and_retained(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
    relative_path: str,
    mutation: str,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, _package, run_id, due, _coordinator, identity, epoch, _context = (
        _admit_scheduled_user(
            home,
            workflow_writer,
            name=f"scheduled-sealed-{relative_path}-{mutation}",
            binding=binding,
        )
    )
    target = store.run_directory(run_id) / relative_path
    if mutation == "missing":
        target.unlink()
        expected = None
    else:
        target.write_bytes(target.read_bytes() + b"\n")
        expected = target.read_bytes()

    failed = _advance_with_binding(store, run_id, due, identity, epoch, binding)

    assert failed["status"] == "failed"
    assert failed["last_error"]["code"] == "schedule_revalidation_failed"
    assert target.exists() is (expected is not None)
    if expected is not None:
        assert target.read_bytes() == expected
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 0
        )


def test_missing_persisted_identity_fails_closed_before_claim(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, _package, run_id, due, _coordinator, identity, epoch, _context = (
        _admit_scheduled_user(
            home,
            workflow_writer,
            name="scheduled-missing-identity",
            binding=binding,
        )
    )
    metadata = dict(store.load_run(run_id)["run_metadata"])
    metadata.pop("sealed_definition_digest")
    store.append_event(
        run_id,
        "test_identity_corrupted",
        projection_updates={"run_metadata": metadata},
    )

    failed = _advance_with_binding(store, run_id, due, identity, epoch, binding)

    assert failed["status"] == "failed"
    assert failed["last_error"]["code"] == "schedule_revalidation_failed"
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 0
        )


def test_new_scheduled_admission_cannot_bypass_fire_time_authorization(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, _package, run_id, due, _coordinator, identity, epoch, _context = (
        _admit_scheduled_user(
            home,
            workflow_writer,
            name="scheduled-token-required",
            binding=binding,
        )
    )

    assert store.try_promote_run(run_id, now=due) is False
    assert store.load_run(run_id)["status"] == "queued"

    scheduler = RunScheduler(
        store,
        execution_fence=ExecutionFence(identity.owner_id, epoch),
        utcnow=lambda: due,
    )
    try:
        result = scheduler.advance(run_id)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert result["status"] == "failed"
    assert result["last_error"]["code"] == "schedule_revalidation_failed"
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 0
        )


def test_crash_after_revalidation_before_promotion_revalidates_after_restart(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, _package, run_id, due, _coordinator, identity, epoch, _context = (
        _admit_scheduled_user(
            home,
            workflow_writer,
            name="scheduled-crash-before-promotion",
            binding=binding,
        )
    )
    revalidations = 0
    original_revalidate = scheduled_revalidation_module.revalidate_scheduled_run

    def count_revalidation(*args, **kwargs):
        nonlocal revalidations
        revalidations += 1
        return original_revalidate(*args, **kwargs)

    monkeypatch.setattr(
        scheduled_revalidation_module,
        "revalidate_scheduled_run",
        count_revalidation,
    )
    original_promote = store.try_promote_run

    def crash_before_promotion(*_args, **_kwargs):
        raise RuntimeError("injected crash before promotion")

    monkeypatch.setattr(store, "try_promote_run", crash_before_promotion)
    first = RunScheduler(
        store,
        runner_binding=binding,
        execution_fence=ExecutionFence(identity.owner_id, epoch),
        utcnow=lambda: due,
    )
    with pytest.raises(RuntimeError, match="injected crash before promotion"):
        first.advance(run_id, max_nodes=1)
    first.shutdown(deadline_seconds=2)
    assert store.load_run(run_id)["status"] == "queued"
    monkeypatch.setattr(store, "try_promote_run", original_promote)

    restarted = RunStore(home)
    result = _advance_with_binding(
        restarted,
        run_id,
        due,
        identity,
        epoch,
        binding,
    )

    assert result["status"] == "succeeded"
    assert revalidations == 2
    assert (
        len([
            event
            for event in restarted.tail_events(run_id)
            if event["event_type"] == "run_promoted"
        ])
        == 1
    )


def test_crash_after_promotion_before_claim_continues_correlated_run_once(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, _package, run_id, due, _coordinator, identity, epoch, _context = (
        _admit_scheduled_user(
            home,
            workflow_writer,
            name="scheduled-crash-after-promotion",
            binding=binding,
        )
    )
    revalidations = 0
    original_revalidate = scheduled_revalidation_module.revalidate_scheduled_run

    def count_revalidation(*args, **kwargs):
        nonlocal revalidations
        revalidations += 1
        return original_revalidate(*args, **kwargs)

    monkeypatch.setattr(
        scheduled_revalidation_module,
        "revalidate_scheduled_run",
        count_revalidation,
    )
    original_claim = store.claim_node

    def crash_before_claim(*_args, **_kwargs):
        raise RuntimeError("injected crash before claim")

    monkeypatch.setattr(store, "claim_node", crash_before_claim)
    first = RunScheduler(
        store,
        runner_binding=binding,
        execution_fence=ExecutionFence(identity.owner_id, epoch),
        utcnow=lambda: due,
    )
    with pytest.raises(RuntimeError, match="injected crash before claim"):
        first.advance(run_id, max_nodes=1)
    first.shutdown(deadline_seconds=2)
    promoted = store.load_run(run_id)
    assert promoted["status"] == "running"
    assert (
        promoted["schedule_revalidation"]["execution_identity"]
        == (promoted["run_metadata"]["execution_identity"])
    )
    monkeypatch.setattr(store, "claim_node", original_claim)

    restarted = RunStore(home)
    result = _advance_with_binding(
        restarted,
        run_id,
        due,
        identity,
        epoch,
        binding,
    )

    assert result["status"] == "succeeded"
    assert revalidations == 1
    assert (
        len([
            event
            for event in restarted.tail_events(run_id)
            if event["event_type"] == "run_promoted"
        ])
        == 1
    )
