from __future__ import annotations

import argparse
import importlib
import hashlib
import json
import shlex
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from plugins.workflow.models import ExecutionFence, RunExecutionLimits
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.api_admission import ApiAdmissionAuthority, start_api_run
from plugins.workflow.cli import _runtime_config, register_cli
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.gateway_command import workflow_gateway_command
from plugins.workflow.execution_semantics import WorkflowExecutionSemanticsError
from plugins.workflow.runner_binding import (
    assess_package_execution,
    background_execution_context,
    production_workflow_runner_binding,
)
from plugins.workflow.schema import load_workflow
from plugins.workflow.scheduler import RunScheduler
import plugins.workflow.showcase as showcase_module
from plugins.workflow.showcase import run_showcase
from plugins.workflow.store import RunStore
from plugins.workflow.trust import (
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
)
from hermes_cli.plugin_invocation import PluginInvocationContext
from tools.managed_process import ProcessResourceLimits
import yaml


def _execution_semantics_module():
    try:
        return importlib.import_module("plugins.workflow.execution_semantics")
    except ModuleNotFoundError:
        pytest.fail("Phase 3 effective execution semantics codec is not implemented")


class _RecordingAuthorizationRunStore(RunStore):
    """Observe real store-issued authorizations without replacing verification."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.issued_scheduled_authorizations: list[object] = []

    def _scheduled_promotion_authorization(self, *args, **kwargs):
        authorization = super()._scheduled_promotion_authorization(*args, **kwargs)
        self.issued_scheduled_authorizations.append(authorization)
        return authorization


def _archon_package(tmp_path, workflow_writer, *, nodes, sidecar=None):
    path = workflow_writer(tmp_path, name="phase3-execution", nodes=nodes)
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        yaml.safe_dump(
            {
                "language_compatibility": "archon-2026-07",
                **dict(sidecar or {}),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
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
            "ai_idle_timeout_seconds", 300
        ),
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


def test_all_admission_boundaries_seal_identical_canonical_execution_semantics(
    tmp_path,
    workflow_writer,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = workflow_writer(
        home / "workflows",
        name="archon-execution-parity",
        filename="archon-execution-parity.yaml",
        nodes=[
            {"id": "a-shell", "bash": "true", "timeout": 90_000},
            {
                "id": "scripted",
                "script": "print('ok')",
                "runtime": "uv",
                "timeout": 180_000,
                "retry": {"max_attempts": 1, "on_error": "all"},
            },
            {
                "id": "agent",
                "prompt": "work",
                "idle_timeout": 150_000,
                "retry": {"max_attempts": 5},
            },
        ],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        yaml.safe_dump(
            {
                "language_compatibility": "archon-2026-07",
                "delivery_defaults": {
                    "inputs": {
                        "arguments": {
                            "kind": "text",
                            "required": True,
                            "max_bytes": 1024,
                        }
                    }
                },
                "limits": {
                    "ai_idle_timeout_seconds": 160,
                    "ai_wall_timeout_seconds": 300,
                    "provider_request_timeout_seconds": 100,
                    "subprocess_timeout_seconds": 60,
                    "combined_retries": 4,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "plugins": {
                    "entries": {
                        "workflow": {
                            "runtime": {
                                "ai_idle_timeout_seconds": 200,
                                "ai_wall_timeout_seconds": 400,
                                "provider_request_timeout_seconds": 120,
                                "subprocess_timeout_seconds": 80,
                                "combined_retries": 5,
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    package = load_workflow(path)
    binding = production_workflow_runner_binding()
    context = background_execution_context(binding, requires_ai=True)
    _compatibility, risk = assess_package_execution(package, context)
    WorkflowTrustStore(home).trust(
        compute_package_digest(package).sha256,
        actor="test",
        risk_digest=risk.risk_digest,
    )
    store = RunStore(home)
    now = datetime.now(timezone.utc)
    coordinator = CoordinatorStore(store.database)
    coordinator_identity = CoordinatorIdentity(
        owner_id="execution-parity",
        host_kind="web",
        host_instance_id="execution-parity",
        pid=1,
        process_start_time=None,
    )
    coordinator_lease = coordinator.try_acquire(
        coordinator_identity,
        now=now,
        lease_seconds=300,
    )
    assert coordinator_lease.is_leader
    values = {"arguments": "same-input"}
    run_ids: dict[str, str] = {}
    authority = ApiAdmissionAuthority(
        principal="execution-parity",
        namespace="execution-parity",
        operator_scope=None,
        source_instance="desktop:execution-parity",
        assurance="local_admin_claim",
        trigger_source="desktop",
    )
    due = now.replace(microsecond=0) + timedelta(minutes=5)
    scheduled = start_api_run(
        store,
        hermes_home=home,
        workdir=tmp_path,
        user_home=tmp_path,
        workflow_name=package.definition.name,
        values=values,
        idempotency_key="execution-parity-scheduled",
        concurrency_policy="allow",
        authority=authority,
        catalog_source="profile",
        runner_binding=binding,
        schedule_at=due.isoformat().replace("+00:00", "Z"),
        schedule_now_utc=now,
    )
    assert scheduled["run_id"] is not None
    run_ids["scheduled"] = str(scheduled["run_id"])

    cli_parser = argparse.ArgumentParser()
    register_cli(cli_parser)
    cli_args = cli_parser.parse_args(
        [
            "--workdir",
            str(tmp_path),
            "--hermes-home",
            str(home),
            "run",
            str(path),
            "--arguments",
            values["arguments"],
            "--no-wait",
            "--idempotency-key",
            "execution-parity-cli",
            "--json",
        ]
    )
    assert cli_args.func(cli_args) == 0
    cli_envelope = json.loads(capsys.readouterr().out)
    assert cli_envelope["result"]["run_id"] is not None
    run_ids["cli"] = str(cli_envelope["result"]["run_id"])

    invocation = PluginInvocationContext(
        boundary="gateway",
        principal="gateway:telegram:user-1",
        operator_scope="gateway:default:telegram:chat-1:user-1",
        assurance="verified_adapter",
        return_route_capability="opaque-capability",
    )
    gateway = json.loads(
        workflow_gateway_command(
            "run "
            f"{shlex.quote(str(path))} "
            f"--arguments {shlex.quote(values['arguments'])} "
            "--idempotency-key execution-parity-gateway",
            invocation,
            hermes_home=home,
            workdir=tmp_path,
        )
    )
    assert gateway["result"]["run_id"] is not None
    run_ids["gateway"] = str(gateway["result"]["run_id"])

    api = start_api_run(
        store,
        hermes_home=home,
        workdir=tmp_path,
        user_home=tmp_path,
        workflow_name=package.definition.name,
        values=values,
        idempotency_key="execution-parity-api",
        concurrency_policy="queue",
        authority=authority,
        catalog_source="profile",
        runner_binding=binding,
    )
    assert api["run_id"] is not None
    run_ids["api"] = str(api["run_id"])

    base_scenario = showcase_module.load_showcase_catalog()["resilience"]
    scenario = replace(
        base_scenario,
        id="archon-execution-parity",
        display_name="Archon execution parity",
        package_digest=risk.package_digest,
        capability_claims=(),
        requires_ai=True,
    )
    monkeypatch.setattr(
        showcase_module,
        "load_showcase_catalog",
        lambda: {scenario.id: scenario},
    )
    monkeypatch.setattr(
        showcase_module,
        "_scenario_package",
        lambda _scenario, **_kwargs: package,
    )
    monkeypatch.setattr(
        showcase_module,
        "preflight_showcase",
        lambda *_args, **_kwargs: {"bundle_digest": "b" * 64},
    )
    monkeypatch.setattr(
        showcase_module,
        "_verified_distribution_risk",
        lambda *_args, **_kwargs: risk,
    )
    assert coordinator.release(
        coordinator_identity,
        epoch=coordinator_lease.lease.epoch,
        now=now,
    )
    showcase = run_showcase(
        scenario.id,
        hermes_home=home,
        symptom=values["arguments"],
        no_wait=True,
        idempotency_key="execution-parity-showcase",
    )
    assert showcase["run_id"] is not None, showcase
    run_ids["showcase"] = str(showcase["run_id"])

    resolved = RunExecutionLimits.resolve(
        _runtime_config(home, sidecar=package.sidecar)
    )
    direct = store.prepare_run_snapshot(
        package,
        values=values,
        execution_limits=resolved,
    )
    direct_resources = (direct.staging_directory / "resources.json").read_bytes()
    expected_digest = hashlib.sha256(direct_resources).hexdigest()
    expected_semantics = json.dumps(
        json.loads(direct_resources)["phase3_execution_semantics"],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    for surface, run_id in run_ids.items():
        run_directory = store.run_directory(run_id)
        resource_bytes = (run_directory / "resources.json").read_bytes()
        semantics_bytes = json.dumps(
            json.loads(resource_bytes)["phase3_execution_semantics"],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        assert semantics_bytes == expected_semantics, surface
        assert hashlib.sha256(resource_bytes).hexdigest() == expected_digest, surface
        assert store.load_run(run_id)["input_manifest_digest"] == expected_digest

    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "plugins": {
                    "entries": {
                        "workflow": {
                            "runtime": {
                                "ai_idle_timeout_seconds": 300,
                                "ai_wall_timeout_seconds": 1800,
                                "provider_request_timeout_seconds": 300,
                                "subprocess_timeout_seconds": 120,
                                "combined_retries": 1,
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    restarted_store = _RecordingAuthorizationRunStore(
        home,
        max_executing_runs=8,
    )
    restarted_coordinator = CoordinatorStore(restarted_store.database)
    restarted_identity = CoordinatorIdentity(
        owner_id="execution-parity-restarted",
        host_kind="web",
        host_instance_id="execution-parity-restarted",
        pid=1,
        process_start_time=None,
    )
    restarted_lease = restarted_coordinator.try_acquire(
        restarted_identity,
        now=datetime.now(timezone.utc),
        lease_seconds=300,
    )
    assert restarted_lease.is_leader
    changed_scheduler = RunScheduler(
        restarted_store,
        runner_binding=binding,
        execution_fence=ExecutionFence(
            restarted_identity.owner_id,
            restarted_lease.lease.epoch,
        ),
        utcnow=lambda: due,
        ai_idle_timeout_seconds=300.0,
        ai_wall_timeout_seconds=1800.0,
        provider_request_timeout_seconds=300.0,
        subprocess_timeout_seconds=120.0,
        default_max_attempts=1,
    )
    try:
        resumed = changed_scheduler.advance(
            run_ids["scheduled"],
            max_nodes=1,
        )
    finally:
        changed_scheduler.shutdown(deadline_seconds=2)
    assert resumed["status"] == "running"
    assert resumed["nodes"]["a-shell"]["state"] == "succeeded"
    assert resumed["schedule_revalidation"] == {
        "execution_identity": resumed["run_metadata"]["execution_identity"],
        "admission_state_version": 1,
    }
    assert (
        restarted_store.run_directory(run_ids["scheduled"]) / "resources.json"
    ).read_bytes() == direct_resources
    assert len(restarted_store.issued_scheduled_authorizations) == 1
    with pytest.raises(RuntimeError, match="already consumed"):
        restarted_store._consume_scheduled_promotion_authorization(
            restarted_store.issued_scheduled_authorizations[0],
            run_ids["scheduled"],
            resumed,
        )
    promoted = [
        event
        for event in restarted_store.tail_events(run_ids["scheduled"])
        if event["event_type"] == "run_promoted"
    ]
    assert len(promoted) == 1


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


def _reseal_resources(
    store: RunStore,
    run_id: str,
    encoded: bytes,
) -> None:
    run_directory = store.run_directory(run_id)
    (run_directory / "resources.json").write_bytes(encoded)
    from plugins.workflow.scheduled_revalidation import sealed_snapshot_digest

    projection = store.load_run(run_id)
    snapshot_digest = sealed_snapshot_digest(run_directory)
    updates = {
        "input_manifest_digest": hashlib.sha256(encoded).hexdigest(),
        "sealed_snapshot_digest": snapshot_digest,
    }
    metadata = projection.get("run_metadata")
    if isinstance(metadata, dict) and "sealed_input_digest" in metadata:
        updates["run_metadata"] = {
            **metadata,
            "sealed_input_digest": updates["input_manifest_digest"],
            "sealed_snapshot_digest": snapshot_digest,
        }
    store.append_event(
        run_id,
        "test_reseal_resources",
        projection_updates=updates,
    )


def test_scheduler_resume_uses_sealed_limits_after_current_configuration_changes(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    package = _archon_package(
        tmp_path / "package",
        workflow_writer,
        nodes=[{"id": "shell", "bash": "true"}],
        sidecar={
            "limits": {
                "max_parallel_nodes": 2,
                # Invalid against the restarted current idle limit if the
                # legacy five-field resolver runs before sealed v3 authority.
                "ai_wall_timeout_seconds": 1,
            },
            "resource_limits": {
                "process_tree_rss_bytes": 64 * 1024 * 1024,
                "process_tree_cpu_seconds": 45.0,
                "max_descendants": 3,
            },
        },
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
        max_parallel_nodes=3,
        cooperative_shutdown_seconds=1.0,
        term_grace_seconds=2.0,
        kill_reap_grace_seconds=3.0,
        resource_limits=ProcessResourceLimits(
            max_rss_bytes=128 * 1024 * 1024,
            max_cpu_seconds=90.0,
            max_descendants=6,
        ),
    )
    current_config_reads = 0

    def reject_current_config_read(_package):
        nonlocal current_config_reads
        current_config_reads += 1
        raise AssertionError("v3 resume consulted current execution limits")

    monkeypatch.setattr(scheduler, "_run_execution_limits", reject_current_config_read)
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
    assert current_config_reads == 0
    assert (
        resumed_limits.max_parallel_nodes,
        resumed_limits.process_tree_rss_bytes,
        resumed_limits.process_tree_cpu_seconds,
        resumed_limits.max_descendants,
        resumed_limits.cooperative_shutdown_seconds,
        resumed_limits.term_grace_seconds,
        resumed_limits.kill_reap_grace_seconds,
    ) == (2, 64 * 1024 * 1024, 45.0, 3, 1.0, 2.0, 3.0)


@pytest.mark.parametrize(
    "rewrite",
    [
        lambda encoded: encoded.replace(
            b'"ai_idle_timeout_seconds":300.0',
            b'"ai_idle_timeout_seconds":300',
            1,
        ),
        lambda encoded: encoded.replace(
            b'"ai_idle_timeout_seconds":300.0',
            b'"ai_idle_timeout_seconds":3e2',
            1,
        ),
        lambda encoded: encoded.replace(
            b'"ai_idle_timeout_seconds":300.0',
            b'"ai_idle_timeout_seconds":300.00',
            1,
        ),
        lambda encoded: encoded.replace(b"{", b"{ ", 1),
        lambda encoded: json.dumps(
            dict(reversed(tuple(json.loads(encoded).items()))),
            separators=(",", ":"),
        ).encode(),
    ],
    ids=("integer", "exponent", "decimal", "whitespace", "field-order"),
)
def test_scheduler_rejects_noncanonical_execution_semantics_bytes(
    tmp_path,
    workflow_writer,
    rewrite,
) -> None:
    package = _archon_package(
        tmp_path / "package",
        workflow_writer,
        nodes=[{"id": "shell", "bash": "true"}],
    )
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    run_id = _admit_snapshot(store, package, prepared, key="noncanonical-semantics")
    resources_path = store.run_directory(run_id) / "resources.json"
    encoded = resources_path.read_bytes()
    changed = rewrite(encoded)
    assert changed != encoded
    _reseal_resources(store, run_id, changed)

    scheduler = RunScheduler(store)
    try:
        with pytest.raises(WorkflowExecutionSemanticsError) as exc:
            scheduler._prepare_run_package(run_id, None)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert exc.value.code == "workflow_execution_semantics_mismatch"
    assert store.load_run(run_id)["nodes"]["shell"]["attempts"] == []


def test_scheduled_promotion_preserves_execution_semantics_mismatch(
    tmp_path,
    workflow_writer,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = _archon_package(
        home / "workflows",
        workflow_writer,
        nodes=[{"id": "shell", "bash": "true"}],
    )
    binding = production_workflow_runner_binding()
    context = background_execution_context(binding, requires_ai=False)
    _compatibility, risk = assess_package_execution(package, context)
    WorkflowTrustStore(home).trust(
        compute_package_digest(package).sha256,
        actor="test",
        risk_digest=risk.risk_digest,
    )
    store = RunStore(home)
    due = datetime.now(timezone.utc)
    identity = CoordinatorIdentity(
        owner_id="scheduled-semantics-tamper",
        host_kind="web",
        host_instance_id="scheduled-semantics-tamper",
        pid=1,
        process_start_time=None,
    )
    lease = CoordinatorStore(store.database).try_acquire(
        identity,
        now=due,
        lease_seconds=60,
    )
    assert lease.is_leader
    admitted = start_api_run(
        store,
        hermes_home=home,
        workdir=tmp_path,
        user_home=tmp_path,
        workflow_name=package.definition.name,
        values={},
        idempotency_key="scheduled-semantics-tamper",
        concurrency_policy="queue",
        authority=ApiAdmissionAuthority(
            principal="scheduled-semantics-tamper",
            namespace="scheduled-semantics-tamper",
            operator_scope=None,
            source_instance="desktop:scheduled-semantics-tamper",
            assurance="local_admin_claim",
            trigger_source="desktop",
        ),
        catalog_source="profile",
        runner_binding=binding,
        schedule_at=due.isoformat().replace("+00:00", "Z"),
        schedule_now_utc=due - timedelta(seconds=10),
    )
    run_id = str(admitted["run_id"])
    restarted_store = _RecordingAuthorizationRunStore(home)
    resources_path = restarted_store.run_directory(run_id) / "resources.json"
    resources = json.loads(resources_path.read_bytes())
    resources["phase3_execution_semantics"]["nodes"]["shell"]["retry"][
        "effective_total_attempts"
    ] = 5
    _reseal_resources(
        restarted_store,
        run_id,
        json.dumps(resources, sort_keys=True, separators=(",", ":")).encode(),
    )

    scheduler = RunScheduler(
        restarted_store,
        runner_binding=binding,
        execution_fence=ExecutionFence(identity.owner_id, lease.lease.epoch),
        utcnow=lambda: due,
    )
    executor_calls: list[object] = []
    monkeypatch.setattr(
        scheduler,
        "_execute_claim",
        lambda *args, **kwargs: executor_calls.append((args, kwargs)),
    )
    try:
        terminal = scheduler.advance(run_id, max_nodes=1)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert terminal["status"] == "failed"
    assert terminal["last_error"]["code"] == "workflow_execution_semantics_mismatch"
    assert terminal["nodes"]["shell"]["attempts"] == []
    assert executor_calls == []
    assert len(restarted_store.issued_scheduled_authorizations) == 1
    with pytest.raises(RuntimeError, match="already consumed"):
        restarted_store._consume_scheduled_promotion_authorization(
            restarted_store.issued_scheduled_authorizations[0],
            run_id,
            terminal,
        )
    with restarted_store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
        ).fetchone()[0] == 0
    assert any(
        event["event_type"] == "run_failed"
        and event["payload"].get("validation_code")
        == "workflow_execution_semantics_mismatch"
        for event in restarted_store.tail_events(run_id, limit=20)
    )


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
    _reseal_resources(store, run_id, encoded)
    scheduler = RunScheduler(store)
    try:
        with pytest.raises(WorkflowExecutionSemanticsError) as exc:
            scheduler._prepare_run_package(run_id, None)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert exc.value.code == "workflow_execution_semantics_mismatch"
    assert store.load_run(run_id)["nodes"]["shell"]["attempts"] == []
