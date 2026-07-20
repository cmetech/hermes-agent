"""Authenticated, background-only workflow admission for plugin REST routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Literal, Mapping

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.coordinator_store import CoordinatorStore
from plugins.workflow.provenance import TriggerProvenance
from plugins.workflow.store import RunStore
from plugins.workflow.models import WorkflowValidationError
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


def _catalog_package(
    workflow_name: str,
    *,
    hermes_home: Path,
    workdir: Path,
    user_home: Path,
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
    package = _catalog_package(
        workflow_name,
        hermes_home=home,
        workdir=Path(workdir).resolve(),
        user_home=Path(user_home).resolve(),
    )
    if package is None:
        raise ApiAdmissionError("workflow_not_found", status_code=404)

    compatibility = assess_compatibility(package)
    from plugins.workflow.catalog_api import (
        CATALOG_MAX_RESOURCE_FILE_BYTES,
        CATALOG_MAX_RESOURCE_FILES,
        CATALOG_MAX_RESOURCE_TOTAL_BYTES,
        CATALOG_MAX_TRUST_STORE_BYTES,
    )

    resource_budget = WorkflowResourceReadBudget(
        max_file_bytes=CATALOG_MAX_RESOURCE_FILE_BYTES,
        max_total_bytes=CATALOG_MAX_RESOURCE_TOTAL_BYTES,
        max_files=CATALOG_MAX_RESOURCE_FILES,
    )
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
    resource_budget.seal()

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

    coordinator = CoordinatorStore(store.database).health(
        now=datetime.now(timezone.utc)
    )
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
    if prepared.definition_digest != risk.package_digest:
        shutil.rmtree(prepared.staging_directory, ignore_errors=True)
        raise ApiAdmissionError("workflow_package_changed", status_code=409)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source=authority.trigger_source,
            idempotency_key=idempotency_key,
            idempotency_namespace=authority.namespace,
            concurrency_key=str(
                package.sidecar.get("concurrency_key") or package.definition.name
            ),
            concurrency_policy=concurrency_policy,
            execution_mode="background",
            operator_scope=authority.operator_scope,
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
]
