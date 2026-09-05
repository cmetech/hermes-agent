from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from plugins.workflow.marketplace.lifecycle_models import (
    LifecycleOperation,
    PackageState,
)
from plugins.workflow.marketplace.models import (
    ExternalRequirements,
    FileDigestChange,
    InstallReview,
    InstalledPackage,
    InstalledPackageIdentity,
    PackageDiagnostic,
    PackageInspection,
    PackageInspectionResource,
    PackageReviewAssessment,
    RemoveReview,
    RequirementChanges,
    StringSetChange,
    TrustReview,
    UpdateCheck,
    UpdateReview,
    WorkflowCompatibilityChanges,
    WorkflowRiskChanges,
    WorkflowTrustReviewItem,
)


_DIGEST = "a" * 64
_RISK_DIGEST = "b" * 64
_REVIEW_DIGEST = "c" * 64
_COMMIT = "d" * 40
_NOW = "2026-09-04T12:00:00Z"
_STARTED = "2026-09-04T12:00:01Z"
_FINISHED = "2026-09-04T12:00:02Z"
_EPOCH = "e" * 32
_REQUEST_ID = f"wmreq_{_EPOCH}_1788523200000_{'f' * 32}"


def _identity(
    *, source_key: str = "company", package_id: str = "laptop-support"
) -> InstalledPackageIdentity:
    return InstalledPackageIdentity(sourceKey=source_key, packageId=package_id)


def _requirements() -> ExternalRequirements:
    return ExternalRequirements(
        runtimes=[], tools=[], providers=[], services=[], secrets=[]
    )


def _workflow_review(
    *,
    workflow_name: str = "diagnostic",
    definition_path: str = "workflows/diagnostic.yaml",
) -> WorkflowTrustReviewItem:
    return WorkflowTrustReviewItem(
        workflowName=workflow_name,
        definitionPath=definition_path,
        companionPath=None,
        packageDigest=_DIGEST,
        riskDigest=_RISK_DIGEST,
        trustState="untrusted",
        shellOrScriptNodes=[],
        commandNodes=[],
        approvalNodes=[],
        commandResources=[],
        scriptResources=[],
        mcpResources=[],
        mcpResourceFiles=[],
        requestedTools=[],
        requestedSkills=[],
        localMcpServers=[],
        remoteMcpServers=[],
        providers=[],
        outwardActionNodes=[],
        requiredSecrets=[],
        externalRequirements=_requirements(),
        packageResourceSet="package",
        compatibility=[],
    )


def _assessment() -> PackageReviewAssessment:
    return PackageReviewAssessment(
        packageDigest=_DIGEST,
        reviewDigest=_REVIEW_DIGEST,
        workflowNames=["diagnostic"],
        blockers=[],
        advisories=[],
        externalRequirements=_requirements(),
        packageResources=["workflows/diagnostic.yaml"],
    )


def _installed(
    *, identity: InstalledPackageIdentity | None = None, version: str = "1.0.0"
) -> InstalledPackage:
    identity = identity or _identity()
    return InstalledPackage(
        identity=identity,
        sourceName=identity.source_key,
        repositoryUrl="https://example.test/workflows.git",
        configuredRef="main",
        resolvedCommit=_COMMIT,
        packagePath=f"packages/{identity.package_id}",
        version=version,
        contractVersion=1,
        distributionDigest=_DIGEST,
        installedAt=_NOW,
        actor="marketplace:operator",
        workflowPaths=["workflows/diagnostic.yaml"],
        orphanedSource=False,
    )


def _install_review() -> InstallReview:
    return InstallReview(
        operation="install",
        confirmationToken="secret-review-token",
        reviewDigest=_REVIEW_DIGEST,
        identity=_identity(),
        sourceName="company",
        repositoryUrl="https://example.test/workflows.git",
        configuredRef="main",
        resolvedCommit=_COMMIT,
        packagePath="packages/laptop-support",
        candidateVersion="1.0.0",
        candidateDigest=_DIGEST,
        assessment=_assessment(),
        fileChanges=[
            FileDigestChange(
                path="workflows/diagnostic.yaml",
                kind="added",
                candidateDigest=_DIGEST,
            )
        ],
        workflowReviews=[_workflow_review()],
    )


def _empty_change() -> StringSetChange:
    return StringSetChange(added=[], removed=[])


def _update_review() -> UpdateReview:
    return UpdateReview(
        operation="update",
        result="update_available",
        confirmationToken="secret-review-token",
        reviewDigest=_REVIEW_DIGEST,
        identity=_identity(),
        sourceName="company",
        repositoryUrl="https://example.test/workflows.git",
        configuredRef="main",
        oldVersion="1.0.0",
        candidateVersion="2.0.0",
        oldCommit="1" * 40,
        candidateCommit=_COMMIT,
        oldDigest="2" * 64,
        candidateDigest=_DIGEST,
        fileChanges=[],
        workflowChanges=_empty_change(),
        requirementChanges=RequirementChanges(
            runtimes=_empty_change(),
            tools=_empty_change(),
            providers=_empty_change(),
            services=_empty_change(),
            secrets=_empty_change(),
        ),
        riskChanges=WorkflowRiskChanges(added=[], removed=[]),
        compatibilityChanges=WorkflowCompatibilityChanges(added=[], removed=[]),
        assessment=_assessment(),
        workflowReviews=[_workflow_review()],
    )


def _remove_review() -> RemoveReview:
    return RemoveReview(
        operation="remove",
        confirmationToken="secret-review-token",
        reviewDigest=_REVIEW_DIGEST,
        identity=_identity(),
        currentVersion="1.0.0",
        currentCommit=_COMMIT,
        distributionDigest=_DIGEST,
        workflowNames=["diagnostic"],
    )


def _trust_review() -> TrustReview:
    return TrustReview(
        confirmationToken="secret-review-token",
        reviewDigest=_REVIEW_DIGEST,
        identity=_identity(),
        sourceName="company",
        version="1.0.0",
        resolvedCommit=_COMMIT,
        distributionDigest=_DIGEST,
        packageResources=["workflows/diagnostic.yaml"],
        workflows=[_workflow_review()],
    )


def _inspection() -> PackageInspection:
    return PackageInspection(
        identifier="company/laptop-support",
        identity=_identity(),
        sourceName="company",
        repositoryUrl="https://example.test/workflows.git",
        configuredRef="main",
        resolvedCommit=_COMMIT,
        verifiedAt=_NOW,
        verified=True,
        sourceState="fresh",
        id="laptop-support",
        version="1.0.0",
        displayName="Laptop Support",
        description="Support workflows",
        license="MIT",
        publisher="Example",
        tags=["support"],
        packagePath="packages/laptop-support",
        contractVersion=1,
        packageDigest=_DIGEST,
        workflows=[_workflow_review()],
        resources=[
            PackageInspectionResource(
                path="workflows/diagnostic.yaml", types=["workflow_definition"]
            )
        ],
        externalRequirements=_requirements(),
        blockers=[],
        advisories=[],
        installStatus="not_installed",
        updateStatus="not_applicable",
    )


def _snake(value) -> dict:
    return value.model_dump(mode="json", by_alias=False)


def _review(value) -> dict:
    payload = _snake(value)
    payload.pop("confirmation_token")
    payload.update(
        confirmation_available=True,
        expires_at="2026-09-04T12:05:00Z",
    )
    return payload


def _subject(kind: str) -> dict:
    if kind == "refresh":
        return {"type": "source", "source_name": "company"}
    if kind == "update_check":
        return {"type": "all_packages"}
    return {
        "type": "package",
        "identity": _snake(_identity()),
    }


def _selection(kind: str) -> dict | None:
    if kind in {"trust_prepare", "trust_confirm", "trust_revoke"}:
        return {"type": "all"}
    return None


def _installed_state() -> dict:
    return {
        "profile": "support",
        "identity": _snake(_identity()),
        "observed_at": _FINISHED,
        "state": "installed",
        "installed": _snake(_installed()),
        "trust": {
            "identity": _snake(_identity()),
            "distribution_digest": _DIGEST,
            "workflows": [
                {
                    "workflow_name": "diagnostic",
                    "definition_path": "workflows/diagnostic.yaml",
                    "state": "trusted",
                }
            ],
        },
        "recovery": "clear",
        "busy": False,
    }


def _absent_state() -> dict:
    payload = _installed_state()
    payload.update(state="absent", installed=None, trust=None)
    return payload


def _trust_result(kind: str) -> dict:
    value = {
        "identity": _snake(_identity()),
        "selection": {"type": "all"},
        "distribution_digest": _DIGEST,
        "workflows": deepcopy(_installed_state()["trust"]["workflows"]),
    }
    if kind == "trust_revoke":
        value["revoked"] = 1
    return {
        "type": "trust_revoke" if kind == "trust_revoke" else "trust_grant",
        "value": value,
    }


def _result(kind: str) -> dict:
    values = {
        "refresh": {
            "type": "source_refresh",
            "value": {
                "source_name": "company",
                "repository_url": "https://example.test/workflows.git",
                "state": "fresh",
                "resolved_commit": _COMMIT,
                "verified_at": _FINISHED,
                "package_count": 1,
                "diagnostic_code": None,
                "message": None,
            },
        },
        "inspect": {"type": "package_detail", "value": _snake(_inspection())},
        "update_check": {
            "type": "update_checks",
            "value": {
                "checks": [
                    _snake(
                        UpdateCheck(
                            identity=_identity(),
                            status="current",
                            installedVersion="1.0.0",
                        )
                    )
                ]
            },
        },
        "install_prepare": {
            "type": "install_review",
            "value": _review(_install_review()),
        },
        "update_prepare": {"type": "update_review", "value": _review(_update_review())},
        "remove_prepare": {"type": "remove_review", "value": _review(_remove_review())},
        "trust_prepare": {
            "type": "trust_review",
            "value": {
                **_review(_trust_review()),
                "package_workflows": [
                    {
                        "workflow_name": "diagnostic",
                        "definition_path": "workflows/diagnostic.yaml",
                    }
                ],
            },
        },
        "install_confirm": {"type": "installed_package", "value": _snake(_installed())},
        "update_confirm": {
            "type": "updated_package",
            "value": _snake(_installed(version="2.0.0")),
        },
        "remove_confirm": {"type": "removed_package", "value": _snake(_installed())},
        "trust_confirm": _trust_result("trust_confirm"),
        "trust_revoke": _trust_result("trust_revoke"),
    }
    return deepcopy(values[kind])


def _outcome(kind: str) -> dict:
    if kind == "refresh":
        return {"type": "committed", "package_state": None}
    if kind in {
        "inspect",
        "update_check",
        "install_prepare",
        "update_prepare",
        "remove_prepare",
        "trust_prepare",
    }:
        return {
            "type": "known_unchanged",
            "evidence": "read_only",
            "package_state": None,
        }
    if kind == "remove_confirm":
        return {"type": "committed", "package_state": _absent_state()}
    state = _installed_state()
    if kind == "update_confirm":
        state["installed"]["version"] = "2.0.0"
    return {"type": "committed", "package_state": state}


def _operation(kind: str, state: str = "succeeded") -> dict:
    payload = {
        "schema_version": 2,
        "id": f"wmop_{'1' * 12}_{'2' * 32}",
        "registry_epoch": _EPOCH,
        "request_id": _REQUEST_ID,
        "kind": kind,
        "subject": _subject(kind),
        "selection": _selection(kind),
        "profile": "support",
        "state": state,
        "phase": "completed",
        "progress": 100,
        "created_at": _NOW,
        "started_at": _STARTED,
        "updated_at": _FINISHED,
        "finished_at": _FINISHED,
        "result": _result(kind),
        "error": None,
        "outcome": _outcome(kind),
    }
    if state == "pending":
        payload.update(
            phase="queued",
            progress=0,
            started_at=None,
            updated_at=_NOW,
            finished_at=None,
            result=None,
            outcome=None,
        )
    elif state == "running":
        payload.update(
            phase="running",
            progress=1,
            finished_at=None,
            result=None,
            outcome=None,
        )
    elif state == "failed":
        payload.update(
            phase="failed",
            progress=75,
            result=None,
            error={
                "code": "marketplace_operation_failed",
                "message": "Workflow marketplace operation failed.",
            },
            outcome={
                "type": "outcome_unknown",
                "reason": "terminal_invalid",
            },
        )
    elif state == "cancelled":
        payload.update(
            phase="cancelled",
            progress=25,
            result=None,
            outcome={
                "type": "cancelled_before_commit",
                "package_state": None,
            },
        )
    return payload


_KINDS = (
    "refresh",
    "inspect",
    "update_check",
    "install_prepare",
    "update_prepare",
    "remove_prepare",
    "trust_prepare",
    "install_confirm",
    "update_confirm",
    "remove_confirm",
    "trust_confirm",
    "trust_revoke",
)

_RUNNING_PHASES = {
    "refresh": ("running", "fetching"),
    "inspect": ("running", "fetching", "reviewing"),
    "update_check": ("running", "fetching", "reviewing"),
    "install_prepare": ("running", "fetching", "reviewing"),
    "update_prepare": ("running", "fetching", "reviewing"),
    "remove_prepare": ("running", "reviewing"),
    "trust_prepare": ("running", "reviewing"),
    "install_confirm": ("running", "validating", "committing", "recovering"),
    "update_confirm": ("running", "validating", "committing", "recovering"),
    "remove_confirm": ("running", "validating", "committing", "recovering"),
    "trust_confirm": ("running", "validating", "committing"),
    "trust_revoke": ("running", "validating", "committing"),
}


@pytest.mark.parametrize("kind", _KINDS)
def test_each_kind_accepts_its_exact_subject_selection_result_and_outcome(kind) -> None:
    operation = LifecycleOperation.model_validate(_operation(kind))

    assert operation.kind == kind
    assert operation.result is not None


@pytest.mark.parametrize(
    ("kind", "phase"),
    [(kind, phase) for kind, phases in _RUNNING_PHASES.items() for phase in phases],
)
def test_each_kind_accepts_every_contract_running_phase(kind, phase) -> None:
    payload = _operation(kind, "running")
    payload["phase"] = phase

    operation = LifecycleOperation.model_validate(payload)

    assert operation.phase == phase


@pytest.mark.parametrize(
    "state", ["pending", "running", "succeeded", "failed", "cancelled"]
)
def test_each_operation_state_accepts_its_exact_terminal_shape(state) -> None:
    operation = LifecycleOperation.model_validate(_operation("install_prepare", state))

    assert operation.state == state


@pytest.mark.parametrize("state", ["failed", "cancelled"])
def test_pre_worker_terminal_state_does_not_require_a_start_time(state) -> None:
    payload = _operation("install_confirm", state)
    payload["started_at"] = None

    operation = LifecycleOperation.model_validate(payload)

    assert operation.started_at is None


@pytest.mark.parametrize(
    ("kind", "subject"),
    [
        (
            "refresh",
            {
                "type": "package",
                "identity": {"source_key": "company", "package_id": "laptop-support"},
            },
        ),
        ("inspect", {"type": "source", "source_name": "company"}),
        (
            "update_check",
            {
                "type": "direct_install",
                "source_key": "company",
                "selector_id": "a" * 64,
            },
        ),
        ("install_prepare", {"type": "all_packages"}),
        ("update_prepare", {"type": "all_packages"}),
        (
            "remove_prepare",
            {
                "type": "direct_install",
                "source_key": "company",
                "selector_id": "a" * 64,
            },
        ),
        ("trust_prepare", {"type": "source", "source_name": "company"}),
        (
            "install_confirm",
            {
                "type": "direct_install",
                "source_key": "company",
                "selector_id": "a" * 64,
            },
        ),
        ("update_confirm", {"type": "all_packages"}),
        ("remove_confirm", {"type": "source", "source_name": "company"}),
        ("trust_confirm", {"type": "all_packages"}),
        ("trust_revoke", {"type": "all_packages"}),
    ],
)
def test_kind_rejects_an_incompatible_subject(kind, subject) -> None:
    payload = _operation(kind)
    payload["subject"] = subject

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


@pytest.mark.parametrize("kind", _KINDS)
def test_each_kind_rejects_a_result_from_another_kind(kind) -> None:
    payload = _operation(kind)
    other_kind = _KINDS[(_KINDS.index(kind) + 1) % len(_KINDS)]
    payload["result"] = _result(other_kind)

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


@pytest.mark.parametrize(
    "selector_id",
    ["a" * 63, "a" * 65, "A" * 64, "g" * 64, True],
)
def test_direct_install_selector_is_exact_lowercase_hex64(selector_id) -> None:
    payload = _operation("install_prepare")
    payload["subject"] = {
        "type": "direct_install",
        "source_key": "company",
        "selector_id": selector_id,
    }

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


@pytest.mark.parametrize("kind", ["trust_prepare", "trust_confirm", "trust_revoke"])
def test_trust_kinds_require_a_selection(kind) -> None:
    payload = _operation(kind)
    payload["selection"] = None

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


@pytest.mark.parametrize("kind", ["trust_prepare", "trust_confirm", "trust_revoke"])
def test_trust_kinds_accept_exactly_one_selected_workflow(kind) -> None:
    payload = _operation(kind)
    selection = {"type": "one", "workflow_name": "diagnostic"}
    payload["selection"] = selection
    if kind != "trust_prepare":
        payload["result"]["value"]["selection"] = selection

    operation = LifecycleOperation.model_validate(payload)

    assert operation.selection is not None
    assert operation.selection.type == "one"


def test_non_trust_kind_rejects_a_selection() -> None:
    payload = _operation("install_prepare")
    payload["selection"] = {"type": "all"}

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


def test_confirm_cannot_publish_another_kind_result() -> None:
    payload = _operation("update_confirm")
    payload["result"] = _result("install_confirm")

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


@pytest.mark.parametrize(
    ("kind", "phase"),
    [
        ("refresh", "committing"),
        ("remove_prepare", "fetching"),
        ("trust_confirm", "recovering"),
        ("install_confirm", "reviewing"),
    ],
)
def test_running_operation_rejects_a_phase_not_allowed_for_its_kind(
    kind, phase
) -> None:
    payload = _operation(kind, "running")
    payload["phase"] = phase

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


@pytest.mark.parametrize(
    ("state", "field", "value"),
    [
        ("pending", "progress", 1),
        ("pending", "started_at", _STARTED),
        ("running", "progress", 100),
        ("running", "finished_at", _FINISHED),
        ("succeeded", "progress", 99),
        (
            "succeeded",
            "error",
            {
                "code": "marketplace_operation_failed",
                "message": "Workflow marketplace operation failed.",
            },
        ),
        ("failed", "result", _result("install_prepare")),
        (
            "cancelled",
            "error",
            {
                "code": "marketplace_operation_failed",
                "message": "Workflow marketplace operation failed.",
            },
        ),
    ],
)
def test_state_rejects_incompatible_time_progress_and_terminal_fields(
    state, field, value
) -> None:
    payload = _operation("install_prepare", state)
    payload[field] = value

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("created_at", "2026-09-04T12:00:01+00:00"),
        ("started_at", "2026-09-04T11:59:59Z"),
        ("finished_at", "2026-09-04T12:00:00Z"),
        ("updated_at", "2026-09-04T12:00:01Z"),
    ],
)
def test_operation_timestamps_are_canonical_and_chronological(field, value) -> None:
    payload = _operation("install_prepare")
    payload[field] = value

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


def test_request_id_epoch_must_match_the_registry_epoch() -> None:
    payload = _operation("install_prepare")
    payload["request_id"] = f"wmreq_{'0' * 32}_1788523200000_{'f' * 32}"

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


def test_refresh_result_source_must_match_the_source_subject() -> None:
    payload = _operation("refresh")
    payload["result"]["value"]["source_name"] = "other"

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


def test_package_result_identity_must_match_the_subject() -> None:
    payload = _operation("install_confirm")
    payload["result"]["value"]["identity"]["package_id"] = "other"

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


def test_one_package_update_check_contains_exactly_the_requested_identity() -> None:
    payload = _operation("update_check")
    payload["subject"] = {"type": "package", "identity": _snake(_identity())}
    payload["result"]["value"]["checks"].append(
        _snake(
            UpdateCheck(
                identity=_identity(package_id="other"),
                status="current",
                installedVersion="1.0.0",
            )
        )
    )

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


def test_all_package_update_checks_reject_duplicate_identities() -> None:
    payload = _operation("update_check")
    payload["result"]["value"]["checks"] *= 2

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


def test_one_workflow_review_must_match_the_selected_workflow() -> None:
    payload = _operation("trust_prepare")
    payload["selection"] = {"type": "one", "workflow_name": "other"}

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


@pytest.mark.parametrize("mutation", ["duplicate", "unknown"])
def test_trust_review_rejects_invalid_package_workflow_inventory(mutation) -> None:
    payload = _operation("trust_prepare")
    value = payload["result"]["value"]
    if mutation == "duplicate":
        value["package_workflows"] *= 2
    else:
        value["workflows"][0]["workflow_name"] = "unknown"

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


def test_token_free_reviews_reject_v1_aliases_and_confirmation_tokens() -> None:
    payload = _operation("install_prepare")
    payload["result"]["value"]["confirmationToken"] = "must-not-cross"

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)
    encoded = LifecycleOperation.model_validate(
        _operation("install_prepare")
    ).model_dump_json()
    assert "confirmation_token" not in encoded
    assert "secret-review-token" not in encoded
    assert '"review_digest"' in encoded
    assert "reviewDigest" not in encoded


def test_v2_nested_results_reject_camel_case_v1_aliases() -> None:
    payload = _operation("install_prepare")
    assessment = payload["result"]["value"]["assessment"]
    assessment["reviewDigest"] = assessment.pop("review_digest")

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


@pytest.mark.parametrize("kind", ["refresh", "install_prepare", "update_prepare"])
def test_v2_results_reject_repository_credentials(kind) -> None:
    payload = _operation(kind)
    payload["result"]["value"]["repository_url"] = (
        "https://operator:secret@example.test/workflows.git"
    )

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


@pytest.mark.parametrize(
    ("kind", "field", "value"),
    [
        ("install_prepare", "package_path", "../escape"),
        ("install_prepare", "configured_ref", " main "),
        ("update_prepare", "configured_ref", " main "),
    ],
)
def test_v2_reviews_reject_noncanonical_repository_coordinates(
    kind, field, value
) -> None:
    payload = _operation(kind)
    payload["result"]["value"][field] = value

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


def test_review_availability_requires_an_expiry_and_unchanged_update_has_neither() -> (
    None
):
    install = _operation("install_prepare")
    install["result"]["value"]["expires_at"] = None
    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(install)

    update = _operation("update_prepare")
    update["result"]["value"].update(
        result="unchanged",
        confirmation_available=False,
        expires_at=None,
    )
    assert LifecycleOperation.model_validate(update).result is not None

    missing_expiry = _operation("remove_prepare")
    missing_expiry["result"]["value"].pop("expires_at")
    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(missing_expiry)


def test_successful_mutation_requires_matching_verified_package_state() -> None:
    payload = _operation("update_confirm")
    payload["outcome"]["package_state"]["installed"]["version"] = "3.0.0"

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


def test_successful_trust_result_must_match_authoritative_full_trust_state() -> None:
    payload = _operation("trust_confirm")
    payload["result"]["value"]["workflows"][0]["state"] = "untrusted"

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


@pytest.mark.parametrize(
    "outcome",
    [
        {"type": "known_unchanged", "evidence": "read_only", "package_state": None},
        {"type": "recovery_required", "reason": "rollback_failed"},
        {"type": "outcome_unknown", "reason": "terminal_invalid"},
    ],
)
def test_successful_package_mutation_rejects_a_non_committed_outcome(outcome) -> None:
    payload = _operation("install_confirm")
    payload["outcome"] = outcome

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


def test_failed_operation_cannot_claim_a_committed_outcome() -> None:
    payload = _operation("install_confirm", "failed")
    payload["outcome"] = {
        "type": "committed",
        "package_state": _installed_state(),
    }

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


def test_cancelled_operation_requires_cancelled_before_commit_outcome() -> None:
    payload = _operation("install_confirm", "cancelled")
    payload["outcome"] = {"type": "outcome_unknown", "reason": "response_lost"}

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


def test_rollback_verified_requires_current_package_state() -> None:
    payload = _operation("install_confirm", "failed")
    payload["outcome"] = {
        "type": "known_unchanged",
        "evidence": "rollback_verified",
        "package_state": None,
    }

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


def test_failed_outcome_package_state_must_match_subject_and_profile() -> None:
    payload = _operation("install_confirm", "failed")
    state = _installed_state()
    state["identity"]["package_id"] = "other"
    state["installed"]["identity"]["package_id"] = "other"
    state["installed"]["package_path"] = "packages/other"
    state["trust"]["identity"]["package_id"] = "other"
    payload["outcome"] = {
        "type": "known_unchanged",
        "evidence": "before_mutation",
        "package_state": state,
    }

    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)


def test_package_state_accepts_verified_installed_absent_and_unconfirmed_shapes() -> (
    None
):
    installed = PackageState.model_validate(_installed_state())
    absent = PackageState.model_validate(_absent_state())
    unconfirmed_payload = _absent_state()
    unconfirmed_payload.update(state="unconfirmed", recovery="required")
    unconfirmed = PackageState.model_validate(unconfirmed_payload)

    assert (installed.state, absent.state, unconfirmed.state) == (
        "installed",
        "absent",
        "unconfirmed",
    )


def test_recovery_required_cannot_claim_verified_installed_state() -> None:
    payload = _installed_state()
    payload["recovery"] = "required"

    with pytest.raises(ValidationError):
        PackageState.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("installed", None),
        ("trust", None),
        ("recovery", "unconfirmed"),
    ],
)
def test_installed_state_requires_verified_install_trust_and_clear_recovery(
    field, value
) -> None:
    payload = _installed_state()
    payload[field] = value

    with pytest.raises(ValidationError):
        PackageState.model_validate(payload)


def test_absent_state_rejects_installed_or_trust_claims() -> None:
    payload = _absent_state()
    payload["installed"] = _snake(_installed())

    with pytest.raises(ValidationError):
        PackageState.model_validate(payload)


@pytest.mark.parametrize("mutation", ["identity", "digest", "path", "duplicate"])
def test_installed_state_correlates_identity_digest_and_complete_trust_membership(
    mutation,
) -> None:
    payload = _installed_state()
    if mutation == "identity":
        payload["trust"]["identity"]["package_id"] = "other"
    elif mutation == "digest":
        payload["trust"]["distribution_digest"] = "0" * 64
    elif mutation == "path":
        payload["trust"]["workflows"][0]["definition_path"] = "workflows/other.yaml"
    else:
        payload["trust"]["workflows"] *= 2

    with pytest.raises(ValidationError):
        PackageState.model_validate(payload)


def test_strict_models_reject_extra_fields_and_boolean_integer_coercion() -> None:
    operation = _operation("install_prepare")
    operation["unexpected"] = True
    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(operation)

    state = _installed_state()
    state["busy"] = 1
    with pytest.raises(ValidationError):
        PackageState.model_validate(state)
