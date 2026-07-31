from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.runtime import (
    StoreRegistryCapacityError,
    WorkflowApiLimits,
    WorkflowStoreRegistry,
)
from plugins.workflow.schema import load_workflow
import plugins.workflow.store as store_module
from plugins.workflow.store import RunStore


class _Store:
    def __init__(self, home: Path) -> None:
        self.home = home


def test_registry_reuses_profile_store_and_evicts_only_idle_lru(tmp_path) -> None:
    created: list[Path] = []

    def factory(home: Path):
        created.append(home)
        return _Store(home)

    registry = WorkflowStoreRegistry(max_profiles=2, store_factory=factory)
    one = tmp_path / "one"
    two = tmp_path / "two"
    three = tmp_path / "three"

    with registry.lease(one) as first:
        with registry.lease(one) as repeated:
            assert repeated is first
        with registry.lease(two):
            with pytest.raises(StoreRegistryCapacityError):
                with registry.lease(three):
                    pass

    with registry.lease(three):
        pass

    assert created == [one.resolve(), two.resolve(), three.resolve()]
    assert registry.snapshot()["profiles"] == 2


@pytest.mark.parametrize(
    "values",
    [
        {"max_cached_profiles": 0},
        {"max_event_waiters": 0},
        {"store_io_workers": 0},
        {"max_cached_profiles": True},
        {"max_event_waiters": "many"},
    ],
)
def test_api_limits_reject_zero_unbounded_or_non_integer_values(values) -> None:
    with pytest.raises(ValueError):
        WorkflowApiLimits.from_mapping(values)


def test_api_limit_defaults_are_bounded() -> None:
    limits = WorkflowApiLimits.from_mapping({})
    assert limits.max_cached_profiles == 8
    assert limits.max_event_waiters == 16
    assert limits.store_io_workers == 4


def test_coordinator_candidate_scan_never_opens_artifact_bodies(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="coordinator-metadata-only")
    )
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="coordinator-metadata-only",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    body = (
        store.run_directory(admitted.run_id)
        / "publications"
        / ("a" * 32)
        / "content.md"
    )
    body.parent.mkdir(parents=True)
    body.write_bytes(b"BODY_MUST_NOT_BE_OPENED")
    monkeypatch.setattr(
        store_module,
        "_read_descriptor_relative",
        lambda *_args, **_kwargs: pytest.fail(
            "coordinator candidate scan must not open artifact bodies"
        ),
    )

    candidates, _cursor, _exhausted = store.coordinator_candidates(
        after=None,
        now=datetime.now(timezone.utc),
    )

    assert [candidate["run_id"] for candidate in candidates] == [admitted.run_id]
