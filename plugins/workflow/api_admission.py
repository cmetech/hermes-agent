"""Authenticated, background-only workflow admission for plugin REST routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
from typing import Literal, Mapping

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.coordinator_store import CoordinatorStore
from plugins.workflow.entitlement import verified_showcase_run_metadata
from plugins.workflow.input_contract import (
    API_INPUT_AGGREGATE_MAX_BYTES,
    API_INPUT_VALUE_MAX_BYTES,
    WorkflowInputContractError,
    workflow_input_declarations,
)
from plugins.workflow.models import WorkflowPackage, WorkflowValidationError
from plugins.workflow.provenance import TriggerProvenance
from plugins.workflow.store import InputSnapshotError, RunStore
from plugins.workflow.trust import (
    WorkflowResourceCapacityError,
    WorkflowResourceCacheMissError,
    WorkflowResourceReadBudget,
    WorkflowTrustError,
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
    preflight_execution,
)


@dataclass(frozen=True, slots=True)
class ApiAdmissionAuthority:
    principal: str
    namespace: str
    operator_scope: str | None
    source_instance: str
    assurance: Literal["verified_adapter", "local_admin_claim"]
    return_route: str | None = None
    trigger_source: Literal["desktop", "api"] = "api"


class ApiAdmissionError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        status_code: int,
        retryable: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


def validate_api_value_bounds(values: Mapping[str, str]) -> dict[str, str]:
    """Apply endpoint-owned byte caps without reclassifying schema failures."""
    total = 0
    normalized = dict(values)
    for name, value in normalized.items():
        encoded = value.encode("utf-8")
        if len(encoded) > API_INPUT_VALUE_MAX_BYTES:
            raise ApiAdmissionError("workflow_input_too_large", status_code=422)
        total += len(name.encode("utf-8")) + len(encoded)
    if total > API_INPUT_AGGREGATE_MAX_BYTES:
        raise ApiAdmissionError("workflow_input_too_large", status_code=422)
    return normalized


def validate_declared_api_values(
    package: WorkflowPackage,
    values: Mapping[str, str],
    *,
    verified_value_bindings: Mapping[str, str] | None = None,
    verified_fixture_names: frozenset[str] | None = None,
) -> dict[str, str]:
    """Validate declared mode while leaving legacy-flat values unchanged."""
    try:
        declarations = workflow_input_declarations(package)
    except WorkflowInputContractError as exc:
        raise ApiAdmissionError("workflow_invalid_definition", status_code=422) from exc
    normalized = dict(values)
    if not declarations:
        return normalized

    bindings = dict(verified_value_bindings or {})
    fixture_names = frozenset(verified_fixture_names or ())
    binding_targets = frozenset(bindings.values())
    reserved = binding_targets | fixture_names
    allowed_direct = {
        name
        for name, declaration in declarations.items()
        if declaration.accepts_api_value and name not in reserved
    }
    allowed = allowed_direct | frozenset(bindings)
    if set(normalized) - allowed or set(normalized) & reserved:
        raise ApiAdmissionError("workflow_inputs_invalid", status_code=422)

    supplied_targets = set(normalized) & allowed_direct
    supplied_targets.update(
        target for public, target in bindings.items() if public in normalized
    )
    missing = {
        name
        for name, declaration in declarations.items()
        if declaration.required
        and declaration.accepts_api_value
        and name not in supplied_targets
    }
    if missing:
        raise ApiAdmissionError("workflow_input_required", status_code=422)
    return normalized


def _catalog_package(
    workflow_name: str,
    *,
    hermes_home: Path,
    workdir: Path,
    user_home: Path,
    catalog_source: Literal["project", "profile"] | None = None,
):
    del user_home  # Catalog locations are project/profile scoped in Desktop v1.
    from plugins.workflow.catalog_api import (
        WorkflowCatalogCapacityError,
        WorkflowCatalogInvalidDefinitionError,
        WorkflowCatalogUnavailableError,
        resolve_workflow_catalog_package,
    )

    try:
        return resolve_workflow_catalog_package(
            workflow_name,
            hermes_home=hermes_home,
            workdir=workdir,
            catalog_source=catalog_source,
        )
    except WorkflowCatalogCapacityError as exc:
        raise ApiAdmissionError(
            "workflow_catalog_capacity", status_code=503, retryable=True
        ) from exc
    except WorkflowCatalogInvalidDefinitionError as exc:
        raise ApiAdmissionError("workflow_invalid_definition", status_code=422) from exc
    except WorkflowCatalogUnavailableError as exc:
        raise ApiAdmissionError(
            "workflow_catalog_unavailable", status_code=503, retryable=True
        ) from exc


def start_api_run(
    store: RunStore,
    *,
    hermes_home: str | Path,
    workdir: str | Path,
    user_home: str | Path,
    workflow_name: str,
    values: Mapping[str, str],
    idempotency_key: str,
    concurrency_policy: Literal["queue", "allow", "forbid"],
    authority: ApiAdmissionAuthority,
    catalog_source: Literal["project", "profile", "showcase"] | None = None,
) -> dict[str, object]:
    """Admit one trusted catalog workflow without executing any workflow node."""
    home = Path(hermes_home).resolve()
    provenance = TriggerProvenance.authenticated_api(
        source=authority.trigger_source,
        assurance=authority.assurance,
        intent_key=idempotency_key,
        source_instance=authority.source_instance,
        principal=authority.principal,
        return_route=authority.return_route,
    )
    from plugins.workflow.catalog_api import (
        CATALOG_MAX_RESOURCE_FILE_BYTES,
        CATALOG_MAX_RESOURCE_FILES,
        CATALOG_MAX_RESOURCE_TOTAL_BYTES,
        CATALOG_MAX_TRUST_STORE_BYTES,
        workflow_catalog_run_support,
    )

    resource_budget = WorkflowResourceReadBudget(
        max_file_bytes=CATALOG_MAX_RESOURCE_FILE_BYTES,
        max_total_bytes=CATALOG_MAX_RESOURCE_TOTAL_BYTES,
        max_files=CATALOG_MAX_RESOURCE_FILES,
    )
    verified_showcase = None
    if catalog_source == "showcase":
        from plugins.workflow.showcase import (
            ShowcaseCatalogError,
            load_verified_showcase_package,
        )

        try:
            verified_showcase = load_verified_showcase_package(
                workflow_name,
                read_budget=resource_budget,
                force_reverify=True,
            )
        except WorkflowResourceCapacityError as exc:
            raise ApiAdmissionError(
                "workflow_catalog_capacity", status_code=503, retryable=True
            ) from exc
        except (ShowcaseCatalogError, OSError, UnicodeError, ValueError) as exc:
            raise ApiAdmissionError(
                "workflow_showcase_verification_failed", status_code=409
            ) from exc
        package = verified_showcase.package
    else:
        if catalog_source not in {None, "project", "profile"}:
            raise ApiAdmissionError("workflow_catalog_source_invalid", status_code=422)
        package = _catalog_package(
            workflow_name,
            hermes_home=home,
            workdir=Path(workdir).resolve(),
            user_home=Path(user_home).resolve(),
            catalog_source=catalog_source,
        )
        if package is None:
            raise ApiAdmissionError("workflow_not_found", status_code=404)

    run_support = workflow_catalog_run_support(
        package,
        showcase_scenario=(
            verified_showcase.scenario if verified_showcase is not None else None
        ),
    )
    if not run_support["supported"]:
        if run_support["reason"] == "unsupported_inputs":
            raise ApiAdmissionError("workflow_inputs_unsupported", status_code=422)
        raise ApiAdmissionError("workflow_showcase_cli_required", status_code=409)

    scenario = verified_showcase.scenario if verified_showcase is not None else None
    value_bindings = (
        getattr(scenario, "input_value_bindings", None) if scenario is not None else None
    )
    fixture_mapping = (
        getattr(scenario, "input_fixtures", None) if scenario is not None else None
    )
    values = validate_declared_api_values(
        package,
        values,
        verified_value_bindings=(
            value_bindings if isinstance(value_bindings, Mapping) else None
        ),
        verified_fixture_names=(
            frozenset(fixture_mapping)
            if isinstance(fixture_mapping, Mapping)
            else None
        ),
    )

    compatibility = assess_compatibility(package)
    try:
        package_digest = compute_package_digest(
            package, read_budget=resource_budget
        )
        risk = build_risk_summary(
            package, compatibility, read_budget=resource_budget
        )
    except WorkflowResourceCapacityError as exc:
        raise ApiAdmissionError(
            "workflow_catalog_capacity", status_code=503, retryable=True
        ) from exc
    except WorkflowValidationError as exc:
        raise ApiAdmissionError("workflow_invalid_definition", status_code=422) from exc
    if package_digest.sha256 != risk.package_digest:
        raise ApiAdmissionError("workflow_package_changed", status_code=409)
    if verified_showcase is not None and (
        package_digest.sha256 != verified_showcase.risk.package_digest
        or risk.risk_digest != verified_showcase.risk.risk_digest
    ):
        raise ApiAdmissionError(
            "workflow_showcase_verification_failed", status_code=409
        )
    resource_budget.seal()

    if verified_showcase is None:
        trust_store = WorkflowTrustStore(home)
        trust_snapshot = trust_store.snapshot_read_only(
            max_bytes=CATALOG_MAX_TRUST_STORE_BYTES
        )
        trusted = (
            trust_store.check_snapshot(
                trust_snapshot,
                risk.package_digest,
                risk_digest=risk.risk_digest,
            )
            == "trusted"
        )
    else:
        trusted = True
    if not trusted:
        raise ApiAdmissionError("workflow_trust_required", status_code=403)
    if not compatibility.runnable:
        raise ApiAdmissionError(
            "workflow_compatibility_blocked",
            status_code=409,
        )
    try:
        preflight_execution(risk, trusted=True)
    except WorkflowTrustError as exc:
        raise ApiAdmissionError("workflow_preflight_failed", status_code=409) from exc

    try:
        coordinator = CoordinatorStore(store.database).health(
            now=datetime.now(timezone.utc)
        )
    except (
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
        IndexError,
        KeyError,
        AssertionError,
        OverflowError,
    ) as exc:
        raise ApiAdmissionError(
            "coordinator_unavailable", status_code=503, retryable=True
        ) from exc
    if coordinator.status != "healthy":
        raise ApiAdmissionError(
            "coordinator_unavailable",
            status_code=503,
            retryable=True,
        )

    try:
        prepared = store.prepare_run_snapshot(
            package,
            values=values or None,
            resource_read_budget=resource_budget,
            trusted_package_digest=package_digest,
        )
    except WorkflowResourceCapacityError as exc:
        raise ApiAdmissionError(
            "workflow_catalog_capacity", status_code=503, retryable=True
        ) from exc
    except WorkflowValidationError as exc:
        raise ApiAdmissionError("workflow_invalid_definition", status_code=422) from exc
    except WorkflowResourceCacheMissError as exc:
        raise ApiAdmissionError("workflow_package_changed", status_code=409) from exc
    except InputSnapshotError as exc:
        if exc.code == "workflow_input_too_large":
            raise ApiAdmissionError(exc.code, status_code=422) from exc
        raise
    if prepared.definition_digest != risk.package_digest:
        shutil.rmtree(prepared.staging_directory, ignore_errors=True)
        raise ApiAdmissionError("workflow_package_changed", status_code=409)
    if verified_showcase is None:
        run_metadata = None
        concurrency_key = str(
            package.sidecar.get("concurrency_key") or package.definition.name
        )
    else:
        run_metadata = verified_showcase_run_metadata(
            showcase_id=verified_showcase.scenario.id,
            showcase_version=verified_showcase.scenario.package_version,
            bundle_digest=verified_showcase.bundle_digest,
            risk_digest=risk.risk_digest,
            requires_ai=verified_showcase.scenario.requires_ai,
            include_verified_marker=True,
        )
        concurrency_key = f"showcase:{verified_showcase.scenario.id}"

    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source=authority.trigger_source,
            idempotency_key=idempotency_key,
            idempotency_namespace=authority.namespace,
            concurrency_key=concurrency_key,
            concurrency_policy=concurrency_policy,
            execution_mode="background",
            operator_scope=authority.operator_scope,
            run_metadata=run_metadata,
            provenance=provenance,
        ),
        immutable_snapshot=prepared,
    )
    if admitted.run_id is None:
        reason = admitted.reason_code or "admission_rejected"
        if reason == "coordinator_unavailable":
            status_code = 503
        elif reason == "idempotency_conflict":
            status_code = 409
        else:
            status_code = 409
        raise ApiAdmissionError(
            reason,
            status_code=status_code,
            retryable=reason == "coordinator_unavailable",
        )
    status = store.get_run_status(
        admitted.run_id,
        operator_scope=authority.operator_scope,
    )
    return {
        "run_id": admitted.run_id,
        "status": status["status"],
        "admission_disposition": admitted.disposition,
        "queue_position": admitted.queue_position,
        "blocked_by_run_id": admitted.blocked_by_run_id,
    }


__all__ = [
    "ApiAdmissionAuthority",
    "ApiAdmissionError",
    "start_api_run",
    "validate_api_value_bounds",
    "validate_declared_api_values",
]
