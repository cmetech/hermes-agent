"""Real domain evidence: bytes, journals, trust, and ephemeral authorization."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import importlib
import importlib.util
import json
import threading
from contextvars import copy_context
from pathlib import Path
import yaml

import pytest

from plugins.workflow.marketplace.lifecycle_models import (
    AllTrustSelection,
    OneTrustSelection,
    PackageIdentity,
    PackageSubject,
)
from plugins.workflow.marketplace.models import InstallRequest, InstalledPackageIdentity
from plugins.workflow.marketplace.package import (
    WorkflowMarketplaceError,
    load_distribution,
)
from test_marketplace_service import (  # noqa: F401
    _add_and_refresh,
    _write_package,
    _publish,
    published_repo,
    service,
)


@pytest.fixture
def domain():
    class Producer:
        def __getattr__(self, method):
            name = "plugins.workflow.marketplace.lifecycle_state"
            assert importlib.util.find_spec(name) is not None, (
                "domain evidence producer is missing"
            )
            return getattr(importlib.import_module(name), method)

    return Producer()


@pytest.fixture
def source_service(service, published_repo):
    from plugins.workflow.marketplace.models import WorkflowMarketplaceSource

    service.add_source(
        WorkflowMarketplaceSource(
            name="company", repositoryUrl=published_repo.remote.as_uri()
        )
    )
    return service


def _source_completion(service, *, call=None, source_name="company"):
    module = importlib.import_module("plugins.workflow.marketplace.lifecycle_state")
    producer = getattr(module, "complete_source_refresh", None)
    assert callable(producer), "source publication evidence producer is missing"
    return producer(
        service,
        source_name=source_name,
        call=call or (lambda: service.refresh_source(source_name)),
    )


def test_source_completion_captures_exact_verified_publication(source_service):
    completion = _source_completion(source_service)
    store = source_service.catalog.source_store
    cached = store.cached(store.get("company"))
    assert completion.state == "succeeded"
    assert completion.outcome.type == "committed"
    assert completion.outcome.package_state is None
    assert completion.result.type == "source_refresh"
    assert completion.result.value.source_name == "company"
    assert completion.result.value.resolved_commit == cached.resolved_commit
    assert completion.result.value.verified_at == cached.verified_at
    assert store.status("company").state == "fresh"
    assert completion.review_token is None


def test_source_completion_disabled_makes_no_publication(source_service):
    source_service.set_source_enabled("company", False)
    completion = _source_completion(source_service)
    assert completion.state == "succeeded"
    assert completion.result.value.state == "disabled"
    assert completion.outcome.type == "known_unchanged"
    assert not source_service.catalog.source_store.catalog_path.exists()


def test_source_completion_fetch_failure_preserves_verified_cache(
    source_service, monkeypatch
):
    original = source_service.refresh_source("company")

    def unavailable(*args, **kwargs):
        raise WorkflowMarketplaceError("source_unavailable", "repository unavailable")

    monkeypatch.setattr(source_service.catalog.git_fetcher, "fetch", unavailable)
    completion = _source_completion(source_service)
    assert completion.state == "succeeded"
    assert completion.result.value.state == "stale"
    assert completion.outcome.type == "known_unchanged"
    assert completion.result.value.resolved_commit == original.resolved_commit
    assert source_service.catalog.source_store.status("company").state == "stale"


def test_source_completion_encoding_failure_proves_no_publication(source_service):
    store = source_service.catalog.source_store
    store.max_catalog_state_bytes = 1
    completion = _source_completion(source_service)
    assert completion.state == "succeeded"
    assert completion.result.value.state == "unavailable"
    assert completion.outcome.type == "known_unchanged"
    assert not store.catalog_path.exists()


@pytest.mark.parametrize("replace_first", [False, True])
@pytest.mark.parametrize("fail_status", [False, True])
def test_source_completion_atomic_uncertainty_survives_fallback_status(
    source_service, monkeypatch, replace_first, fail_status
):
    import plugins.workflow.marketplace.source_store as stores

    store = source_service.catalog.source_store
    original = stores.atomic_write_text
    attempted = False

    def fail_publication(path, *args, **kwargs):
        nonlocal attempted
        if path == store.catalog_path:
            if not attempted:
                attempted = True
                if replace_first:
                    original(path, *args, **kwargs)
                raise OSError("atomic publication outcome unavailable")
            if fail_status:
                raise OSError("status publication unavailable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(stores, "atomic_write_text", fail_publication)
    completion = _source_completion(source_service)
    assert completion.state == "failed"
    assert completion.result is None
    assert completion.outcome.type == "outcome_unknown"
    cached = store.cached(store.get("company"))
    assert (cached is not None) is replace_first
    if cached is not None:
        assert cached.packages[0].id == "laptop-support"


@pytest.mark.parametrize("checkpoint", ["fetch", "encoding"])
def test_source_completion_cancellation_before_write_is_proven(
    source_service, monkeypatch, checkpoint
):
    import plugins.workflow.marketplace.source_store as stores
    from plugins.workflow.marketplace.operations import MarketplaceOperationCancelled

    cancelled = False
    if checkpoint == "fetch":
        owner, name = source_service.catalog.git_fetcher, "fetch"
    else:
        owner, name = stores, "_render_state"
    original = getattr(owner, name)

    def cancel_after(*args, **kwargs):
        nonlocal cancelled
        result = original(*args, **kwargs)
        cancelled = True
        return result

    monkeypatch.setattr(owner, name, cancel_after)
    with pytest.raises(MarketplaceOperationCancelled):
        _source_completion(
            source_service,
            call=lambda: source_service.refresh_source(
                "company", cancelled=lambda: cancelled
            ),
        )
    assert not source_service.catalog.source_store.catalog_path.exists()


def test_source_completion_late_cancellation_preserves_publication(
    source_service, monkeypatch
):
    import plugins.workflow.marketplace.source_store as stores

    cancelled = False
    original = stores.atomic_write_text

    def cancel_after_replace(*args, **kwargs):
        nonlocal cancelled
        original(*args, **kwargs)
        cancelled = True

    monkeypatch.setattr(stores, "atomic_write_text", cancel_after_replace)
    completion = _source_completion(
        source_service,
        call=lambda: source_service.refresh_source(
            "company", cancelled=lambda: cancelled
        ),
    )
    assert completion.state == "succeeded"
    assert completion.outcome.type == "committed"
    assert source_service.catalog.source_store.status("company").state == "fresh"


def test_source_completion_rejects_retained_result_without_execution(source_service):
    retained = source_service.refresh_source("company")
    completion = _source_completion(source_service, call=lambda: retained)
    assert completion.state == "failed"
    assert completion.outcome.type == "outcome_unknown"


@pytest.mark.parametrize(
    "field,value", [("source_name", "other"), ("package_count", 0)]
)
def test_source_completion_rejects_substituted_publication_result(
    source_service, field, value
):
    from dataclasses import replace

    def substituted():
        result = source_service.refresh_source("company")
        return replace(result, **{field: value})

    completion = _source_completion(source_service, call=substituted)
    assert completion.state == "failed"
    assert completion.outcome.type == "outcome_unknown"
    assert source_service.catalog.source_store.status("company").state == "fresh"


def test_source_completion_lost_domain_return_does_not_erase_publication(
    source_service,
):
    from plugins.workflow.marketplace.operations import MarketplaceOperationCancelled

    def lost_return():
        source_service.refresh_source("company")
        raise MarketplaceOperationCancelled("late transport cancellation")

    completion = _source_completion(source_service, call=lost_return)
    assert completion.state == "failed"
    assert completion.outcome.type == "outcome_unknown"
    assert source_service.catalog.source_store.status("company").state == "fresh"


def test_source_completion_wrong_store_cannot_publish(source_service, tmp_path):
    from plugins.workflow.marketplace.service import WorkflowMarketplaceService

    other = WorkflowMarketplaceService(tmp_path / "other-home", profile="support")
    other.add_source(source_service.catalog.source_store.get("company"))
    completion = _source_completion(
        source_service, call=lambda: other.refresh_source("company")
    )
    assert completion.state == "failed"
    assert completion.outcome.type == "outcome_unknown"
    assert not other.catalog.source_store.catalog_path.exists()


def test_source_completion_catalog_replacement_cannot_transfer_authority(
    source_service, tmp_path
):
    from plugins.workflow.marketplace.service import WorkflowMarketplaceService

    other = WorkflowMarketplaceService(tmp_path / "other-home", profile="support")
    other.add_source(source_service.catalog.source_store.get("company"))

    def replace_catalog():
        source_service.catalog = other.catalog
        return source_service.refresh_source("company")

    completion = _source_completion(source_service, call=replace_catalog)
    assert completion.state == "failed"
    assert completion.outcome.type == "outcome_unknown"
    assert not other.catalog.source_store.catalog_path.exists()


@pytest.mark.parametrize("path", ["fresh", "disabled", "fetch_failed"])
@pytest.mark.parametrize("boundary", ["before_entry", "during_fetch"])
def test_source_completion_backing_store_replacement_never_touches_foreign_home(
    source_service, tmp_path, monkeypatch, path, boundary
):
    from plugins.workflow.marketplace.source_store import WorkflowSourceStore

    original_store = source_service.catalog.source_store
    foreign = WorkflowSourceStore(tmp_path / "foreign-home")
    foreign.add(original_store.get("company"))
    if path == "disabled":
        if boundary == "before_entry":
            foreign.set_enabled("company", False)
        else:
            # Change the backing store during the disabled cache-read path.
            original_store.set_enabled("company", False)
    before = foreign.path.read_bytes()

    def replace_store():
        source_service.catalog.source_store = foreign

    real_fetch = source_service.catalog.git_fetcher.fetch

    def fetch(*args, **kwargs):
        if boundary == "during_fetch":
            replace_store()
        if path == "fetch_failed":
            raise WorkflowMarketplaceError("source_unavailable", "fetch failed")
        return real_fetch(*args, **kwargs)

    monkeypatch.setattr(source_service.catalog.git_fetcher, "fetch", fetch)
    if path == "disabled" and boundary == "during_fetch":
        original_cached = original_store.cached

        def cached(*args, **kwargs):
            result = original_cached(*args, **kwargs)
            replace_store()
            return result

        monkeypatch.setattr(original_store, "cached", cached)

    def call():
        if boundary == "before_entry":
            replace_store()
        return source_service.refresh_source("company")

    completion = _source_completion(source_service, call=call)
    assert completion.state == "failed"
    assert completion.outcome.type == "outcome_unknown"
    assert foreign.path.read_bytes() == before
    assert not foreign.catalog_path.exists()
    assert not original_store.catalog_path.exists()


def test_source_completion_store_swap_during_fallback_never_writes_foreign_status(
    source_service, tmp_path, monkeypatch
):
    from plugins.workflow.marketplace.source_store import WorkflowSourceStore

    original_store = source_service.catalog.source_store
    foreign = WorkflowSourceStore(tmp_path / "foreign-home")
    foreign.add(original_store.get("company"))
    before = foreign.path.read_bytes()

    def fail_fetch(*args, **kwargs):
        raise WorkflowMarketplaceError("source_unavailable", "fetch failed")

    def transfer_fallback(*args, **kwargs):
        source_service.catalog.source_store = foreign
        return foreign.record_failed_refresh(*args, **kwargs)

    monkeypatch.setattr(source_service.catalog.git_fetcher, "fetch", fail_fetch)
    monkeypatch.setattr(original_store, "record_failed_refresh", transfer_fallback)
    completion = _source_completion(source_service)
    assert completion.state == "failed"
    assert completion.outcome.type == "outcome_unknown"
    assert foreign.path.read_bytes() == before
    assert not foreign.catalog_path.exists()
    assert not original_store.catalog_path.exists()


@pytest.mark.parametrize("refresh", ["fresh", "disabled", "cancelled"])
@pytest.mark.parametrize("exit_kind", ["return", "error", "cancel"])
@pytest.mark.parametrize(
    "replacement", ["store", "catalog", "catalog_none", "catalog_missing"]
)
def test_source_completion_outer_callback_cannot_transfer_terminal_authority(
    source_service, tmp_path, refresh, exit_kind, replacement
):
    from plugins.workflow.marketplace.operations import MarketplaceOperationCancelled
    from plugins.workflow.marketplace.service import WorkflowMarketplaceService

    owner = source_service.catalog.source_store
    foreign = WorkflowMarketplaceService(tmp_path / "foreign-home", profile="support")
    foreign.add_source(owner.get("company"))
    foreign_store = foreign.catalog.source_store
    before = foreign_store.path.read_bytes()
    if refresh == "disabled":
        owner.set_enabled("company", False)

    def call():
        result = source_service.refresh_source(
            "company", cancelled=lambda: refresh == "cancelled"
        )
        if replacement == "store":
            source_service.catalog.source_store = foreign_store
        elif replacement == "catalog":
            source_service.catalog = foreign.catalog
        elif replacement == "catalog_none":
            source_service.catalog = None
        else:
            del source_service.catalog
        if exit_kind == "error":
            raise RuntimeError("outer callback failed")
        if exit_kind == "cancel":
            raise MarketplaceOperationCancelled("outer callback cancelled")
        return result

    completion = _source_completion(source_service, call=call)
    # A real commit remains in its owner home; scope rejection is no rollback.
    assert owner.catalog_path.exists() is (refresh == "fresh")
    assert foreign_store.path.read_bytes() == before
    assert not foreign_store.catalog_path.exists()
    assert (completion.state, completion.outcome.type) == (
        "failed",
        "outcome_unknown",
    )


def test_source_completion_reuse_retains_uncertainty_after_publication(source_service):
    def reused():
        result = source_service.refresh_source("company")
        with pytest.raises(WorkflowMarketplaceError):
            source_service.refresh_source("company", cancelled=lambda: True)
        return result

    completion = _source_completion(source_service, call=reused)
    assert completion.state == "failed"
    assert completion.outcome.type == "outcome_unknown"
    assert source_service.catalog.source_store.status("company").state == "fresh"


def test_source_completion_unpersisted_diagnostic_uses_canonical_sanitizer(
    source_service, monkeypatch
):
    def cancellation_error(*args, **kwargs):
        raise WorkflowMarketplaceError(
            "source_cancelled", "cancelled in /tmp/hermes-private-stage"
        )

    monkeypatch.setattr(source_service.catalog.git_fetcher, "fetch", cancellation_error)
    completion = _source_completion(source_service)
    assert completion.state == "succeeded"
    assert completion.outcome.type == "known_unchanged"
    assert (
        completion.result.value.message == "workflow marketplace source refresh failed"
    )
    assert "hermes-private-stage" not in completion.result.model_dump_json()


def test_source_completion_rejects_wrong_source_before_write(source_service):
    completion = _source_completion(
        source_service,
        source_name="other",
        call=lambda: source_service.refresh_source("company"),
    )
    assert completion.state == "failed"
    assert completion.outcome.type == "outcome_unknown"
    assert not source_service.catalog.source_store.catalog_path.exists()


@pytest.mark.parametrize("closed", [False, True])
def test_source_completion_copied_context_cannot_publish(source_service, closed):
    contexts = []

    def call():
        contexts.append(copy_context())
        if not closed:
            with ThreadPoolExecutor(max_workers=1) as pool:
                with pytest.raises(WorkflowMarketplaceError):
                    pool.submit(
                        contexts[0].run, source_service.refresh_source, "company"
                    ).result(timeout=5)

    completion = _source_completion(source_service, call=call)
    if closed:
        with pytest.raises(WorkflowMarketplaceError):
            contexts[0].run(source_service.refresh_source, "company")
    assert completion.state == "failed"
    assert completion.outcome.type == "outcome_unknown"
    assert not source_service.catalog.source_store.catalog_path.exists()


@pytest.fixture
def lifecycle_domain(domain, service, published_repo):
    class Domain:
        identity = InstalledPackageIdentity(
            sourceKey="company", packageId="laptop-support"
        )
        subject = PackageSubject(
            type="package",
            identity=PackageIdentity(source_key="company", package_id="laptop-support"),
        )

        def __init__(self):
            self.service = service
            _add_and_refresh(service, published_repo)

        def prepare(self):
            return service.prepare_install(
                InstallRequest(identifier="company/laptop-support"), actor="alice"
            )

        def install_version(self, version="1.0.0"):
            assert version == "1.0.0"
            review = self.prepare()
            return service.confirm_install(review.confirmation_token, actor="alice")

        def prepare_update(self, version="2.0.0"):
            _write_package(
                published_repo.work, "laptop-support", version=version, marker="v2"
            )
            published_repo.publish("update")
            return service.prepare_update(self.identity, actor="alice")

        def mutation(self, kind, call, selection=None):
            return domain.complete_mutation(
                service,
                kind=kind,
                subject=self.subject,
                selection=selection,
                actor="alice",
                call=call,
            )

        def state(self):
            return domain.read_package_state(service, self.identity)

        def read_installed_bytes(self):
            return load_distribution(
                service.installed_store.package_root(self.identity)
            )

    return Domain()


def test_absence_is_verified_without_creating_package_or_journal(domain, service):
    identity = InstalledPackageIdentity(sourceKey="company", packageId="laptop-support")
    state = domain.read_package_state(service, identity)
    assert (state.state, state.installed, state.trust, state.recovery, state.busy) == (
        "absent",
        None,
        None,
        "clear",
        False,
    )
    assert not service.transactions.journal_path.exists()
    assert not service.installed_store.package_root(identity).exists()


def test_installed_state_contains_verified_full_workflow_membership(lifecycle_domain):
    d = lifecycle_domain
    installed = d.install_version()
    state = d.state()
    assert state.state == "installed"
    assert state.installed.distribution_digest == installed.distribution_digest
    assert [
        (x.workflow_name, x.definition_path, x.state) for x in state.trust.workflows
    ] == [
        ("diagnostic", "workflows/diagnostic.yaml", "untrusted"),
        ("repair", "workflows/repair.yaml", "untrusted"),
    ]


@pytest.mark.parametrize(
    "corruption", ["bytes", "provenance", "trust", "journals", "missing_bytes"]
)
def test_corruption_cannot_certify_installed_or_absent(lifecycle_domain, corruption):
    d = lifecycle_domain
    d.install_version()
    if corruption == "bytes":
        (
            d.service.installed_store.package_root(d.identity)
            / "workflows/diagnostic.yaml"
        ).write_text("changed")
    elif corruption == "missing_bytes":
        d.service.installed_store.package_root(d.identity).rename(
            d.service.home / "detached"
        )
    else:
        path = {
            "provenance": d.service.installed_store.path,
            "trust": d.service.trust_store.path,
            "journals": d.service.transactions.journal_path,
        }[corruption]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{corrupt")
    state = d.state()
    assert state.state == "unconfirmed"
    assert state.installed is None and state.trust is None
    assert state.recovery != "clear"


def test_cleanup_failure_has_no_verified_old_version(lifecycle_domain, monkeypatch):
    d = lifecycle_domain
    d.install_version()
    review = d.prepare_update()
    real = d.service.transactions.atomic_install

    def fault(point):
        if point == "after_trust_revoke":
            raise OSError("cleanup unavailable")

    monkeypatch.setattr(
        d.service.transactions,
        "atomic_install",
        lambda *args, **kwargs: real(*args, **kwargs, fault=fault),
    )
    result = d.mutation(
        "update_confirm",
        lambda: d.service.confirm_update(review.confirmation_token, actor="alice"),
    )
    assert result.state == "failed"
    assert result.outcome.type == "recovery_required"
    assert d.read_installed_bytes().manifest.version == "2.0.0"
    assert d.state().state == "unconfirmed"
    assert d.state().installed is None


@pytest.mark.parametrize("installed_first", [False, True])
def test_rollback_requires_reverified_package_trust_and_clear_journals(
    lifecycle_domain, monkeypatch, installed_first
):
    d = lifecycle_domain
    if installed_first:
        d.install_version()
        review = d.prepare_update()
        kind, confirm = "update_confirm", d.service.confirm_update
    else:
        review = d.prepare()
        kind, confirm = "install_confirm", d.service.confirm_install
    real = d.service.transactions.atomic_install

    def fault(point):
        if point == "after_candidate_swap":
            raise OSError("swap follow-up failed")

    monkeypatch.setattr(
        d.service.transactions,
        "atomic_install",
        lambda *args, **kwargs: real(*args, **kwargs, fault=fault),
    )
    result = d.mutation(kind, lambda: confirm(review.confirmation_token, actor="alice"))
    assert result.error.code == "transaction_rollback_completed"
    assert result.outcome.type == "known_unchanged"
    assert result.outcome.evidence == "rollback_verified"
    state = result.outcome.package_state
    assert state.state == ("installed" if installed_first else "absent")
    assert state.recovery == "clear"
    if installed_first:
        assert state.installed.version == "1.0.0"


def test_committed_install_and_remove_supply_exact_state(lifecycle_domain):
    d = lifecycle_domain
    review = d.prepare()
    result = d.mutation(
        "install_confirm",
        lambda: d.service.confirm_install(review.confirmation_token, actor="alice"),
    )
    assert result.state == "succeeded" and result.outcome.type == "committed"
    assert result.outcome.package_state.installed.version == "1.0.0"
    review = d.service.prepare_remove(d.identity, actor="alice")
    result = d.mutation(
        "remove_confirm",
        lambda: d.service.confirm_remove(review.confirmation_token, actor="alice"),
    )
    assert result.result.value.version == "1.0.0"
    assert result.outcome.package_state.state == "absent"


def test_selected_trust_result_is_complete_a_and_b_map(lifecycle_domain):
    d = lifecycle_domain
    d.install_version()
    review = d.service.review_trust(
        d.identity, actor="alice", workflow_name="diagnostic"
    )
    selection = OneTrustSelection(type="one", workflow_name="diagnostic")
    result = d.mutation(
        "trust_confirm", lambda: d.service.grant_trust(review, actor="alice"), selection
    )
    assert result.state == "succeeded"
    assert result.result.value.selection == selection
    assert [(x.workflow_name, x.state) for x in result.result.value.workflows] == [
        ("diagnostic", "trusted"),
        ("repair", "untrusted"),
    ]
    assert result.outcome.package_state.trust.workflows == result.result.value.workflows


def test_revoke_all_returns_full_map_and_preserves_manual_grant(lifecycle_domain):
    d = lifecycle_domain
    d.install_version()
    review = d.service.review_trust(d.identity, actor="alice")
    d.service.grant_trust(review, actor="alice")
    first = review.workflows[0]
    d.service.trust_store.trust(
        first.package_digest, actor="manual", risk_digest=first.risk_digest
    )
    result = d.mutation(
        "trust_revoke",
        lambda: d.service.revoke_trust(d.identity),
        AllTrustSelection(type="all"),
    )
    assert result.state == "succeeded"
    assert result.result.value.revoked == 2
    assert [(x.workflow_name, x.state) for x in result.result.value.workflows] == [
        ("diagnostic", "trusted"),
        ("repair", "untrusted"),
    ]


def test_active_consumed_lease_prevents_current_state_claim(lifecycle_domain):
    d = lifecycle_domain
    review = d.prepare()
    d.service.transactions.consume(
        review.confirmation_token, actor="alice", profile="support"
    )
    state = d.state()
    assert state.busy and state.state == "unconfirmed"
    assert state.installed is None


def test_generic_callback_never_supplies_mutation_evidence(lifecycle_domain):
    d = lifecycle_domain
    installed = d.install_version()
    result = d.mutation("install_confirm", lambda: installed)
    assert result.state == "failed"
    assert result.outcome.type == "outcome_unknown"


def test_evidence_does_not_leak_between_workers_or_after_exception(lifecycle_domain):
    d = lifecycle_domain
    review = d.prepare()
    with ThreadPoolExecutor(max_workers=2) as pool:
        committed = pool.submit(
            d.mutation,
            "install_confirm",
            lambda: d.service.confirm_install(review.confirmation_token, actor="alice"),
        ).result()
        assert committed.state == "succeeded"
        result = pool.submit(
            d.mutation, "install_confirm", lambda: committed.result.value
        ).result()
    assert result.outcome.type == "outcome_unknown"
    assert d.mutation("install_confirm", lambda: None).outcome.type == "outcome_unknown"


def test_read_recovery_diagnostic_is_never_read_only(lifecycle_domain, domain):
    d = lifecycle_domain

    def fail():
        raise WorkflowMarketplaceError(
            "transaction_recovery_ambiguous", "private detail"
        )

    result = domain.complete_read(
        d.service,
        kind="remove_prepare",
        subject=d.subject,
        selection=None,
        actor="alice",
        call=fail,
    )
    assert result.outcome.type == "recovery_required"
    assert "private detail" not in result.error.model_dump_json()


def test_preparation_is_token_free_and_metadata_authority_expires(
    lifecycle_domain, domain
):
    d = lifecycle_domain
    result = domain.complete_read(
        d.service,
        kind="install_prepare",
        subject=d.subject,
        selection=None,
        actor="alice",
        call=d.prepare,
    )
    assert result.state == "succeeded"
    metadata = result.review_token
    assert metadata is not None
    assert metadata.confirmation_token not in result.result.model_dump_json()
    assert "confirmation_token" not in result.result.model_dump_json()
    assert metadata.validate_unused(metadata.confirmation_token)
    original_expiry = metadata.expires_at
    now = d.service.clock()
    d.service.transactions.clock = lambda: now + timedelta(minutes=10)
    assert not metadata.validate_unused(metadata.confirmation_token)
    assert metadata.expires_at == original_expiry


def test_token_metadata_rejects_foreign_actor_and_wrong_selection(
    lifecycle_domain, domain
):
    d = lifecycle_domain
    d.install_version()
    review = d.service.review_trust(
        d.identity, actor="alice", workflow_name="diagnostic"
    )
    for actor, selection in [
        ("bob", OneTrustSelection(type="one", workflow_name="diagnostic")),
        ("alice", OneTrustSelection(type="one", workflow_name="repair")),
        ("alice", AllTrustSelection(type="all")),
    ]:
        with pytest.raises(WorkflowMarketplaceError):
            domain.review_token_metadata(
                d.service, review, actor=actor, subject=d.subject, selection=selection
            )


def test_state_and_selection_use_yaml_name_not_filename(
    lifecycle_domain, domain, published_repo
):
    d = lifecycle_domain
    root = published_repo.work / "packages/laptop-support"
    path = root / "workflows/diagnostic.yaml"
    workflow = yaml.safe_load(path.read_text())
    workflow["name"] = "actual-workflow-a"
    path.write_text(yaml.safe_dump(workflow))
    _publish(root)
    published_repo.publish("distinct workflow name")
    d.service.refresh_source("company")
    d.install_version()
    review = d.service.review_trust(
        d.identity, actor="alice", workflow_name="actual-workflow-a"
    )
    selection = OneTrustSelection(type="one", workflow_name="actual-workflow-a")
    result = domain.complete_read(
        d.service,
        kind="trust_prepare",
        subject=d.subject,
        selection=selection,
        actor="alice",
        call=lambda: review,
    )
    assert result.state == "succeeded"
    assert [
        (w.workflow_name, w.definition_path)
        for w in result.result.value.package_workflows
    ] == [
        ("actual-workflow-a", "workflows/diagnostic.yaml"),
        ("repair", "workflows/repair.yaml"),
    ]
    grant = d.mutation(
        "trust_confirm", lambda: d.service.grant_trust(review, actor="alice"), selection
    )
    assert grant.state == "succeeded"
    assert grant.result.value.workflows[0].workflow_name == "actual-workflow-a"


def test_dangling_trust_symlink_cannot_certify_untrusted(lifecycle_domain):
    d = lifecycle_domain
    d.install_version()
    d.service.trust_store.path.symlink_to(d.service.home / "missing-trust")
    assert d.state().state == "unconfirmed"


def test_legacy_trust_result_comes_from_commit_snapshot(lifecycle_domain, monkeypatch):
    d = lifecycle_domain
    d.install_version()
    review = d.service.review_trust(
        d.identity, actor="alice", workflow_name="diagnostic"
    )

    # A failure in a later independent read must not obscure the committed result.
    def unavailable(_identity):
        raise OSError("later trust read unavailable")

    monkeypatch.setattr(d.service, "workflow_trust", unavailable)
    result = d.service.grant_trust(review, actor="alice")
    assert result == {"diagnostic": "trusted", "repair": "untrusted"}


def test_legacy_token_can_still_be_consumed_by_recreated_service(lifecycle_domain):
    from plugins.workflow.marketplace.service import WorkflowMarketplaceService

    d = lifecycle_domain
    d.install_version()
    review = d.service.review_trust(d.identity, actor="alice")
    recreated = WorkflowMarketplaceService(d.service.home, profile="support")
    assert recreated.grant_trust(review, actor="alice") == {
        "diagnostic": "trusted",
        "repair": "trusted",
    }


def test_trust_snapshot_precedes_waiting_manual_writer(lifecycle_domain, monkeypatch):
    from plugins.workflow.trust import WorkflowTrustStore

    d = lifecycle_domain
    d.install_version()
    full = d.service.review_trust(d.identity, actor="alice")
    repair = next(w for w in full.workflows if w.workflow_name == "repair")
    review = d.service.review_trust(
        d.identity, actor="alice", workflow_name="diagnostic"
    )
    selection = OneTrustSelection(type="one", workflow_name="diagnostic")
    writing = threading.Event()
    attempted = threading.Event()
    real_write = d.service.trust_store._write

    def pause_write(payload):
        real_write(payload)
        writing.set()
        assert attempted.wait(5)

    monkeypatch.setattr(d.service.trust_store, "_write", pause_write)

    def manual():
        assert writing.wait(5)
        attempted.set()
        WorkflowTrustStore(d.service.home).trust(
            repair.package_digest, actor="manual", risk_digest=repair.risk_digest
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        waiting = pool.submit(manual)
        result = d.mutation(
            "trust_confirm",
            lambda: d.service.grant_trust(review, actor="alice"),
            selection,
        )
        waiting.result(timeout=10)
    assert result.state == "succeeded"
    assert [(w.workflow_name, w.state) for w in result.result.value.workflows] == [
        ("diagnostic", "trusted"),
        ("repair", "untrusted"),
    ]
    assert all(w.state == "trusted" for w in d.state().trust.workflows)


def test_post_write_trust_projection_failure_is_not_unchanged(
    lifecycle_domain, monkeypatch
):
    import plugins.workflow.marketplace.service as service_module

    d = lifecycle_domain
    d.install_version()
    review = d.service.review_trust(d.identity, actor="alice")

    def fail(*args):
        raise OSError("post-write projection")

    monkeypatch.setattr(service_module, "_capture_trust", fail)
    result = d.mutation(
        "trust_confirm",
        lambda: d.service.grant_trust(review, actor="alice"),
        AllTrustSelection(type="all"),
    )
    assert result.state == "failed"
    assert result.outcome.type in {"recovery_required", "outcome_unknown"}
    assert all(w.state == "trusted" for w in d.state().trust.workflows)


def test_copied_context_cannot_capture_another_threads_mutation(lifecycle_domain):
    d = lifecycle_domain
    review = d.prepare()

    def different_worker():
        context = copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                context.run,
                lambda: d.service.confirm_install(
                    review.confirmation_token, actor="alice"
                ),
            ).result()

    result = d.mutation("install_confirm", different_worker)
    assert result.outcome.type == "outcome_unknown"
    assert d.state().state == "absent"


def test_caught_copied_context_call_remains_invalid_and_does_not_mutate(
    lifecycle_domain,
):
    d = lifecycle_domain
    review = d.prepare()

    def different_worker():
        context = copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(WorkflowMarketplaceError):
                pool.submit(
                    context.run,
                    lambda: d.service.confirm_install(
                        review.confirmation_token, actor="alice"
                    ),
                ).result()

    result = d.mutation("install_confirm", different_worker)
    assert result.outcome.type == "outcome_unknown"
    assert d.state().state == "absent"


def test_copied_context_call_then_owner_cancellation_cannot_report_cancelled(
    lifecycle_domain,
):
    from plugins.workflow.marketplace.operations import MarketplaceOperationCancelled

    d = lifecycle_domain
    d.install_version()
    review = d.service.review_trust(d.identity, actor="alice")
    d.service.grant_trust(review, actor="alice")

    def calls():
        context = copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                pool.submit(
                    context.run, lambda: d.service.revoke_trust(d.identity)
                ).result()
            except WorkflowMarketplaceError:
                pass
        d.service.revoke_trust(d.identity, enter_atomic=lambda: False)

    try:
        result = d.mutation("trust_revoke", calls, AllTrustSelection(type="all"))
    except MarketplaceOperationCancelled:
        pytest.fail("invalid copied-context call was rewritten as cancellation")
    assert result.outcome.type == "outcome_unknown"
    assert all(workflow.state == "trusted" for workflow in d.state().trust.workflows)


def test_unrelated_legacy_worker_can_still_mutate_without_lifecycle_scope(
    lifecycle_domain,
):
    d = lifecycle_domain
    d.install_version()
    review = d.service.review_trust(d.identity, actor="alice")
    d.service.grant_trust(review, actor="alice")

    with ThreadPoolExecutor(max_workers=1) as pool:
        revoked = pool.submit(d.service.revoke_trust, d.identity).result()

    assert revoked == 2
    assert all(workflow.state == "untrusted" for workflow in d.state().trust.workflows)


def test_copied_context_cannot_mutate_after_originating_scope_closes(
    lifecycle_domain,
):
    d = lifecycle_domain
    review = d.prepare()
    copied = []

    result = d.mutation("install_confirm", lambda: copied.append(copy_context()))
    assert result.outcome.type == "outcome_unknown"

    with pytest.raises(WorkflowMarketplaceError):
        copied[0].run(
            d.service.confirm_install,
            review.confirmation_token,
            actor="alice",
        )
    assert d.state().state == "absent"


def test_wrong_subject_cannot_mutate_a_different_package(lifecycle_domain, domain):
    d = lifecycle_domain
    review = d.prepare()
    wrong = PackageSubject(
        type="package",
        identity=PackageIdentity(source_key="company", package_id="other"),
    )
    result = domain.complete_mutation(
        d.service,
        kind="install_confirm",
        subject=wrong,
        selection=None,
        actor="alice",
        call=lambda: d.service.confirm_install(
            review.confirmation_token, actor="alice"
        ),
    )
    assert result.state == "failed"
    assert result.outcome.evidence == "before_mutation"
    assert d.state().state == "absent"
    assert d.service.transactions.inspect_token(
        review.confirmation_token, actor="alice", profile="support"
    )


def test_token_availability_is_false_after_consumption(lifecycle_domain, domain):
    d = lifecycle_domain
    review = d.prepare()
    metadata = domain.review_token_metadata(
        d.service, review, actor="alice", subject=d.subject, selection=None
    )
    d.service.confirm_install(review.confirmation_token, actor="alice")
    assert not metadata.validate_unused(metadata.confirmation_token)


def test_review_authority_rejects_altered_digest_bound_facts(lifecycle_domain, domain):
    d = lifecycle_domain
    review = d.prepare()
    altered = review.model_copy(update={"candidate_version": "9.9.9"})

    with pytest.raises(WorkflowMarketplaceError, match="binding is invalid"):
        domain.review_token_metadata(
            d.service,
            altered,
            actor="alice",
            subject=d.subject,
            selection=None,
        )

    result = domain.complete_read(
        d.service,
        kind="install_prepare",
        subject=d.subject,
        selection=None,
        actor="alice",
        call=lambda: altered,
    )
    assert result.state == "failed"
    assert result.review_token is None


def test_review_authority_rejects_altered_facts_for_every_review_kind(
    lifecycle_domain, domain
):
    d = lifecycle_domain
    install = d.prepare()
    altered_assessment = install.assessment.model_copy(update={"package_resources": []})
    altered_install = install.model_copy(update={"assessment": altered_assessment})
    with pytest.raises(WorkflowMarketplaceError, match="binding is invalid"):
        domain.review_token_metadata(
            d.service,
            altered_install,
            actor="alice",
            subject=d.subject,
            selection=None,
        )

    d.service.confirm_install(install.confirmation_token, actor="alice")
    update = d.prepare_update()
    altered_workflow = update.workflow_reviews[0].model_copy(
        update={"risk_digest": "f" * 64}
    )
    altered_update = update.model_copy(
        update={"workflow_reviews": [altered_workflow, *update.workflow_reviews[1:]]}
    )

    remove = d.service.prepare_remove(d.identity, actor="alice")
    altered_remove = remove.model_copy(update={"workflow_names": []})

    trust = d.service.review_trust(d.identity, actor="alice")
    altered_trust_workflow = trust.workflows[0].model_copy(
        update={"trust_state": "trusted"}
    )
    altered_trust = trust.model_copy(
        update={"workflows": [altered_trust_workflow, *trust.workflows[1:]]}
    )

    for review, selection in (
        (altered_update, None),
        (altered_remove, None),
        (altered_trust, AllTrustSelection(type="all")),
    ):
        with pytest.raises(WorkflowMarketplaceError, match="binding is invalid"):
            domain.review_token_metadata(
                d.service,
                review,
                actor="alice",
                subject=d.subject,
                selection=selection,
            )


def test_issued_install_review_remains_immutable_when_trust_changes(
    lifecycle_domain, domain
):
    d = lifecycle_domain
    issued = d.prepare()
    workflow = issued.workflow_reviews[0]
    assert workflow.trust_state == "untrusted"
    d.service.trust_store.trust(
        workflow.package_digest,
        actor="manual",
        risk_digest=workflow.risk_digest,
    )
    altered_workflow = workflow.model_copy(update={"trust_state": "trusted"})
    altered = issued.model_copy(
        update={"workflow_reviews": [altered_workflow, *issued.workflow_reviews[1:]]}
    )

    metadata = domain.review_token_metadata(
        d.service,
        issued,
        actor="alice",
        subject=d.subject,
        selection=None,
    )
    assert metadata.validate_unused(issued.confirmation_token)
    with pytest.raises(WorkflowMarketplaceError, match="binding is invalid"):
        domain.review_token_metadata(
            d.service,
            altered,
            actor="alice",
            subject=d.subject,
            selection=None,
        )


def test_issued_trust_review_remains_immutable_when_trust_changes(
    lifecycle_domain, domain
):
    d = lifecycle_domain
    d.install_version()
    issued = d.service.review_trust(d.identity, actor="alice")
    workflow = issued.workflows[0]
    assert workflow.trust_state == "untrusted"
    d.service.trust_store.trust(
        workflow.package_digest,
        actor="manual",
        risk_digest=workflow.risk_digest,
    )
    altered_workflow = workflow.model_copy(update={"trust_state": "trusted"})
    altered = issued.model_copy(
        update={"workflows": [altered_workflow, *issued.workflows[1:]]}
    )
    selection = AllTrustSelection(type="all")

    metadata = domain.review_token_metadata(
        d.service,
        issued,
        actor="alice",
        subject=d.subject,
        selection=selection,
    )
    assert metadata.validate_unused(issued.confirmation_token)
    with pytest.raises(WorkflowMarketplaceError, match="binding is invalid"):
        domain.review_token_metadata(
            d.service,
            altered,
            actor="alice",
            subject=d.subject,
            selection=selection,
        )


def test_recreated_service_lacks_v2_review_proof_but_legacy_confirmation_works(
    lifecycle_domain, domain
):
    from plugins.workflow.marketplace.service import WorkflowMarketplaceService

    d = lifecycle_domain
    issued = d.prepare()
    recreated = WorkflowMarketplaceService(d.service.home, profile="support")

    with pytest.raises(WorkflowMarketplaceError, match="binding is invalid"):
        domain.review_token_metadata(
            recreated,
            issued,
            actor="alice",
            subject=d.subject,
            selection=None,
        )

    installed = recreated.confirm_install(issued.confirmation_token, actor="alice")
    assert installed.version == "1.0.0"


def test_review_authority_capacity_never_evicts_a_live_review(
    lifecycle_domain, domain, monkeypatch
):
    import plugins.workflow.marketplace.service as service_module

    d = lifecycle_domain
    monkeypatch.setattr(service_module, "_ISSUED_REVIEW_LIMIT", 1)
    issued = d.prepare()
    assert issued.confirmation_token not in repr(d.service._issued_reviews)

    with pytest.raises(WorkflowMarketplaceError) as exhausted:
        d.prepare()
    assert exhausted.value.code == "transaction_state_size_limit"

    metadata = domain.review_token_metadata(
        d.service,
        issued,
        actor="alice",
        subject=d.subject,
        selection=None,
    )
    assert metadata.validate_unused(issued.confirmation_token)


def test_consumed_review_authority_is_pruned_before_new_capture(
    lifecycle_domain, domain, monkeypatch
):
    import plugins.workflow.marketplace.service as service_module

    d = lifecycle_domain
    monkeypatch.setattr(service_module, "_ISSUED_REVIEW_LIMIT", 1)
    install = d.prepare()
    d.service.confirm_install(install.confirmation_token, actor="alice")
    remove = d.service.prepare_remove(d.identity, actor="alice")

    metadata = domain.review_token_metadata(
        d.service,
        remove,
        actor="alice",
        subject=d.subject,
        selection=None,
    )
    assert metadata.validate_unused(remove.confirmation_token)


@pytest.mark.parametrize("second_kind", ["trust", "remove"])
def test_concurrent_review_capture_never_prunes_newer_live_proof(
    lifecycle_domain, domain, monkeypatch, second_kind
):
    d = lifecycle_domain
    d.install_version()
    paused = threading.Event()
    finish = threading.Event()
    newer_started = threading.Event()
    original_lock = d.service._issued_review_lock

    class Gate:
        def __enter__(self):
            if threading.current_thread().name.startswith("old-snapshot"):
                paused.set()
                assert finish.wait(5)
            original_lock.acquire()

        def __exit__(self, *_args):
            original_lock.release()

    monkeypatch.setattr(d.service, "_issued_review_lock", Gate())
    all_selection = AllTrustSelection(type="all")

    def prepare_newer():
        newer_started.set()
        if second_kind == "trust":
            return d.service.review_trust(d.identity, actor="alice")
        return d.service.prepare_remove(d.identity, actor="alice")

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="old-snapshot") as pool:
        older_future = pool.submit(d.service.review_trust, d.identity, actor="alice")
        assert paused.wait(5)
        try:
            newer_future = pool.submit(prepare_newer)
            assert newer_started.wait(5)
        finally:
            finish.set()
        older = older_future.result(timeout=5)
        newer = newer_future.result(timeout=5)

    selection = all_selection if second_kind == "trust" else None
    original_metadata = domain.review_token_metadata(
        d.service,
        newer,
        actor="alice",
        subject=d.subject,
        selection=selection,
    )
    original_expiry = original_metadata.expires_at
    assert original_metadata.validate_unused(newer.confirmation_token)
    assert domain.review_token_metadata(
        d.service,
        older,
        actor="alice",
        subject=d.subject,
        selection=all_selection,
    ).validate_unused(older.confirmation_token)
    newer_metadata = domain.review_token_metadata(
        d.service,
        newer,
        actor="alice",
        subject=d.subject,
        selection=selection,
    )
    assert newer_metadata.expires_at == original_expiry
    assert newer_metadata.validate_unused(newer.confirmation_token)
    assert original_metadata.validate_unused(newer.confirmation_token)


@pytest.mark.parametrize("checkpoint", ["cancelled", "enter_atomic"])
def test_verified_prewrite_cancellation_reaches_registry_cancel_path(
    lifecycle_domain, checkpoint
):
    from plugins.workflow.marketplace.operations import MarketplaceOperationCancelled

    d = lifecycle_domain
    review = d.prepare()
    kwargs = (
        {"cancelled": lambda: True}
        if checkpoint == "cancelled"
        else {"enter_atomic": lambda: False}
    )
    with pytest.raises(MarketplaceOperationCancelled):
        d.mutation(
            "install_confirm",
            lambda: d.service.confirm_install(
                review.confirmation_token, actor="alice", **kwargs
            ),
        )
    assert not d.service.installed_store.package_root(d.identity).exists()


def test_read_cancellation_is_not_published_as_failed_read_only(
    lifecycle_domain, domain
):
    from plugins.workflow.marketplace.operations import MarketplaceOperationCancelled

    d = lifecycle_domain
    with pytest.raises(MarketplaceOperationCancelled):
        domain.complete_read(
            d.service,
            kind="install_prepare",
            subject=d.subject,
            selection=None,
            actor="alice",
            call=lambda: d.service.prepare_install(
                InstallRequest(identifier="company/laptop-support"),
                actor="alice",
                cancelled=lambda: True,
            ),
        )


def test_cancel_shaped_error_after_write_cannot_cancel(lifecycle_domain, monkeypatch):
    import plugins.workflow.marketplace.service as service_module

    d = lifecycle_domain
    d.install_version()
    review = d.service.review_trust(d.identity, actor="alice")

    def fail(*args):
        raise WorkflowMarketplaceError("marketplace_operation_cancelled", "too late")

    monkeypatch.setattr(service_module, "_capture_trust", fail)
    result = d.mutation(
        "trust_confirm",
        lambda: d.service.grant_trust(review, actor="alice"),
        AllTrustSelection(type="all"),
    )
    assert result.state == "failed"
    assert result.outcome.type in {"recovery_required", "outcome_unknown"}


def test_second_domain_call_cannot_overwrite_first_committed_evidence(lifecycle_domain):
    d = lifecycle_domain
    review = d.prepare()

    def two_calls():
        d.service.confirm_install(review.confirmation_token, actor="alice")
        d.service.confirm_install(review.confirmation_token, actor="alice")

    result = d.mutation("install_confirm", two_calls)
    assert result.outcome.type == "outcome_unknown"
    assert d.state().state == "installed"


def test_second_domain_call_cannot_cancel_after_first_commit(lifecycle_domain):
    from plugins.workflow.marketplace.operations import MarketplaceOperationCancelled

    d = lifecycle_domain
    d.install_version()
    review = d.service.review_trust(d.identity, actor="alice")
    d.service.grant_trust(review, actor="alice")

    def two_calls():
        d.service.revoke_trust(d.identity)
        d.service.revoke_trust(d.identity, enter_atomic=lambda: False)

    try:
        result = d.mutation("trust_revoke", two_calls, AllTrustSelection(type="all"))
    except MarketplaceOperationCancelled:
        pytest.fail("second call falsely cancelled an already committed mutation")
    assert result.outcome.type == "outcome_unknown"
    assert all(workflow.state == "untrusted" for workflow in d.state().trust.workflows)


def test_caught_second_domain_call_still_fails_closed(lifecycle_domain):
    d = lifecycle_domain
    review = d.prepare()

    def two_calls():
        d.service.confirm_install(review.confirmation_token, actor="alice")
        try:
            d.service.confirm_install(review.confirmation_token, actor="alice")
        except WorkflowMarketplaceError:
            pass

    result = d.mutation("install_confirm", two_calls)
    assert result.outcome.type == "outcome_unknown"
    assert d.state().state == "installed"


def test_read_adapter_refuses_a_mutation_callback(lifecycle_domain, domain):
    d = lifecycle_domain
    review = d.prepare()
    result = domain.complete_read(
        d.service,
        kind="install_prepare",
        subject=d.subject,
        selection=None,
        actor="alice",
        call=lambda: d.service.confirm_install(
            review.confirmation_token, actor="alice"
        ),
    )
    assert result.state == "failed"
    assert d.state().state == "absent"


def test_duplicate_trust_keys_cannot_certify_state(lifecycle_domain):
    d = lifecycle_domain
    d.install_version()
    d.service.trust_store.path.write_text('{"version":2,"records":{},"records":{}}')
    assert d.state().state == "unconfirmed"


def test_failed_rollback_verification_requires_recovery(lifecycle_domain, monkeypatch):
    d = lifecycle_domain
    d.install_version()
    review = d.prepare_update()
    real = d.service.transactions.atomic_install

    def fault(point):
        if point == "after_candidate_swap":
            d.service.trust_store.path.write_text("corrupt")
            raise OSError("swap follow-up failed")

    monkeypatch.setattr(
        d.service.transactions,
        "atomic_install",
        lambda *args, **kwargs: real(*args, **kwargs, fault=fault),
    )
    result = d.mutation(
        "update_confirm",
        lambda: d.service.confirm_update(review.confirmation_token, actor="alice"),
    )
    assert result.outcome.type == "recovery_required"
    assert result.outcome.reason == "state_unverified"
    assert d.read_installed_bytes().manifest.version == "1.0.0"


@pytest.mark.parametrize("store", ["trust", "provenance", "journals"])
def test_evidence_reads_enforce_existing_byte_limits(
    lifecycle_domain, monkeypatch, store
):
    import plugins.workflow.trust as trust_module

    d = lifecycle_domain
    d.install_version()
    if store == "trust":
        d.service.trust_store.path.write_text(json.dumps({"version": 2, "records": {}}))
        monkeypatch.setattr(trust_module, "WORKFLOW_TRUST_MAX_STORE_BYTES", 8)
    elif store == "provenance":
        monkeypatch.setattr(d.service.installed_store, "max_state_bytes", 8)
    else:
        monkeypatch.setattr(d.service.transactions, "max_state_bytes", 8)
    assert d.state().state == "unconfirmed"


def test_shared_writer_lock_reports_busy_without_package_claim(lifecycle_domain):
    from plugins.workflow.locks import workflow_lock

    d = lifecycle_domain
    entered = threading.Event()
    release = threading.Event()

    def writer():
        with workflow_lock(d.service.transactions.lock_path):
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        assert entered.wait(5)
        try:
            d.service.transactions.lock_timeout_seconds = 0.02
            state = d.state()
            assert state.busy and state.state == "unconfirmed"
        finally:
            release.set()
            future.result(timeout=5)


def test_remove_cleanup_failure_preserves_recovery_uncertainty(
    lifecycle_domain, monkeypatch
):
    d = lifecycle_domain
    d.install_version()
    review = d.service.prepare_remove(d.identity, actor="alice")
    real = d.service.transactions.atomic_remove

    def fault(point):
        if point == "after_trust_revoke":
            raise OSError("remove cleanup failed")

    monkeypatch.setattr(
        d.service.transactions,
        "atomic_remove",
        lambda *args, **kwargs: real(*args, **kwargs, fault=fault),
    )
    result = d.mutation(
        "remove_confirm",
        lambda: d.service.confirm_remove(review.confirmation_token, actor="alice"),
    )
    assert result.outcome.type == "recovery_required"
    assert d.state().state == "unconfirmed"
    assert not d.service.installed_store.package_root(d.identity).exists()


@pytest.mark.parametrize(
    "kind",
    ["inspect", "update_check", "update_prepare", "remove_prepare", "trust_prepare"],
)
def test_real_read_results_project_their_matching_lifecycle_union(
    lifecycle_domain, domain, kind
):
    d = lifecycle_domain
    d.install_version()
    selection = AllTrustSelection(type="all") if kind == "trust_prepare" else None
    calls = {
        "inspect": lambda: d.service.inspect("company/laptop-support"),
        "update_check": lambda: d.service.check_updates(d.identity),
        "update_prepare": lambda: d.service.prepare_update(d.identity, actor="alice"),
        "remove_prepare": lambda: d.service.prepare_remove(d.identity, actor="alice"),
        "trust_prepare": lambda: d.service.review_trust(d.identity, actor="alice"),
    }
    result = domain.complete_read(
        d.service,
        kind=kind,
        subject=d.subject,
        selection=selection,
        actor="alice",
        call=calls[kind],
    )
    assert result.state == "succeeded", result.error
    assert result.outcome.evidence == "read_only"
    assert (
        result.result.type
        == {
            "inspect": "package_detail",
            "update_check": "update_checks",
            "update_prepare": "update_review",
            "remove_prepare": "remove_review",
            "trust_prepare": "trust_review",
        }[kind]
    )
    if kind == "update_prepare":
        assert result.result.value.result == "unchanged"
        assert (
            result.review_token is None
            and not result.result.value.confirmation_available
        )


@pytest.mark.parametrize(
    "code,reason",
    [
        ("transaction_rollback_failed", "rollback_failed"),
        ("transaction_recovery_ambiguous", "recovery_ambiguous"),
    ],
)
def test_missing_positive_evidence_never_erases_recovery_diagnostic(
    lifecycle_domain, code, reason
):
    d = lifecycle_domain

    def fail():
        raise WorkflowMarketplaceError(code, "private")

    result = d.mutation("install_confirm", fail)
    assert result.outcome.type == "recovery_required"
    assert result.outcome.reason == reason
