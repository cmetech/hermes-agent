"""Central classification and runner policy for workflow AI execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent.plugin_agent import PluginAgentRunResult

if TYPE_CHECKING:
    from plugins.workflow.models import WorkflowDefinition


AGENT_BACKED_SHOWCASE_FEATURES = frozenset(
    {"command", "prompt", "loop", "inline_agents"}
)
SHOWCASE_IDENTITY_KEYS = frozenset(
    {
        "showcase_id",
        "showcase_version",
        "bundle_digest",
        "risk_digest",
        "showcase_provenance",
    }
)
LEGACY_SHOWCASE_IDENTITY_KEYS = frozenset(
    {"showcase_id", "showcase_version", "bundle_digest", "risk_digest"}
)


@dataclass(frozen=True, slots=True)
class AIEntitlementResolution:
    value: str
    error_code: str | None = None
    error_message: str | None = None


class AIExecutionIntegrityError(RuntimeError):
    """An explicit real grant failed immutable scenario corroboration."""


class DeterministicAgentRunner:
    """Model-free implementation shared by every deterministic consumer."""

    _RESPONSE = (
        "Deterministic revision recorded: keep every proposed action manual "
        "and reversible.\nDETERMINISTIC_COMPLETE"
    )

    def run(self, request, *, is_cancelled=None):
        if is_cancelled is not None and is_cancelled():
            status = "cancelled"
            response = ""
        else:
            status = "completed"
            response = self._RESPONSE
        return PluginAgentRunResult(
            final_response=response,
            session_id="workflow-deterministic",
            provider="deterministic",
            model="none",
            status=status,
            pending_interaction=None,
            usage={},
            audit={
                "isolated_worker": False,
                "deterministic": True,
                "provider_attempts": 0,
            },
        )


_DETERMINISTIC_AGENT_RUNNER = DeterministicAgentRunner()


def entitled_agent_runner(
    resolution: AIEntitlementResolution,
    real_runner,
    deterministic_runner=None,
):
    """Select the sole runner allowed by the already-derived run decision."""
    if resolution.error_code:
        raise AIExecutionIntegrityError(
            resolution.error_message or "AI entitlement integrity failure"
        )
    if resolution.value == "deterministic":
        return (
            deterministic_runner
            if deterministic_runner is not None
            else _DETERMINISTIC_AGENT_RUNNER
        )
    if resolution.value == "real":
        return real_runner
    return (
        deterministic_runner
        if deterministic_runner is not None
        else _DETERMINISTIC_AGENT_RUNNER
    )


def _integrity_failure(message: str) -> AIEntitlementResolution:
    return AIEntitlementResolution(
        "deterministic",
        error_code="execution_integrity",
        error_message=message,
    )


def derive_ai_entitlement(
    metadata: Mapping[str, object],
    *,
    definition_digest: str | None = None,
    execution_context=None,
) -> AIEntitlementResolution:
    """Derive immutable per-run model authority without mutating metadata."""
    if not isinstance(metadata, Mapping):
        return AIEntitlementResolution("deterministic")
    if "ai_entitlement" not in metadata:
        current_marker = metadata.get("showcase_provenance") == "verified_bundled"
        complete_legacy = all(
            isinstance(metadata.get(key), str) and bool(metadata.get(key))
            for key in LEGACY_SHOWCASE_IDENTITY_KEYS
        )
        malformed_showcase = any(key in metadata for key in SHOWCASE_IDENTITY_KEYS)
        if current_marker or complete_legacy or malformed_showcase:
            return AIEntitlementResolution("deterministic")
        return AIEntitlementResolution("real")

    if metadata.get("ai_entitlement") != "real":
        return AIEntitlementResolution("deterministic")
    if metadata.get("showcase_provenance") != "verified_bundled":
        return _integrity_failure(
            "explicit real entitlement lacks verified bundled provenance"
        )
    if definition_digest is None:
        return _integrity_failure(
            "explicit real entitlement lacks immutable definition identity"
        )
    if not all(
        isinstance(metadata.get(key), str) and bool(metadata.get(key))
        for key in LEGACY_SHOWCASE_IDENTITY_KEYS
    ):
        return _integrity_failure(
            "explicit real entitlement has incomplete showcase identity"
        )

    verification_budget = None
    try:
        from plugins.workflow.showcase import load_verified_showcase_package
        from plugins.workflow.trust import WorkflowResourceReadBudget

        verification_budget = WorkflowResourceReadBudget(
            max_file_bytes=1024 * 1024,
            max_total_bytes=8 * 1024 * 1024,
            max_files=512,
        )
        verified = load_verified_showcase_package(
            str(metadata["showcase_id"]),
            read_budget=verification_budget,
        )
    except Exception:
        return _integrity_failure(
            "explicit real entitlement cannot be corroborated"
        )

    try:
        from plugins.workflow.runner_binding import (
            assess_package_execution,
            background_execution_context,
            production_workflow_runner_binding,
        )
        from plugins.workflow.language import supports_phase4_semantics

        context = execution_context or background_execution_context(
            production_workflow_runner_binding(),
            requires_ai=verified.scenario.requires_ai,
        )
        _compatibility, risk = assess_package_execution(
            verified.package,
            context,
            read_budget=verification_budget,
            compilation=(
                verified.compilation
                if supports_phase4_semantics(
                    verified.package.language.effective_profile,
                    verified.package.language.normalizer_version,
                )
                else None
            ),
        )
    except Exception:
        return _integrity_failure(
            "explicit real entitlement cannot be corroborated"
        )

    if not (
        verified.scenario.requires_ai
        and verified.scenario.verified_bundled_provenance
        and metadata.get("showcase_version")
        == verified.scenario.package_version
        and metadata.get("bundle_digest") == verified.bundle_digest
        and metadata.get("risk_digest") == risk.risk_digest
        and definition_digest == risk.package_digest
    ):
        return _integrity_failure(
            "explicit real entitlement does not match the verified AI scenario"
        )
    return AIEntitlementResolution("real")


def verified_showcase_run_metadata(
    *,
    showcase_id: str,
    showcase_version: str,
    bundle_digest: str,
    risk_digest: str,
    requires_ai: bool,
    include_verified_marker: bool,
) -> dict[str, str]:
    """Build admission metadata while preserving legacy non-AI identities."""
    metadata = {
        "showcase_id": showcase_id,
        "showcase_version": showcase_version,
        "bundle_digest": bundle_digest,
        "risk_digest": risk_digest,
    }
    if include_verified_marker or requires_ai:
        metadata["showcase_provenance"] = "verified_bundled"
    if requires_ai:
        metadata["ai_entitlement"] = "real"
    return metadata


def agent_backed_features(definition: WorkflowDefinition) -> frozenset[str]:
    """Return every positively identified agent-runner consumer."""
    features: set[str] = set()
    for node in definition.nodes:
        if node.node_type in {"command", "prompt", "loop"}:
            features.add(node.node_type)
        if node.options.get("agents"):
            features.add("inline_agents")
        if (
            node.node_type == "approval"
            and isinstance(node.value, Mapping)
            and node.value.get("on_reject")
        ):
            features.add("approval_rework")
    return frozenset(features)


def validate_showcase_ai_contract(
    showcase_id: str,
    *,
    requires_ai: bool,
    definition: WorkflowDefinition,
) -> None:
    """Reject non-AI showcase definitions containing real agent features."""
    prohibited = agent_backed_features(definition) & AGENT_BACKED_SHOWCASE_FEATURES
    if not requires_ai and prohibited:
        from plugins.workflow.showcase import ShowcaseCatalogError

        joined = ", ".join(sorted(prohibited))
        raise ShowcaseCatalogError(
            f"showcase requires_ai is false but uses agent-backed features: "
            f"{showcase_id}: {joined}"
        )


__all__ = [
    "AGENT_BACKED_SHOWCASE_FEATURES",
    "AIExecutionIntegrityError",
    "AIEntitlementResolution",
    "DeterministicAgentRunner",
    "agent_backed_features",
    "derive_ai_entitlement",
    "entitled_agent_runner",
    "verified_showcase_run_metadata",
    "validate_showcase_ai_contract",
]
