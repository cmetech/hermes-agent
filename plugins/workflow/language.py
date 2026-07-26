"""Versioned, deterministic workflow-language normalization contracts."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import json
import math
from typing import Any, Mapping

from plugins.workflow.models import (
    CompatibilityFinding,
    CompatibilityLevel,
    WorkflowDefinition,
    WorkflowLanguageMetadata,
    WorkflowLanguageProfile,
    WorkflowLanguageSelection,
)


WORKFLOW_NORMALIZER_VERSION = 1
SUPPORTED_NORMALIZER_VERSIONS = frozenset({1})


class WorkflowLanguageCompatibilityError(ValueError):
    """Raised when a workflow language contract cannot be resolved safely."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class NormalizedWorkflow:
    definition: WorkflowDefinition
    metadata: WorkflowLanguageMetadata


def resolve_language_profile(sidecar: Mapping[str, object]) -> WorkflowLanguageSelection:
    """Resolve the sidecar's declared language profile, defaulting to legacy."""
    if "language_compatibility" not in sidecar:
        return WorkflowLanguageSelection(
            declared_profile=None,
            effective_profile=WorkflowLanguageProfile.HERMES_LEGACY,
        )
    declared = sidecar["language_compatibility"]
    if not isinstance(declared, str):
        raise WorkflowLanguageCompatibilityError(
            "workflow_language_profile_unsupported",
            "language_compatibility must be hermes-legacy or archon-2026-07",
        )
    try:
        profile = WorkflowLanguageProfile(declared)
    except (TypeError, ValueError) as exc:
        raise WorkflowLanguageCompatibilityError(
            "workflow_language_profile_unsupported",
            "language_compatibility must be hermes-legacy or archon-2026-07",
        ) from exc
    return WorkflowLanguageSelection(
        declared_profile=profile,
        effective_profile=profile,
    )


def normalize_workflow(
    source_definition: WorkflowDefinition,
    *,
    selection: WorkflowLanguageSelection,
    normalizer_version: int,
) -> NormalizedWorkflow:
    """Bind immutable workflow semantics to a supported normalizer version."""
    if (
        isinstance(normalizer_version, bool)
        or not isinstance(normalizer_version, int)
        or normalizer_version not in SUPPORTED_NORMALIZER_VERSIONS
    ):
        raise WorkflowLanguageCompatibilityError(
            "workflow_normalizer_version_unsupported",
            f"workflow normalizer version {normalizer_version!r} is unsupported",
        )

    normalized_document = _json_safe(
        {
            "profile": selection.effective_profile.value,
            "normalizer_version": normalizer_version,
            "definition": {
                "name": source_definition.name,
                "description": source_definition.description,
                "nodes": [
                    {
                        "id": node.id,
                        "node_type": node.node_type,
                        "value": node.value,
                        "depends_on": list(node.depends_on),
                        "options": node.options,
                    }
                    for node in source_definition.nodes
                ],
                "options": source_definition.options,
            },
        }
    )
    metadata = WorkflowLanguageMetadata(
        declared_profile=selection.declared_profile,
        effective_profile=selection.effective_profile,
        normalizer_version=normalizer_version,
        normalized_definition_digest=_sha256_json(normalized_document),
    )
    # Version 1 is an identity normalizer. Future versions may transform at
    # this single, explicit boundary without changing source diagnostics.
    return NormalizedWorkflow(definition=source_definition, metadata=metadata)


def language_compatibility_findings(
    source_definition: WorkflowDefinition,
    metadata: WorkflowLanguageMetadata,
) -> tuple[CompatibilityFinding, ...]:
    """Return profile-specific language findings for one source definition."""
    profile = metadata.effective_profile
    findings: list[CompatibilityFinding] = []

    def add(
        path: str,
        code: str,
        message: str,
        migration: str,
        *,
        blocking: bool,
    ) -> None:
        findings.append(
            CompatibilityFinding(
                path=path,
                level=(
                    CompatibilityLevel.UNSUPPORTED
                    if blocking
                    else CompatibilityLevel.MAPPED
                ),
                message=message,
                blocking=blocking,
                code=code,
                severity="error" if blocking else "warning",
                effective_profile=profile,
                migration=migration,
            )
        )

    if profile is WorkflowLanguageProfile.HERMES_LEGACY:
        add(
            "sidecar.language_compatibility",
            "legacy_language_profile",
            "workflow uses permissive Hermes legacy language semantics",
            "Declare archon-2026-07 after workflow doctor reports no blocking findings.",
            blocking=False,
        )

    for index, node in enumerate(source_definition.nodes):
        options = node.options
        prefix = f"nodes[{index}]"
        if profile is WorkflowLanguageProfile.HERMES_LEGACY:
            if "timeout" in options:
                add(
                    f"{prefix}.timeout",
                    "legacy_timeout_seconds",
                    "legacy timeout is interpreted in seconds",
                    "Convert timeout seconds to milliseconds before changing profiles.",
                    blocking=False,
                )
            retry = options.get("retry")
            if isinstance(retry, Mapping) and "max_attempts" in retry:
                add(
                    f"{prefix}.retry.max_attempts",
                    "legacy_retry_total_attempts",
                    "legacy retry.max_attempts counts total attempts",
                    "Account for Archon retry-count semantics in Phase 3 before changing profiles.",
                    blocking=False,
                )
            if "output_format" in options:
                add(
                    f"{prefix}.output_format",
                    "legacy_output_format_post_validation",
                    "legacy output_format is validated after model execution",
                    "Wait for Phase 2 output-format enforcement before changing profiles.",
                    blocking=False,
                )
            if "output_type" in options:
                add(
                    f"{prefix}.output_type",
                    "legacy_output_type_not_published",
                    "legacy output_type does not publish a typed artifact",
                    "Wait for Phase 2 typed artifacts before changing profiles.",
                    blocking=False,
                )
            continue

        if "timeout" in options:
            add(
                f"{prefix}.timeout",
                "archon_timeout_semantics_unavailable",
                "Archon timeout semantics are not enforceable in Phase 1",
                "Remove timeout or wait for Phase 3 timeout semantics.",
                blocking=True,
            )
        if "retry" in options:
            add(
                f"{prefix}.retry",
                "archon_retry_semantics_unavailable",
                "Archon retry semantics are not enforceable in Phase 1",
                "Remove retry or wait for Phase 3 retry semantics.",
                blocking=True,
            )
        if "output_format" in options:
            add(
                f"{prefix}.output_format",
                "archon_output_format_unavailable",
                "Archon output_format enforcement is not available in Phase 1",
                "Remove output_format or wait for Phase 2 enforcement.",
                blocking=True,
            )
        if "output_type" in options:
            add(
                f"{prefix}.output_type",
                "archon_output_type_unavailable",
                "Archon output_type artifacts are not available in Phase 1",
                "Remove output_type or wait for Phase 2 typed artifacts.",
                blocking=True,
            )
        if "maxBudgetUsd" in options:
            add(
                f"{prefix}.maxBudgetUsd",
                "archon_budget_enforcement_unavailable",
                "Archon budget enforcement is not available in Phase 1",
                "Remove maxBudgetUsd or wait for Phase 5 budget enforcement.",
                blocking=True,
            )
        if "sandbox" in options:
            add(
                f"{prefix}.sandbox",
                "archon_sandbox_enforcement_unavailable",
                "Archon sandbox enforcement is not available in Phase 1",
                "Remove sandbox or wait for Phase 5 sandbox enforcement.",
                blocking=True,
            )

    if (
        profile is WorkflowLanguageProfile.ARCHON_2026_07
        and "sandbox" in source_definition.options
    ):
        add(
            "sandbox",
            "archon_sandbox_enforcement_unavailable",
            "Archon sandbox enforcement is not available in Phase 1",
            "Remove sandbox or wait for Phase 5 sandbox enforcement.",
            blocking=True,
        )
    return tuple(findings)


def bind_semantic_fingerprint(
    package_digest: str, metadata: WorkflowLanguageMetadata
) -> str:
    """Bind a trusted package digest to its normalized language semantics."""
    return _sha256_json(
        {
            "package_digest": package_digest,
            "effective_profile": metadata.effective_profile.value,
            "normalizer_version": metadata.normalizer_version,
            "normalized_definition_digest": metadata.normalized_definition_digest,
        }
    )


def language_projection(
    metadata: WorkflowLanguageMetadata, *, semantic_fingerprint: str | None = None
) -> dict[str, object]:
    """Return a bounded, JSON-safe projection of immutable language metadata."""
    projection: dict[str, object] = {
        "declared_profile": (
            metadata.declared_profile.value if metadata.declared_profile is not None else None
        ),
        "effective_profile": metadata.effective_profile.value,
        "normalizer_version": metadata.normalizer_version,
        "normalized_definition_digest": metadata.normalized_definition_digest,
    }
    if semantic_fingerprint is not None:
        projection["semantic_fingerprint"] = semantic_fingerprint
    return projection


def _json_safe(value: Any) -> Any:
    """Encode the complete workflow value graph with collision-proof types."""
    if isinstance(value, Mapping):
        return {
            "type": "mapping",
            "entries": [
                [_json_safe(str(key)), _json_safe(item)]
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            ],
        }
    if isinstance(value, tuple | list):
        return {"type": "sequence", "items": [_json_safe(item) for item in value]}
    if isinstance(value, frozenset | set):
        items = sorted((_json_safe(item) for item in value), key=_canonical_json)
        return {"type": "set", "items": items}
    if isinstance(value, datetime):
        return {"type": "timestamp", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, bytes):
        return {
            "type": "binary",
            "base64": base64.b64encode(value).decode("ascii"),
        }
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, int):
        return {"type": "integer", "value": value}
    if isinstance(value, float):
        if math.isfinite(value):
            return {"type": "float", "value": value}
        return {
            "type": "float",
            "kind": "nan" if math.isnan(value) else "infinity",
            "sign": "negative" if math.copysign(1.0, value) < 0 else "positive",
        }
    if isinstance(value, str):
        return {"type": "string", "value": value}
    raise TypeError(f"unsupported workflow value type: {type(value).__name__}")


def _sha256_json(document: Mapping[str, Any]) -> str:
    return sha256(_canonical_json(document).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
