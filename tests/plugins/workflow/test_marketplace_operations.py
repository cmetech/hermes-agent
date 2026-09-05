from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import time

import pytest

from plugins.workflow.marketplace.operations import (
    MarketplaceSourceRefreshOperationResult,
    MarketplaceSourceRefreshValue,
    MarketplaceTrustGrantOperationResult,
    MarketplaceTrustState,
    MarketplaceTrustStatesValue,
    MarketplaceOperationRegistryError,
    MarketplaceOperationCapacityError,
    MarketplaceOperationConflictError,
    MarketplaceOperationNotFoundError,
    WorkflowMarketplaceOperationRegistry,
    validate_marketplace_operation_result,
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


def _result(value: str = "ok") -> MarketplaceTrustGrantOperationResult:
    return MarketplaceTrustGrantOperationResult(
        type="trust_grant",
        value=MarketplaceTrustStatesValue(
            workflows=[MarketplaceTrustState(workflow_name=value, state="trusted")]
        ),
    )


def _refresh_result(source_name: str) -> MarketplaceSourceRefreshOperationResult:
    return MarketplaceSourceRefreshOperationResult(
        type="source_refresh",
        value=MarketplaceSourceRefreshValue(
            source_name=source_name,
            repository_url="https://example.test/workflows.git",
            state="fresh",
            resolved_commit="c" * 40,
            verified_at="2026-09-04T00:00:00Z",
            package_count=1,
        ),
    )


@pytest.mark.parametrize(
    "method", ["get_legacy", "list_legacy", "cancel_legacy", "cancel_lifecycle"]
)
def test_explicit_observation_version_uses_actor_and_birth_version(tmp_path, method):
    from plugins.workflow.marketplace.lifecycle_models import AllPackagesSubject

    registry = WorkflowMarketplaceOperationRegistry(
        profile_key=str(tmp_path), profile="support"
    )
    entered, release = threading.Event(), threading.Event()
    operation = registry.start(
        "update_check",
        lambda token: (entered.set(), release.wait(10))[-1],
        actor="owner",
        subject=AllPackagesSubject(type="all_packages"),
        request_id=registry.admissions.new_request_id(),
    )
    assert entered.wait(5)
    try:
        adapter = getattr(registry, method, None)
        assert callable(adapter), "explicit versioned observation is missing"
        if method == "list_legacy":
            assert adapter(actor="owner", offset=0, limit=1) == ()
        else:
            with pytest.raises(MarketplaceOperationNotFoundError):
                adapter(operation.id, actor="foreign")
            if method == "cancel_lifecycle":
                assert adapter(operation.id, actor="owner").schema_version == 2
            else:
                with pytest.raises(MarketplaceOperationRegistryError) as caught:
                    adapter(operation.id, actor="owner")
                assert caught.value.code == "marketplace_lifecycle_upgrade_required"
                assert not registry._records[operation.id].cancellation.is_cancelled()
    finally:
        release.set()
        registry.close()


@pytest.mark.parametrize(
    "kind",
    [
        "install_confirm",
        "update_confirm",
        "remove_confirm",
        "trust_confirm",
        "trust_revoke",
        "inspect",
        "update_prepare",
    ],
)
def test_busy_identity_is_profile_wide_without_actor_projection(tmp_path, kind):
    from plugins.workflow.marketplace.lifecycle_models import (
        PackageIdentity,
        PackageSubject,
        AllTrustSelection,
    )
    from plugins.workflow.marketplace.models import InstalledPackageIdentity

    registry = WorkflowMarketplaceOperationRegistry(
        profile_key=str(tmp_path), profile="support", max_workers=1
    )
    entered, release = threading.Event(), threading.Event()
    subject = PackageSubject(
        type="package",
        identity=PackageIdentity(source_key="company", package_id="support"),
    )
    identity = InstalledPackageIdentity(sourceKey="company", packageId="support")
    blocker = registry.start(
        "refresh",
        lambda _: (entered.set(), release.wait(5), _refresh_result("other"))[-1],
        target="source:other",
    )
    assert entered.wait(5)
    second_entered, second_release = threading.Event(), threading.Event()
    try:
        operation = registry.start(
            kind,
            lambda _: (second_entered.set(), second_release.wait(5), None)[-1],
            actor="foreign",
            subject=subject,
            request_id=registry.admissions.new_request_id(),
            selection=AllTrustSelection(type="all")
            if kind.startswith("trust_")
            else None,
        )
        assert operation.state == "pending"
        busy = getattr(registry, "intersects_active_mutation", None)
        assert callable(busy), "profile-wide exact mutation query is missing"
        expected = kind.endswith("confirm") or kind == "trust_revoke"
        assert busy(identity) is expected
        assert (
            busy(InstalledPackageIdentity(sourceKey="other", packageId="support"))
            is False
        )
        assert (
            busy(InstalledPackageIdentity(sourceKey="company", packageId="different"))
            is False
        )
        assert registry.list_snapshot(actor="caller").items == ()
        release.set()
        assert second_entered.wait(5)
        assert registry.get_lifecycle(operation.id, actor="foreign").state == "running"
        assert busy(identity) is expected
        second_release.set()
        _wait_terminal(registry, operation.id, actor="foreign")
        assert busy(identity) is False
    finally:
        release.set()
        second_release.set()
        registry.close()


@pytest.mark.parametrize(
    "outcome_type", ["committed", "outcome_unknown", "recovery_required"]
)
def test_typed_terminal_evidence_wins_over_late_registry_cancellation(
    tmp_path, outcome_type
):
    from plugins.workflow.marketplace import lifecycle_models as wire
    from plugins.workflow.marketplace.operations import LifecycleCompletion

    registry = WorkflowMarketplaceOperationRegistry(
        profile_key=str(tmp_path), profile="support"
    )
    returned, release = threading.Event(), threading.Event()
    result = wire.SourceRefreshResult.model_validate(
        _refresh_result("company").model_dump(mode="json")
    )
    outcome = {
        "committed": wire.CommittedOutcome(type="committed", package_state=None),
        "outcome_unknown": wire.OutcomeUnknown(
            type="outcome_unknown", reason="terminal_invalid"
        ),
        "recovery_required": wire.RecoveryRequiredOutcome(
            type="recovery_required", reason="state_unverified"
        ),
    }[outcome_type]

    def worker(token):
        completion = LifecycleCompletion(
            state="succeeded" if outcome_type == "committed" else "failed",
            result=result if outcome_type == "committed" else None,
            error=None
            if outcome_type == "committed"
            else wire.LifecyclePublicError(code="source_cancelled"),
            outcome=outcome,
        )
        returned.set()
        assert release.wait(5)
        return completion

    try:
        operation = registry.start(
            "refresh",
            worker,
            actor="operator-a",
            request_id=registry.admissions.new_request_id(),
            subject=wire.SourceSubject(type="source", source_name="company"),
        )
        assert returned.wait(5)
        registry.cancel(operation.id, actor="operator-a")
        release.set()
        terminal = _wait_terminal(registry, operation.id)
        assert terminal.state == (
            "succeeded" if outcome_type == "committed" else "failed"
        )
        assert terminal.outcome == outcome
    finally:
        release.set()
        registry.close()


@pytest.mark.parametrize(
    "invalid", ["missing", "wrong_source", "untyped", "failed_with_result"]
)
@pytest.mark.parametrize("cancel", [False, True])
def test_legacy_bridge_never_downgrades_invalid_completion_to_success(
    tmp_path, invalid, cancel
):
    from plugins.workflow.marketplace import lifecycle_models as wire
    from plugins.workflow.marketplace.operations import (
        LifecycleCompletion,
        LegacyLifecycleCompletion,
    )

    registry = WorkflowMarketplaceOperationRegistry(
        profile_key=str(tmp_path), profile="support"
    )
    value = _refresh_result("company").model_dump(mode="json")
    value["value"].update(
        state="disabled", resolved_commit=None, verified_at=None, package_count=0
    )
    result = wire.SourceRefreshResult.model_validate(value)
    completion = LifecycleCompletion(
        state="succeeded",
        result=result,
        error=None,
        outcome=wire.KnownUnchangedOutcome(
            type="known_unchanged", evidence="before_mutation", package_state=None
        ),
    )
    legacy = {
        "missing": None,
        "wrong_source": _refresh_result("other"),
        "untyped": object(),
        "failed_with_result": _refresh_result("company"),
    }[invalid]
    if invalid == "failed_with_result":
        completion = LifecycleCompletion(
            state="failed",
            result=None,
            error=wire.LifecyclePublicError(code="source_unavailable"),
            outcome=wire.OutcomeUnknown(
                type="outcome_unknown", reason="terminal_invalid"
            ),
        )
    entered, release = threading.Event(), threading.Event()

    def worker(_token):
        entered.set()
        assert release.wait(5)
        return LegacyLifecycleCompletion(completion, legacy)

    try:
        operation = registry.start(
            "refresh",
            worker,
            actor="operator-a",
            target="source:company",
            subject=wire.SourceSubject(type="source", source_name="company"),
        )
        assert entered.wait(5)
        if cancel:
            registry.cancel(operation.id, actor="operator-a")
        release.set()
        # The V2 observation must also fail, even if V1 serialization is broken.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            terminal = registry.get_lifecycle(operation.id, actor="operator-a")
            if terminal.state in {"failed", "succeeded"}:
                break
            time.sleep(0.01)
        assert terminal.state == "failed"
        assert terminal.outcome.type == "outcome_unknown"
        legacy_terminal = registry.get(operation.id, actor="operator-a")
        assert legacy_terminal.state == "failed" and legacy_terminal.result is None
    finally:
        release.set()
        registry.close()


@pytest.mark.parametrize(
    "evidence,atomic,expected",
    [
        ("before_mutation", False, "cancelled"),
        ("read_only", False, "cancelled"),
        ("before_mutation", True, "failed"),
        ("rollback_verified", False, "failed"),
    ],
)
def test_late_cancel_requires_validated_pre_mutation_evidence(
    tmp_path, evidence, atomic, expected
):
    from plugins.workflow.marketplace import lifecycle_models as wire
    from plugins.workflow.marketplace.operations import LifecycleCompletion

    registry = WorkflowMarketplaceOperationRegistry(
        profile_key=str(tmp_path), profile="support"
    )
    subject = wire.PackageSubject(
        type="package",
        identity=wire.PackageIdentity(source_key="company", package_id="support"),
    )
    package_state = wire.PackageState(
        profile="support",
        identity=subject.identity,
        observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        state="absent",
        installed=None,
        trust=None,
        recovery="clear",
        busy=False,
    )
    outcome = wire.KnownUnchangedOutcome(
        type="known_unchanged",
        evidence=evidence,
        package_state=package_state if evidence == "rollback_verified" else None,
    )
    entered, release = threading.Event(), threading.Event()

    def worker(token):
        if atomic:
            token.begin_atomic()
        entered.set()
        assert release.wait(5)
        return LifecycleCompletion(
            state="failed",
            result=None,
            error=wire.LifecyclePublicError(code="marketplace_operation_failed"),
            outcome=outcome,
        )

    try:
        operation = registry.start(
            "remove_confirm",
            worker,
            actor="operator-a",
            subject=subject,
            request_id=registry.admissions.new_request_id(),
        )
        assert entered.wait(5)
        registry.cancel(operation.id, actor="operator-a")
        release.set()
        terminal = _wait_terminal(registry, operation.id)
        assert terminal.state == expected
        assert terminal.outcome.type == (
            "cancelled_before_commit" if expected == "cancelled" else "known_unchanged"
        )
    finally:
        release.set()
        registry.close()


def test_legacy_verifying_phase_has_a_strict_v2_projection(tmp_path):
    from plugins.workflow.marketplace.lifecycle_models import SourceSubject

    registry = WorkflowMarketplaceOperationRegistry(
        profile_key=str(tmp_path), profile="support"
    )
    verifying, release = threading.Event(), threading.Event()

    def worker(token):
        token.set_progress("verifying", 90)
        verifying.set()
        assert release.wait(5)
        token.checkpoint()

    try:
        operation = registry.start(
            "refresh",
            worker,
            actor="operator-a",
            target="source:company",
            subject=SourceSubject(type="source", source_name="company"),
        )
        assert verifying.wait(5)
        assert registry.get(operation.id, actor="operator-a").phase == "verifying"
        lifecycle = registry.get_lifecycle(operation.id, actor="operator-a")
        assert lifecycle.phase == "fetching" and lifecycle.progress == 90
        assert registry.list_snapshot(actor="operator-a").items == (lifecycle,)
        registry.cancel(operation.id, actor="operator-a")
        release.set()
        assert _wait_terminal(registry, operation.id).state == "cancelled"
    finally:
        release.set()
        registry.close()


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
        MarketplaceTrustGrantOperationResult.model_validate({
            "type": "trust_grant",
            "value": {"workflows": []},
            "unexpected": True,
        })
    with pytest.raises(Exception):
        MarketplaceTrustGrantOperationResult.model_validate({
            "type": "wrong",
            "value": {"workflows": []},
        })
    with pytest.raises(Exception):
        MarketplaceTrustGrantOperationResult.model_validate({
            "type": "trust_grant",
            "value": {
                "workflows": [
                    {"workflow_name": "x" * (2 * 1024 * 1024), "state": "trusted"}
                ]
            },
        })


@pytest.mark.parametrize(
    "candidate",
    [
        {"type": "unknown", "value": {}},
        {
            "type": "trust_revoke",
            "value": {"revoked": 1, "accessToken": "must-not-cross"},
        },
        {
            "type": "trust_revoke",
            "value": {"workflows": []},
        },
        {
            "type": "trust_revoke",
            "value": {"revoked": 1, "confirmationToken": "A" * 40},
        },
        {"type": "trust_revoke", "value": {"revoked": float("nan")}},
    ],
)
def test_operation_result_union_is_closed_and_discriminator_exact(candidate) -> None:
    with pytest.raises(Exception):
        validate_marketplace_operation_result(candidate)


def test_terminal_result_revalidation_detaches_producer_aliases(registry) -> None:
    producer_workflows = [
        MarketplaceTrustState(workflow_name="diagnostic", state="trusted")
    ]
    unsafe_value = MarketplaceTrustStatesValue.model_construct(
        workflows=producer_workflows
    )
    unsafe_result = MarketplaceTrustGrantOperationResult.model_construct(
        type="trust_grant", value=unsafe_value
    )
    started = registry.start(
        "trust_confirm", lambda _token: unsafe_result, actor="operator-a"
    )
    terminal = _wait_terminal(registry, started.id)

    producer_workflows.clear()

    assert terminal.state == "succeeded"
    assert terminal.result is not None
    assert len(terminal.result.value.workflows) == 1
    current = registry.get(started.id, actor="operator-a")
    assert current.result is not None
    assert len(current.result.value.workflows) == 1


def test_operation_success_is_an_immutable_profile_scoped_projection(registry) -> None:
    started = registry.start(
        "refresh",
        lambda cancellation: _refresh_result("company"),
        actor="operator-a",
        target="source:company",
    )

    terminal = _wait_terminal(registry, started.id)

    assert started.profile == "support"
    assert started.source_name == "company"
    assert started.id.startswith("wmop_")
    assert terminal.state == "succeeded"
    assert terminal.phase == "completed"
    assert terminal.progress == 100
    assert terminal.result == _refresh_result("company")
    assert terminal.error is None
    with pytest.raises(Exception):
        terminal.state = "failed"
    assert terminal.result is not None
    with pytest.raises(Exception):
        terminal.result.value.source_name = "other"
    assert registry.get(started.id, actor="operator-a").result == _refresh_result(
        "company"
    )
    assert registry.list(actor="operator-a")[0].source_name == "company"
    assert "target" not in started.model_dump(mode="json", by_alias=False)


def test_succeeded_refresh_rejects_a_result_for_a_different_source(registry) -> None:
    started = registry.start(
        "refresh",
        lambda _cancellation: _refresh_result("other"),
        actor="operator-a",
        target="source:company",
    )

    terminal = _wait_terminal(registry, started.id)

    assert terminal.source_name == "company"
    assert terminal.state == "failed"
    assert terminal.result is None


def test_registry_rejects_a_mismatched_result_before_publication_and_releases_target(
    registry,
) -> None:
    started = registry.start(
        "update_confirm",
        lambda _cancellation: _result(),
        actor="operator-a",
        target="package:company/support",
    )

    terminal = _wait_terminal(registry, started.id)

    assert terminal.state == "failed"
    assert terminal.result is None
    assert terminal.error is not None
    replacement = registry.start(
        "update_confirm",
        lambda _cancellation: _result(),
        actor="operator-a",
        target="package:company/support",
    )
    assert replacement.id != started.id


def test_registry_rejects_an_unknown_kind_before_reserving_a_target(registry) -> None:
    with pytest.raises(ValueError, match="operation kind"):
        registry.start(
            "future_kind",
            lambda _cancellation: _result(),
            actor="operator-a",
            target="package:company/support",
        )

    admitted = registry.start(
        "trust_confirm",
        lambda _cancellation: _result(),
        actor="operator-a",
        target="package:company/support",
    )
    assert _wait_terminal(registry, admitted.id).state == "succeeded"


def test_registry_rejects_a_running_phase_not_allowed_for_the_kind(registry) -> None:
    def invalid_refresh(cancellation):
        cancellation.set_progress("committing", 50)
        return _refresh_result("company")

    started = registry.start(
        "refresh",
        invalid_refresh,
        actor="operator-a",
        target="source:company",
    )

    terminal = _wait_terminal(registry, started.id)

    assert terminal.state == "failed"
    assert terminal.result is None
    assert terminal.error is not None


def test_v1_operation_wire_aliases_remain_unchanged(registry) -> None:
    started = registry.start(
        "refresh",
        lambda _cancellation: _refresh_result("company"),
        actor="operator-a",
        target="source:company",
    )

    public = started.model_dump(mode="json", by_alias=True)

    assert public == {
        "schemaVersion": 1,
        "id": started.id,
        "kind": "refresh",
        "source_name": "company",
        "profile": "support",
        "state": "pending",
        "phase": "queued",
        "progress": 0,
        "createdAt": started.created_at,
        "startedAt": None,
        "updatedAt": started.updated_at,
        "finishedAt": None,
        "result": None,
        "error": None,
    }


def test_operation_source_identity_is_refresh_only_and_target_derived(registry) -> None:
    package = registry.start(
        "install_confirm",
        lambda _cancellation: _result(),
        actor="operator-a",
        target="package:company/support",
    )

    assert package.source_name is None

    for target in (
        None,
        "company",
        "source:",
        "source:Company",
        "source:company/extra",
    ):
        with pytest.raises(ValueError, match="refresh operation target"):
            registry.start(
                "refresh",
                lambda _cancellation: _result(),
                actor="operator-a",
                target=target,
            )


def test_cancelled_fetch_never_reports_a_result(registry) -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocking_fetch(cancellation):
        entered.set()
        release.wait(timeout=5)
        return _refresh_result("company")

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
        "trust_confirm",
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
        "trust_confirm",
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
                _refresh_result("first"),
            )[-1],
            actor="operator-a",
            target="source:first",
        )
        assert blocker_entered.wait(timeout=2)
        queued = registry.start(
            "refresh",
            lambda cancellation: (queued_ran.set(), _refresh_result("second"))[1],
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
            lambda cancellation: (
                entered.set(),
                release.wait(timeout=5),
                _refresh_result("first"),
            )[-1],
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
            "trust_confirm",
            lambda cancellation: (entered.set(), release.wait(timeout=5), _result())[
                -1
            ],
            actor="operator-a",
            target="package:company/support",
        )
        assert entered.wait(timeout=2)

        with pytest.raises(MarketplaceOperationConflictError) as caught:
            registry.start(
                "trust_confirm",
                lambda cancellation: _result(),
                actor="operator-b",
                target="package:company/support",
            )
        unrelated = registry.start(
            "trust_confirm",
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
        lambda cancellation: _refresh_result("company"),
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
            lambda cancellation: _refresh_result("first"),
            actor="operator-a",
            target="source:first",
        )
        assert _wait_terminal(registry, first.id).state == "succeeded"
        now[0] += timedelta(seconds=1)
        active = registry.start(
            "refresh",
            lambda cancellation: (
                entered.set(),
                release.wait(timeout=5),
                _refresh_result("active"),
            )[-1],
            actor="operator-a",
            target="source:active",
        )
        assert entered.wait(timeout=2)
        now[0] += timedelta(seconds=1)
        second = registry.start(
            "refresh",
            lambda cancellation: _refresh_result("second"),
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
            lambda cancellation: _refresh_result("company"),
            actor="operator-a",
            target="source:company",
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
        lambda cancellation: (
            entered.set(),
            release.wait(timeout=5),
            _refresh_result("company"),
        )[-1],
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


def test_idle_registry_can_be_retired_but_active_registry_is_preserved() -> None:
    registry = WorkflowMarketplaceOperationRegistry(
        profile_key="/profiles/support",
        profile="support",
        max_workers=1,
        max_in_flight=1,
    )
    entered = threading.Event()
    release = threading.Event()
    try:
        started = registry.start(
            "refresh",
            lambda _token: (
                entered.set(),
                release.wait(timeout=5),
                _refresh_result("company"),
            )[-1],
            actor="operator-a",
            target="source:company",
        )
        assert entered.wait(timeout=2)
        assert registry.retire_if_idle() is False
        release.set()
        assert _wait_terminal(registry, started.id).state == "succeeded"
        assert registry.retire_if_idle() is True
        registry.close_retired()
        with pytest.raises(MarketplaceOperationRegistryError) as closed:
            registry.start(
                "refresh",
                lambda _token: _refresh_result("company"),
                target="source:company",
            )
        assert closed.value.code == "marketplace_operation_unavailable"
    finally:
        release.set()
        registry.close_retired()


def test_legacy_read_receipt_prevents_retiring_its_profile(registry) -> None:
    from plugins.workflow.marketplace.lifecycle_models import SourceSubject

    try:
        operation = registry.start(
            "refresh",
            lambda _: _refresh_result("company"),
            actor="operator-a",
            target="source:company",
            subject=SourceSubject(type="source", source_name="company"),
            canonical_body={"source_name": "company"},
        )
    except TypeError as error:
        pytest.fail(f"read admission metadata is not supported: {error}")
    assert _wait_terminal(registry, operation.id).state == "failed"
    assert registry.retire_if_idle() is False
    terminal = registry.get_lifecycle(operation.id, actor="operator-a")
    assert terminal.outcome.type == "outcome_unknown"
    assert registry.list_snapshot(actor="operator-a").items == (terminal,)
