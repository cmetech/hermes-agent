from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import time
from types import SimpleNamespace

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
from plugins.workflow.models import (
    ExecutionFence,
    ValidationIssue,
    WorkflowValidationError,
)
from plugins.workflow.scheduler import RunScheduler
from hermes_cli.plugin_services import BackgroundServiceContext
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from plugins.workflow.trust import WorkflowTrustStore, compute_package_digest
import plugins.workflow.showcase as showcase_module
import plugins.workflow.runner_binding as runner_binding_module
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
    assert metadata["catalog_source_root"] == str((home / "workflows").resolve())
    assert metadata["catalog_source_relative"] == ("scheduled-fire-time-identity.yaml")
    assert metadata["package_digest"] == run["definition_digest"]
    assert metadata["risk_digest"]
    assert metadata["execution_identity"]
    assert metadata["execution_identity"] == context.identity_digest_for(package)
    assert metadata["execution_runtime_identity"] == context.identity_digest
    assert metadata["sealed_snapshot_digest"] == (
        scheduled_revalidation_module.sealed_snapshot_digest(
            store.run_directory(str(result["run_id"]))
        )
    )
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


def test_same_binding_refreshes_runtime_before_scheduled_admission(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    config_source = {
        "current": {
            "model": {
                "provider": "openrouter",
                "default": "openai/gpt-5.3",
            }
        }
    }
    monkeypatch.setattr(
        runner_binding_module,
        "read_raw_config",
        lambda: config_source["current"],
    )
    binding = runner_binding_module.production_workflow_runner_binding()

    config_source["current"] = {
        "model": {
            "provider": "anthropic",
            "default": "claude-sonnet-4-5",
        }
    }
    store, _package, run_id, due, _coordinator, identity, epoch, context = (
        _admit_scheduled_user(
            home,
            workflow_writer,
            name="scheduled-config-changed-before-admission",
            binding=binding,
        )
    )

    assert context.runtime_capabilities == ExecutionRuntimeCapabilities(
        api_mode="anthropic_messages",
        hermes_managed_tool_loop=True,
        effective_provider="anthropic",
        model="claude-sonnet-4-5",
        base_url_trust_class="trusted_direct",
        declared_structured_output_strategy="native_json_schema",
        structured_output_declaration_source="provider_profile",
    )
    admitted = store.load_run(run_id)
    assert admitted["run_metadata"]["execution_identity"] == (
        context.identity_digest_for(_package)
    )
    assert admitted["run_metadata"]["execution_runtime_identity"] == (
        context.identity_digest
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
    assert succeeded["last_error"] is None
    assert succeeded["schedule_revalidation"]["execution_identity"] == (
        context.identity_digest_for(_package)
    )


def test_same_binding_rejects_runtime_change_after_scheduled_admission(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda *_args, **_kwargs: ("authenticated ascii skill", ["ascii-art"], []),
    )
    config_source = {
        "current": {
            "model": {
                "provider": "openai-codex",
                "default": "gpt-5.3-codex",
            }
        }
    }
    monkeypatch.setattr(
        runner_binding_module,
        "read_raw_config",
        lambda: config_source["current"],
    )
    binding = runner_binding_module.production_workflow_runner_binding()
    real_runner_requests: list[object] = []

    def record_real_runner_request(
        _runner,
        request,
        *,
        is_cancelled=None,
    ) -> PluginAgentRunResult:
        assert is_cancelled is None or not is_cancelled()
        real_runner_requests.append(request)
        return PluginAgentRunResult(
            final_response=json.dumps({"summary": "recorded", "simulated": True}),
            session_id="scheduled-runtime-change-session",
            provider="scheduled-runtime-change-trap",
            model="offline-recording-model",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={"provider_attempts": 0},
        )

    monkeypatch.setattr(type(binding.real_runner), "run", record_real_runner_request)
    store, run_id, due, identity, epoch = _admit_scheduled_showcase(
        home,
        showcase_id="ai-extensions",
        binding=binding,
    )

    config_source["current"] = {
        "model": {
            "provider": "openai-codex",
            "default": "gpt-5.3-codex",
            "openai_runtime": "codex_app_server",
        }
    }
    failed = _advance_with_binding(
        store,
        run_id,
        due,
        identity,
        epoch,
        binding,
    )

    assert failed["status"] == "failed"
    assert failed["last_error"]["code"] == "schedule_revalidation_failed"
    assert real_runner_requests == []
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 0
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
        deadline = time.monotonic() + 2
        while (
            store.load_run(run_id)["status"] == "queued" and time.monotonic() < deadline
        ):
            time.sleep(0.01)
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


def test_sealed_snapshot_revalidation_includes_language_identity(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, _package, run_id, _due, _coordinator, _identity, _epoch, _context = (
        _admit_scheduled_user(
            home,
            workflow_writer,
            name="scheduled-language-identity",
            binding=binding,
        )
    )
    run = store.load_run(run_id)
    resources = json.loads(
        (store.run_directory(run_id) / "resources.json").read_bytes()
    )
    assert run["language"] == resources["language"]
    assert run["sealed_snapshot_digest"] == run["run_metadata"][
        "sealed_snapshot_digest"
    ]
    changed = dict(run)
    changed["language"] = {
        **run["language"],
        "semantic_fingerprint": "0" * 64,
    }

    with pytest.raises(
        scheduled_revalidation_module.ScheduledRunRevalidationError,
        match="language identity changed",
    ):
        scheduled_revalidation_module.verify_sealed_snapshot(
            changed,
            run_directory=store.run_directory(run_id),
        )

    changed = dict(run)
    changed["sealed_snapshot_digest"] = "0" * 64
    with pytest.raises(
        scheduled_revalidation_module.ScheduledRunRevalidationError,
        match="snapshot identity changed",
    ):
        scheduled_revalidation_module.verify_sealed_snapshot(
            changed,
            run_directory=store.run_directory(run_id),
        )


def test_sealed_snapshot_allows_regular_publication_runtime_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "run"
    root.mkdir()
    sealed_paths = ("definition.yaml", "inputs.json", "resources.json")
    (root / "definition.yaml").write_text("name: example\n", encoding="utf-8")
    (root / "inputs.json").write_text("{}\n", encoding="utf-8")
    (root / "resources.json").write_text("{}\n", encoding="utf-8")
    expected = scheduled_revalidation_module.sealed_snapshot_digest(
        root,
        relative_paths=sealed_paths,
    )
    bundle = root / "publications" / "opaque-publication"
    bundle.mkdir(parents=True)
    (bundle / "content.md").write_text("result", encoding="utf-8")
    (bundle / "metadata.json").write_text("{}\n", encoding="utf-8")

    observed = scheduled_revalidation_module.sealed_snapshot_digest(
        root,
        relative_paths=sealed_paths,
    )

    assert observed == expected


def test_root_mutable_file_names_include_only_store_owned_recovery_artifacts() -> None:
    artifact_id = "a" * 32
    accepted = {
        ".lock",
        ".snapshot-owner.json",
        "events.jsonl",
        "run.json",
        f"run.json.corrupt-{artifact_id}",
        f"events.jsonl.torn-{artifact_id}",
    }
    rejected = {
        f"nested/run.json.corrupt-{artifact_id}",
        f"nested/events.jsonl.torn-{artifact_id}",
        f"run.json.corrupt-{artifact_id[:-1]}",
        f"events.jsonl.torn-{artifact_id}0",
        "run.json.corrupt-not-an-artifact-id",
        "events.jsonl.torn-not-an-artifact-id",
    }

    assert all(
        scheduled_revalidation_module._is_mutable_run_file(path)
        for path in accepted
    )
    assert not any(
        scheduled_revalidation_module._is_mutable_run_file(path)
        for path in rejected
    )


@pytest.mark.parametrize(
    "artifact_name",
    (
        f"run.json.corrupt-{'a' * 32}",
        f"events.jsonl.torn-{'b' * 32}",
    ),
)
def test_sealed_snapshot_ignores_store_owned_root_recovery_artifact(
    tmp_path: Path,
    artifact_name: str,
) -> None:
    root = tmp_path / "run"
    root.mkdir()
    sealed_paths = ("definition.yaml", "inputs.json", "resources.json")
    (root / "definition.yaml").write_text("name: example\n", encoding="utf-8")
    (root / "inputs.json").write_text("{}\n", encoding="utf-8")
    (root / "resources.json").write_text("{}\n", encoding="utf-8")
    expected_explicit = scheduled_revalidation_module.sealed_snapshot_digest(
        root,
        relative_paths=sealed_paths,
    )
    expected_legacy = scheduled_revalidation_module.sealed_snapshot_digest(root)
    (root / artifact_name).write_text("preserved recovery bytes\n", encoding="utf-8")

    assert scheduled_revalidation_module.sealed_snapshot_digest(
        root,
        relative_paths=sealed_paths,
    ) == expected_explicit
    assert (
        scheduled_revalidation_module.sealed_snapshot_digest(root)
        == expected_legacy
    )


@pytest.mark.parametrize("unsafe_kind", ["symlink", "fifo"])
def test_sealed_snapshot_rejects_unsafe_publication_runtime_entries(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    root = tmp_path / unsafe_kind
    root.mkdir()
    (root / "definition.yaml").write_text("name: example\n", encoding="utf-8")
    (root / "inputs.json").write_text("{}\n", encoding="utf-8")
    (root / "resources.json").write_text("{}\n", encoding="utf-8")
    publications = root / "publications"
    publications.mkdir()
    unsafe = publications / "unsafe"
    if unsafe_kind == "symlink":
        unsafe.symlink_to(root / "definition.yaml")
    else:
        os.mkfifo(unsafe)

    with pytest.raises(
        scheduled_revalidation_module.ScheduledRunRevalidationError,
        match="symlink|special file",
    ):
        scheduled_revalidation_module.sealed_snapshot_digest(
            root,
            relative_paths=("definition.yaml", "inputs.json", "resources.json"),
        )


def test_coordinator_poll_before_fire_does_not_reopen_installed_workflow(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, package, run_id, due, coordinator, identity, epoch, _context = (
        _admit_scheduled_user(
            home,
            workflow_writer,
            name="scheduled-poll-sealed-only",
            binding=binding,
        )
    )
    package.workflow_path.unlink()
    observed = due - timedelta(seconds=1)
    scheduler = RunScheduler(
        store,
        runner_binding=binding,
        execution_fence=ExecutionFence(identity.owner_id, epoch),
        utcnow=lambda: observed,
    )
    service = WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind="web",
            host_instance_id="schedule-revalidation-test",
        ),
        hermes_home=home,
        utcnow=lambda: observed,
        runner_binding=binding,
    )
    try:
        service._sweep_once(store, coordinator, identity, epoch, scheduler)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert store.load_run(run_id)["status"] == "queued"


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


def test_scheduled_user_with_catalog_sized_trust_store_promotes(
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
            name="scheduled-catalog-sized-trust-store",
            binding=binding,
        )
    )
    trust = WorkflowTrustStore(home)
    payload = json.loads(trust.path.read_text(encoding="utf-8"))
    payload["padding"] = "x" * (1024 * 1024)
    trust.path.write_text(json.dumps(payload), encoding="utf-8")
    trust.lock_path.unlink()

    assert 1024 * 1024 < trust.path.stat().st_size < 4 * 1024 * 1024

    result = _advance_with_binding(store, run_id, due, identity, epoch, binding)

    assert result["status"] == "succeeded"
    assert not trust.lock_path.exists()


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


@pytest.mark.parametrize(
    "missing_field",
    [
        "sealed_definition_digest",
        "sealed_snapshot_digest",
        "catalog_source_root",
        "catalog_source_relative",
    ],
)
def test_missing_persisted_identity_fails_closed_before_claim(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
    missing_field: str,
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
    metadata.pop(missing_field)
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
    original_consume = store._consume_scheduled_promotion_authorization

    def crash_before_promotion(*args, **kwargs):
        original_consume(*args, **kwargs)
        raise RuntimeError("injected crash before promotion")

    monkeypatch.setattr(
        store,
        "_consume_scheduled_promotion_authorization",
        crash_before_promotion,
    )
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
    monkeypatch.setattr(
        store,
        "_consume_scheduled_promotion_authorization",
        original_consume,
    )

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


def test_fire_time_revalidation_and_package_prep_share_one_resource_budget(
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
            name="scheduled-shared-resource-budget",
            binding=binding,
        )
    )
    observed: list[object] = []
    original_verify = scheduled_revalidation_module.verify_sealed_snapshot
    original_revalidate = scheduled_revalidation_module.revalidate_scheduled_run

    def record_verify(*args, **kwargs):
        observed.append(kwargs.get("read_budget"))
        return original_verify(*args, **kwargs)

    def record_revalidate(*args, **kwargs):
        observed.append(kwargs.get("read_budget"))
        return original_revalidate(*args, **kwargs)

    monkeypatch.setattr(
        scheduled_revalidation_module,
        "verify_sealed_snapshot",
        record_verify,
    )
    monkeypatch.setattr(
        scheduled_revalidation_module,
        "revalidate_scheduled_run",
        record_revalidate,
    )
    scheduler = RunScheduler(
        store,
        runner_binding=binding,
        execution_fence=ExecutionFence(identity.owner_id, epoch),
        utcnow=lambda: due,
    )
    original_load = scheduler._load_verified_run_package

    def record_load(target_run_id, *, read_budget=None):
        observed.append(read_budget)
        return original_load(target_run_id, read_budget=read_budget)

    monkeypatch.setattr(scheduler, "_load_verified_run_package", record_load)
    try:
        result = scheduler.advance(run_id, max_nodes=1)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert result["status"] == "succeeded"
    assert observed
    assert observed[0] is not None
    assert {id(budget) for budget in observed} == {id(observed[0])}


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


@pytest.mark.parametrize("batch", [False, True], ids=["single", "advance-all"])
def test_live_trust_change_after_package_load_fails_at_atomic_promotion(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
    batch: bool,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, package, run_id, due, _coordinator, identity, epoch, _context = (
        _admit_scheduled_user(
            home,
            workflow_writer,
            name=f"scheduled-atomic-trust-{batch}",
            binding=binding,
        )
    )
    scheduler = RunScheduler(
        store,
        runner_binding=binding,
        execution_fence=ExecutionFence(identity.owner_id, epoch),
        utcnow=lambda: due,
    )
    original_load = scheduler._load_verified_run_package
    mutated = False

    def load_then_revoke(loaded_run_id: str, *, read_budget=None):
        nonlocal mutated
        sealed = original_load(loaded_run_id, read_budget=read_budget)
        if not mutated:
            mutated = True
            assert WorkflowTrustStore(home).revoke(
                compute_package_digest(package).sha256
            )
        return sealed

    monkeypatch.setattr(
        scheduler, "_load_verified_run_package", load_then_revoke
    )
    try:
        if batch:
            result = scheduler.advance_all([run_id])[run_id]
        else:
            result = scheduler.advance(run_id, max_nodes=1)
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


@pytest.mark.parametrize(
    ("relative_path", "invalid_bytes"),
    [
        pytest.param("definition.yaml", b"nodes: [\n", id="definition"),
        pytest.param("policy.yaml", b"limits: [\n", id="policy"),
    ],
)
def test_invalid_sealed_package_before_single_load_is_terminal(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
    relative_path: str,
    invalid_bytes: bytes,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, _package, run_id, due, _coordinator, identity, epoch, _context = (
        _admit_scheduled_user(
            home,
            workflow_writer,
            name="scheduled-invalid-single-load",
            binding=binding,
        )
    )
    scheduler = RunScheduler(
        store,
        runner_binding=binding,
        execution_fence=ExecutionFence(identity.owner_id, epoch),
        utcnow=lambda: due,
    )
    original_authorize = scheduler._authorize_scheduled_promotion
    authorization = None

    def authorize_then_corrupt(loaded_run_id: str, projection):
        nonlocal authorization
        result = original_authorize(loaded_run_id, projection)
        authorization = result[1]
        (store.run_directory(loaded_run_id) / relative_path).write_bytes(invalid_bytes)
        return result

    monkeypatch.setattr(
        scheduler,
        "_authorize_scheduled_promotion",
        authorize_then_corrupt,
    )
    try:
        result = scheduler.advance(run_id, max_nodes=1)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert result["status"] == "failed"
    assert result["last_error"]["code"] == "schedule_revalidation_failed"
    assert (store.run_directory(run_id) / relative_path).read_bytes() == invalid_bytes
    assert authorization is not None
    with pytest.raises(RuntimeError, match="already consumed"):
        store._consume_scheduled_promotion_authorization(
            authorization,
            run_id,
            store.load_run(run_id),
        )
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 0
        )


def test_valid_changed_definition_after_eager_check_fails_at_promotion_boundary(
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
            name="scheduled-valid-changed-load",
            binding=binding,
        )
    )
    scheduler = RunScheduler(
        store,
        runner_binding=binding,
        execution_fence=ExecutionFence(identity.owner_id, epoch),
        utcnow=lambda: due,
    )
    original_authorize = scheduler._authorize_scheduled_promotion

    def authorize_then_change(loaded_run_id: str, projection):
        result = original_authorize(loaded_run_id, projection)
        definition = store.run_directory(loaded_run_id) / "definition.yaml"
        definition.write_bytes(definition.read_bytes() + b"\n# valid change\n")
        return result

    monkeypatch.setattr(
        scheduler,
        "_authorize_scheduled_promotion",
        authorize_then_change,
    )
    try:
        result = scheduler.advance(run_id, max_nodes=1)
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


def test_invalid_scheduled_package_does_not_abort_valid_advance_all_peer(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, _package, invalid_id, due, _coordinator, identity, epoch, _context = (
        _admit_scheduled_user(
            home,
            workflow_writer,
            name="scheduled-invalid-batch-load",
            binding=binding,
        )
    )
    valid_package, _valid_context = _trusted_profile_package(
        home,
        workflow_writer,
        name="scheduled-valid-batch-peer",
        binding=binding,
    )
    valid = start_api_run(
        store,
        hermes_home=home,
        workdir=tmp_path,
        user_home=tmp_path,
        workflow_name=valid_package.definition.name,
        values={},
        idempotency_key="scheduled-valid-batch-peer",
        concurrency_policy="queue",
        authority=_authority(),
        catalog_source="profile",
        runner_binding=binding,
        schedule_at=due.isoformat().replace("+00:00", "Z"),
        schedule_now_utc=due - timedelta(seconds=10),
    )
    valid_id = str(valid["run_id"])
    scheduler = RunScheduler(
        store,
        runner_binding=binding,
        execution_fence=ExecutionFence(identity.owner_id, epoch),
        utcnow=lambda: due,
    )
    original_authorize = scheduler._authorize_scheduled_promotion
    invalid_definition = b"nodes: [\n"
    invalid_authorization = None

    def authorize_then_corrupt(loaded_run_id: str, projection):
        nonlocal invalid_authorization
        result = original_authorize(loaded_run_id, projection)
        if loaded_run_id == invalid_id:
            invalid_authorization = result[1]
            (store.run_directory(loaded_run_id) / "definition.yaml").write_bytes(
                invalid_definition
            )
        return result

    monkeypatch.setattr(
        scheduler,
        "_authorize_scheduled_promotion",
        authorize_then_corrupt,
    )
    try:
        results = scheduler.advance_all([invalid_id, valid_id])
    finally:
        scheduler.shutdown(deadline_seconds=2)

    invalid_result = store.load_run(invalid_id)
    assert invalid_result["status"] == "failed"
    assert invalid_result["last_error"]["code"] == "schedule_revalidation_failed"
    assert results[valid_id]["status"] == "succeeded"
    assert (store.run_directory(invalid_id) / "definition.yaml").read_bytes() == (
        invalid_definition
    )
    assert invalid_authorization is not None
    with pytest.raises(RuntimeError, match="already consumed"):
        store._consume_scheduled_promotion_authorization(
            invalid_authorization,
            invalid_id,
            store.load_run(invalid_id),
        )
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (invalid_id,)
            ).fetchone()[0]
            == 0
        )


def _admit_scheduled_authenticated_command(
    home: Path,
    workflow_writer,
    *,
    name: str,
    binding: WorkflowRunnerBinding,
):
    (home / "commands").mkdir(parents=True, exist_ok=True)
    (home / "commands/consume.md").write_text(
        "Use $producer.output.present\n", encoding="utf-8"
    )
    workflow = workflow_writer(
        home / "workflows",
        name=name,
        filename=f"{name}.yaml",
        nodes=[
            {
                "id": "producer",
                "prompt": "Produce",
                "output_format": {
                    "type": "object",
                    "properties": {"present": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            {
                "id": "consumer",
                "command": "consume",
                "depends_on": ["producer"],
            },
        ],
    )
    workflow.with_name(f"{name}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n"
        "limits:\n  max_parallel_nodes: 1\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow)
    context = background_execution_context(binding, requires_ai=None)
    _compatibility, risk = assess_package_execution(package, context)
    WorkflowTrustStore(home).trust(
        compute_package_digest(package).sha256,
        actor="schedule-revalidation-test",
        risk_digest=risk.risk_digest,
    )
    store = RunStore(home)
    _coordinator, identity, epoch = _healthy_coordinator(store)
    due = datetime.now(UTC) + timedelta(seconds=10)
    admitted = start_api_run(
        store,
        hermes_home=home,
        workdir=home.parent,
        user_home=home.parent,
        workflow_name=name,
        values={},
        idempotency_key=name,
        concurrency_policy="queue",
        authority=_authority(),
        catalog_source="profile",
        runner_binding=binding,
        schedule_at=due.isoformat().replace("+00:00", "Z"),
        schedule_now_utc=due - timedelta(seconds=10),
    )
    return store, str(admitted["run_id"]), due, identity, epoch


@pytest.mark.parametrize("entrypoint", ("advance", "advance_all"))
def test_scheduled_package_validation_matches_immediate_durable_failure(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
    entrypoint: str,
) -> None:
    home = tmp_path / f"home-{entrypoint}"
    monkeypatch.setenv("HERMES_HOME", str(home))
    runner = _RecordingAIRunner()
    binding = _binding(real_runner=runner)
    store, run_id, due, identity, epoch = (
        _admit_scheduled_authenticated_command(
            home,
            workflow_writer,
            name=f"scheduled-package-validation-{entrypoint}",
            binding=binding,
        )
    )
    scheduler = RunScheduler(
        store,
        runner_binding=binding,
        execution_fence=ExecutionFence(identity.owner_id, epoch),
        utcnow=lambda: due,
    )
    executor_calls = []
    authorization = None
    original_authorize = scheduler._authorize_scheduled_promotion

    def reject_authenticated_command(_package, _command_bodies):
        raise WorkflowValidationError(
            ValidationIssue(
                path="nodes[1].command",
                code="structured_output_field_impossible",
                message=(
                    "structured output field missing is impossible for node producer"
                ),
            )
        )

    def record_authorization(loaded_run_id, projection):
        nonlocal authorization
        result = original_authorize(loaded_run_id, projection)
        authorization = result[1]
        return result

    def reject_execution(*args, **kwargs):
        executor_calls.append((args, kwargs))
        raise AssertionError("scheduled executor ran before package validation")

    monkeypatch.setattr(
        scheduler, "_authorize_scheduled_promotion", record_authorization
    )
    monkeypatch.setattr(
        "plugins.workflow.scheduler.validate_authenticated_command_references",
        reject_authenticated_command,
    )
    monkeypatch.setattr(scheduler, "_execute_claim", reject_execution)
    try:
        if entrypoint == "advance":
            failed = scheduler.advance(run_id)
            replay = scheduler.advance(run_id)
        else:
            failed = scheduler.advance_all([run_id])[run_id]
            replay = scheduler.advance_all([run_id])[run_id]
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert failed["status"] == replay["status"] == "failed"
    assert failed["last_error"] == replay["last_error"] == {
        "code": "structured_output_field_impossible",
        "path": "nodes[1].command",
        "message": "structured output field missing is impossible for node producer",
    }
    assert failed["event_sequence"] == replay["event_sequence"]
    assert runner.requests == []
    assert executor_calls == []
    assert authorization is not None
    with pytest.raises(RuntimeError, match="already consumed"):
        store._consume_scheduled_promotion_authorization(
            authorization,
            run_id,
            store.load_run(run_id),
        )
    failures = [
        event
        for event in store.tail_events(run_id, limit=20)
        if event["event_type"] == "run_failed"
    ]
    assert [event["payload"] for event in failures] == [{
        "reason_code": "package_validation_failed",
        "validation_code": "structured_output_field_impossible",
        "validation_path": "nodes[1].command",
    }]
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
        ).fetchone()[0] == 0


def test_scheduled_package_validation_revalidates_before_terminal_mutation(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    runner = _RecordingAIRunner()
    binding = _binding(real_runner=runner)
    store, run_id, due, identity, epoch = (
        _admit_scheduled_authenticated_command(
            home,
            workflow_writer,
            name="scheduled-package-validation-revalidation",
            binding=binding,
        )
    )
    initial = store.load_run(run_id)
    scheduler = RunScheduler(
        store,
        runner_binding=binding,
        execution_fence=ExecutionFence(identity.owner_id, epoch),
        utcnow=lambda: due,
    )
    observed_projections: list[tuple[object, object, object]] = []
    executor_calls = []
    authorization = None

    def reject_revalidation(projection):
        observed_projections.append(
            (
                projection.get("status"),
                projection.get("state_version"),
                projection.get("desired_status"),
            )
        )
        raise scheduled_revalidation_module.ScheduledRunRevalidationError(
            "scheduled authority changed"
        )

    def authorize(loaded_run_id, _projection):
        nonlocal authorization
        authorization = store._scheduled_promotion_authorization(
            loaded_run_id,
            reject_revalidation,
        )
        return True, authorization

    def reject_execution(*args, **kwargs):
        executor_calls.append((args, kwargs))
        raise AssertionError("scheduled executor ran after rejected revalidation")

    with store._connect() as connection:
        wakes_before = connection.execute(
            "SELECT COUNT(*) FROM coordinator_wakes WHERE run_id=?",
            (run_id,),
        ).fetchone()[0]
    monkeypatch.setattr(scheduler, "_authorize_scheduled_promotion", authorize)
    monkeypatch.setattr(scheduler, "_execute_claim", reject_execution)
    try:
        failed = scheduler.advance(run_id, max_nodes=1)
        replay = scheduler.advance(run_id, max_nodes=1)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert observed_projections == [
        ("queued", initial["state_version"], None)
    ]
    assert failed["status"] == replay["status"] == "failed"
    assert failed["last_error"] == replay["last_error"] == {
        "code": "schedule_revalidation_failed",
        "message": "scheduled run authorization changed before execution",
    }
    assert failed["event_sequence"] == replay["event_sequence"]
    assert runner.requests == []
    assert executor_calls == []
    assert authorization is not None
    with pytest.raises(RuntimeError, match="already consumed"):
        store._consume_scheduled_promotion_authorization(
            authorization,
            run_id,
            store.load_run(run_id),
        )
    failures = [
        event
        for event in store.tail_events(run_id, limit=20)
        if event["event_type"] == "run_failed"
    ]
    assert [event["payload"] for event in failures] == [{
        "reason_code": "schedule_revalidation_failed",
    }]
    with store._connect() as connection:
        indexed = connection.execute(
            "SELECT status, projection_state_version FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        assert (indexed["status"], indexed["projection_state_version"]) == (
            "failed",
            failed["state_version"],
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM coordinator_wakes WHERE run_id=?",
            (run_id,),
        ).fetchone()[0] == wakes_before + 1


def test_scheduled_package_validation_propagates_unexpected_verifier_fault(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding(real_runner=_RecordingAIRunner())
    store, run_id, due, identity, epoch = (
        _admit_scheduled_authenticated_command(
            home,
            workflow_writer,
            name="scheduled-package-validation-verifier-fault",
            binding=binding,
        )
    )
    before = store.load_run(run_id)
    scheduler = RunScheduler(
        store,
        runner_binding=binding,
        execution_fence=ExecutionFence(identity.owner_id, epoch),
        utcnow=lambda: due,
    )
    verifier_calls = 0
    authorization = None

    def fail_unexpectedly(_projection):
        nonlocal verifier_calls
        verifier_calls += 1
        raise RuntimeError("unexpected verifier fault")

    def authorize(loaded_run_id, _projection):
        nonlocal authorization
        authorization = store._scheduled_promotion_authorization(
            loaded_run_id,
            fail_unexpectedly,
        )
        return True, authorization

    monkeypatch.setattr(scheduler, "_authorize_scheduled_promotion", authorize)
    try:
        with pytest.raises(RuntimeError, match="unexpected verifier fault"):
            scheduler.advance(run_id, max_nodes=1)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert verifier_calls == 1
    assert store.load_run(run_id) == before
    assert authorization is not None
    with pytest.raises(RuntimeError, match="already consumed"):
        store._consume_scheduled_promotion_authorization(
            authorization,
            run_id,
            store.load_run(run_id),
        )
    assert not any(
        event["event_type"] == "run_failed"
        for event in store.tail_events(run_id, limit=20)
    )
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
        ).fetchone()[0] == 0


def test_package_preparation_error_without_server_authorization_propagates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    scheduler = RunScheduler(RunStore(tmp_path / "home"))

    def fail_load(_run_id: str):
        raise RuntimeError("ordinary package load failure")

    monkeypatch.setattr(scheduler, "_load_verified_run_package", fail_load)
    try:
        with pytest.raises(RuntimeError, match="ordinary package load failure"):
            scheduler._prepare_run_package("ordinary-or-legacy", None)
    finally:
        scheduler.shutdown(deadline_seconds=2)


def test_projected_revalidation_fields_cannot_forge_promotion_authority(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, _package, run_id, due, _coordinator, _identity, _epoch, _context = (
        _admit_scheduled_user(
            home,
            workflow_writer,
            name="scheduled-forged-authorization",
            binding=binding,
        )
    )
    projection = store.load_run(run_id)
    forged = SimpleNamespace(
        run_id=run_id,
        state_version=projection["state_version"],
        execution_identity=projection["run_metadata"]["execution_identity"],
    )

    with pytest.raises(RuntimeError, match="opaque scheduled authorization"):
        store.try_promote_run(
            run_id,
            now=due,
            schedule_revalidation=forged,
        )

    assert store.load_run(run_id)["status"] == "queued"


def test_store_owned_authorization_is_one_use_and_cannot_cross_runs(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, _package, first_id, due, _coordinator, _identity, _epoch, _context = (
        _admit_scheduled_user(
            home,
            workflow_writer,
            name="scheduled-opaque-first",
            binding=binding,
        )
    )
    second_package, _second_context = _trusted_profile_package(
        home,
        workflow_writer,
        name="scheduled-opaque-second",
        binding=binding,
    )
    second = start_api_run(
        store,
        hermes_home=home,
        workdir=tmp_path,
        user_home=tmp_path,
        workflow_name=second_package.definition.name,
        values={},
        idempotency_key="scheduled-opaque-second",
        concurrency_policy="queue",
        authority=_authority(),
        catalog_source="profile",
        runner_binding=binding,
        schedule_at=due.isoformat().replace("+00:00", "Z"),
        schedule_now_utc=due - timedelta(seconds=10),
    )
    second_id = str(second["run_id"])
    factory = getattr(store, "_scheduled_promotion_authorization", None)
    consume = getattr(store, "_consume_scheduled_promotion_authorization", None)
    assert callable(factory)
    assert callable(consume)
    calls = 0

    def verify(_projection):
        nonlocal calls
        calls += 1

    authorization = factory(first_id, verify)
    with pytest.raises(RuntimeError, match="different run"):
        store.try_promote_run(
            second_id,
            now=due,
            schedule_revalidation=authorization,
        )
    consume(authorization, first_id, store.load_run(first_id))
    with pytest.raises(RuntimeError, match="already consumed"):
        consume(authorization, first_id, store.load_run(first_id))
    assert calls == 1
    assert store.load_run(first_id)["status"] == "queued"
    assert store.load_run(second_id)["status"] == "queued"


def _admit_resource_rich_scheduled_user(
    home: Path,
    workflow_writer,
    monkeypatch,
    *,
    binding: WorkflowRunnerBinding,
):
    (home / "commands").mkdir(parents=True)
    (home / "commands/inspect.md").write_text("Inspect safely.", encoding="utf-8")
    (home / "scripts").mkdir()
    (home / "scripts/helper.py").write_text("print('ok')\n", encoding="utf-8")
    (home / "mcp").mkdir()
    (home / "mcp/echo.yaml").write_text(
        "command: python\nargs: [servers/echo.py]\n",
        encoding="utf-8",
    )
    (home / "servers").mkdir()
    (home / "servers/echo.py").write_text("print('echo')\n", encoding="utf-8")
    path = workflow_writer(
        home / "workflows",
        name="scheduled-rich-snapshot",
        filename="scheduled-rich-snapshot.yaml",
        nodes=[
            {
                "id": "inspect",
                "command": "inspect",
                "skills": ["parent-skill"],
                "mcp": "echo",
                "agents": {
                    "reviewer": {
                        "description": "review",
                        "prompt": "inspect",
                        "skills": ["child-skill"],
                    }
                },
            },
            {
                "id": "script",
                "script": "helper",
                "runtime": "uv",
                "depends_on": ["inspect"],
            },
        ],
    )
    path.with_name("scheduled-rich-snapshot.hermes.yaml").write_text(
        "delivery_defaults:\n"
        "  inputs:\n"
        "    note: {type: text, required: true}\n"
        "limits:\n"
        "  max_parallel_nodes: 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: (f"skill:{','.join(names)}", names, []),
    )
    package = load_workflow(path)
    context = background_execution_context(binding, requires_ai=None)
    _compatibility, risk = assess_package_execution(package, context)
    WorkflowTrustStore(home).trust(
        compute_package_digest(package).sha256,
        actor="schedule-revalidation-test",
        risk_digest=risk.risk_digest,
    )
    store = RunStore(home)
    _coordinator, identity, epoch = _healthy_coordinator(store)
    due = datetime.now(UTC) + timedelta(seconds=10)
    admitted = start_api_run(
        store,
        hermes_home=home,
        workdir=home.parent,
        user_home=home.parent,
        workflow_name=package.definition.name,
        values={"note": "sealed input"},
        idempotency_key="scheduled-rich-snapshot",
        concurrency_policy="queue",
        authority=_authority(),
        catalog_source="profile",
        runner_binding=binding,
        schedule_at=due.isoformat().replace("+00:00", "Z"),
        schedule_now_utc=due - timedelta(seconds=10),
    )
    return store, str(admitted["run_id"]), due, identity, epoch


@pytest.mark.parametrize(
    "relative_path",
    [
        "commands/inspect.md",
        "scripts/helper.py",
        "mcp/echo.yaml",
        "servers/echo.py",
        "inputs.json",
        "inputs/note.txt",
        "node-skills/inspect.md",
        "node-agent-skills/inspect/reviewer.md",
    ],
)
def test_every_execution_readable_sealed_resource_is_revalidated(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
    relative_path: str,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, run_id, due, identity, epoch = _admit_resource_rich_scheduled_user(
        home,
        workflow_writer,
        monkeypatch,
        binding=binding,
    )
    target = store.run_directory(run_id) / relative_path
    target.write_bytes(target.read_bytes() + b"\nchanged")

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
    ("shadow_relative", "content"),
    [
        pytest.param(
            "scripts/helper",
            "print('unsealed script shadow')\n",
            id="script-extensionless",
        ),
        pytest.param(
            "mcp/echo",
            "command: python\nargs: [-c, 'print(2)']\n",
            id="mcp-extensionless",
        ),
    ],
)
def test_scheduled_revalidation_rejects_unsealed_resource_precedence_shadow(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
    shadow_relative: str,
    content: str,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, run_id, due, identity, epoch = _admit_resource_rich_scheduled_user(
        home,
        workflow_writer,
        monkeypatch,
        binding=binding,
    )
    shadow = store.run_directory(run_id) / shadow_relative
    shadow.write_text(content, encoding="utf-8")

    failed = _advance_with_binding(store, run_id, due, identity, epoch, binding)

    assert failed["status"] == "failed"
    assert failed["last_error"]["code"] == "schedule_revalidation_failed"


def _admit_scheduled_project(
    home: Path,
    workdir: Path,
    workflow_writer,
    *,
    binding: WorkflowRunnerBinding,
):
    path = workflow_writer(
        workdir / ".hermes/workflows",
        name="exact-project-source",
        description=f"source:{workdir.name}",
        filename="exact-project-source.yaml",
    )
    path.with_name("exact-project-source.hermes.yaml").write_text(
        "limits:\n  max_parallel_nodes: 1\n",
        encoding="utf-8",
    )
    package = load_workflow(path, source="project", precedence=1)
    context = background_execution_context(binding, requires_ai=None)
    _compatibility, risk = assess_package_execution(package, context)
    WorkflowTrustStore(home).trust(
        compute_package_digest(package).sha256,
        actor="schedule-revalidation-test",
        risk_digest=risk.risk_digest,
    )
    store = RunStore(home)
    _coordinator, identity, epoch = _healthy_coordinator(store)
    due = datetime.now(UTC) + timedelta(seconds=10)
    admitted = start_api_run(
        store,
        hermes_home=home,
        workdir=workdir,
        user_home=home.parent,
        workflow_name=package.definition.name,
        values={},
        idempotency_key=f"scheduled-project-{workdir.name}",
        concurrency_policy="queue",
        authority=_authority(),
        catalog_source="project",
        runner_binding=binding,
        schedule_at=due.isoformat().replace("+00:00", "Z"),
        schedule_now_utc=due - timedelta(seconds=10),
    )
    return store, package, str(admitted["run_id"]), due, identity, epoch


@pytest.mark.parametrize(
    ("original_change", "expected_status"),
    [
        pytest.param("unchanged", "succeeded", id="cwd-changed-original-retained"),
        pytest.param("deleted", "failed", id="deleted-never-falls-back"),
        pytest.param("replaced", "failed", id="replaced-never-falls-back"),
    ],
)
def test_scheduled_project_revalidates_exact_admission_source_not_current_cwd(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
    original_change: str,
    expected_status: str,
) -> None:
    home = tmp_path / "home"
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    monkeypatch.setenv("HERMES_HOME", str(home))
    binding = _binding()
    store, package_a, run_id, due, identity, epoch = _admit_scheduled_project(
        home,
        project_a,
        workflow_writer,
        binding=binding,
    )
    original_bytes = package_a.workflow_path.read_bytes()
    project_b_path = workflow_writer(
        project_b / ".hermes/workflows",
        name="exact-project-source",
        description=(
            "source:project-b" if original_change == "unchanged" else "source:project-a"
        ),
        filename="exact-project-source.yaml",
    )
    project_b_path.with_name("exact-project-source.hermes.yaml").write_text(
        "limits:\n  max_parallel_nodes: 1\n",
        encoding="utf-8",
    )
    if original_change in {"deleted", "replaced"}:
        project_b_path.write_bytes(original_bytes)
    if original_change == "deleted":
        package_a.workflow_path.unlink()
    elif original_change == "replaced":
        package_a.workflow_path.write_bytes(original_bytes + b"\n# replaced\n")
    monkeypatch.chdir(project_b)
    if original_change == "unchanged":
        projection = store.load_run(run_id)
        scheduled_revalidation_module.revalidate_scheduled_run(
            projection,
            scheduled_revalidation_module.scheduled_execution_context(
                projection,
                binding,
            ),
            hermes_home=home,
            run_directory=store.run_directory(run_id),
        )

    result = _advance_with_binding(store, run_id, due, identity, epoch, binding)

    assert result["status"] == expected_status
    if expected_status == "failed":
        assert result["last_error"]["code"] == "schedule_revalidation_failed"
    assert result["run_metadata"]["catalog_source"] == "project"
    assert result["run_metadata"]["catalog_source_root"] == str(
        (project_a / ".hermes/workflows").resolve()
    )
    assert result["run_metadata"]["catalog_source_relative"] == (
        "exact-project-source.yaml"
    )
