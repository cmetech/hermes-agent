from __future__ import annotations

from pathlib import Path

import pytest

from plugins.workflow.runtime import (
    StoreRegistryCapacityError,
    WorkflowApiLimits,
    WorkflowStoreRegistry,
)


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
