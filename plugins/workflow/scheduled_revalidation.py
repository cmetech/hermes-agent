"""Read-only fire-time authorization for scheduled workflow runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping

from plugins.workflow.catalog_api import resolve_workflow_catalog_package
from plugins.workflow.runner_binding import (
    ExecutionCapabilityContext,
    WorkflowRunnerBinding,
    assess_package_execution,
)
from plugins.workflow.trust import (
    WorkflowResourceReadBudget,
    WorkflowTrustStore,
    compute_package_digest,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RESOURCE_FILE_BYTES = 1024 * 1024
_RESOURCE_TOTAL_BYTES = 8 * 1024 * 1024
_RESOURCE_FILES = 512
_TRUST_STORE_BYTES = 1024 * 1024


class ScheduledRunRevalidationError(RuntimeError):
    """The current source or execution authority no longer matches admission."""


@dataclass(frozen=True, slots=True)
class ScheduledRunRevalidation:
    run_id: str
    state_version: int
    execution_identity: str


def showcase_scenario_digest(scenario: object) -> str:
    """Return a stable digest for the exact authenticated scenario record."""
    material = json.dumps(
        asdict(scenario),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def scheduled_execution_context(
    run: Mapping[str, object],
    binding: WorkflowRunnerBinding,
) -> ExecutionCapabilityContext:
    """Derive actual fire-time capability from server-owned run evidence."""
    metadata = run.get("run_metadata")
    if not isinstance(metadata, Mapping):
        raise ScheduledRunRevalidationError("scheduled admission metadata is missing")
    is_showcase = metadata.get("catalog_source") == "showcase"
    if is_showcase:
        entitlement_value = (
            "real" if metadata.get("ai_entitlement") == "real" else "deterministic"
        )
    else:
        entitlement_value = "real"
    from plugins.workflow.entitlement import AIEntitlementResolution

    return binding.execution_context(
        surface="background",
        entitlement=AIEntitlementResolution(entitlement_value),
    )


def _required_digest(metadata: Mapping[str, object], name: str) -> str:
    value = metadata.get(name)
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ScheduledRunRevalidationError(f"scheduled {name} is missing")
    return value


def _verify_sealed_snapshot(
    run: Mapping[str, object],
    *,
    run_directory: Path,
) -> None:
    definition = run_directory / "definition.yaml"
    policy = run_directory / "policy.yaml"
    resources = run_directory / "resources.json"
    try:
        definition_bytes = definition.read_bytes()
        policy_bytes = policy.read_bytes() if policy.is_file() else b"{}\n"
        resources_bytes = resources.read_bytes()
    except Exception as exc:
        raise ScheduledRunRevalidationError("sealed snapshot is unreadable") from exc
    expected = (
        _required_digest(run["run_metadata"], "sealed_definition_digest"),
        _required_digest(run["run_metadata"], "sealed_policy_digest"),
        _required_digest(run["run_metadata"], "sealed_input_digest"),
    )
    actual = (
        hashlib.sha256(definition_bytes).hexdigest(),
        hashlib.sha256(policy_bytes).hexdigest(),
        hashlib.sha256(resources_bytes).hexdigest(),
    )
    if expected != actual:
        raise ScheduledRunRevalidationError("sealed snapshot identity changed")
    if expected[1:] != (
        str(run.get("policy_digest") or ""),
        str(run.get("input_manifest_digest") or ""),
    ):
        raise ScheduledRunRevalidationError("sealed admission identity changed")


def revalidate_scheduled_run(
    run: Mapping[str, object],
    execution_capability_context: ExecutionCapabilityContext,
    *,
    hermes_home: str | Path,
    workdir: str | Path,
    run_directory: str | Path,
) -> ScheduledRunRevalidation:
    """Reauthorize one scheduled run without mutating its durable state."""
    metadata = run.get("run_metadata")
    if not isinstance(metadata, Mapping) or not isinstance(
        metadata.get("schedule_at"), str
    ):
        raise ScheduledRunRevalidationError("scheduled admission metadata is missing")
    run_id = run.get("run_id")
    state_version = run.get("state_version")
    if not isinstance(run_id, str) or not isinstance(state_version, int):
        raise ScheduledRunRevalidationError("scheduled run identity is missing")
    execution_identity = _required_digest(metadata, "execution_identity")
    if execution_identity != execution_capability_context.identity_digest:
        raise ScheduledRunRevalidationError("execution capability changed")
    package_identity = _required_digest(metadata, "package_digest")
    risk_identity = _required_digest(metadata, "risk_digest")
    if package_identity != str(run.get("definition_digest")):
        raise ScheduledRunRevalidationError("admission package identity changed")
    _verify_sealed_snapshot(run, run_directory=Path(run_directory))

    budget = WorkflowResourceReadBudget(
        max_file_bytes=_RESOURCE_FILE_BYTES,
        max_total_bytes=_RESOURCE_TOTAL_BYTES,
        max_files=_RESOURCE_FILES,
    )
    source = metadata.get("catalog_source")
    if source == "showcase":
        from plugins.workflow.showcase import load_verified_showcase_package

        showcase_id = metadata.get("showcase_id")
        if not isinstance(showcase_id, str):
            raise ScheduledRunRevalidationError("showcase identity is missing")
        try:
            verified = load_verified_showcase_package(
                showcase_id,
                read_budget=budget,
                force_reverify=True,
            )
            compatibility, risk = assess_package_execution(
                verified.package,
                execution_capability_context,
                read_budget=budget,
            )
        except Exception as exc:
            raise ScheduledRunRevalidationError("showcase verification failed") from exc
        comparisons = (
            (metadata.get("showcase_version"), verified.scenario.package_version),
            (metadata.get("bundle_digest"), verified.bundle_digest),
            (
                metadata.get("showcase_scenario_digest"),
                showcase_scenario_digest(verified.scenario),
            ),
            (package_identity, verified.package_digest),
            (risk_identity, risk.risk_digest),
        )
        if not compatibility.runnable or not all(
            isinstance(left, str) and left == str(right) for left, right in comparisons
        ):
            raise ScheduledRunRevalidationError("showcase identity changed")
    elif source in {"project", "profile"}:
        try:
            package = resolve_workflow_catalog_package(
                str(run.get("workflow") or ""),
                hermes_home=Path(hermes_home),
                workdir=Path(workdir),
                catalog_source=source,
            )
            if package is None:
                raise ScheduledRunRevalidationError("catalog source is unavailable")
            live_digest = compute_package_digest(package, read_budget=budget).sha256
            compatibility, risk = assess_package_execution(
                package,
                execution_capability_context,
                read_budget=budget,
            )
            trust = WorkflowTrustStore(Path(hermes_home)).snapshot_read_only(
                max_bytes=_TRUST_STORE_BYTES
            )
        except ScheduledRunRevalidationError:
            raise
        except Exception as exc:
            raise ScheduledRunRevalidationError("catalog verification failed") from exc
        if (
            not compatibility.runnable
            or package_identity != live_digest
            or risk_identity != risk.risk_digest
            or WorkflowTrustStore(Path(hermes_home)).check_snapshot(
                trust,
                live_digest,
                risk_digest=risk.risk_digest,
            )
            != "trusted"
        ):
            raise ScheduledRunRevalidationError("catalog authorization changed")
    else:
        raise ScheduledRunRevalidationError("catalog source is missing")

    return ScheduledRunRevalidation(
        run_id=run_id,
        state_version=state_version,
        execution_identity=execution_identity,
    )


__all__ = [
    "ScheduledRunRevalidation",
    "ScheduledRunRevalidationError",
    "revalidate_scheduled_run",
    "scheduled_execution_context",
    "showcase_scenario_digest",
]
