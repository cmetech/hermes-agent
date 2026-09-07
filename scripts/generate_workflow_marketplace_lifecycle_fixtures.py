"""Generate V1 inspection and token-free V2 fixtures through real Git/services.

Run with the repository test interpreter and --write or --check. No user profile
is opened. Structural Desktop types/schema are derived from the public models;
Python's custom validators remain the differential acceptance oracle.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import sys
import time
import unicodedata
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/plugins/workflow"))

from pydantic import TypeAdapter, ValidationError  # noqa: E402
from hermes_cli import git_source  # noqa: E402
from agent import redact  # noqa: E402
from plugins.workflow.marketplace import lifecycle_models as wire  # noqa: E402
from plugins.workflow.marketplace.api import _sanitize_result, _legacy_completion  # noqa: E402
from plugins.workflow.marketplace.lifecycle_api import (  # noqa: E402
    _Capabilities,
    _public_completion,
    _EmptyBody,
    _InstallBody,
    _IdentityBody,
    _CheckBody,
    _TrustBody,
    _ConfirmBody,
)
from plugins.workflow.marketplace.lifecycle_state import (  # noqa: E402
    complete_mutation,
    complete_read,
    complete_source_refresh,
    read_package_state,
)
from plugins.workflow.marketplace.models import (  # noqa: E402
    InstallRequest,
    InstalledPackageIdentity,
    WorkflowMarketplaceSource,
)
from plugins.workflow.marketplace.operations import (  # noqa: E402
    MarketplaceOperation,
    AdmissionEvicted,
    AdmissionFound,
    LifecycleOperationPage,
    ReviewTokenResponse,
    WorkflowMarketplaceOperationRegistry,
)
from plugins.workflow.marketplace.service import (
    WorkflowMarketplaceService,
    direct_source_key,
)  # noqa: E402
from plugins.workflow.marketplace.admissions import LifecycleAdmissionStore  # noqa: E402
from plugins.workflow.marketplace.package import WorkflowMarketplaceError  # noqa: E402
from test_marketplace_service import published_repo, _write_package  # noqa: E402

NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
UTC = "2026-09-05T12:00:00Z"
EPOCH = "e" * 32
CORPUS = ROOT / "tests/fixtures/workflow-marketplace-lifecycle-v2.json"
INSPECTION_V1 = ROOT / "tests/fixtures/workflow-marketplace-inspection-v1.json"
TYPES = ROOT / "apps/desktop/src/types/workflow-marketplace-lifecycle.ts"


def _case(name, value, model, **validation_options):
    try:
        model.model_validate_json(json.dumps(value), **validation_options)
    except ValidationError:
        accepted = False
    else:
        accepted = True
    return {"name": name, "value": value, "accepted": accepted}


def _inspect_v1(service, identity, subject):
    # Fixed identity affects only this memory-only registry's public ID prefix.
    registry_home = "/fixture-profile/support"
    ids = iter(range(3000, 3020))
    registry = WorkflowMarketplaceOperationRegistry(
        profile_key=registry_home,
        profile="support",
        clock=lambda: NOW,
        admissions=LifecycleAdmissionStore(
            profile_key=registry_home,
            epoch=EPOCH,
            clock=lambda: NOW,
            random_hex=lambda: f"{next(ids):032x}",
        ),
    )

    def work(cancellation):
        cancellation.set_progress("fetching", 10)
        return _legacy_completion(
            complete_read(
                service,
                kind="inspect",
                subject=subject,
                selection=None,
                actor="alice",
                call=lambda: service.inspect(
                    "company/laptop-support", cancelled=cancellation.is_cancelled
                ),
            )
        )

    try:
        # No caller request_id: this is an actual V1 admission, not a projection
        # of a V2-started operation. get_legacy enforces that immutable eligibility.
        current = registry.start(
            "package_detail",
            work,
            actor="alice",
            subject=subject,
            canonical_body=identity.model_dump(mode="json"),
        )
        operation_id = current.id
        deadline = time.monotonic() + 10
        while True:
            current = registry.get_legacy(operation_id, actor="alice")
            if current.state not in {"pending", "running"}:
                assert current.state == "succeeded"
                return current.model_dump(mode="json", by_alias=False)
            assert time.monotonic() < deadline, "V1 inspection did not complete"
            time.sleep(0.001)
    finally:
        registry.close()


def generate_corpora():
    operations = []
    states = []
    envelopes = {}
    identity = InstalledPackageIdentity(sourceKey="company", packageId="laptop-support")
    subject = wire.PackageSubject(
        type="package",
        identity=wire.PackageIdentity(
            source_key="company", package_id="laptop-support"
        ),
    )
    source = wire.SourceSubject(type="source", source_name="company")
    one = wire.OneTrustSelection(type="one", workflow_name="A")
    all_selection = wire.AllTrustSelection(type="all")

    def record(name, kind, completion, operation_subject=subject, selection=None):
        completion = _public_completion(completion)
        number = len(operations) + 1
        value = wire.LifecycleOperation.model_validate({
            "schema_version": 2,
            "id": f"wmop_{'a' * 12}_{number:032x}",
            "registry_epoch": EPOCH,
            "request_id": f"wmreq_{EPOCH}_{int(NOW.timestamp() * 1000):013d}_{number:032x}",
            "kind": kind,
            "subject": operation_subject.model_dump(mode="json"),
            "selection": selection.model_dump(mode="json") if selection else None,
            "profile": "support",
            "state": completion.state,
            "phase": "completed" if completion.state == "succeeded" else "failed",
            "progress": 100 if completion.state == "succeeded" else 0,
            "created_at": UTC,
            "started_at": UTC,
            "updated_at": UTC,
            "finished_at": UTC,
            "result": completion.result.model_dump(mode="json")
            if completion.result
            else None,
            "error": completion.error.model_dump(mode="json")
            if completion.error
            else None,
            "outcome": completion.outcome.model_dump(mode="json"),
        }).model_dump(mode="json")
        operations.append(_case(name, value, wire.LifecycleOperation))
        return completion

    with TemporaryDirectory(prefix="hermes-lifecycle-fixtures-") as directory:
        root = Path(directory).resolve()
        environment = {
            "HOME": directory,
            "HERMES_HOME": str(root / "profile"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_DATE": UTC,
            "GIT_COMMITTER_DATE": UTC,
        }
        with (
            patch.dict(os.environ, environment),
            patch.object(Path, "home", return_value=root),
        ):
            repo = published_repo.__wrapped__(root)
            _write_package(
                repo.work, "laptop-support", version="1.0.0", workflow_names=("A", "B")
            )
            repo.publish("publish A and B")
            # Git's ephemeral URL rewrite keeps reviewed public identity stable
            # while every fetch still reads the actual temporary bare repository.
            os.environ.update({
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": f"url.{repo.remote.as_uri()}.insteadOf",
                "GIT_CONFIG_VALUE_0": "https://fixtures.example/workflows.git",
            })
            service = WorkflowMarketplaceService(
                root / "profile", profile="support", clock=lambda: NOW
            )
            service.add_source(
                WorkflowMarketplaceSource(
                    name="company",
                    repositoryUrl="https://fixtures.example/workflows.git",
                )
            )

            def state(name):
                value = _sanitize_result(
                    read_package_state(service, identity).model_dump(mode="json"),
                    allow_confirmation_token=False,
                )
                states.append(_case(name, value, wire.PackageState))

            def read(name, kind, call, selection=None, operation_subject=subject):
                return record(
                    name,
                    kind,
                    complete_read(
                        service,
                        kind=kind,
                        subject=operation_subject,
                        selection=selection,
                        actor="alice",
                        call=call,
                    ),
                    operation_subject,
                    selection,
                )

            def mutation(name, kind, call, selection=None):
                return record(
                    name,
                    kind,
                    complete_mutation(
                        service,
                        kind=kind,
                        subject=subject,
                        selection=selection,
                        actor="alice",
                        call=call,
                    ),
                    selection=selection,
                )

            record(
                "service refresh",
                "refresh",
                complete_source_refresh(
                    service,
                    source_name="company",
                    call=lambda: service.refresh_source("company"),
                ),
                source,
            )
            state("service absent")
            read(
                "service inspect",
                "inspect",
                lambda: service.inspect("company/laptop-support"),
            )
            inspection_v1 = {
                "operationCases": [
                    _case(
                        "service inspect",
                        _inspect_v1(service, identity, subject),
                        MarketplaceOperation,
                        by_alias=False,
                        by_name=True,
                    )
                ]
            }
            repository_url = "https://fixtures.example/workflows.git"
            admissions = LifecycleAdmissionStore(
                profile_key=str(service.home), epoch=EPOCH, clock=lambda: NOW
            )
            with patch(
                "plugins.workflow.marketplace.admissions._PROCESS_SECRET", b"f" * 32
            ):
                selector = admissions.direct_selector_id(
                    repository_url, None, "packages/laptop-support"
                )
            direct = wire.DirectInstallSubject(
                type="direct_install",
                source_key=direct_source_key(repository_url),
                selector_id=selector,
            )
            read(
                "service direct prepare",
                "install_prepare",
                lambda: service.prepare_install(
                    InstallRequest(
                        identifier=repository_url, packagePath="packages/laptop-support"
                    ),
                    actor="alice",
                ),
                operation_subject=direct,
            )
            prepared = read(
                "service install prepare",
                "install_prepare",
                lambda: service.prepare_install(
                    InstallRequest(identifier="company/laptop-support"), actor="alice"
                ),
            )
            mutation(
                "service install confirm",
                "install_confirm",
                lambda: service.confirm_install(
                    prepared.review_token.confirmation_token, actor="alice"
                ),
            )
            state("service installed untrusted")
            read(
                "service update check",
                "update_check",
                lambda: service.check_updates(identity),
            )
            service.remove_source("company")
            read(
                "service orphaned update check",
                "update_check",
                lambda: service.check_updates(identity),
            )
            service.add_source(
                WorkflowMarketplaceSource(name="company", repositoryUrl=repository_url)
            )
            # A registered-source check reads its catalog. Exercise the fetch
            # failure with an actually installed direct identity instead, in an
            # isolated profile so its membership cannot affect later cases.
            direct_service = WorkflowMarketplaceService(
                root / "direct-profile", profile="support", clock=lambda: NOW
            )
            direct_review = direct_service.prepare_install(
                InstallRequest(
                    identifier=repository_url, packagePath="packages/laptop-support"
                ),
                actor="alice",
            )
            direct_installed = direct_service.confirm_install(
                direct_review.confirmation_token, actor="alice"
            )
            direct_identity = direct_installed.identity
            direct_subject = wire.PackageSubject(
                type="package",
                identity=wire.PackageIdentity.model_validate(
                    direct_identity.model_dump(mode="json", by_alias=False)
                ),
            )
            with patch.object(
                direct_service.catalog.git_fetcher,
                "fetch",
                side_effect=WorkflowMarketplaceError(
                    "source_unavailable", "Fixture repository unavailable."
                ),
            ):
                record(
                    "service failed update check",
                    "update_check",
                    complete_read(
                        direct_service,
                        kind="update_check",
                        subject=direct_subject,
                        selection=None,
                        actor="alice",
                        call=lambda: direct_service.check_updates(direct_identity),
                    ),
                    direct_subject,
                )
            read(
                "service all update checks",
                "update_check",
                lambda: service.check_updates(),
                operation_subject=wire.AllPackagesSubject(type="all_packages"),
            )
            read(
                "service unchanged update",
                "update_prepare",
                lambda: service.prepare_update(identity, actor="alice"),
            )
            review = read(
                "service review one A",
                "trust_prepare",
                lambda: service.review_trust(
                    identity, actor="alice", workflow_name="A"
                ),
                one,
            )
            mutation(
                "service grant one A",
                "trust_confirm",
                lambda: service.grant_trust(
                    review.review_token.confirmation_token, actor="alice"
                ),
                one,
            )
            state("service installed A trusted")
            review = read(
                "service review all",
                "trust_prepare",
                lambda: service.review_trust(identity, actor="alice"),
                all_selection,
            )
            mutation(
                "service grant all",
                "trust_confirm",
                lambda: service.grant_trust(
                    review.review_token.confirmation_token, actor="alice"
                ),
                all_selection,
            )
            mutation(
                "service revoke one A",
                "trust_revoke",
                lambda: service.revoke_trust(identity, workflow_name="A"),
                one,
            )
            _write_package(
                repo.work,
                "laptop-support",
                version="2.0.0",
                workflow_names=("A", "B"),
                marker="v2",
            )
            repo.publish("publish v2")
            service.refresh_source("company")
            available_check = complete_read(
                service,
                kind="update_check",
                subject=subject,
                selection=None,
                actor="alice",
                call=lambda: service.check_updates(identity),
            )
            review = read(
                "service update prepare",
                "update_prepare",
                lambda: service.prepare_update(identity, actor="alice"),
            )
            mutation(
                "service update confirm",
                "update_confirm",
                lambda: service.confirm_update(
                    review.review_token.confirmation_token, actor="alice"
                ),
            )
            review = read(
                "service remove prepare",
                "remove_prepare",
                lambda: service.prepare_remove(identity, actor="alice"),
            )
            mutation(
                "service remove confirm",
                "remove_confirm",
                lambda: service.confirm_remove(
                    review.review_token.confirmation_token, actor="alice"
                ),
            )
            review = service.prepare_install(
                InstallRequest(identifier="company/laptop-support"), actor="alice"
            )
            service.confirm_install(review.confirmation_token, actor="alice")
            _write_package(
                repo.work,
                "laptop-support",
                version="3.0.0",
                workflow_names=("A", "B"),
                marker="v3",
            )
            repo.publish("publish v3")
            review = service.prepare_update(identity, actor="alice")
            original = service.transactions.atomic_install

            def rollback_fault(point):
                if point == "after_candidate_swap":
                    raise OSError("fixture after swap unavailable")

            with patch.object(
                service.transactions,
                "atomic_install",
                lambda *args, **kwargs: original(*args, **kwargs, fault=rollback_fault),
            ):
                mutation(
                    "service verified rollback",
                    "update_confirm",
                    lambda: service.confirm_update(
                        review.confirmation_token, actor="alice"
                    ),
                )
            review = service.prepare_update(identity, actor="alice")

            def fault(point):
                if point == "after_trust_revoke":
                    raise OSError("fixture cleanup unavailable")

            with patch.object(
                service.transactions,
                "atomic_install",
                lambda *args, **kwargs: original(*args, **kwargs, fault=fault),
            ):
                mutation(
                    "service rollback failed",
                    "update_confirm",
                    lambda: service.confirm_update(
                        review.confirmation_token, actor="alice"
                    ),
                )
            state("service recovery required")
            read(
                "service read-only failure",
                "inspect",
                lambda: service.inspect("company/missing-package"),
            )
            service.set_source_enabled("company", False)
            record(
                "service disabled refresh",
                "refresh",
                complete_source_refresh(
                    service,
                    source_name="company",
                    call=lambda: service.refresh_source("company"),
                ),
                source,
            )
            service.set_source_enabled("company", True)

            # Real registry admission, mixed V1/V2 observation, immutable snapshot
            # pagination and full-result eviction. Only clock/random IDs are fixed.
            registry_ids = iter(range(2000, 2020))
            # This registry is memory-only. Its fixed synthetic home identity
            # stabilizes the public hash prefix without touching that path.
            registry_home = "/fixture-profile/support"
            admissions = LifecycleAdmissionStore(
                profile_key=registry_home,
                epoch=EPOCH,
                clock=lambda: NOW,
                random_hex=lambda: f"{next(registry_ids):032x}",
            )
            registry = WorkflowMarketplaceOperationRegistry(
                profile_key=registry_home,
                profile="support",
                clock=lambda: NOW,
                max_terminal=2,
                admissions=admissions,
            )

            def admitted(number, legacy=False):
                def work(_cancellation):
                    completion = complete_source_refresh(
                        service,
                        source_name="company",
                        call=lambda: service.refresh_source("company"),
                    )
                    return (
                        _legacy_completion(completion)
                        if legacy
                        else _public_completion(completion)
                    )

                keywords = (
                    {}
                    if legacy
                    else {
                        "request_id": f"wmreq_{EPOCH}_{int(NOW.timestamp() * 1000):013d}_{number:032x}",
                        "canonical_body": {},
                    }
                )
                current = registry.start(
                    "refresh",
                    work,
                    actor="alice",
                    subject=source,
                    target="source:company",
                    **keywords,
                )
                deadline = time.monotonic() + 10
                while True:
                    current = registry.get_lifecycle(current.id, actor="alice")
                    if current.state not in {"pending", "running"}:
                        assert current.state == "succeeded"
                        return current
                    assert time.monotonic() < deadline, (
                        "fixture operation did not complete"
                    )
                    time.sleep(0.001)

            try:
                with patch(
                    "plugins.workflow.marketplace.operations.secrets.token_hex",
                    return_value="f" * 32,
                ):
                    first = admitted(1000)
                    legacy = admitted(1001, legacy=True)
                    assert {
                        item.id
                        for item in registry.list_legacy(
                            actor="alice", offset=0, limit=100
                        )
                    } == {legacy.id}
                    envelopes["admissionFound"] = registry.lookup_admission(
                        first.request_id, actor="alice"
                    ).model_dump(mode="json")
                    page = registry.list_snapshot(actor="alice", limit=1)
                    assert not page.complete and page.next_cursor
                    envelopes["operationPage"] = page.model_dump(mode="json")
                    envelopes["operationPageFinal"] = registry.list_snapshot(
                        actor="alice", limit=1, cursor=page.next_cursor
                    ).model_dump(mode="json")
                    envelopes["legacyOperation"] = legacy.model_dump(mode="json")
                    admitted(1002)
                    envelopes["admissionEvicted"] = registry.lookup_admission(
                        first.request_id, actor="alice"
                    ).model_dump(mode="json")
                    assert envelopes["admissionEvicted"]["state"] == "evicted"
            finally:
                registry.close()

            # Same real-publication fault boundary exercised by the lifecycle
            # API recovery test: the candidate exists, but its recovery record
            # is unreadable. Never manufacture a successful/rollback outcome.
            ambiguous_service = WorkflowMarketplaceService(
                root / "ambiguous-profile", profile="support", clock=lambda: NOW
            )
            ambiguous_service.add_source(
                WorkflowMarketplaceSource(
                    name="company",
                    repositoryUrl="https://fixtures.example/workflows.git",
                )
            )
            ambiguous_review = ambiguous_service.prepare_install(
                InstallRequest(identifier="company/laptop-support"), actor="alice"
            )

            def ambiguous_publication():
                installed = ambiguous_service.confirm_install(
                    ambiguous_review.confirmation_token, actor="alice"
                )
                assert installed.version == "3.0.0"
                ambiguous_service.transactions.journal_path.write_text(
                    "{incomplete", encoding="utf-8"
                )
                raise WorkflowMarketplaceError(
                    "transaction_recovery_ambiguous", "private-recovery-location"
                )

            record(
                "service recovery ambiguous",
                "install_confirm",
                complete_mutation(
                    ambiguous_service,
                    kind="install_confirm",
                    subject=subject,
                    selection=None,
                    actor="alice",
                    call=ambiguous_publication,
                ),
            )
            assert ambiguous_service.installed_packages()[0].version == "3.0.0"
            ambiguous_state = read_package_state(ambiguous_service, identity)
            assert ambiguous_state.state == "unconfirmed"
            assert ambiguous_state.installed is None and ambiguous_state.trust is None
            states.append(
                _case(
                    "service ambiguous state",
                    _sanitize_result(
                        ambiguous_state.model_dump(mode="json"),
                        allow_confirmation_token=False,
                    ),
                    wire.PackageState,
                )
            )
            # Append the captured immutable result without renumbering the
            # existing operation identities used by cross-language consumers.
            record("service available update check", "update_check", available_check)
            states.append(
                _case(
                    "service direct installed",
                    _sanitize_result(
                        read_package_state(direct_service, direct_identity).model_dump(
                            mode="json"
                        ),
                        allow_confirmation_token=False,
                    ),
                    wire.PackageState,
                )
            )

    # Relationships are derived from complete genuine projections. Python decides
    # acceptance; no expected-valid flag bypasses its actual validators.
    genuine = list(operations)
    for case in genuine:
        for name, changes in (
            (
                "pending",
                dict(
                    state="pending",
                    phase="queued",
                    progress=0,
                    started_at=None,
                    finished_at=None,
                    result=None,
                    error=None,
                    outcome=None,
                ),
            ),
            (
                "running",
                dict(
                    state="running",
                    phase="running",
                    progress=50,
                    finished_at=None,
                    result=None,
                    error=None,
                    outcome=None,
                ),
            ),
            (
                "cancelled",
                dict(
                    state="cancelled",
                    phase="cancelled",
                    progress=0,
                    result=None,
                    error=None,
                    outcome={"type": "cancelled_before_commit", "package_state": None},
                ),
            ),
        ):
            value = deepcopy(case["value"])
            value.update(changes)
            operations.append(
                _case(f"{case['name']} {name}", value, wire.LifecycleOperation)
            )
        for name, field, replacement in (
            (
                "wrong kind",
                "kind",
                "refresh" if case["value"]["kind"] != "refresh" else "inspect",
            ),
            ("wrong subject", "subject", {"type": "all_packages"}),
            ("wrong selection", "selection", {"type": "one", "workflow_name": "B"}),
            ("wrong epoch", "registry_epoch", "d" * 32),
            ("boolean progress", "progress", True),
            ("unknown key", "unexpected", "extra"),
            ("wrong phase", "phase", "queued"),
            ("noncanonical UTC", "updated_at", "2026-09-05T12:00:00+00:00"),
            ("reversed time", "created_at", "2026-09-06T12:00:00Z"),
            ("failed committed", "state", "failed"),
        ):
            value = deepcopy(case["value"])
            value[field] = replacement
            operations.append(
                _case(f"{case['name']} {name}", value, wire.LifecycleOperation)
            )
    selected = next(
        item["value"] for item in genuine if item["name"] == "service grant one A"
    )
    for name, change in (
        ("missing B", lambda value: value["result"]["value"]["workflows"].pop()),
        (
            "duplicate A",
            lambda value: value["result"]["value"]["workflows"].append(
                deepcopy(value["result"]["value"]["workflows"][0])
            ),
        ),
        (
            "selected A untrusted",
            lambda value: value["result"]["value"]["workflows"][0].update(
                state="untrusted"
            ),
        ),
        (
            "wrong digest",
            lambda value: value["result"]["value"].update(distribution_digest="0" * 64),
        ),
        (
            "wrong outcome profile",
            lambda value: value["outcome"]["package_state"].update(profile="other"),
        ),
    ):
        value = deepcopy(selected)
        change(value)
        operations.append(_case(name, value, wire.LifecycleOperation))
    for case in genuine:
        for phase in (
            "running",
            "fetching",
            "reviewing",
            "validating",
            "committing",
            "recovering",
        ):
            value = deepcopy(case["value"])
            value.update(
                state="running",
                phase=phase,
                progress=1,
                finished_at=None,
                result=None,
                error=None,
                outcome=None,
            )
            operations.append(
                _case(f"{case['name']} phase {phase}", value, wire.LifecycleOperation)
            )
        if case["value"]["result"]:
            value = deepcopy(case["value"])
            value["result"]["value"]["unexpected_nested"] = True
            operations.append(
                _case(
                    f"{case['name']} extra result key", value, wire.LifecycleOperation
                )
            )

    # Exercise Python-specific scalar domains against complete service output.
    installed_case = next(
        item["value"] for item in genuine if item["name"] == "service install confirm"
    )
    for field, replacements in {
        "actor": [" alice", "alice\x00", "alice\ninside", "\ufeffalice"],
        "configured_ref": [" main", "main\x00", "main\ninside"],
        "installed_at": [
            "2026-09-05T12:00:00.000001Z",
            "2026-09-05T12:00:00.000000Z",
            "2026-02-30T12:00:00Z",
        ],
        "package_path": [
            "../escape",
            "packages/.git/item",
            "packages/e\u0301",
            "packages/é",
            "packages/line\ninside",
            "packages/ab:cd",
            "packages/:cd",
            "packages/12:34",
            "packages/普通:文件",
            "packages/ab::cd",
            "packages/a:b",
        ],
        "repository_url": [
            "https://example.test/public.git",
            "file:///fixture-example/public.git",
            "http://example.test/public.git",
            "owner/public",
        ],
        "workflow_paths": [
            ["workflows/ß.yaml", "workflows/ss.yaml"],
            ["workflows/A.yaml", "workflows/B.yaml"],
        ],
    }.items():
        for index, replacement in enumerate(replacements):
            value = deepcopy(installed_case)
            value["result"]["value"][field] = replacement
            value["outcome"]["package_state"]["installed"][field] = replacement
            operations.append(
                _case(
                    f"installed scalar {field} {index}", value, wire.LifecycleOperation
                )
            )
    for case in list(states):
        for name, field, replacement in (
            ("boolean busy", "busy", 1),
            ("wrong state", "state", "installed"),
            ("wrong recovery", "recovery", "required"),
        ):
            value = deepcopy(case["value"])
            value[field] = replacement
            states.append(_case(f"{case['name']} {name}", value, wire.PackageState))
    inspection = next(
        item["value"] for item in genuine if item["name"] == "service inspect"
    )
    for extra in (0, 1):
        value = deepcopy(inspection)
        diagnostics = [
            {
                "code": f"fixture_budget_{index:03d}",
                "message": "x" * 4096,
                "severity": "advisory",
            }
            for index in range(512)
        ]
        value["result"]["value"]["advisories"] = diagnostics
        rendered = json.dumps(
            value["result"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        shrink = len(rendered) - (2 * 1024 * 1024 + extra)
        for item in reversed(diagnostics):
            take = min(shrink, len(item["message"]) - 1)
            item["message"] = item["message"][: len(item["message"]) - take]
            shrink -= take
            if not shrink:
                break
        assert shrink == 0
        operations.append(
            _case(
                f"result UTF-8 budget boundary plus {extra}",
                value,
                wire.LifecycleOperation,
            )
        )
    capabilities = _Capabilities(
        schema_version=2,
        profile="support",
        registry_epoch=EPOCH,
        principal_binding="b" * 64,
        server_time=UTC,
        capabilities=list(wire.LIFECYCLE_CAPABILITIES),
    ).model_dump(mode="json")
    for prefix in ("\ufeff", ""):
        for repository in (
            "https://fixtures.example/public.git",
            "http://fixtures.example/public.git",
            "file:///fixture-example/public.git",
            "owner/public",
            "https://fixtures.example/a/../public.git",
            "https://fixtures.example/public.git#path",
            "git@fixtures.example:public.git",
            "ssh://git@fixtures.example/public.git",
            "ssh://developer@fixtures.example/public.git",
            "https://fixtures.example/OPENAI_API_KEY=fixture-example",
            "https://fixtures.example/Authorization: Basic fixture-example",
            "https://fixtures.example/SECRETARY=fixture-example",
            "https://fixtures.example/KEY=***",
            "https://fixtures.example]/public.git",
            "https://fixtures.example\uff1aport/public.git",
            "https://fixtures.example/public.git?token=",
            "ftp://fixtures.example/public.git",
            "https://example.test/repo.git?%fftoken=x",
            "https://example.test/repo.git?%ff%74oken=x",
            "https://example.test/repo.git?%FFapi%4Bey=x",
            "https://example.test/repo.git?%E2%82token=x",
            "https://example.test/repo.git?%ffbranch=x",
            "https://example.test/repo.git?%E2%82branch=x",
            "https://example.test/repo.git?%25ff%2574oken=x",
            "https://example.test/repo.git?%25252574oken=x",
            "https://example.test/repo.git?%61pisecret=x",
            "file:///fixture/repo.git?",
            "file:///fixture/repo.git?#fragment",
            "file:///fixture/repo.git?branch=main",
            "ssh://git@example.test/repo.git?",
            "ssh://git@example.test/repo.git?branch=main",
            "https://[v1.host]/repo.git",
            "https://[V1.host]/repo.git",
            "https://[v1.]/repo.git",
            "https://[::1]/repo.git",
            "https://[fe80::1%scope]/repo.git",
            "https://[::1]:nonnumeric/repo.git",
            "https://[192.0.2.1]/repo.git",
            "https://before[::1]/repo.git",
            "https://[::1]after/repo.git",
            "https://[1:2:3:4:5:6:7:8]/repo.git",
            "https://[1:2:3:4:5:6:7]/repo.git",
            "https://[1:2:3:4:5:6:7:8:9]/repo.git",
            "https://[1:2:3:4:5:6:7::]/repo.git",
            "https://[1:2:3:4:5:6:7:8::]/repo.git",
            "https://[1::2::3]/repo.git",
            "https://[:::]/repo.git",
            "https://[:1:2:3:4:5:6:7]/repo.git",
            "https://[1:2:3:4:5:6:7:]/repo.git",
            "https://[::ffff:192.0.2.1]/repo.git",
            "https://[::ffff:192.0.02.1]/repo.git",
            "https://[::ffff:256.0.2.1]/repo.git",
            "https://[fe80::1%]/repo.git",
            "https://[fe80::1%scope%extra]/repo.git",
            "https://[vF.host]/repo.git",
            "https://[vg.host]/repo.git",
            "https://[::1/repo.git",
            "https://example.test\uff0fpath/repo.git",
            "https://example.test\uff20host/repo.git",
            "https://example.test/repo.git?%EF%BB%BFtoken=x",
            "https://example.test/repo.git?%C0%AFtoken=x",
            "https://example.test/repo.git?%ED%A0%80token=x",
            "https://example.test/repo.git?%zzbranch=x",
        ):
            value = deepcopy(installed_case)

            def replace_repository(obj):
                if isinstance(obj, dict):
                    for key, item in obj.items():
                        if key == "repository_url":
                            obj[key] = prefix + repository
                        else:
                            replace_repository(item)
                elif isinstance(obj, list):
                    for item in obj:
                        replace_repository(item)

            replace_repository(value)
            operations.append(
                _case(
                    f"repository {'FEFF' if prefix else 'plain'} {repository}",
                    value,
                    wire.LifecycleOperation,
                )
            )
    for code in list(range(32)) + list(range(127, 160)) + [0x2028, 0x2029, 0xFEFF]:
        value = deepcopy(installed_case)
        value["result"]["value"]["package_path"] = "packages/a" + chr(code) + "b"
        value["outcome"]["package_state"]["installed"]["package_path"] = value[
            "result"
        ]["value"]["package_path"]
        operations.append(
            _case(f"path code point {code}", value, wire.LifecycleOperation)
        )
    preparation = next(
        case["value"] for case in genuine if case["name"] == "service review one A"
    )
    token_metadata = {
        "operation_id": preparation["id"],
        "request_id": preparation["request_id"],
        "subject": preparation["subject"],
        "selection": preparation["selection"],
        "review_digest": preparation["result"]["value"]["review_digest"],
        "expires_at": preparation["result"]["value"]["expires_at"],
    }
    token_cases = []

    def token_case(name, metadata, length=32, character="a"):
        # Endpoint-only material is constructed transiently for the Python oracle;
        # artifacts contain only a recipe and public preparation metadata.
        case = _case(
            name,
            {**metadata, "confirmation_token": character * length},
            ReviewTokenResponse,
        )
        token_cases.append({
            "name": name,
            "value": metadata,
            "length": length,
            "character": character,
            "accepted": case["accepted"],
        })

    for length in (0, 31, 32, 256, 257):
        token_case(f"token length {length}", token_metadata, length)
    token_case("token alphabet", token_metadata, character="!")
    for field in token_metadata:
        metadata = deepcopy(token_metadata)
        del metadata[field]
        token_case("token missing " + field, metadata)
        token_case("token malformed " + field, {**token_metadata, field: False})
    lifecycle_v2 = {
        **envelopes,
        "domains": generate_domains(),
        "httpErrorCodes": sorted(wire.LIFECYCLE_HTTP_ERROR_CODES),
        "outerEnvelopeCases": outer_cases(capabilities, envelopes),
        "tokenEndpointCases": token_cases,
        "operationCases": operations,
        "packageStateCases": states,
        "validOperationB": genuine[1]["value"],
        "operationAId": genuine[0]["value"]["id"],
        "validOperationA": genuine[0]["value"],
        "capabilities": capabilities,
    }
    return lifecycle_v2, inspection_v1


def generate_corpus():
    return generate_corpora()[0]


def render(value):
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def generate_domains():
    path_schema = TypeAdapter(wire.LifecycleRelativePath).json_schema()
    forbidden = []
    # Probe only the authority's character-wide control/separator domain here.
    # Drive prefixes and backslashes are separate structural path constraints;
    # rejection of a:b must never imply rejection of ab:cd.
    for code in [
        code for code in range(160) if unicodedata.category(chr(code)) == "Cc"
    ] + [0x2028, 0x2029]:
        try:
            wire.lifecycle_relative_path("a" + chr(code) + "b")
        except ValueError:
            forbidden.append(code)
    return {
        "clean_text": {
            "stripCodePoints": [
                code for code in range(sys.maxunicode + 1) if chr(code).isspace()
            ]
        },
        "casefold": {"unicodeVersion": unicodedata.unidata_version},
        "lifecycle_relative_path": {
            "minLength": path_schema["minLength"],
            "maxLength": path_schema["maxLength"],
            "forbiddenCodePoints": forbidden,
            "normalization": "NFC",
        },
        "repository_identity": {
            "authority": "validate_credential_free_git_source",
            "credentialWords": sorted(git_source._CREDENTIAL_PARAMETER_WORDS),
            "credentialQualifiers": list(git_source._CREDENTIAL_COMPOUND_QUALIFIERS),
            "credentialSuffixes": list(git_source._CREDENTIAL_COMPOUND_SUFFIXES),
            "credentialPrefixes": [redact._PREFIX_RE.pattern],
            "credentialSplitControls": redact._CONTROL_CHARS_RE.pattern,
            "credentialRedactionPatterns": [
                {
                    "pattern": pattern.pattern,
                    "flags": "i" if pattern.flags & re.IGNORECASE else "",
                }
                for pattern in (
                    redact._URL_BARE_TOKEN_RE,
                    redact._JWT_RE,
                    redact._TELEGRAM_RE,
                    redact._PRIVATE_KEY_RE,
                    redact._DB_CONNSTR_RE,
                    redact._SIGNAL_PHONE_RE,
                )
            ],
            "credentialAuthority": git_source._USER_PASSWORD_AUTHORITY_RE.pattern,
            "secretKeywordPattern": redact._KEY_KEYWORD_RE.pattern,
            "programLookupPattern": redact._ENV_LOOKUP_VALUE_RE.pattern,
            "redactionFields": [
                {
                    "pattern": pattern.pattern.replace("++", "+").replace("*+", "*"),
                    "flags": ("i" if pattern.flags & re.IGNORECASE else "")
                    + ("m" if pattern.flags & re.MULTILINE else ""),
                    "kind": kind,
                    "skipUrls": skip_urls,
                }
                for pattern, kind, skip_urls in (
                    (redact._ENV_ASSIGN_RE, "assignment", False),
                    (redact._ENV_ASSIGN_LOWER_RE, "assignment", True),
                    (redact._CFG_DOTTED_RE, "assignment", True),
                    (redact._CFG_ANCHORED_RE, "assignment", True),
                    (redact._YAML_ASSIGN_RE, "yaml", True),
                    (redact._JSON_FIELD_RE, "json", False),
                    (redact._AUTH_HEADER_RE, "authorization", False),
                    (redact._SECRET_HEADER_RE, "header", False),
                )
            ],
        },
    }


def outer_cases(capabilities, envelopes):
    cases = []

    def add(name, value, model):
        cases.append({**_case(name, value, model), "model": model.__name__})

    for binding in ("", "b" * 63, "b" * 65, "B" * 64, "b" * 64 + "\n", "g" * 64):
        add(
            "principal binding domain " + repr(binding),
            {**capabilities, "principal_binding": binding},
            wire.LifecycleCapabilities,
        )

    for model, original in (
        (wire.LifecycleCapabilities, capabilities),
        (AdmissionFound, envelopes["admissionFound"]),
        (AdmissionEvicted, envelopes["admissionEvicted"]),
        (LifecycleOperationPage, envelopes["operationPage"]),
    ):
        add(model.__name__ + " valid", original, model)
        for field in original:
            value = deepcopy(original)
            del value[field]
            add(model.__name__ + " missing " + field, value, model)
            value = deepcopy(original)
            value[field] = 1 if isinstance(original[field], bool) else False
            add(model.__name__ + " wrong scalar " + field, value, model)
        add(model.__name__ + " extra", {**original, "unexpected": True}, model)
    for values in (
        [],
        ["operations", "trust"],
        ["trust", "operations"],
        ["trust", "trust"],
        ["unknown"],
    ):
        add(
            "capability sequence " + repr(values),
            {**capabilities, "capabilities": values},
            wire.LifecycleCapabilities,
        )
    for field, replacements in {
        "profile": ["\ufeffsupport", " support", "x" * 257, ""],
        "registry_epoch": ["e" * 31, "E" * 32],
        "server_time": ["2026-09-05T12:00:00.000000Z", "2026-02-30T12:00:00Z"],
    }.items():
        for index, value in enumerate(replacements):
            add(
                f"capabilities {field} {index}",
                {**capabilities, field: value},
                wire.LifecycleCapabilities,
            )
    original = envelopes["admissionEvicted"]
    for field, value in {
        "registry_epoch": "d" * 32,
        "kind": "trust_prepare",
        "selection": {"type": "all"},
        "subject": {"type": "all_packages"},
        "profile": "\ufeffsupport",
    }.items():
        add(
            "evicted correlation " + field, {**original, field: value}, AdmissionEvicted
        )
    item = envelopes["admissionFound"]["operation"]
    for size in (0, 1, 100, 101):
        items = [
            {
                **item,
                "id": f"wmop_aaaaaaaaaaaa_{index:032x}",
                "request_id": f"wmreq_{EPOCH}_1788609600000_{index:032x}",
            }
            for index in range(size)
        ]
        add(
            f"page size {size}",
            {"items": items, "complete": True, "next_cursor": None},
            LifecycleOperationPage,
        )
    for name, items, complete, cursor in (
        ("incomplete empty", [], False, "e" * 32),
        ("duplicate operation", [item, item], True, None),
        (
            "duplicate request",
            [item, {**item, "id": "wmop_aaaaaaaaaaaa_" + "a" * 32}],
            True,
            None,
        ),
        (
            "mixed profile",
            [
                item,
                {
                    **item,
                    "id": "wmop_aaaaaaaaaaaa_" + "a" * 32,
                    "request_id": f"wmreq_{EPOCH}_1788609600000_" + "a" * 32,
                    "profile": "other",
                },
            ],
            True,
            None,
        ),
        ("complete cursor", [item], True, "e" * 32),
        ("invalid cursor", [item], False, "E" * 32),
    ):
        add(
            "page " + name,
            {"items": items, "complete": complete, "next_cursor": cursor},
            LifecycleOperationPage,
        )
    return cases


def generate_types():
    schemas = {}
    for model in (
        wire.LifecycleOperation,
        wire.PackageState,
        _Capabilities,
        AdmissionFound,
        AdmissionEvicted,
        LifecycleOperationPage,
        ReviewTokenResponse,
        _EmptyBody,
        _InstallBody,
        _IdentityBody,
        _CheckBody,
        _TrustBody,
        _ConfirmBody,
    ):
        schema = model.model_json_schema(by_alias=False)
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema

    def ts(schema):
        if "$ref" in schema:
            return schema["$ref"].split("/")[-1]
        if "const" in schema:
            return json.dumps(schema["const"])
        if "enum" in schema:
            return " | ".join(json.dumps(value) for value in schema["enum"])
        if "anyOf" in schema or "oneOf" in schema:
            return " | ".join(
                ts(value) for value in schema.get("anyOf", schema.get("oneOf"))
            )
        kind = schema.get("type")
        if kind == "object":
            return (
                "{\n"
                + "\n".join(
                    f"  {key}: {ts(value)}"
                    for key, value in schema["properties"].items()
                )
                + "\n}"
            )
        if kind == "array":
            return f"Array<{ts(schema['items'])}>"
        return {
            "string": "string",
            "integer": "number",
            "number": "number",
            "boolean": "boolean",
            "null": "null",
        }[kind]

    def structural(value):
        if isinstance(value, list):
            return [structural(item) for item in value]
        if isinstance(value, dict):
            return {
                key: (
                    {name: structural(field) for name, field in item.items()}
                    if key == "properties"
                    else structural(item)
                )
                for key, item in value.items()
                if key not in {"title", "description", "discriminator"}
            }
        return value

    lines = [
        "// Generated from accepted Python lifecycle models. Do not edit.",
        "// Regenerate with scripts/generate_workflow_marketplace_lifecycle_fixtures.py --write.",
    ]
    for name, schema in sorted(schemas.items()):
        type_name = "LifecycleOperationWire" if name == "LifecycleOperation" else name
        lines.append(f"export type {type_name} = {ts(schema)}\n")
    lines.append("export type LifecycleCapabilities = _Capabilities\n")
    lines.append(
        "export const lifecycleDomains = "
        + json.dumps(generate_domains(), ensure_ascii=True, sort_keys=True)
        + " as const\n"
    )
    lines.append(
        "export const lifecycleHttpErrorCodes = "
        + json.dumps(sorted(wire.LIFECYCLE_HTTP_ERROR_CODES))
        + " as const\n"
    )
    lines.append(
        "export type LifecycleHttpErrorCode = typeof lifecycleHttpErrorCodes[number]\n"
    )
    lines.append(
        "export const lifecycleCapabilityOrder = "
        + json.dumps(list(wire.LIFECYCLE_CAPABILITIES))
        + " as const\n"
    )
    lines.append(
        "export const lifecycleSchemas = "
        + json.dumps(structural(schemas), ensure_ascii=False, sort_keys=True)
        + " as const\n"
    )
    lines.append(
        "export const lifecycleRules = "
        + json.dumps(
            {
                "results": dict(wire.RESULT_TYPE_BY_KIND),
                "subjects": {
                    key: sorted(value)
                    for key, value in wire.SUBJECT_TYPES_BY_KIND.items()
                },
                "phases": {
                    key: sorted(value)
                    for key, value in wire.RUNNING_PHASES_BY_KIND.items()
                },
            },
            sort_keys=True,
        )
        + " as const\n"
    )
    lines.append(
        "export const lifecycleCaseFold = "
        + json.dumps(
            {
                chr(code): chr(code).casefold()
                for code in range(sys.maxunicode + 1)
                if chr(code).casefold() != chr(code)
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        + " as const\n"
    )
    lines.append("""
export type LifecycleKind = LifecycleOperationWire['kind']
type OperationCommon<K extends LifecycleKind> = Omit<LifecycleOperationWire, 'kind' | 'subject' | 'selection' | 'state' | 'phase' | 'progress' | 'result' | 'error' | 'outcome'> & {
  kind: K
  subject: Extract<LifecycleOperationWire['subject'], { type: typeof lifecycleRules.subjects[K][number] }>
  selection: K extends `trust_${string}` ? NonNullable<LifecycleOperationWire['selection']> : null
}
type OperationState<K extends LifecycleKind> =
  | { state: 'pending'; phase: 'queued'; progress: 0; started_at: null; finished_at: null; result: null; error: null; outcome: null }
  | { state: 'running'; phase: typeof lifecycleRules.phases[K][number]; progress: number; started_at: string; finished_at: null; result: null; error: null; outcome: null }
  | { state: 'succeeded'; phase: 'completed'; progress: 100; started_at: string; finished_at: string; result: Extract<NonNullable<LifecycleOperationWire['result']>, { type: typeof lifecycleRules.results[K] }>; error: null; outcome: NonNullable<LifecycleOperationWire['outcome']> }
  | { state: 'failed'; phase: 'failed'; progress: number; finished_at: string; result: null; error: LifecyclePublicError; outcome: Exclude<NonNullable<LifecycleOperationWire['outcome']>, CommittedOutcome | CancelledBeforeCommitOutcome> }
  | { state: 'cancelled'; phase: 'cancelled'; progress: number; finished_at: string; result: null; error: null; outcome: CancelledBeforeCommitOutcome }
export type LifecycleOperation = { [K in LifecycleKind]: OperationCommon<K> & OperationState<K> }[LifecycleKind]
""")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    # Formatting is performed through the existing Desktop Prettier, never a
    # separate dependency installation or a handwritten generated-file rewrite.
    import subprocess

    types = subprocess.run(
        [str(ROOT / "node_modules/.bin/prettier"), "--stdin-filepath", str(TYPES)],
        input=generate_types(),
        text=True,
        capture_output=True,
        check=True,
        cwd=ROOT / "apps/desktop",
    ).stdout
    lifecycle_v2, inspection_v1 = generate_corpora()
    for path, content in (
        (CORPUS, render(lifecycle_v2)),
        (INSPECTION_V1, render(inspection_v1)),
        (TYPES, types),
    ):
        if args.write:
            path.write_text(content, encoding="utf-8")
        elif not path.exists() or path.read_text(encoding="utf-8") != content:
            parser.exit(
                1,
                f"Lifecycle generated artifact drift: {path.name}; run generator --write.\n",
            )
    print(
        "Lifecycle fixture corpus verified."
        if args.check
        else "Lifecycle fixture corpus written."
    )


if __name__ == "__main__":
    main()
