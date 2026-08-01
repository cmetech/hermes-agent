"""Versioned, deterministic workflow-language normalization contracts."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from hashlib import sha256
import math
import re
from types import MappingProxyType
from typing import Any, Mapping

from agent.structured_output import (
    StructuredOutputError,
    canonical_json_bytes,
    normalize_schema,
)

from plugins.workflow.models import (
    CompatibilityFinding,
    CompatibilityLevel,
    WorkflowDefinition,
    WorkflowLanguageMetadata,
    WorkflowLanguageProfile,
    WorkflowLanguageSelection,
    WorkflowPackage,
    WorkflowNode,
    WorkflowStructuredOutput,
    freeze_value,
)


WORKFLOW_NORMALIZER_VERSION = 2
SUPPORTED_NORMALIZER_VERSIONS = frozenset({1, 2})
STRUCTURED_OUTPUT_CANONICALIZATION_VERSION = 1
MAX_SNAPSHOTTED_STRUCTURED_OUTPUTS = 32
# The normalized type-tag document expands the already bounded workflow and
# structured schemas. This conservative ceiling keeps semantic hashing bounded
# without constraining any admitted 2 MiB workflow or its 32 schema snapshots.
_MAX_NORMALIZED_DEFINITION_CANONICAL_BYTES = 16 * 1024 * 1024
LEGACY_LANGUAGE_FINDING_FIELDS = frozenset({
    "idle_timeout",
    "timeout",
    "retry.max_attempts",
    "output_format",
    "output_type",
})
ARCHON_LANGUAGE_FINDING_FIELDS = frozenset({
    "idle_timeout",
    "timeout",
    "retry",
    "output_format",
    "output_type",
    "maxBudgetUsd",
    "sandbox",
})
WORKFLOW_LANGUAGE_FINDINGS_PER_NODE_MAX = max(
    len(LEGACY_LANGUAGE_FINDING_FIELDS),
    len(ARCHON_LANGUAGE_FINDING_FIELDS),
)
WORKFLOW_LANGUAGE_PACKAGE_FINDINGS_MAX = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
WORKFLOW_LANGUAGE_PROFILE_UNSUPPORTED_CODE = "workflow_language_profile_unsupported"
WORKFLOW_NORMALIZER_VERSION_UNSUPPORTED_CODE = "workflow_normalizer_version_unsupported"
UNKNOWN_TOP_LEVEL_FIELD_CODE = "unknown_top_level_field"
ARCHON_UNKNOWN_TOP_LEVEL_FIELD_CODE = "archon_unknown_top_level_field"


class WorkflowLanguageCompatibilityError(ValueError):
    """Raised when a workflow language contract cannot be resolved safely."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class WorkflowStructuredOutputNormalizationError(StructuredOutputError):
    """Attach a workflow node location to a structured-output failure."""

    def __init__(self, source_index: int, error: StructuredOutputError):
        self.source_index = source_index
        super().__init__(str(error))


@dataclass(frozen=True, slots=True)
class DynamicCompatibilityCode:
    """One stable runtime language code not attached to an inventory field."""

    code: str
    profiles: frozenset[WorkflowLanguageProfile]
    status: str
    enforcement_phase: int
    fields: tuple[str, ...]


DYNAMIC_LANGUAGE_COMPATIBILITY_CODES = (
    DynamicCompatibilityCode(
        code=WORKFLOW_LANGUAGE_PROFILE_UNSUPPORTED_CODE,
        profiles=frozenset(WorkflowLanguageProfile),
        status="blocking",
        enforcement_phase=1,
        fields=("sidecar.language_compatibility",),
    ),
    DynamicCompatibilityCode(
        code=WORKFLOW_NORMALIZER_VERSION_UNSUPPORTED_CODE,
        profiles=frozenset(WorkflowLanguageProfile),
        status="blocking",
        enforcement_phase=1,
        fields=("normalizer_version",),
    ),
    DynamicCompatibilityCode(
        code=UNKNOWN_TOP_LEVEL_FIELD_CODE,
        profiles=frozenset({WorkflowLanguageProfile.HERMES_LEGACY}),
        status="warning",
        enforcement_phase=1,
        fields=("*",),
    ),
    DynamicCompatibilityCode(
        code=ARCHON_UNKNOWN_TOP_LEVEL_FIELD_CODE,
        profiles=frozenset({WorkflowLanguageProfile.ARCHON_2026_07}),
        status="blocking",
        enforcement_phase=1,
        fields=("*",),
    ),
)


@dataclass(frozen=True)
class NormalizedWorkflow:
    definition: WorkflowDefinition
    metadata: WorkflowLanguageMetadata


@dataclass(frozen=True)
class WorkflowLanguageSnapshot:
    effective_profile: WorkflowLanguageProfile
    normalizer_version: int
    normalized_definition_digest: str
    semantic_fingerprint: str
    structured_outputs: Mapping[str, WorkflowStructuredOutput] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "structured_outputs",
            MappingProxyType(dict(self.structured_outputs)),
        )

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "effective_profile": self.effective_profile.value,
            "normalizer_version": self.normalizer_version,
            "normalized_definition_digest": self.normalized_definition_digest,
            "semantic_fingerprint": self.semantic_fingerprint,
        }
        if self.normalizer_version == 2:
            value["structured_outputs"] = _structured_outputs_projection(
                self.structured_outputs
            )
        return value


def resolve_language_profile(
    sidecar: Mapping[str, object],
) -> WorkflowLanguageSelection:
    """Resolve the sidecar's declared language profile, defaulting to legacy."""
    if "language_compatibility" not in sidecar:
        return WorkflowLanguageSelection(
            declared_profile=None,
            effective_profile=WorkflowLanguageProfile.HERMES_LEGACY,
        )
    declared = sidecar["language_compatibility"]
    if not isinstance(declared, str):
        raise WorkflowLanguageCompatibilityError(
            WORKFLOW_LANGUAGE_PROFILE_UNSUPPORTED_CODE,
            "language_compatibility must be hermes-legacy or archon-2026-07",
        )
    try:
        profile = WorkflowLanguageProfile(declared)
    except (TypeError, ValueError) as exc:
        raise WorkflowLanguageCompatibilityError(
            WORKFLOW_LANGUAGE_PROFILE_UNSUPPORTED_CODE,
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
            WORKFLOW_NORMALIZER_VERSION_UNSUPPORTED_CODE,
            f"workflow normalizer version {normalizer_version!r} is unsupported",
        )

    normalized_definition = source_definition
    structured_outputs: Mapping[str, WorkflowStructuredOutput] = MappingProxyType({})
    if normalizer_version == 2:
        normalized_definition, structured_outputs = _normalize_v2(
            source_definition, selection.effective_profile
        )

    normalized_document_value: dict[str, object] = {
        "profile": selection.effective_profile.value,
        "normalizer_version": normalizer_version,
        "definition": {
            "name": normalized_definition.name,
            "description": normalized_definition.description,
            "nodes": [
                {
                    "id": node.id,
                    "node_type": node.node_type,
                    "value": node.value,
                    "depends_on": list(node.depends_on),
                    "options": node.options,
                }
                for node in normalized_definition.nodes
            ],
            "options": normalized_definition.options,
        },
    }
    if normalizer_version == 2:
        normalized_document_value["structured_outputs"] = (
            _structured_outputs_projection(structured_outputs)
        )
    normalized_document = _json_safe(normalized_document_value)
    metadata = WorkflowLanguageMetadata(
        declared_profile=selection.declared_profile,
        effective_profile=selection.effective_profile,
        normalizer_version=normalizer_version,
        normalized_definition_digest=_sha256_json(normalized_document),
        structured_outputs=structured_outputs,
    )
    return NormalizedWorkflow(definition=normalized_definition, metadata=metadata)


def _normalize_v2(
    source_definition: WorkflowDefinition,
    profile: WorkflowLanguageProfile,
) -> tuple[WorkflowDefinition, Mapping[str, WorkflowStructuredOutput]]:
    """Normalize Archon structured-output schemas without changing v1 semantics."""
    if profile is not WorkflowLanguageProfile.ARCHON_2026_07:
        return source_definition, MappingProxyType({})

    normalized_nodes: list[WorkflowNode] = []
    structured_outputs: dict[str, WorkflowStructuredOutput] = {}
    for node in source_definition.nodes:
        output_format = node.options.get("output_format")
        if output_format is None:
            normalized_nodes.append(node)
            continue
        if not isinstance(output_format, Mapping):
            raise StructuredOutputError("structured-output schema must be an object")
        try:
            normalized_schema = normalize_schema(_thaw(output_format))
        except StructuredOutputError as exc:
            raise WorkflowStructuredOutputNormalizationError(
                node.source_index, exc
            ) from exc
        options = dict(node.options)
        options["output_format"] = _thaw(normalized_schema.canonical_schema)
        normalized_nodes.append(replace(node, options=freeze_value(options)))
        structured_outputs[node.id] = WorkflowStructuredOutput(
            canonical_schema=normalized_schema.canonical_schema,
            schema_fingerprint=normalized_schema.schema_fingerprint,
            canonicalization_version=STRUCTURED_OUTPUT_CANONICALIZATION_VERSION,
        )
        if len(structured_outputs) > MAX_SNAPSHOTTED_STRUCTURED_OUTPUTS:
            raise WorkflowStructuredOutputNormalizationError(
                node.source_index,
                StructuredOutputError("workflow exceeds structured outputs limit"),
            )
    return replace(source_definition, nodes=tuple(normalized_nodes)), MappingProxyType(
        structured_outputs
    )


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
            if "idle_timeout" in options:
                add(
                    f"{prefix}.idle_timeout",
                    "legacy_idle_timeout_seconds",
                    "legacy idle_timeout is interpreted in seconds",
                    "Convert idle_timeout seconds to milliseconds only after Phase 3 Archon semantics are available.",
                    blocking=False,
                )
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

        if "idle_timeout" in options:
            add(
                f"{prefix}.idle_timeout",
                "archon_idle_timeout_semantics_unavailable",
                "Archon idle_timeout semantics are not enforceable in Phase 1",
                "Remove idle_timeout or wait for Phase 3 millisecond normalization.",
                blocking=True,
            )
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
        if metadata.normalizer_version < 2 and "output_format" in options:
            add(
                f"{prefix}.output_format",
                "archon_output_format_unavailable",
                "Archon output_format enforcement is not available in Phase 1",
                "Remove output_format or wait for Phase 2 enforcement.",
                blocking=True,
            )
        if metadata.normalizer_version < 2 and "output_type" in options:
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
    document: dict[str, object] = {
        "package_digest": package_digest,
        "effective_profile": metadata.effective_profile.value,
        "normalizer_version": metadata.normalizer_version,
        "normalized_definition_digest": metadata.normalized_definition_digest,
    }
    if metadata.normalizer_version == 2:
        document["structured_outputs"] = _structured_outputs_projection(
            metadata.structured_outputs
        )
    return _sha256_json(document)


def make_language_snapshot(
    package: WorkflowPackage, package_digest: str
) -> WorkflowLanguageSnapshot:
    """Bind a loaded package's normalized semantics to its trusted digest."""
    return WorkflowLanguageSnapshot(
        effective_profile=package.language.effective_profile,
        normalizer_version=package.language.normalizer_version,
        normalized_definition_digest=package.language.normalized_definition_digest,
        semantic_fingerprint=bind_semantic_fingerprint(
            package_digest, package.language
        ),
        structured_outputs=_copy_structured_outputs(
            package.language.structured_outputs
        ),
    )


def read_language_snapshot(
    value: object | None,
) -> WorkflowLanguageSnapshot | None:
    """Parse untrusted sealed language metadata using its versioned shape."""
    if value is None:
        return None
    v1_keys = {
        "effective_profile",
        "normalizer_version",
        "normalized_definition_digest",
        "semantic_fingerprint",
    }
    if not isinstance(value, Mapping):
        raise WorkflowLanguageCompatibilityError(
            "workflow_language_snapshot_invalid",
            "workflow language snapshot must contain the exact supported fields",
        )

    profile_value = value.get("effective_profile")
    if not isinstance(profile_value, str):
        raise WorkflowLanguageCompatibilityError(
            WORKFLOW_LANGUAGE_PROFILE_UNSUPPORTED_CODE,
            "workflow language snapshot profile is unsupported",
        )
    try:
        profile = WorkflowLanguageProfile(profile_value)
    except ValueError as exc:
        raise WorkflowLanguageCompatibilityError(
            WORKFLOW_LANGUAGE_PROFILE_UNSUPPORTED_CODE,
            "workflow language snapshot profile is unsupported",
        ) from exc

    if "normalizer_version" not in value:
        raise WorkflowLanguageCompatibilityError(
            "workflow_language_snapshot_invalid",
            "workflow language snapshot must contain the exact supported fields",
        )
    normalizer_version = value["normalizer_version"]
    if (
        isinstance(normalizer_version, bool)
        or not isinstance(normalizer_version, int)
        or normalizer_version not in SUPPORTED_NORMALIZER_VERSIONS
    ):
        raise WorkflowLanguageCompatibilityError(
            WORKFLOW_NORMALIZER_VERSION_UNSUPPORTED_CODE,
            f"workflow normalizer version {normalizer_version!r} is unsupported",
        )

    expected_keys = (
        v1_keys if normalizer_version == 1 else v1_keys | {"structured_outputs"}
    )
    if set(value) != expected_keys:
        raise WorkflowLanguageCompatibilityError(
            "workflow_language_snapshot_invalid",
            "workflow language snapshot must contain the exact supported fields",
        )

    normalized_digest = value["normalized_definition_digest"]
    fingerprint = value["semantic_fingerprint"]
    if (
        not isinstance(normalized_digest, str)
        or _SHA256.fullmatch(normalized_digest) is None
        or not isinstance(fingerprint, str)
        or _SHA256.fullmatch(fingerprint) is None
    ):
        raise WorkflowLanguageCompatibilityError(
            "workflow_language_snapshot_invalid",
            "workflow language snapshot digests must be lowercase SHA-256 values",
        )
    structured_outputs = (
        MappingProxyType({})
        if normalizer_version == 1
        else _read_structured_outputs(value["structured_outputs"])
    )
    return WorkflowLanguageSnapshot(
        effective_profile=profile,
        normalizer_version=normalizer_version,
        normalized_definition_digest=normalized_digest,
        semantic_fingerprint=fingerprint,
        structured_outputs=structured_outputs,
    )


def verify_language_snapshot(
    package: WorkflowPackage,
    package_digest: str,
    snapshot: WorkflowLanguageSnapshot,
) -> None:
    """Fail closed unless sealed and freshly normalized semantics are identical."""
    expected = make_language_snapshot(package, package_digest)
    if snapshot != expected:
        raise WorkflowLanguageCompatibilityError(
            "workflow_language_snapshot_mismatch",
            "workflow language snapshot does not match sealed package semantics",
        )


def language_projection(
    metadata: WorkflowLanguageMetadata, *, semantic_fingerprint: str | None = None
) -> dict[str, object]:
    """Return a bounded, JSON-safe projection of immutable language metadata."""
    projection: dict[str, object] = {
        "declared_profile": (
            metadata.declared_profile.value
            if metadata.declared_profile is not None
            else None
        ),
        "effective_profile": metadata.effective_profile.value,
        "normalizer_version": metadata.normalizer_version,
        "normalized_definition_digest": metadata.normalized_definition_digest,
    }
    if semantic_fingerprint is not None:
        projection["semantic_fingerprint"] = semantic_fingerprint
    return projection


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_thaw(item) for item in value]
    return value


def _structured_outputs_projection(
    structured_outputs: Mapping[str, WorkflowStructuredOutput],
) -> dict[str, object]:
    return {
        node_id: {
            "canonical_schema": _thaw(output.canonical_schema),
            "schema_fingerprint": output.schema_fingerprint,
            "canonicalization_version": output.canonicalization_version,
        }
        for node_id, output in sorted(structured_outputs.items())
    }


def _copy_structured_outputs(
    structured_outputs: Mapping[str, WorkflowStructuredOutput],
) -> Mapping[str, WorkflowStructuredOutput]:
    return MappingProxyType({
        node_id: WorkflowStructuredOutput(
            canonical_schema=_thaw(output.canonical_schema),
            schema_fingerprint=output.schema_fingerprint,
            canonicalization_version=output.canonicalization_version,
        )
        for node_id, output in structured_outputs.items()
    })


def _read_structured_outputs(value: object) -> Mapping[str, WorkflowStructuredOutput]:
    if (
        not isinstance(value, Mapping)
        or len(value) > MAX_SNAPSHOTTED_STRUCTURED_OUTPUTS
        or any(not isinstance(node_id, str) or not node_id for node_id in value)
    ):
        raise WorkflowLanguageCompatibilityError(
            "workflow_language_snapshot_invalid",
            "workflow language snapshot structured outputs are invalid",
        )
    outputs: dict[str, WorkflowStructuredOutput] = {}
    expected_keys = {
        "canonical_schema",
        "schema_fingerprint",
        "canonicalization_version",
    }
    for node_id, raw_output in value.items():
        if not isinstance(raw_output, Mapping) or set(raw_output) != expected_keys:
            raise WorkflowLanguageCompatibilityError(
                "workflow_language_snapshot_invalid",
                "workflow language snapshot structured outputs are invalid",
            )
        version = raw_output["canonicalization_version"]
        schema = raw_output["canonical_schema"]
        fingerprint = raw_output["schema_fingerprint"]
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != STRUCTURED_OUTPUT_CANONICALIZATION_VERSION
            or not isinstance(schema, Mapping)
            or not isinstance(fingerprint, str)
            or _SHA256.fullmatch(fingerprint) is None
        ):
            raise WorkflowLanguageCompatibilityError(
                "workflow_language_snapshot_invalid",
                "workflow language snapshot structured outputs are invalid",
            )
        try:
            normalized_schema = normalize_schema(schema)
        except StructuredOutputError as exc:
            raise WorkflowLanguageCompatibilityError(
                "workflow_language_snapshot_invalid",
                "workflow language snapshot structured outputs are invalid",
            ) from exc
        if (
            _thaw(normalized_schema.canonical_schema) != _thaw(schema)
            or normalized_schema.schema_fingerprint != fingerprint
        ):
            raise WorkflowLanguageCompatibilityError(
                "workflow_language_snapshot_invalid",
                "workflow language snapshot structured outputs are invalid",
            )
        outputs[node_id] = WorkflowStructuredOutput(
            canonical_schema=normalized_schema.canonical_schema,
            schema_fingerprint=fingerprint,
            canonicalization_version=version,
        )
    return MappingProxyType(outputs)


def prove_output_path_impossible(
    schema: Mapping[str, object], path_parts: tuple[str, ...] | list[str]
) -> bool:
    """Prove a normalized output field path cannot exist without runtime guesses."""
    if not path_parts:
        return False
    return _schema_path_impossible(schema, tuple(path_parts), schema, frozenset())


def _schema_path_impossible(
    schema: object,
    path_parts: tuple[str, ...],
    root: Mapping[str, object],
    resolving: frozenset[str],
) -> bool:
    if schema is False:
        return True
    if schema is True or not isinstance(schema, Mapping):
        return False

    schema_type = schema.get("type")
    if isinstance(schema_type, str) and schema_type != "object":
        return True
    if isinstance(schema_type, (tuple, list)) and "object" not in schema_type:
        return True

    if "$ref" in schema:
        target = _resolve_local_ref(root, schema["$ref"])
        reference = str(schema["$ref"])
        if target is not None and reference not in resolving:
            if _schema_path_impossible(
                target, path_parts, root, resolving | frozenset({reference})
            ):
                return True

    local = _object_property_path_impossible(schema, path_parts, root, resolving)
    if local:
        return True

    all_of = schema.get("allOf")
    if isinstance(all_of, (tuple, list)) and any(
        _schema_path_impossible(branch, path_parts, root, resolving)
        for branch in all_of
    ):
        return True
    for union_keyword in ("anyOf", "oneOf"):
        union = schema.get(union_keyword)
        if (
            isinstance(union, (tuple, list))
            and union
            and all(
                _schema_path_impossible(branch, path_parts, root, resolving)
                for branch in union
            )
        ):
            return True
    return False


def _object_property_path_impossible(
    schema: Mapping[str, object],
    path_parts: tuple[str, ...],
    root: Mapping[str, object],
    resolving: frozenset[str],
) -> bool:
    property_name, *remaining = path_parts
    properties = schema.get("properties")
    if isinstance(properties, Mapping) and property_name in properties:
        if not remaining:
            return properties[property_name] is False
        return _schema_path_impossible(
            properties[property_name], tuple(remaining), root, resolving
        )

    patterns = schema.get("patternProperties")
    if isinstance(patterns, Mapping) and patterns:
        return False
    additional = schema.get("additionalProperties", True)
    if additional is False:
        return True
    if isinstance(additional, Mapping) and remaining:
        return _schema_path_impossible(additional, tuple(remaining), root, resolving)
    return additional is False


def _resolve_local_ref(root: Mapping[str, object], value: object) -> object | None:
    if not isinstance(value, str) or not value.startswith("#/"):
        return None
    current: object = root
    for raw_part in value[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


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
    return sha256(
        canonical_json_bytes(
            document,
            max_bytes=_MAX_NORMALIZED_DEFINITION_CANONICAL_BYTES,
        )
    ).hexdigest()


def _canonical_json(value: Any) -> str:
    return canonical_json_bytes(
        value,
        max_bytes=_MAX_NORMALIZED_DEFINITION_CANONICAL_BYTES,
    ).decode("utf-8")
