"""Marketplace lifecycle through authenticated APIs and real temporary Git bytes."""

import json
import os
import shutil
import threading

import pytest
from test_marketplace_lifecycle_api import (  # noqa: F401
    IDENTITY,
    PRINCIPAL_HEADER,
    V2,
    LifecycleApi,
    lifecycle_api,
)
from test_marketplace_service import (  # noqa: F401
    _write_package,
    published_repo,
    service,
)
from test_marketplace_trust import _healthy_coordinator

from plugins.workflow.api_admission import (
    ApiAdmissionAuthority,
    ApiAdmissionError,
    start_api_run,
)
from plugins.workflow.catalog_api import resolve_workflow_catalog_compilation
from plugins.workflow.marketplace.models import InstalledPackageIdentity
from plugins.workflow.marketplace.service import WorkflowMarketplaceService
from plugins.workflow.marketplace.transactions import MarketplaceTransactionStore
from plugins.workflow.store import RunStore


def _compilation(api, *, source="profile", workdir=None, home=None):
    return resolve_workflow_catalog_compilation(
        "diagnostic",
        hermes_home=home or api.home,
        workdir=workdir or api.home,
        catalog_source=source,
    )


@pytest.mark.parametrize("provenance", ["exact", "missing", "foreign", "changed"])
def test_bounded_catalog_binding_requires_exact_profile_provenance(
    lifecycle_api, tmp_path, provenance
):
    api = lifecycle_api
    api.install()
    identity = InstalledPackageIdentity(sourceKey="company", packageId="laptop-support")
    root = api.service.installed_store.package_root(identity)
    project = tmp_path / "project"
    shutil.copytree(root, project / ".hermes/workflows/copied")
    copied = _compilation(api, source="project", workdir=project)
    assert copied.package.marketplace_binding is None
    # Even identical package bytes in a different profile must not borrow trust.
    other_home = tmp_path / "other-profile"
    shutil.copytree(root, other_home / "workflows/marketplace/company/laptop-support")
    assert _compilation(api, home=other_home).package.marketplace_binding is None
    if provenance == "missing":
        api.service.installed_store.remove(identity)
    elif provenance == "foreign":
        state = json.loads(api.service.installed_store.path.read_bytes())
        state["packages"][0]["destination"] = str(
            other_home / "workflows/marketplace/company/laptop-support"
        )
        api.service.installed_store.path.write_text(json.dumps(state))
    elif provenance == "changed":
        # Valid newly digested publisher bytes are not the installed provenance.
        from test_marketplace_service import _publish

        (root / "commands/guide.md").write_text("changed package-owned resource\n")
        _publish(root)
    before = api.service.installed_store.path.read_bytes()
    compilation = _compilation(api)
    assert api.service.installed_store.path.read_bytes() == before
    if provenance == "exact":
        assert (
            compilation.package.marketplace_binding.installation_key
            == "company/laptop-support"
        )
        assert (
            compilation.package.marketplace_binding.distribution_digest
            == _state(api)["installed"]["distribution_digest"]
        )
    else:
        assert compilation.package.marketplace_binding is None


def test_installed_binding_preserves_catalog_result_and_shared_read_budgets(
    lifecycle_api, monkeypatch
):
    from plugins.workflow import catalog_api

    api = lifecycle_api
    api.install()
    monkeypatch.setattr(catalog_api, "CATALOG_LIMIT", 1)
    candidates, failures, truncated = catalog_api._catalog_candidates(
        api.home, api.home
    )
    assert truncated and not failures
    assert [item[2].workflow_path.name for item in candidates] == ["diagnostic.yaml"]
    assert (
        candidates[0][2].marketplace_binding.installation_key
        == "company/laptop-support"
    )
    monkeypatch.setattr(catalog_api, "CATALOG_MAX_RESOURCE_TOTAL_BYTES", 1)
    candidates, failures, truncated = catalog_api._catalog_candidates(
        api.home, api.home
    )
    assert not candidates and truncated
    assert failures[0][2].catalog_capacity


def test_two_profiles_and_manual_foreign_origin_grants_remain_independent(
    lifecycle_api, tmp_path
):
    api = lifecycle_api
    other = LifecycleApi(
        WorkflowMarketplaceService(tmp_path / "operations", profile="operations"),
        api.repository,
    )
    try:
        api.install()
        other.install()
        _confirm(
            api, api.review("/trust/review", {"identity": IDENTITY}), "/trust/grant"
        )
        assert _state(api)["profile"] == "support"
        assert _state(other)["profile"] == "operations"
        assert all(
            item["state"] == "untrusted" for item in _state(other)["trust"]["workflows"]
        )
        review = api.review("/trust/review", {"identity": IDENTITY})
        members = review["result"]["value"]["workflows"]
        manual, foreign = members
        api.service.trust_store.trust(
            manual["package_digest"], actor="manual", risk_digest=manual["risk_digest"]
        )
        api.service.trust_store.trust_origin(
            foreign["package_digest"],
            actor="other-source",
            risk_digest=foreign["risk_digest"],
            origin="marketplace:partner/laptop-support",
        )
        # An exact request/receipt on one profile is not transferable to another.
        request = api.request({"identity": IDENTITY})
        rejected = other.client.post(
            V2 + "/updates/check",
            json=request,
            headers={PRINCIPAL_HEADER: api.client.headers[PRINCIPAL_HEADER]},
        )
        assert rejected.status_code == 409
        _confirm(
            api,
            api.review("/remove/prepare", {"identity": IDENTITY}),
            "/remove/confirm",
        )
        assert _state(api)["state"] == "absent"
        assert _state(other)["state"] == "installed"
        for member in members:
            assert (
                api.service.trust_store.check(
                    member["package_digest"], risk_digest=member["risk_digest"]
                )
                == "trusted"
            )
        grants = json.loads(api.service.trust_store.path.read_bytes())["records"]
        assert (
            "marketplace:company/laptop-support"
            not in grants[manual["package_digest"]]["grants"]
        )
        assert "manual" in grants[manual["package_digest"]]["grants"]
        assert (
            "marketplace:partner/laptop-support"
            in grants[foreign["package_digest"]]["grants"]
        )
    finally:
        other.client.close()
        other.context.close()


@pytest.mark.parametrize(
    "fault_point,expected",
    [
        ("after_candidate_swap", "known_unchanged"),
        ("after_trust_revoke", "recovery_required"),
    ],
)
def test_real_update_fault_replay_and_restart_recovery_preserve_truth(
    lifecycle_api, monkeypatch, fault_point, expected
):
    api = lifecycle_api
    api.install()
    _confirm(api, api.review("/trust/review", {"identity": IDENTITY}), "/trust/grant")
    _write_package(api.repository.work, "laptop-support", version="2.0.0", marker="v2")
    api.repository.publish("faulted update")
    request = api.confirm_request(api.review("/update/prepare", {"identity": IDENTITY}))
    original = api.service.transactions.atomic_install
    commits = []

    def fault(point):
        if point == fault_point:
            raise OSError("injected update boundary failure")

    def actual(*args, **kwargs):
        commits.append(args[0].transaction_id)
        return original(*args, **kwargs, fault=fault)

    monkeypatch.setattr(api.service.transactions, "atomic_install", actual)
    response = api.post("/update/confirm", request)
    assert response.status_code == 202
    operation = api.wait(response.json()["id"])
    assert operation["outcome"]["type"] == expected
    assert api.post("/update/confirm", request).json()["id"] == operation["id"]
    assert len(commits) == 1
    if expected == "known_unchanged":
        assert operation["outcome"]["evidence"] == "rollback_verified"
        assert _state(api)["installed"]["version"] == "1.0.0"
        assert all(
            item["state"] == "trusted" for item in _state(api)["trust"]["workflows"]
        )
    else:
        assert operation["outcome"]["reason"] == "rollback_failed"
        assert _state(api)["state"] == "unconfirmed"
        assert _state(api)["installed"] is None and _state(api)["trust"] is None
    restarted = MarketplaceTransactionStore(api.home)
    restarted.recover_transactions()
    assert restarted.recover_transactions() == ()
    current = _state(api)
    assert current["recovery"] == "clear"
    assert current["installed"]["version"] == (
        "1.0.0" if expected == "known_unchanged" else "2.0.0"
    )
    # Recovery updates current truth; it cannot rewrite historical uncertainty.
    assert (
        api.client.get(V2 + "/operations/" + operation["id"]).json()["outcome"]
        == operation["outcome"]
    )


def test_cancelled_real_install_preparation_does_not_create_installed_bytes(
    lifecycle_api, monkeypatch
):
    api = lifecycle_api
    entered, release = threading.Event(), threading.Event()
    original = api.service.prepare_install

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(api.service, "prepare_install", blocked)
    response = api.post(
        "/install/prepare", api.request({"identifier": "company/laptop-support"})
    )
    assert response.status_code == 202 and entered.wait(5)
    try:
        cancelled = api.post("/operations/" + response.json()["id"] + "/cancel", {})
        assert cancelled.status_code == 200
    finally:
        release.set()
    operation = api.wait(response.json()["id"])
    assert operation["outcome"]["type"] == "cancelled_before_commit"
    assert _state(api)["state"] == "absent"


def test_ambiguous_journal_never_certifies_visible_candidate_or_erases_it(
    lifecycle_api, monkeypatch
):
    from plugins.workflow.marketplace.package import WorkflowMarketplaceError

    api = lifecycle_api
    request = api.confirm_request(api.review())
    original = api.service.confirm_install

    def ambiguous(*args, **kwargs):
        installed = original(*args, **kwargs)
        assert installed.version == "1.0.0"
        api.service.transactions.journal_path.write_text("{incomplete")
        raise WorkflowMarketplaceError(
            "transaction_recovery_ambiguous", "injected ambiguous journal"
        )

    monkeypatch.setattr(api.service, "confirm_install", ambiguous)
    response = api.post("/install/confirm", request)
    assert response.status_code == 202
    operation = api.wait(response.json()["id"])
    assert operation["outcome"] == {
        "type": "recovery_required",
        "reason": "recovery_ambiguous",
    }
    root = api.home / "workflows/marketplace/company/laptop-support"
    before = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    provenance = api.service.installed_store.path.read_bytes()
    with pytest.raises(WorkflowMarketplaceError):
        MarketplaceTransactionStore(api.home).recover_transactions()
    assert {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    } == before
    assert api.service.installed_store.path.read_bytes() == provenance
    current = _state(api)
    assert (
        current["state"] == "unconfirmed"
        and current["installed"] is None
        and current["trust"] is None
    )
    assert api.post("/install/confirm", request).json()["id"] == operation["id"]
    assert (
        api.client.get(V2 + "/operations/" + operation["id"]).json()["outcome"]
        == operation["outcome"]
    )


def test_real_workflow_backend_initialization_allows_marketplace_install(lifecycle_api):
    api = lifecycle_api
    # Desktop starts the normal workflow runtime before opening Marketplace.
    # Isolate the inherited process umask so this models a usual POSIX home.
    previous_umask = os.umask(0o022)
    try:
        RunStore(api.home)
    finally:
        os.umask(previous_umask)
    review = api.start("/install/prepare", {"identifier": "company/laptop-support"})
    assert review["state"] == "succeeded", (review["error"], review["outcome"])
    installed, _ = _confirm(api, review, "/install/confirm")
    assert installed["outcome"]["package_state"]["state"] == "installed"


def test_workflow_runtime_recovery_preserves_live_marketplace_preparation(
    lifecycle_api,
):
    api = lifecycle_api
    review = api.review()
    (envelope,) = api.service.transactions.staging_root.iterdir()
    metadata = envelope.stat()
    before = {
        path.relative_to(envelope): path.read_bytes()
        for path in envelope.rglob("*")
        if path.is_file()
    }
    # Reverse startup order proves this is an ownership collision as well as a
    # directory-mode collision: ordinary runtime recovery must preserve a live
    # package review owned by the marketplace transaction journal/marker.
    RunStore(api.home)
    RunStore(api.home)
    assert {
        path.relative_to(envelope): path.read_bytes()
        for path in envelope.rglob("*")
        if path.is_file()
    } == before
    after = envelope.stat()
    assert (after.st_dev, after.st_ino, after.st_mode) == (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
    )
    installed, _ = _confirm(api, review, "/install/confirm")
    assert installed["outcome"]["package_state"]["state"] == "installed"


def _confirm(api, review, path):
    request = api.confirm_request(review)
    response = api.post(path, request)
    assert response.status_code == 202, response.text
    operation = api.wait(response.json()["id"])
    assert operation["outcome"]["type"] == "committed", (
        operation["error"],
        operation["outcome"],
    )
    return operation, request


def _state(api):
    response = api.client.get(V2 + "/packages/company/laptop-support/state")
    assert response.status_code == 200, response.text
    return response.json()


def _admit(api, key):
    store = RunStore(api.home)
    _healthy_coordinator(store)
    return start_api_run(
        store,
        hermes_home=api.home,
        workdir=api.home,
        user_home=api.home,
        workflow_name="diagnostic",
        values={},
        idempotency_key=key,
        concurrency_policy="queue",
        authority=ApiAdmissionAuthority(
            principal="operator",
            namespace="operator",
            operator_scope=None,
            source_instance="desktop:test",
            assurance="local_admin_claim",
        ),
        catalog_source="profile",
    )


def test_local_removal_review_with_concurrent_read_only_inspection(
    lifecycle_api, monkeypatch
):
    """A remote read must not contradict local verified removal availability."""
    api = lifecycle_api
    api.install()
    entered, release = threading.Event(), threading.Event()
    original = api.service.inspect

    def inspect(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(api.service, "inspect", inspect)
    response = api.post("/packages/company/laptop-support", api.request({}))
    assert response.status_code == 202, response.text
    assert entered.wait(5)
    try:
        assert _state(api)["state"] == "installed"
        assert _state(api)["busy"] is False
        removal = api.post("/remove/prepare", api.request({"identity": IDENTITY}))
        assert removal.status_code == 202, removal.text
        review = api.wait(removal.json()["id"])
        assert review["state"] == "succeeded"
        _confirm(api, review, "/remove/confirm")
        assert _state(api)["state"] == "absent"
    finally:
        release.set()
    assert api.wait(response.json()["id"])["state"] == "succeeded"
    assert _state(api)["state"] == "absent"


def test_exact_admission_replay_trust_one_all_changed_update_and_removal(
    lifecycle_api, monkeypatch
):
    from plugins.workflow import api_admission

    original_assessment = api_admission.assess_workflow_admission
    assessments = []

    def observe_assessment(*args, **kwargs):
        result = original_assessment(*args, **kwargs)
        assessments.append(result)
        return result

    monkeypatch.setattr(api_admission, "assess_workflow_admission", observe_assessment)
    api = lifecycle_api
    original_install = api.service.transactions.atomic_install
    committed_transactions = []

    def observe_install(*args, **kwargs):
        result = original_install(*args, **kwargs)
        committed_transactions.append(args[0].transaction_id)
        return result

    monkeypatch.setattr(api.service.transactions, "atomic_install", observe_install)
    inspection = api.start("/packages/company/laptop-support", {})
    assert inspection["result"]["type"] == "package_detail"
    review = api.review()
    assert _state(api)["state"] == "absent"
    installed, request = _confirm(api, review, "/install/confirm")
    # The transport caller loses the first response; its exact request discovers
    # the original admission after real filesystem/provenance writes completed.
    replay = api.post("/install/confirm", request)
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == installed["id"]
    lookup = api.client.get(V2 + "/admissions/" + request["request_id"])
    assert lookup.json()["operation"]["id"] == installed["id"]
    assert len(committed_transactions) == 1
    first = _state(api)
    assert first["installed"]["version"] == "1.0.0"
    assert {
        item["workflow_name"]: item["state"] for item in first["trust"]["workflows"]
    } == {"diagnostic": "untrusted", "repair": "untrusted"}
    with pytest.raises(ApiAdmissionError) as refused:
        _admit(api, "untrusted-install")
    assert refused.value.code == "workflow_trust_required"
    one = api.review(
        "/trust/review", {"identity": IDENTITY, "workflow_name": "diagnostic"}
    )
    granted, _ = _confirm(api, one, "/trust/grant")
    selected = one["result"]["value"]["workflows"][0]
    runtime = assessments[-1]
    assert (selected["package_digest"], selected["risk_digest"]) == (
        runtime.package_digest.sha256,
        runtime.risk.risk_digest,
    ), {
        "review_digest": selected["package_digest"],
        "runtime_digest": runtime.package_digest.sha256,
        "runtime_binding": runtime.package.marketplace_binding,
        "runtime_paths": runtime.package_digest.covered_relative_paths,
    }
    assert {
        item["workflow_name"]: item["state"]
        for item in granted["result"]["value"]["workflows"]
    } == {"diagnostic": "trusted", "repair": "untrusted"}
    admitted = _admit(api, "trusted-install")
    run = RunStore(api.home).run_directory(str(admitted["run_id"]))
    assert (run / "definition.yaml").read_bytes() == (
        api.home
        / "workflows/marketplace/company/laptop-support/workflows/diagnostic.yaml"
    ).read_bytes()
    all_review = api.review("/trust/review", {"identity": IDENTITY})
    _confirm(api, all_review, "/trust/grant")
    assert all(item["state"] == "trusted" for item in _state(api)["trust"]["workflows"])
    _write_package(api.repository.work, "laptop-support", version="2.0.0", marker="v2")
    api.repository.publish("publish changed distribution")
    updated, _ = _confirm(
        api, api.review("/update/prepare", {"identity": IDENTITY}), "/update/confirm"
    )
    current = _state(api)
    assert current["installed"]["version"] == "2.0.0"
    assert (
        current["trust"]["distribution_digest"] != first["trust"]["distribution_digest"]
    )
    assert all(item["state"] == "untrusted" for item in current["trust"]["workflows"])
    with pytest.raises(ApiAdmissionError) as refused:
        _admit(api, "untrusted-update")
    assert refused.value.code == "workflow_trust_required"
    assert updated["outcome"]["package_state"]["installed"]["version"] == "2.0.0"
    _confirm(
        api, api.review("/remove/prepare", {"identity": IDENTITY}), "/remove/confirm"
    )
    assert _state(api)["state"] == "absent"
    # Retained successful installation is historical evidence, not current state.
    history = api.client.get(V2 + "/operations/" + installed["id"]).json()
    assert history["outcome"]["package_state"]["installed"]["version"] == "1.0.0"
    assert "confirmation_token" not in json.dumps(history)
