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
    declared = sidecar.get("language_compatibility")
    if declared is None:
        return WorkflowLanguageSelection(
            declared_profile=None,
            effective_profile=WorkflowLanguageProfile.HERMES_LEGACY,
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

    normalized_document = {
        "profile": selection.effective_profile.value,
        "normalizer_version": normalizer_version,
        "definition": {
            "name": source_definition.name,
            "description": source_definition.description,
            "nodes": [
                {
                    "id": node.id,
                    "node_type": node.node_type,
                    "value": _json_safe(node.value),
                    "depends_on": list(node.depends_on),
                    "options": _json_safe(node.options),
                }
                for node in source_definition.nodes
            ],
            "options": _json_safe(source_definition.options),
        },
    }
    metadata = WorkflowLanguageMetadata(
        declared_profile=selection.declared_profile,
        effective_profile=selection.effective_profile,
        normalizer_version=normalizer_version,
        normalized_definition_digest=_sha256_json(normalized_document),
    )
    # Version 1 is an identity normalizer. Future versions may transform at
    # this single, explicit boundary without changing source diagnostics.
    return NormalizedWorkflow(definition=source_definition, metadata=metadata)


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
    """Convert frozen workflow values into canonical JSON-compatible values."""
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_safe(item) for item in value]
    if isinstance(value, frozenset | set):
        return sorted((_json_safe(item) for item in value), key=_canonical_json)
    if isinstance(value, float) and not math.isfinite(value):
        return {
            "__yaml_scalar__": "nonfinite-float",
            "kind": "nan" if math.isnan(value) else "infinity",
            "sign": "negative" if math.copysign(1.0, value) < 0 else "positive",
        }
    if isinstance(value, datetime):
        return {"__yaml_scalar__": "timestamp", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__yaml_scalar__": "date", "value": value.isoformat()}
    if isinstance(value, bytes):
        return {
            "__yaml_scalar__": "binary",
            "base64": base64.b64encode(value).decode("ascii"),
        }
    return value


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
