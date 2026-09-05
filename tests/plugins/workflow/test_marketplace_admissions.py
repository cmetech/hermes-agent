"""Behavior checks for process-local admission and observation authority."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import itertools
import threading

import pytest

from plugins.workflow.marketplace import operations
from plugins.workflow.marketplace.lifecycle_models import (
    AllPackagesSubject,
    KnownUnchangedOutcome,
    LifecyclePublicError,
    PackageIdentity,
    PackageSubject,
    RemoveReviewProjection,
    RemoveReviewResult,
    UpdateChecksResult,
    UpdateChecksValue,
)


EPOCH = "a" * 32
NOW = datetime(2026, 9, 4, tzinfo=timezone.utc)
SUBJECT = PackageSubject(
    type="package", identity=PackageIdentity(source_key="company", package_id="support")
)


def test_direct_selector_is_epoch_keyed_and_binds_each_canonical_component(tmp_path):
    from plugins.workflow.marketplace.admissions import LifecycleAdmissionStore

    store = LifecycleAdmissionStore(profile_key=str(tmp_path), epoch=EPOCH)
    recreated = LifecycleAdmissionStore(profile_key=str(tmp_path), epoch=EPOCH)
    selector = store.direct_selector_id(
        "https://example.test/a.git", "main", "packages/a"
    )
    assert selector == recreated.direct_selector_id(
        "https://example.test/a.git", "main", "packages/a"
    )
    assert len(selector) == 64 and all(
        character in "0123456789abcdef" for character in selector
    )
    for repository, ref, path in [
        ("https://example.test/b.git", "main", "packages/a"),
        ("https://example.test/a.git", None, "packages/a"),
        ("https://example.test/a.git", "main", "packages/b"),
        ("https://example.test/a.git", "main", None),
    ]:
        assert store.direct_selector_id(repository, ref, path) != selector
    different_epoch = LifecycleAdmissionStore(profile_key=str(tmp_path), epoch="b" * 32)
    assert (
        different_epoch.direct_selector_id(
            "https://example.test/a.git", "main", "packages/a"
        )
        != selector
    )


def request_id(number=1, *, now=NOW, epoch=EPOCH):
    return f"wmreq_{epoch}_{int(now.timestamp() * 1000):013d}_{number:032x}"


class AdmittedConfirm:
    def __init__(self, tmp_path, **limits):
        from plugins.workflow.marketplace.admissions import LifecycleAdmissionStore

        self.now = NOW
        self.counter = itertools.count(1)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.worker_invocations = 0
        self.validations = 0
        self.consumed = False
        self.store = LifecycleAdmissionStore(
            profile_key=str(tmp_path),
            epoch=EPOCH,
            clock=lambda: self.now,
            random_hex=lambda: f"{next(self.counter):032x}",
        )
        self.registry = operations.WorkflowMarketplaceOperationRegistry(
            profile_key=str(tmp_path),
            profile="support",
            admissions=self.store,
            clock=lambda: self.now,
            max_workers=limits.pop("max_workers", 1),
            max_in_flight=4,
            max_terminal=1,
            **limits,
        )

    def validate(self):
        self.validations += 1
        if self.consumed:
            raise ValueError("consumed confirmation")

    def worker(self, _cancellation):
        self.worker_invocations += 1
        self.consumed = True
        self.entered.set()
        assert self.release.wait(5)
        # This controlled worker explicitly fails before mutation. It supplies
        # evidence, rather than asking the registry to infer it from an error.
        return operations.LifecycleCompletion(
            state="failed",
            result=None,
            error=LifecyclePublicError(code="confirmation_token_invalid"),
            outcome=KnownUnchangedOutcome(
                type="known_unchanged", evidence="before_mutation", package_state=None
            ),
        )

    def start(self, **changes):
        arguments = dict(
            actor="alice",
            target="private:never-public",
            request_id=request_id(),
            canonical_body={"confirmation_token": "private-token"},
            subject=SUBJECT,
            selection=None,
            authorize=self.validate,
        )
        arguments.update(changes)
        return self.registry.start("remove_confirm", self.worker, **arguments)

    replay = start

    def finish(self, operation):
        self.release.set()
        with self.registry._lock:
            future = self.registry._records[operation.id].future
        future.result(timeout=5)

    def finish_and_evict_result(self):
        operation = self.registry.lookup_admission(
            request_id(), actor="alice"
        ).operation
        self.finish(operation)
        self.now += timedelta(hours=2)
        self.registry.list(actor="alice")

    def close(self):
        self.release.set()
        self.registry.close()


@pytest.fixture
def admitted_confirm(tmp_path):
    fixture = AdmittedConfirm(tmp_path)
    yield fixture
    fixture.close()


def test_concurrent_exact_replays_share_one_worker_and_skip_consumed_authority(
    admitted_confirm,
):
    fixture = admitted_confirm
    with ThreadPoolExecutor(max_workers=8) as callers:
        starts = list(callers.map(lambda _: fixture.start(), range(16)))
    assert fixture.entered.wait(5)
    assert len({operation.id for operation in starts}) == 1
    assert fixture.worker_invocations == fixture.validations == 1
    fixture.finish(starts[0])
    assert fixture.replay().id == starts[0].id
    assert fixture.validations == 1


def test_replay_after_terminal_eviction_never_enqueues_again(admitted_confirm):
    first = admitted_confirm.start()
    admitted_confirm.finish_and_evict_result()
    replay = admitted_confirm.replay()
    assert replay.state == "evicted"
    assert replay.operation_id == first.id
    assert admitted_confirm.worker_invocations == 1


@pytest.mark.parametrize(
    "change",
    [
        {"canonical_body": {"confirmation_token": "different-secret"}},
        {
            "subject": PackageSubject(
                type="package",
                identity=PackageIdentity(source_key="other", package_id="support"),
            )
        },
        {"selection": {"type": "all"}},
    ],
)
def test_changed_replay_conflicts_without_disclosing_original(admitted_confirm, change):
    fixture = admitted_confirm
    first = fixture.start()
    with pytest.raises(Exception) as error:
        fixture.replay(**change)
    assert error.value.code == "marketplace_request_conflict"
    assert "private-token" not in str(error.value)
    assert (
        fixture.registry.lookup_admission(request_id(), actor="alice").operation.id
        == first.id
    )


def test_changed_kind_conflicts_before_authorization(admitted_confirm):
    fixture = admitted_confirm
    fixture.start()
    with pytest.raises(Exception) as error:
        fixture.registry.start(
            "install_confirm",
            fixture.worker,
            actor="alice",
            request_id=request_id(),
            subject=SUBJECT,
            canonical_body={"confirmation_token": "private-token"},
        )
    assert error.value.code == "marketplace_request_conflict"
    assert fixture.validations == 1


def test_canonical_json_key_order_replays_but_string_content_does_not(admitted_confirm):
    fixture = admitted_confirm
    first = fixture.start(canonical_body={"a": " one ", "b": 2})
    assert fixture.replay(canonical_body={"b": 2, "a": " one "}).id == first.id
    with pytest.raises(Exception) as error:
        fixture.replay(canonical_body={"a": "one", "b": 2})
    assert error.value.code == "marketplace_request_conflict"


def test_expired_receipt_cannot_readmit_after_pruning(admitted_confirm):
    fixture = admitted_confirm
    first = fixture.start()
    fixture.finish(first)
    fixture.now += timedelta(hours=25)
    assert fixture.registry.retire_if_idle()
    with pytest.raises(Exception) as error:
        fixture.replay()
    assert error.value.code == "marketplace_request_expired"
    assert fixture.worker_invocations == 1


@pytest.mark.parametrize(
    "now,epoch,code",
    [
        (
            NOW - timedelta(minutes=5, milliseconds=1),
            EPOCH,
            "marketplace_request_expired",
        ),
        (
            NOW + timedelta(seconds=30, milliseconds=1),
            EPOCH,
            "marketplace_request_expired",
        ),
        (NOW, "b" * 32, "marketplace_epoch_changed"),
    ],
)
def test_new_request_window_and_epoch_reject_without_worker(
    admitted_confirm, now, epoch, code
):
    fixture = admitted_confirm
    with pytest.raises(Exception) as error:
        fixture.start(request_id=request_id(now=now, epoch=epoch))
    assert error.value.code == code
    assert fixture.worker_invocations == fixture.validations == 0


def test_active_receipt_survives_24_hours_and_blocks_profile_retirement(
    admitted_confirm,
):
    fixture = admitted_confirm
    first = fixture.start()
    fixture.now += timedelta(hours=25)
    assert fixture.registry.retire_if_idle() is False
    assert fixture.replay().id == first.id
    fixture.finish(first)
    assert fixture.registry.retire_if_idle() is True


def test_actor_and_canonical_profile_are_receipt_boundaries(admitted_confirm, tmp_path):
    fixture = admitted_confirm
    first = fixture.start()
    for actor in ("bob", "unknown"):
        with pytest.raises(Exception) as error:
            fixture.registry.lookup_admission(request_id(), actor=actor)
        assert error.value.code == "marketplace_admission_not_found"
    other = AdmittedConfirm(tmp_path / "other")
    try:
        second = other.start()
        assert second.id != first.id
        assert other.registry.profile == fixture.registry.profile
    finally:
        other.close()


def test_receipt_capacity_is_shared_across_actors_and_never_evicts(admitted_confirm):
    store = admitted_confirm.store
    for number in range(4096):
        reservation = store.reserve(
            request_id(number),
            "alice" if number % 2 else "bob",
            store.profile_key,
            "update_check",
            {},
            AllPackagesSubject(type="all_packages"),
            None,
        )
        store.finish(reservation.receipt)
    with pytest.raises(Exception) as error:
        store.reserve(
            request_id(4096),
            "new",
            store.profile_key,
            "update_check",
            {},
            AllPackagesSubject(type="all_packages"),
            None,
        )
    assert error.value.code == "marketplace_admission_capacity"
    assert (
        store.reserve(
            request_id(0),
            "bob",
            store.profile_key,
            "update_check",
            {},
            AllPackagesSubject(type="all_packages"),
            None,
        ).state
        == "found"
    )


def test_enqueue_failure_keeps_known_unchanged_receipt(admitted_confirm, monkeypatch):
    fixture = admitted_confirm

    def reject(*_args):
        raise RuntimeError("private executor failure")

    monkeypatch.setattr(fixture.registry._executor, "submit", reject)
    operation = fixture.start()
    assert operation.state == "failed"
    assert operation.outcome.evidence == "before_mutation"
    assert fixture.replay().id == operation.id
    assert fixture.worker_invocations == 0
    assert "private executor failure" not in operation.model_dump_json()


def start_check(fixture, number, *, actor="alice"):
    operation = fixture.registry.start(
        "update_check",
        lambda _: operations.LifecycleCompletion(
            state="succeeded",
            result=UpdateChecksResult(
                type="update_checks", value=UpdateChecksValue(checks=[])
            ),
            error=None,
            outcome=KnownUnchangedOutcome(
                type="known_unchanged", evidence="read_only", package_state=None
            ),
        ),
        actor=actor,
        request_id=request_id(number, now=fixture.now),
        subject=AllPackagesSubject(type="all_packages"),
        canonical_body={},
    )
    fixture.finish(operation)
    return operation


def test_snapshot_is_immutable_across_eviction_and_new_operations(admitted_confirm):
    fixture = admitted_confirm
    fixture.registry.max_terminal = 4
    first = start_check(fixture, 10)
    second = start_check(fixture, 11)
    page = fixture.registry.list_snapshot(actor="alice", limit=1)
    assert page.items[0].id == second.id
    fixture.registry.max_terminal = 1
    start_check(fixture, 12)
    next_page = fixture.registry.list_snapshot(
        actor="alice", cursor=page.next_cursor, limit=1
    )
    assert [item.id for item in next_page.items] == [first.id]
    assert next_page.complete and next_page.next_cursor is None
    assert "private" not in next_page.model_dump_json()


def test_snapshot_cursor_is_actor_scoped_expiring_and_capacity_is_explicit(
    admitted_confirm,
):
    fixture = admitted_confirm
    fixture.registry.max_terminal = 4
    start_check(fixture, 10)
    start_check(fixture, 11)
    pages = [fixture.registry.list_snapshot(actor="alice", limit=1) for _ in range(4)]
    with pytest.raises(Exception) as full:
        fixture.registry.list_snapshot(actor="bob", limit=1)
    assert full.value.code == "marketplace_list_capacity"
    with pytest.raises(Exception) as foreign:
        fixture.registry.list_snapshot(actor="bob", cursor=pages[0].next_cursor)
    assert foreign.value.code == "marketplace_list_expired"
    fixture.now += timedelta(seconds=30)
    with pytest.raises(Exception) as expired:
        fixture.registry.list_snapshot(actor="alice", cursor=pages[0].next_cursor)
    assert expired.value.code == "marketplace_list_expired"
    assert fixture.registry.list_snapshot(actor="alice").complete


@pytest.mark.parametrize("unavailable_reason", ["consumed", "expired", "evicted"])
def test_vault_requires_exact_binding_and_never_restores_consumed_or_expired_authority(
    admitted_confirm,
    unavailable_reason,
):
    fixture = admitted_confirm
    expiry = "2026-09-04T00:05:00Z"
    digest = "d" * 64
    inspections = []

    def validate_unused(token):
        observed = threading.Event()

        def observe():
            fixture.registry.list(actor="alice")
            observed.set()

        thread = threading.Thread(target=observe)
        thread.start()
        completed = observed.wait(5)
        thread.join(timeout=5)
        inspections.append(completed)
        return completed and token == "private-raw-token" and not fixture.consumed

    result = RemoveReviewResult(
        type="remove_review",
        value=RemoveReviewProjection(
            operation="remove",
            review_digest=digest,
            identity=SUBJECT.identity,
            current_version="1.0.0",
            current_commit="c" * 40,
            distribution_digest="e" * 64,
            workflow_names=["support"],
            confirmation_available=True,
            expires_at=expiry,
        ),
    )
    operation = fixture.registry.start(
        "remove_prepare",
        lambda _: operations.LifecycleCompletion(
            state="succeeded",
            result=result,
            error=None,
            outcome=KnownUnchangedOutcome(
                type="known_unchanged", evidence="read_only", package_state=None
            ),
            review_token=operations.ReviewTokenMetadata(
                confirmation_token="private-raw-token",
                expires_at=expiry,
                review_digest=digest,
                subject=SUBJECT,
                selection=None,
                validate_unused=validate_unused,
            ),
        ),
        actor="alice",
        request_id=request_id(),
        subject=SUBJECT,
        canonical_body={},
    )
    fixture.finish(operation)
    arguments = dict(
        actor="alice",
        registry_epoch=EPOCH,
        review_digest=digest,
        subject=SUBJECT,
        selection=None,
    )
    token = fixture.registry.review_token(operation.id, **arguments)
    assert token.confirmation_token == "private-raw-token"
    assert fixture.registry.review_token(operation.id, **arguments) == token
    assert (
        "private-raw-token"
        not in fixture.registry.get_lifecycle(
            operation.id, actor="alice"
        ).model_dump_json()
    )
    assert (
        "private-raw-token"
        not in fixture.registry.list_snapshot(actor="alice").model_dump_json()
    )
    for changes in (
        {"actor": "bob"},
        {"review_digest": "f" * 64},
        {"registry_epoch": "b" * 32},
    ):
        with pytest.raises(Exception) as error:
            fixture.registry.review_token(operation.id, **(arguments | changes))
        assert error.value.code == "marketplace_review_unavailable"
    assert inspections and all(inspections)
    if unavailable_reason == "consumed":
        fixture.consumed = True
    elif unavailable_reason == "expired":
        fixture.now += timedelta(minutes=5)
    else:
        start_check(fixture, 2)
    with pytest.raises(Exception) as consumed:
        fixture.registry.review_token(operation.id, **arguments)
    assert consumed.value.code == "marketplace_review_unavailable"
    fixture.consumed = False
    with pytest.raises(Exception):
        fixture.registry.review_token(operation.id, **arguments)
    if unavailable_reason != "evicted":
        assert (
            fixture.registry.get_lifecycle(
                operation.id, actor="alice"
            ).result.value.expires_at
            == expiry
        )


def test_publication_detaches_typed_worker_result(admitted_confirm):
    fixture = admitted_confirm
    names = ["original"]
    result = RemoveReviewResult(
        type="remove_review",
        value=RemoveReviewProjection(
            operation="remove",
            review_digest="d" * 64,
            identity=SUBJECT.identity,
            current_version="1.0.0",
            current_commit="c" * 40,
            distribution_digest="e" * 64,
            workflow_names=names,
            confirmation_available=False,
            expires_at=None,
        ),
    )
    operation = fixture.registry.start(
        "remove_prepare",
        lambda _: operations.LifecycleCompletion(
            state="succeeded",
            result=result,
            error=None,
            outcome=KnownUnchangedOutcome(
                type="known_unchanged", evidence="read_only", package_state=None
            ),
        ),
        actor="alice",
        request_id=request_id(),
        subject=SUBJECT,
        canonical_body={},
    )
    fixture.finish(operation)
    result.value.workflow_names.append("producer-mutated")
    assert fixture.registry.get_lifecycle(
        operation.id, actor="alice"
    ).result.value.workflow_names == ["original"]


def test_authorization_allows_registry_progress_from_another_thread(admitted_confirm):
    fixture = admitted_confirm

    def authorize():
        completed = threading.Event()

        def observe():
            fixture.registry.list(actor="alice")
            completed.set()

        thread = threading.Thread(target=observe)
        thread.start()
        try:
            assert completed.wait(5), "authorization holds the registry lock"
        finally:
            thread.join(timeout=5)

    fixture.start(authorize=authorize)


def test_cancellation_checkpoint_before_terminal_publication_has_explicit_outcome(
    admitted_confirm,
):
    fixture = admitted_confirm
    operation = fixture.start()
    assert fixture.entered.wait(5)
    fixture.registry.cancel(operation.id, actor="alice")
    fixture.finish(operation)
    terminal = fixture.registry.get_lifecycle(operation.id, actor="alice")
    assert terminal.state == "cancelled"
    assert terminal.outcome.type == "cancelled_before_commit"


def test_v2_progress_rejects_legacy_only_phase(admitted_confirm):
    from plugins.workflow.marketplace.lifecycle_models import SourceSubject

    fixture = admitted_confirm
    operation = fixture.registry.start(
        "refresh",
        lambda cancellation: cancellation.set_progress("verifying", 90),
        actor="alice",
        request_id=request_id(),
        subject=SourceSubject(type="source", source_name="company"),
        canonical_body={},
    )
    fixture.finish(operation)
    terminal = fixture.registry.get_lifecycle(operation.id, actor="alice")
    assert terminal.state == "failed"
    assert terminal.progress < 90
    assert terminal.outcome.type == "outcome_unknown"


def test_profile_cache_keeps_receipts_until_expiry_and_epoch_survives_reconstruction(
    tmp_path,
):
    from fastapi import HTTPException
    from plugins.workflow.marketplace.api import WorkflowMarketplaceApiContext

    home = [tmp_path / "a"]
    now = [NOW]
    context = WorkflowMarketplaceApiContext(
        home_resolver=lambda: home[0],
        profile_resolver=lambda _: "same-display-name",
        service_factory=lambda *_: object(),
        max_profiles=1,
    )
    try:
        registry = context.registry_for_current_profile()
        registry.clock = lambda: now[0]
        registry.admissions.clock = lambda: now[0]
        epoch = registry.admissions.epoch
        operation = registry.start(
            "update_check",
            lambda _: operations.LifecycleCompletion(
                state="succeeded",
                result=UpdateChecksResult(
                    type="update_checks", value=UpdateChecksValue(checks=[])
                ),
                error=None,
                outcome=KnownUnchangedOutcome(
                    type="known_unchanged", evidence="read_only", package_state=None
                ),
            ),
            request_id=request_id(epoch=epoch),
            subject=AllPackagesSubject(type="all_packages"),
            canonical_body={},
        )
        registry._records[operation.id].future.result(timeout=5)
        home[0] = tmp_path / "b"
        with pytest.raises(HTTPException):
            context.current()
        home[0] = tmp_path / "a"
        assert (
            context
            .registry_for_current_profile()
            .lookup_admission(request_id(epoch=epoch))
            .operation.id
            == operation.id
        )
        now[0] += timedelta(hours=25)
        home[0] = tmp_path / "b"
        assert context.registry_for_current_profile().admissions.epoch == epoch
    finally:
        context.close()


def test_snapshot_rejects_aggregate_byte_capacity_without_truncation(admitted_confirm):
    fixture = admitted_confirm
    fixture.registry.max_terminal = 130
    large_review = RemoveReviewResult(
        type="remove_review",
        value=RemoveReviewProjection(
            operation="remove",
            review_digest="d" * 64,
            identity=SUBJECT.identity,
            current_version="1.0.0",
            current_commit="c" * 40,
            distribution_digest="e" * 64,
            workflow_names=[f"{index:03d}" + "x" * 252 for index in range(512)],
            confirmation_available=False,
            expires_at=None,
        ),
    )
    for number in range(130):
        operation = fixture.registry.start(
            "remove_prepare",
            lambda _: operations.LifecycleCompletion(
                state="succeeded",
                result=large_review,
                error=None,
                outcome=KnownUnchangedOutcome(
                    type="known_unchanged", evidence="read_only", package_state=None
                ),
            ),
            actor="alice",
            request_id=request_id(number),
            subject=SUBJECT,
            canonical_body={},
        )
        fixture.finish(operation)
    with pytest.raises(Exception) as full:
        fixture.registry.list_snapshot(actor="alice")
    assert full.value.code == "marketplace_list_capacity"
    fixture.registry.max_terminal = 2
    assert len(fixture.registry.list_snapshot(actor="alice").items) == 2


@pytest.mark.parametrize("age", [timedelta(minutes=5), -timedelta(seconds=30)])
def test_first_admission_accepts_exact_time_window_boundaries(admitted_confirm, age):
    assert admitted_confirm.start(request_id=request_id(now=NOW - age)).id.startswith(
        "wmop_"
    )


@pytest.mark.parametrize(
    "identifier", ["", False, 0, "WMREQ_" + "a" * 79, request_id() + "\n"]
)
def test_malformed_request_id_never_becomes_server_generated_admission(
    admitted_confirm, identifier
):
    with pytest.raises(ValueError):
        admitted_confirm.start(request_id=identifier, authorize=None)
    assert admitted_confirm.worker_invocations == 0


def test_snapshot_page_size_is_bounded_and_scan_does_not_skip_items(admitted_confirm):
    fixture = admitted_confirm
    fixture.registry.max_terminal = 128
    operations_started = [start_check(fixture, number) for number in range(103)]
    first = fixture.registry.list_snapshot(actor="alice")
    assert len(first.items) == 100 and not first.complete
    second = fixture.registry.list_snapshot(actor="alice", cursor=first.next_cursor)
    assert len(second.items) == 3 and second.complete
    assert [item.id for item in (*first.items, *second.items)] == [
        item.id for item in reversed(operations_started)
    ]
    for limit in (0, 101, True):
        with pytest.raises(ValueError):
            fixture.registry.list_snapshot(actor="alice", limit=limit)


@pytest.mark.parametrize(
    "body",
    [
        {"secret": float("nan")},
        {"secret": "x" * 65536},
        {1: "value"},
        {"value": (1, 2)},
    ],
)
def test_invalid_body_is_rejected_without_receipt_or_secret_error(
    admitted_confirm, body
):
    fixture = admitted_confirm
    with pytest.raises(ValueError) as invalid:
        fixture.start(canonical_body=body)
    assert "secret" not in str(invalid.value)
    assert fixture.worker_invocations == 0
    with pytest.raises(Exception) as missing:
        fixture.registry.lookup_admission(request_id(), actor="alice")
    assert missing.value.code == "marketplace_admission_not_found"


@pytest.mark.parametrize(
    "result,outcome",
    [("private-invalid-result", None), (None, "private-invalid-outcome")],
)
def test_invalid_typed_completion_is_terminal_unknown_and_releases_reservation(
    admitted_confirm, result, outcome
):
    fixture = admitted_confirm
    operation = fixture.registry.start(
        "remove_confirm",
        lambda _: operations.LifecycleCompletion(
            state="succeeded",
            result=result,
            error=None,
            outcome=outcome,
        ),
        actor="alice",
        target="private:target",
        request_id=request_id(),
        canonical_body={},
        subject=SUBJECT,
    )
    fixture.finish(operation)
    terminal = fixture.registry.get_lifecycle(operation.id, actor="alice")
    assert terminal.state == "failed"
    assert terminal.outcome.type == "outcome_unknown"
    assert "private" not in terminal.model_dump_json()
    replacement = fixture.start(request_id=request_id(2), target="private:target")
    assert replacement.id != operation.id


def _start_review_with_authority(
    fixture, validate_unused, *, confirmation_token="private-raw-token"
):
    expiry = "2026-09-04T00:05:00Z"
    digest = "d" * 64
    return fixture.registry.start(
        "remove_prepare",
        lambda _: operations.LifecycleCompletion(
            state="succeeded",
            result=RemoveReviewResult(
                type="remove_review",
                value=RemoveReviewProjection(
                    operation="remove",
                    review_digest=digest,
                    identity=SUBJECT.identity,
                    current_version="1.0.0",
                    current_commit="c" * 40,
                    distribution_digest="e" * 64,
                    workflow_names=["support"],
                    confirmation_available=True,
                    expires_at=expiry,
                ),
            ),
            error=None,
            outcome=KnownUnchangedOutcome(
                type="known_unchanged", evidence="read_only", package_state=None
            ),
            review_token=operations.ReviewTokenMetadata(
                confirmation_token=confirmation_token,
                expires_at=expiry,
                review_digest=digest,
                subject=SUBJECT,
                selection=None,
                validate_unused=validate_unused,
            ),
        ),
        actor="alice",
        request_id=request_id(),
        subject=SUBJECT,
        canonical_body={},
    )


@pytest.mark.parametrize("authority", ["false", "raises", "valid"])
def test_review_publication_validates_unused_authority_before_vaulting(
    admitted_confirm, authority
):
    fixture = admitted_confirm
    validations = []

    def validator(token):
        validations.append(token)
        if authority == "raises":
            error = RuntimeError("private-raw-token: private callback failure")
            error.code = "source_cancelled"
            raise error
        return authority == "valid"

    operation = _start_review_with_authority(fixture, validator)
    fixture.finish(operation)
    terminal = fixture.registry.get_lifecycle(operation.id, actor="alice")
    assert validations == ["private-raw-token"]
    assert "private" not in terminal.model_dump_json()
    arguments = dict(
        actor="alice",
        registry_epoch=EPOCH,
        review_digest="d" * 64,
        subject=SUBJECT,
        selection=None,
    )
    if authority == "valid":
        assert terminal.state == "succeeded"
        assert terminal.result.value.confirmation_available
        assert (
            fixture.registry.review_token(operation.id, **arguments).confirmation_token
            == "private-raw-token"
        )
        assert validations == ["private-raw-token", "private-raw-token"]
    else:
        assert terminal.state == "failed" and terminal.result is None
        assert terminal.outcome.type == "outcome_unknown"
        assert terminal.error.code == "marketplace_operation_failed"
        with pytest.raises(operations.MarketplaceOperationRegistryError) as unavailable:
            fixture.registry.review_token(operation.id, **arguments)
        assert unavailable.value.code == "marketplace_review_unavailable"
        assert validations == ["private-raw-token"]


@pytest.mark.parametrize("boundary", ["expiry", "cancellation"])
def test_review_publication_rechecks_expiry_and_cancellation_after_authority(
    admitted_confirm, boundary
):
    fixture = admitted_confirm
    validating, release_validator = threading.Event(), threading.Event()

    def validator(_token):
        validating.set()
        assert release_validator.wait(5)
        return True

    operation = _start_review_with_authority(fixture, validator)
    try:
        assert validating.wait(5)
        assert (
            fixture.registry.get_lifecycle(operation.id, actor="alice").state
            == "running"
        )
        if boundary == "expiry":
            fixture.now += timedelta(minutes=5)
        else:
            fixture.registry.cancel(operation.id, actor="alice")
        release_validator.set()
        fixture.finish(operation)
        terminal = fixture.registry.get_lifecycle(operation.id, actor="alice")
        if boundary == "expiry":
            assert terminal.state == "failed"
            assert terminal.outcome.type == "outcome_unknown"
        else:
            assert terminal.state == "cancelled"
            assert terminal.outcome.type == "cancelled_before_commit"
        assert terminal.result is None
        with pytest.raises(operations.MarketplaceOperationRegistryError):
            fixture.registry.review_token(
                operation.id,
                actor="alice",
                registry_epoch=EPOCH,
                review_digest="d" * 64,
                subject=SUBJECT,
                selection=None,
            )
    finally:
        release_validator.set()


def test_review_insertion_transaction_lock_allows_real_worker_progress_and_queued_cancellation(
    tmp_path,
):
    from plugins.workflow.marketplace.package import WorkflowMarketplaceError
    from plugins.workflow.marketplace.transactions import MarketplaceTransactionStore

    fixture = AdmittedConfirm(tmp_path / "profile", max_workers=2)
    fixture.registry.max_terminal = 4
    transactions = MarketplaceTransactionStore(
        tmp_path / "profile", clock=lambda: fixture.now, lock_timeout_seconds=3
    )
    held, inspecting, queued_ready = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    queued, observations, inspector_codes = [], [], []

    def holder(cancellation):
        with transactions._locked():
            held.set()
            assert queued_ready.wait(5)
            assert inspecting.wait(5)
            assert not inspector_codes
            cancellation.set_progress("validating", 50)
            observations.append(
                fixture.registry.cancel(queued[0].id, actor="alice").state
            )
        return operations.LifecycleCompletion(
            state="failed",
            result=None,
            error=LifecyclePublicError(code="confirmation_token_invalid"),
            outcome=KnownUnchangedOutcome(
                type="known_unchanged", evidence="before_mutation", package_state=None
            ),
        )

    def validator(token):
        inspecting.set()
        try:
            transactions.inspect_token(token, actor="alice", profile="support")
        except WorkflowMarketplaceError as error:
            inspector_codes.append(error.code)
        return False

    try:
        holder_operation = fixture.registry.start(
            "remove_confirm",
            holder,
            actor="alice",
            request_id=request_id(2),
            subject=SUBJECT,
            canonical_body={},
        )
        assert held.wait(5)
        preparation = _start_review_with_authority(
            fixture, validator, confirmation_token="p" * 43
        )
        assert inspecting.wait(5)
        queued.append(
            fixture.registry.start(
                "update_check",
                lambda _: None,
                actor="alice",
                request_id=request_id(3),
                subject=AllPackagesSubject(type="all_packages"),
                canonical_body={},
            )
        )
        queued_ready.set()
        fixture.finish(holder_operation)
        fixture.finish(preparation)
        assert observations == ["cancelled"]
        assert inspector_codes == ["confirmation_token_invalid"]
        assert (
            fixture.registry.get_lifecycle(preparation.id, actor="alice").state
            == "failed"
        )
    finally:
        queued_ready.set()
        inspecting.set()
        fixture.close()
