from __future__ import annotations

import importlib
import hashlib
import json
import shlex
from datetime import datetime, timezone

import pytest

from plugins.workflow.models import RunExecutionLimits
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.gateway_command import workflow_gateway_command
from plugins.workflow.execution_semantics import WorkflowExecutionSemanticsError
from plugins.workflow.schema import load_workflow
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore
from plugins.workflow.trust import (
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
)
from hermes_cli.plugin_invocation import PluginInvocationContext
import yaml


def _execution_semantics_module():
    try:
        return importlib.import_module("plugins.workflow.execution_semantics")
    except ModuleNotFoundError:
        pytest.fail("Phase 3 effective execution semantics codec is not implemented")


def _archon_package(tmp_path, workflow_writer, *, nodes):
    path = workflow_writer(tmp_path, name="phase3-execution", nodes=nodes)
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    return load_workflow(path)


def test_effective_execution_semantics_round_trip_exact_schema_and_caps(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(
        tmp_path,
        workflow_writer,
        nodes=[
            {"id": "default-shell", "bash": "true"},
            {
                "id": "authored-script",
                "script": "print('ok')",
                "runtime": "uv",
                "timeout": 240_000,
                "retry": {
                    "max_attempts": 1,
                    "delay_ms": 4_000,
                    "on_error": "all",
                },
            },
            {
                "id": "agent",
                "prompt": "work",
                "idle_timeout": 350_000,
                "retry": {"max_attempts": 5},
            },
        ],
    )
    limits = RunExecutionLimits(
        ai_idle_timeout_seconds=200.0,
        ai_wall_timeout_seconds=400.0,
        provider_request_timeout_seconds=180.0,
        combined_retries=5,
        subprocess_timeout_seconds=90.0,
    )
    module = _execution_semantics_module()

    semantics = module.build_phase3_execution_semantics(package, limits)
    encoded = json.dumps(
        semantics.to_dict(), sort_keys=True, separators=(",", ":")
    ).encode()
    decoded = module.read_phase3_execution_semantics(
        json.loads(encoded), package=package
    )

    assert decoded == semantics
    assert semantics.to_dict() == {
        "schema_version": 1,
        "normalizer_version": 3,
        "limits": {
            "ai_idle_timeout_seconds": 200.0,
            "ai_wall_timeout_seconds": 400.0,
            "provider_request_timeout_seconds": 180.0,
            "subprocess_timeout_seconds": 90.0,
            "combined_total_attempts": 5,
        },
        "nodes": {
            "agent": {
                "requested_attempt_wall_timeout_seconds": 400.0,
                "attempt_wall_timeout_seconds": 400.0,
                "requested_idle_timeout_seconds": 350.0,
                "idle_timeout_seconds": 200.0,
                "provider_request_timeout_seconds": 180.0,
                "timeout_source": "profile_ceiling",
                "timeout_capped": True,
                "retry": {
                    "explicit": True,
                    "requested_retries": 5,
                    "requested_total_attempts": 6,
                    "effective_total_attempts": 5,
                    "delay_ms": 3000,
                    "on_error": "transient",
                    "capped": True,
                },
            },
            "authored-script": {
                "requested_attempt_wall_timeout_seconds": 240.0,
                "attempt_wall_timeout_seconds": 90.0,
                "requested_idle_timeout_seconds": None,
                "idle_timeout_seconds": None,
                "provider_request_timeout_seconds": None,
                "timeout_source": "authored",
                "timeout_capped": True,
                "retry": {
                    "explicit": True,
                    "requested_retries": 1,
                    "requested_total_attempts": 2,
                    "effective_total_attempts": 2,
                    "delay_ms": 4000,
                    "on_error": "all",
                    "capped": False,
                },
            },
            "default-shell": {
                "requested_attempt_wall_timeout_seconds": 120.0,
                "attempt_wall_timeout_seconds": 90.0,
                "requested_idle_timeout_seconds": None,
                "idle_timeout_seconds": None,
                "provider_request_timeout_seconds": None,
                "timeout_source": "archon_default",
                "timeout_capped": True,
                "retry": {
                    "explicit": False,
                    "requested_retries": 0,
                    "requested_total_attempts": 1,
                    "effective_total_attempts": 1,
                    "delay_ms": 3000,
                    "on_error": "transient",
                    "capped": False,
                },
            },
        },
    }


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.__setitem__("schema_version", True),
        lambda value: value.__setitem__("normalizer_version", 3.0),
        lambda value: value["limits"].__setitem__("extra", 1),
        lambda value: value["limits"].__setitem__(
            "provider_request_timeout_seconds", float("inf")
        ),
        lambda value: value["limits"].__setitem__("combined_total_attempts", 6),
        lambda value: value["nodes"]["agent"].pop("timeout_source"),
        lambda value: value["nodes"]["agent"].__setitem__("timeout_capped", 0),
        lambda value: value["nodes"]["agent"]["retry"].__setitem__(
            "explicit", 1
        ),
        lambda value: value["nodes"]["agent"]["retry"].__setitem__(
            "effective_total_attempts", 6
        ),
    ],
)
def test_effective_execution_semantics_rejects_noncanonical_shapes(
    tmp_path, workflow_writer, mutator
) -> None:
    package = _archon_package(
        tmp_path,
        workflow_writer,
        nodes=[{"id": "agent", "prompt": "work"}],
    )
    module = _execution_semantics_module()
    value = module.build_phase3_execution_semantics(
        package, RunExecutionLimits()
    ).to_dict()
    mutator(value)

    with pytest.raises(module.WorkflowExecutionSemanticsError) as exc:
        module.read_phase3_execution_semantics(value, package=package)

    assert exc.value.code == "workflow_execution_semantics_mismatch"


def test_snapshot_seals_effective_semantics_and_direct_store_has_one_default(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(
        tmp_path / "package",
        workflow_writer,
        nodes=[{"id": "shell", "bash": "true"}],
    )
    store = RunStore(tmp_path / "home")
    explicit_limits = RunExecutionLimits(
        ai_idle_timeout_seconds=120.0,
        ai_wall_timeout_seconds=240.0,
        provider_request_timeout_seconds=90.0,
        combined_retries=2,
        subprocess_timeout_seconds=30.0,
    )

    explicit = store.prepare_run_snapshot(
        package, execution_limits=explicit_limits
    )
    defaulted = store.prepare_run_snapshot(package)
    cloned = store.clone_prepared_snapshot(explicit)
    explicit_resources = json.loads(
        (explicit.staging_directory / "resources.json").read_bytes()
    )
    default_resources = json.loads(
        (defaulted.staging_directory / "resources.json").read_bytes()
    )

    assert explicit_resources["phase3_execution_semantics"]["limits"] == {
        "ai_idle_timeout_seconds": 120.0,
        "ai_wall_timeout_seconds": 240.0,
        "provider_request_timeout_seconds": 90.0,
        "subprocess_timeout_seconds": 30.0,
        "combined_total_attempts": 2,
    }
    assert default_resources["phase3_execution_semantics"]["limits"] == {
        "ai_idle_timeout_seconds": 300.0,
        "ai_wall_timeout_seconds": 1800.0,
        "provider_request_timeout_seconds": 300.0,
        "subprocess_timeout_seconds": 120.0,
        "combined_total_attempts": 5,
    }
    assert explicit.input_manifest_digest != defaulted.input_manifest_digest
    assert (
        cloned.staging_directory / "resources.json"
    ).read_bytes() == (explicit.staging_directory / "resources.json").read_bytes()


def test_legacy_snapshot_shape_and_digest_ignore_phase3_execution_authority(
    tmp_path, workflow_writer
) -> None:
    path = workflow_writer(
        tmp_path / "legacy", nodes=[{"id": "shell", "bash": "true"}]
    )
    package = load_workflow(path)
    store = RunStore(tmp_path / "home")

    defaulted = store.prepare_run_snapshot(package)
    tightened = store.prepare_run_snapshot(
        package,
        execution_limits=RunExecutionLimits(
            subprocess_timeout_seconds=30.0,
            combined_retries=1,
        ),
    )
    resources = json.loads(
        (tightened.staging_directory / "resources.json").read_bytes()
    )

    assert "phase3_execution_semantics" not in resources
    assert tightened.input_manifest_digest == defaulted.input_manifest_digest


def test_gateway_admission_seals_resolved_profile_execution_authority(
    tmp_path, workflow_writer
) -> None:
    home = tmp_path / "profile"
    path = workflow_writer(
        tmp_path / "package",
        name="archon-sealed-gateway-limits",
        nodes=[{"id": "start", "bash": "true"}],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump({
            "plugins": {
                "entries": {
                    "workflow": {
                        "runtime": {
                            "ai_idle_timeout_seconds": 120,
                            "ai_wall_timeout_seconds": 240,
                            "provider_request_timeout_seconds": 90,
                            "subprocess_timeout_seconds": 30,
                            "combined_retries": 2,
                        }
                    }
                }
            }
        }),
        encoding="utf-8",
    )
    package = load_workflow(path)
    risk = build_risk_summary(package, assess_compatibility(package))
    WorkflowTrustStore(home).trust(
        compute_package_digest(package).sha256,
        actor="test",
        risk_digest=risk.risk_digest,
    )
    store = RunStore(home)
    assert CoordinatorStore(store.database).try_acquire(
        CoordinatorIdentity(
            owner_id="gateway-sealed-limits",
            host_kind="gateway",
            host_instance_id="gateway-sealed-limits",
            pid=1,
            process_start_time=None,
        ),
        now=datetime.now(timezone.utc),
        lease_seconds=60,
    ).is_leader
    invocation = PluginInvocationContext(
        boundary="gateway",
        principal="gateway:telegram:user-1",
        operator_scope="gateway:default:telegram:chat-1:user-1",
        assurance="verified_adapter",
        return_route_capability="opaque-capability",
    )

    response = json.loads(
        workflow_gateway_command(
            f"run {shlex.quote(str(path))} --idempotency-key sealed-limits",
            invocation,
            hermes_home=home,
            workdir=tmp_path,
        )
    )
    resources = json.loads(
        (
            store.run_directory(str(response["result"]["run_id"]))
            / "resources.json"
        ).read_bytes()
    )

    assert resources["phase3_execution_semantics"]["limits"] == {
        "ai_idle_timeout_seconds": 120.0,
        "ai_wall_timeout_seconds": 240.0,
        "provider_request_timeout_seconds": 90.0,
        "subprocess_timeout_seconds": 30.0,
        "combined_total_attempts": 2,
    }


def _admit_snapshot(store: RunStore, package, prepared, *, key: str) -> str:
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=key,
            execution_mode="foreground",
            foreground_owner_id=f"owner-{key}",
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    return admitted.run_id


def test_scheduler_resume_uses_sealed_limits_after_current_configuration_changes(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(
        tmp_path / "package",
        workflow_writer,
        nodes=[{"id": "shell", "bash": "true"}],
    )
    store = RunStore(tmp_path / "home")
    admitted_limits = RunExecutionLimits(
        ai_idle_timeout_seconds=120.0,
        ai_wall_timeout_seconds=240.0,
        provider_request_timeout_seconds=90.0,
        combined_retries=2,
        subprocess_timeout_seconds=30.0,
    )
    prepared = store.prepare_run_snapshot(
        package, execution_limits=admitted_limits
    )
    run_id = _admit_snapshot(
        store, package, prepared, key="changed-config-resume"
    )
    scheduler = RunScheduler(
        store,
        ai_idle_timeout_seconds=300.0,
        ai_wall_timeout_seconds=1800.0,
        provider_request_timeout_seconds=300.0,
        subprocess_timeout_seconds=120.0,
        default_max_attempts=5,
    )
    try:
        loaded = scheduler._prepare_run_package(run_id, None)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert loaded is not None
    resumed_limits = loaded[1]
    assert (
        resumed_limits.ai_idle_timeout_seconds,
        resumed_limits.ai_wall_timeout_seconds,
        resumed_limits.provider_request_timeout_seconds,
        resumed_limits.subprocess_timeout_seconds,
        resumed_limits.combined_retries,
    ) == (120.0, 240.0, 90.0, 30.0, 2)


def test_scheduler_rejects_execution_semantics_tamper_before_claim(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(
        tmp_path / "package",
        workflow_writer,
        nodes=[{"id": "shell", "bash": "true"}],
    )
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    run_id = _admit_snapshot(store, package, prepared, key="semantics-tamper")
    run_directory = store.run_directory(run_id)
    resources_path = run_directory / "resources.json"
    resources = json.loads(resources_path.read_bytes())
    resources["phase3_execution_semantics"]["nodes"]["shell"]["retry"][
        "effective_total_attempts"
    ] = 5
    encoded = json.dumps(resources, sort_keys=True, separators=(",", ":")).encode()
    resources_path.write_bytes(encoded)
    from plugins.workflow.scheduled_revalidation import sealed_snapshot_digest

    store.append_event(
        run_id,
        "test_reseal_tamper",
        projection_updates={
            "input_manifest_digest": hashlib.sha256(encoded).hexdigest(),
            "sealed_snapshot_digest": sealed_snapshot_digest(run_directory),
        },
    )
    scheduler = RunScheduler(store)
    try:
        with pytest.raises(WorkflowExecutionSemanticsError) as exc:
            scheduler._prepare_run_package(run_id, None)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert exc.value.code == "workflow_execution_semantics_mismatch"
    assert store.load_run(run_id)["nodes"]["shell"]["attempts"] == []
