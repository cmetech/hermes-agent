from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import time

import pytest

from plugins.workflow.marketplace.operations import (
    MarketplaceOperationCapacityError,
    MarketplaceOperationConflictError,
    MarketplaceOperationNotFoundError,
    MarketplaceOperationResult,
    WorkflowMarketplaceOperationRegistry,
)


def _wait_terminal(
    registry: WorkflowMarketplaceOperationRegistry,
    operation_id: str,
    *,
    actor: str = "operator-a",
):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        operation = registry.get(operation_id, actor=actor)
        if operation.state in {"succeeded", "failed", "cancelled"}:
            return operation
        time.sleep(0.01)
    raise AssertionError("operation did not become terminal")


def _result(value: str = "ok") -> MarketplaceOperationResult:
    return MarketplaceOperationResult(type="test", value={"value": value})


@pytest.fixture
def registry():
    value = WorkflowMarketplaceOperationRegistry(
        profile_key="/profiles/support",
        profile="support",
        max_workers=2,
        max_in_flight=4,
        max_terminal=8,
    )
    yield value
    value.close(wait_timeout=2)


def test_operation_result_schema_rejects_unknown_nonfinite_and_oversize_values() -> (
    None
):
    with pytest.raises(Exception):
        MarketplaceOperationResult(type="test", value={"value": "ok"}, unexpected=True)
    with pytest.raises(Exception):
        MarketplaceOperationResult(type="test", value={"value": float("nan")})
    with pytest.raises(Exception):
        MarketplaceOperationResult(
            type="test", value={"value": "x" * (2 * 1024 * 1024)}
        )


def test_operation_success_is_an_immutable_profile_scoped_projection(registry) -> None:
    started = registry.start(
        "refresh",
        lambda cancellation: _result(),
        actor="operator-a",
        target="source:company",
    )

    terminal = _wait_terminal(registry, started.id)

    assert started.profile == "support"
    assert started.id.startswith("wmop_")
    assert terminal.state == "succeeded"
    assert terminal.phase == "completed"
    assert terminal.progress == 100
    assert terminal.result == _result()
    assert terminal.error is None
    with pytest.raises(Exception):
        terminal.state = "failed"
    assert terminal.result is not None
    terminal.result.value["value"] = "caller-mutated-copy"
    assert registry.get(started.id, actor="operator-a").result == _result()


def test_cancelled_fetch_never_reports_a_result(registry) -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocking_fetch(cancellation):
        entered.set()
        release.wait(timeout=5)
        return _result("must-not-leak")

    started = registry.start(
        "refresh",
        blocking_fetch,
        actor="operator-a",
        target="source:company",
    )
    assert entered.wait(timeout=2)

    registry.cancel(started.id, actor="operator-a")
    release.set()
    terminal = _wait_terminal(registry, started.id)

    assert terminal.state == "cancelled"
    assert terminal.result is None
    assert terminal.error is None


def test_cancellation_during_atomic_commit_reports_committed_success(registry) -> None:
    entered_atomic = threading.Event()
    release = threading.Event()

    def atomic_mutation(cancellation):
        cancellation.begin_atomic()
        entered_atomic.set()
        release.wait(timeout=5)
        cancellation.mark_committed()
        return _result("committed")

    started = registry.start(
        "install_confirm",
        atomic_mutation,
        actor="operator-a",
        target="package:company/support",
    )
    assert entered_atomic.wait(timeout=2)

    during = registry.cancel(started.id, actor="operator-a")
    assert during.state == "running"
    release.set()
    terminal = _wait_terminal(registry, started.id)

    assert terminal.state == "succeeded"
    assert terminal.result == _result("committed")


def test_cancellation_immediately_before_atomic_entry_prevents_commit(registry) -> None:
    ready_to_commit = threading.Event()
    release = threading.Event()
    committed = threading.Event()

    def atomic_mutation(cancellation):
        ready_to_commit.set()
        release.wait(timeout=5)
        cancellation.begin_atomic()
        committed.set()
        cancellation.mark_committed()
        return _result("must-not-commit")

    started = registry.start(
        "install_confirm",
        atomic_mutation,
        actor="operator-a",
        target="package:company/support",
    )
    assert ready_to_commit.wait(timeout=2)

    registry.cancel(started.id, actor="operator-a")
    release.set()
    terminal = _wait_terminal(registry, started.id)

    assert terminal.state == "cancelled"
    assert terminal.result is None
    assert committed.is_set() is False


def test_atomic_failure_never_reports_cancelled_or_a_result(registry) -> None:
    def atomic_mutation(cancellation):
        cancellation.begin_atomic()
        raise RuntimeError("/private/tmp/staging transaction failed")

    started = registry.start(
        "remove_confirm",
        atomic_mutation,
        actor="operator-a",
        target="package:company/support",
    )
    terminal = _wait_terminal(registry, started.id)

    assert terminal.state == "failed"
    assert terminal.result is None
    assert terminal.error is not None
    assert "/private" not in terminal.model_dump_json()


def test_queued_cancellation_prevents_the_callable_from_running() -> None:
    registry = WorkflowMarketplaceOperationRegistry(
        profile_key="/profiles/support",
        profile="support",
        max_workers=1,
        max_in_flight=2,
        max_terminal=4,
    )
    blocker_entered = threading.Event()
    release = threading.Event()
    queued_ran = threading.Event()
    try:
        first = registry.start(
            "refresh",
            lambda cancellation: (
                blocker_entered.set(),
                release.wait(timeout=5),
                _result(),
            )[-1],
            actor="operator-a",
            target="source:first",
        )
        assert blocker_entered.wait(timeout=2)
        queued = registry.start(
            "refresh",
            lambda cancellation: (queued_ran.set(), _result())[1],
            actor="operator-a",
            target="source:second",
        )

        cancelled = registry.cancel(queued.id, actor="operator-a")
        release.set()

        assert cancelled.state == "cancelled"
        assert _wait_terminal(registry, first.id).state == "succeeded"
        assert queued_ran.is_set() is False
    finally:
        registry.close(wait_timeout=2)


def test_registry_bounds_pending_and_active_reservations() -> None:
    registry = WorkflowMarketplaceOperationRegistry(
        profile_key="/profiles/support",
        profile="support",
        max_workers=1,
        max_in_flight=1,
        max_terminal=2,
    )
    entered = threading.Event()
    release = threading.Event()
    try:
        registry.start(
            "refresh",
            lambda cancellation: (entered.set(), release.wait(timeout=5), _result())[
                -1
            ],
            actor="operator-a",
            target="source:first",
        )
        assert entered.wait(timeout=2)

        with pytest.raises(MarketplaceOperationCapacityError) as caught:
            registry.start(
                "refresh",
                lambda cancellation: _result(),
                actor="operator-a",
                target="source:second",
            )

        assert caught.value.code == "marketplace_operation_capacity"
    finally:
        release.set()
        registry.close(wait_timeout=2)


def test_same_target_is_single_flight_but_unrelated_targets_can_run() -> None:
    registry = WorkflowMarketplaceOperationRegistry(
        profile_key="/profiles/support",
        profile="support",
        max_workers=2,
        max_in_flight=3,
        max_terminal=3,
    )
    entered = threading.Event()
    release = threading.Event()
    try:
        first = registry.start(
            "install_prepare",
            lambda cancellation: (entered.set(), release.wait(timeout=5), _result())[
                -1
            ],
            actor="operator-a",
            target="package:company/support",
        )
        assert entered.wait(timeout=2)

        with pytest.raises(MarketplaceOperationConflictError) as caught:
            registry.start(
                "update_prepare",
                lambda cancellation: _result(),
                actor="operator-b",
                target="package:company/support",
            )
        unrelated = registry.start(
            "install_prepare",
            lambda cancellation: _result("other"),
            actor="operator-b",
            target="package:company/other",
        )

        assert caught.value.code == "marketplace_operation_conflict"
        assert (
            _wait_terminal(registry, unrelated.id, actor="operator-b").state
            == "succeeded"
        )
        release.set()
        assert _wait_terminal(registry, first.id).state == "succeeded"
    finally:
        release.set()
        registry.close(wait_timeout=2)


def test_operation_lookup_is_actor_scoped_and_unknown_is_indistinguishable(
    registry,
) -> None:
    started = registry.start(
        "refresh",
        lambda cancellation: _result(),
        actor="operator-a",
        target="source:company",
    )
    _wait_terminal(registry, started.id)

    for operation_id, actor in (
        (started.id, "operator-b"),
        ("wmop_deadbeef_missing", "operator-a"),
    ):
        with pytest.raises(MarketplaceOperationNotFoundError) as caught:
            registry.get(operation_id, actor=actor)
        assert caught.value.code == "marketplace_operation_not_found"
        assert str(caught.value) == "marketplace operation was not found"


def test_terminal_eviction_is_deterministic_and_never_evicts_active() -> None:
    now = [datetime(2026, 9, 4, tzinfo=timezone.utc)]
    registry = WorkflowMarketplaceOperationRegistry(
        profile_key="/profiles/support",
        profile="support",
        max_workers=1,
        max_in_flight=2,
        max_terminal=1,
        terminal_ttl=timedelta(hours=1),
        clock=lambda: now[0],
    )
    release = threading.Event()
    entered = threading.Event()
    try:
        first = registry.start(
            "refresh",
            lambda cancellation: _result("first"),
            actor="operator-a",
            target="source:first",
        )
        assert _wait_terminal(registry, first.id).state == "succeeded"
        now[0] += timedelta(seconds=1)
        active = registry.start(
            "refresh",
            lambda cancellation: (entered.set(), release.wait(timeout=5), _result())[
                -1
            ],
            actor="operator-a",
            target="source:active",
        )
        assert entered.wait(timeout=2)
        now[0] += timedelta(seconds=1)
        second = registry.start(
            "refresh",
            lambda cancellation: _result("second"),
            actor="operator-a",
            target="source:second",
        )
        assert registry.get(active.id, actor="operator-a").state == "running"
        release.set()
        assert _wait_terminal(registry, second.id).state == "succeeded"

        with pytest.raises(MarketplaceOperationNotFoundError):
            registry.get(first.id, actor="operator-a")
        assert registry.get(second.id, actor="operator-a").state == "succeeded"
    finally:
        release.set()
        registry.close(wait_timeout=2)


def test_terminal_ttl_evicts_only_expired_terminal_operations() -> None:
    now = [datetime(2026, 9, 4, tzinfo=timezone.utc)]
    registry = WorkflowMarketplaceOperationRegistry(
        profile_key="/profiles/support",
        profile="support",
        max_workers=1,
        max_in_flight=2,
        max_terminal=2,
        terminal_ttl=timedelta(seconds=30),
        clock=lambda: now[0],
    )
    try:
        finished = registry.start(
            "refresh",
            lambda cancellation: _result(),
            actor="operator-a",
        )
        assert _wait_terminal(registry, finished.id).state == "succeeded"

        now[0] += timedelta(seconds=31)

        with pytest.raises(MarketplaceOperationNotFoundError):
            registry.get(finished.id, actor="operator-a")
    finally:
        registry.close(wait_timeout=2)


def test_operation_failure_does_not_expose_exception_text_or_local_paths(
    registry,
) -> None:
    def fail(cancellation):
        raise RuntimeError(
            "https://user:secret@example.test/private /tmp/.staging access_token=secret"
        )

    started = registry.start(
        "refresh",
        fail,
        actor="operator-a",
        target="source:company",
    )

    terminal = _wait_terminal(registry, started.id)
    encoded = terminal.model_dump_json()

    assert terminal.state == "failed"
    assert terminal.result is None
    assert terminal.error is not None
    assert terminal.error.code == "marketplace_operation_failed"
    assert terminal.error.message == "Workflow marketplace operation failed."
    assert "secret" not in encoded
    assert "/tmp" not in encoded


def test_concurrent_cancel_and_get_keep_a_valid_terminal_projection(registry) -> None:
    entered = threading.Event()
    release = threading.Event()
    started = registry.start(
        "refresh",
        lambda cancellation: (entered.set(), release.wait(timeout=5), _result())[-1],
        actor="operator-a",
        target="source:company",
    )
    assert entered.wait(timeout=2)

    threads = [
        threading.Thread(target=lambda: registry.cancel(started.id, actor="operator-a"))
        for _ in range(8)
    ]
    threads.extend(
        threading.Thread(target=lambda: registry.get(started.id, actor="operator-a"))
        for _ in range(8)
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
    release.set()

    terminal = _wait_terminal(registry, started.id)
    assert terminal.state == "cancelled"
    assert terminal.result is None
