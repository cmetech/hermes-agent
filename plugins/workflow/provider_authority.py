"""Immutable provider/model authority for normalizer-v5 workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Mapping

from agent.plugin_agent import (
    PLUGIN_AGENT_MCP_IMPORT_POLICY_VERSION,
    plugin_agent_python_runtime_identity,
)

from hermes_cli.provider_capabilities import (
    CapabilityDisposition,
    ProviderCapabilityDecision,
    WorkflowProviderFeature,
    resolve_provider_capability,
)
from hermes_cli.runtime_provider import (
    ExecutionRuntimeCapabilities,
    classify_execution_runtime,
)
from hermes_cli.workflow_model_resolution import (
    ModelResolutionError,
    ResolvedModelRoute,
    WorkflowModelConfigSnapshot,
    resolve_workflow_model_reference,
)
from plugins.workflow.language import supports_phase5_semantics
from plugins.workflow.models import WorkflowPackage, freeze_value
from plugins.workflow.sanitize import public_display_identifier, sanitize_text


_AUTHORITY_SCHEMA_VERSION = 1
_AUTHORITY_RESOLVER_VERSION = 1
_AUTHORITY_MAX_BYTES = 1024 * 1024
_AUTHORITY_MAX_ROUTES = 512
_AUTHORITY_MAX_OBLIGATIONS = 4096
_AUTHORITY_MAX_WARNINGS = 512
_AUTHORITY_MAX_TEXT_CHARS = 512
_SHA256 = frozenset("0123456789abcdef")
_SUPPORTED_HOOK_EVENTS = frozenset({
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
})
_SUPPORTED_HOOK_OPERATIONS = frozenset({
    "continue",
    "decision",
    "stop_reason",
    "current_turn_context",
    "permission_decision",
    "permission_decision_reason",
    "update_input",
    "additional_context",
    "replace_mcp_tool_output",
    "elicitation_action",
    "elicitation_content",
})


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and not set(value) - _SHA256


@dataclass(frozen=True, slots=True)
class ProviderAuthorityEnvironment:
    """Concrete Hermes adapter preconditions captured for one assessment."""

    session_store_available: bool
    mcp_available: bool
    hook_lifecycle_available: bool
    inline_agent_available: bool
    web_service_available: bool
    authoritative_cost_available: bool

    def __post_init__(self) -> None:
        if any(
            not isinstance(getattr(self, name), bool)
            for name in self.__dataclass_fields__
        ):
            raise TypeError("provider authority environment facts must be boolean")


@dataclass(frozen=True, slots=True)
class ProviderAuthorityWarning:
    path: str
    code: str
    rationale: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "code": self.code,
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class WorkflowResolvedProviderRoute:
    route_id: str
    node_id: str
    role: str
    inline_agent_id: str | None
    reference_kind: str
    requested_reference_sha256: str
    provider: str
    model: str
    api_mode: str
    route_fingerprint: str
    registration_provenance_digest: str
    provider_options: Mapping[str, Any]
    config_scope: str
    base_url_trust_class: str

    def __post_init__(self) -> None:
        if not _valid_digest(self.requested_reference_sha256):
            raise ValueError("requested model reference digest is invalid")
        if not _valid_digest(self.route_fingerprint):
            raise ValueError("provider route fingerprint is invalid")
        object.__setattr__(
            self,
            "provider_options",
            freeze_value(_bounded_provider_option(_thaw(self.provider_options))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "node_id": self.node_id,
            "role": self.role,
            "inline_agent_id": self.inline_agent_id,
            "reference_kind": self.reference_kind,
            "requested_reference_sha256": self.requested_reference_sha256,
            "provider": self.provider,
            "model": self.model,
            "api_mode": self.api_mode,
            "route_fingerprint": self.route_fingerprint,
            "registration_provenance_digest": (self.registration_provenance_digest),
            "provider_options": _thaw(self.provider_options),
            "config_scope": self.config_scope,
            "base_url_trust_class": self.base_url_trust_class,
        }


@dataclass(frozen=True, slots=True)
class WorkflowCapabilityObligation:
    path: str
    route_id: str
    decision: ProviderCapabilityDecision

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "route_id": self.route_id,
            "decision": self.decision.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class WorkflowProviderAuthority:
    config_fingerprint: str
    routes: Mapping[str, WorkflowResolvedProviderRoute]
    obligations: tuple[WorkflowCapabilityObligation, ...]
    warnings: tuple[ProviderAuthorityWarning, ...]
    authority_digest: str
    schema_version: int = _AUTHORITY_SCHEMA_VERSION
    resolver_version: int = _AUTHORITY_RESOLVER_VERSION
    obligations_by_path: Mapping[str, tuple[WorkflowCapabilityObligation, ...]] = field(
        init=False, repr=False
    )

    def __post_init__(self) -> None:
        if not _valid_digest(self.config_fingerprint):
            raise ValueError("provider config fingerprint is invalid")
        if not _valid_digest(self.authority_digest):
            raise ValueError("provider authority digest is invalid")
        frozen_routes = MappingProxyType(dict(sorted(self.routes.items())))
        obligations = tuple(self.obligations)
        warnings = tuple(self.warnings)
        by_path: dict[str, list[WorkflowCapabilityObligation]] = {}
        for obligation in obligations:
            by_path.setdefault(obligation.path, []).append(obligation)
        object.__setattr__(self, "routes", frozen_routes)
        object.__setattr__(self, "obligations", obligations)
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(
            self,
            "obligations_by_path",
            MappingProxyType({
                path: tuple(items) for path, items in sorted(by_path.items())
            }),
        )

    @property
    def unsupported_obligations(self) -> tuple[WorkflowCapabilityObligation, ...]:
        return tuple(
            item
            for item in self.obligations
            if item.decision.disposition is CapabilityDisposition.UNSUPPORTED
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "resolver_version": self.resolver_version,
            "config_fingerprint": self.config_fingerprint,
            "routes": [route.to_dict() for route in self.routes.values()],
            "obligations": [item.to_dict() for item in self.obligations],
            "warnings": [warning.to_dict() for warning in self.warnings],
            "authority_digest": self.authority_digest,
        }

    def canonical_bytes(self) -> bytes:
        """Return the exact credential-free bytes sealed in format-2 roots."""
        encoded = json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        if len(encoded) > _AUTHORITY_MAX_BYTES:
            raise ValueError("provider authority exceeds its byte limit")
        return encoded


def _public_capability_level(authority: WorkflowProviderAuthority) -> str:
    dispositions = {item.decision.disposition for item in authority.obligations}
    if CapabilityDisposition.UNSUPPORTED in dispositions:
        return "unsupported"
    if CapabilityDisposition.DEGRADED_WITH_EXPLICIT_SEMANTICS in dispositions:
        return "degraded"
    return "portable"


def _public_projection_text(value: object, *, max_chars: int = 256) -> str:
    cleaned, _truncated = sanitize_text(str(value), max_chars=max_chars)
    return cleaned


def _public_semantics(value: Any) -> Any:
    """Project already-bounded capability semantics without trusting string values."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return public_display_identifier(value)
    if isinstance(value, Mapping):
        return {str(key): _public_semantics(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_public_semantics(item) for item in value]
    return public_display_identifier(value)


def public_provider_capability_projection(
    authority: WorkflowProviderAuthority,
    *,
    include_details: bool = False,
) -> dict[str, Any]:
    """Return the single closed provider-capability projection for public clients."""
    providers = {route.provider for route in authority.routes.values()}
    unsupported = sum(
        item.decision.disposition is CapabilityDisposition.UNSUPPORTED
        for item in authority.obligations
    )
    degraded = sum(
        item.decision.disposition
        is CapabilityDisposition.DEGRADED_WITH_EXPLICIT_SEMANTICS
        for item in authority.obligations
    )
    projection: dict[str, Any] = {
        "schema_version": 1,
        "level": _public_capability_level(authority),
        "resolved_route_count": len(authority.routes),
        "mixed_provider": len(providers) > 1,
        "unsupported_count": unsupported,
        "degraded_count": degraded,
        "warning_codes": sorted({item.code for item in authority.warnings}),
        "authority_digest": authority.authority_digest,
    }
    if not include_details:
        return projection
    projection["routes"] = [
        {
            "node_id": _public_projection_text(route.node_id, max_chars=128),
            "role": route.role.split(":", 1)[0],
            "inline_agent_id": (
                None
                if route.inline_agent_id is None
                else _public_projection_text(route.inline_agent_id, max_chars=128)
            ),
            "reference_kind": route.reference_kind,
            "provider": public_display_identifier(route.provider),
            "model": public_display_identifier(route.model),
        }
        for route in authority.routes.values()
    ]
    projection["decisions"] = [
        {
            "path": _public_projection_text(item.path),
            "feature": item.decision.feature.value,
            "disposition": item.decision.disposition.value,
            "provider": public_display_identifier(item.decision.provider),
            "model": public_display_identifier(item.decision.model),
            "option": (
                None
                if item.decision.option is None
                else _public_projection_text(item.decision.option, max_chars=128)
            ),
            "effective_semantics": _public_semantics(
                item.decision.effective_semantics
            ),
            "code": _public_projection_text(item.decision.code, max_chars=128),
        }
        for item in authority.obligations
    ]
    return projection


class WorkflowProviderAuthorityError(ValueError):
    def __init__(self, code: str, path: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


def _exact_record(
    value: object,
    fields: frozenset[str],
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{label} fields are invalid")
    return value


def _bounded_text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or (not allow_empty and not value)
        or len(value) > _AUTHORITY_MAX_TEXT_CHARS
        or any(
            ord(character) < 0x20 or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
    ):
        raise ValueError(f"{label} is invalid")
    return value


def _bounded_provider_option(value: object, *, depth: int = 0) -> Any:
    if depth > 4:
        raise ValueError("provider option depth is invalid")
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("provider option number is invalid")
        return value
    if isinstance(value, str):
        return _bounded_text(value, "provider option text", allow_empty=True)
    if isinstance(value, list | tuple):
        if len(value) > 32:
            raise ValueError("provider option sequence is invalid")
        return tuple(_bounded_provider_option(item, depth=depth + 1) for item in value)
    if isinstance(value, Mapping):
        if len(value) > 32:
            raise ValueError("provider option mapping is invalid")
        bounded: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = _bounded_text(raw_key, "provider option key")
            if any(
                part in key.lower()
                for part in ("secret", "token", "password", "credential", "api_key")
            ):
                raise ValueError("provider option key is not public-safe")
            bounded[key] = _bounded_provider_option(item, depth=depth + 1)
        return bounded
    raise ValueError("provider option value is invalid")


def _read_route(value: object) -> WorkflowResolvedProviderRoute:
    record = _exact_record(
        value,
        frozenset({
            "route_id",
            "node_id",
            "role",
            "inline_agent_id",
            "reference_kind",
            "requested_reference_sha256",
            "provider",
            "model",
            "api_mode",
            "route_fingerprint",
            "registration_provenance_digest",
            "provider_options",
            "config_scope",
            "base_url_trust_class",
        }),
        "provider route",
    )
    inline_agent_id = record["inline_agent_id"]
    if inline_agent_id is not None:
        inline_agent_id = _bounded_text(inline_agent_id, "inline agent ID")
    reference_kind = _bounded_text(record["reference_kind"], "reference kind")
    if reference_kind not in {"tier", "configured_alias", "literal"}:
        raise ValueError("reference kind is invalid")
    options = record["provider_options"]
    if not isinstance(options, Mapping):
        raise ValueError("provider options are invalid")
    return WorkflowResolvedProviderRoute(
        route_id=_bounded_text(record["route_id"], "route ID"),
        node_id=_bounded_text(record["node_id"], "node ID"),
        role=_bounded_text(record["role"], "route role"),
        inline_agent_id=inline_agent_id,
        reference_kind=reference_kind,
        requested_reference_sha256=_bounded_text(
            record["requested_reference_sha256"], "model reference digest"
        ),
        provider=_bounded_text(record["provider"], "provider"),
        model=_bounded_text(record["model"], "model"),
        api_mode=_bounded_text(record["api_mode"], "API mode", allow_empty=True),
        route_fingerprint=_bounded_text(
            record["route_fingerprint"], "route fingerprint"
        ),
        registration_provenance_digest=_bounded_text(
            record["registration_provenance_digest"],
            "registration provenance digest",
            allow_empty=True,
        ),
        provider_options=_bounded_provider_option(options),
        config_scope=_bounded_text(record["config_scope"], "config scope"),
        base_url_trust_class=_bounded_text(
            record["base_url_trust_class"], "base URL trust class"
        ),
    )


def _read_obligation(value: object) -> WorkflowCapabilityObligation:
    record = _exact_record(
        value,
        frozenset({"path", "route_id", "decision"}),
        "provider obligation",
    )
    decision_record = _exact_record(
        record["decision"],
        frozenset({
            "feature",
            "disposition",
            "provider",
            "model",
            "option",
            "requested_semantics",
            "effective_semantics",
            "adapter_version",
            "declaration_source",
            "registration_provenance_digest",
            "code",
            "rationale",
        }),
        "provider capability decision",
    )
    try:
        feature = WorkflowProviderFeature(decision_record["feature"])
        disposition = CapabilityDisposition(decision_record["disposition"])
    except (TypeError, ValueError) as exc:
        raise ValueError("provider capability enum is invalid") from exc
    option = decision_record["option"]
    if option is not None:
        option = _bounded_text(option, "provider capability option")
    adapter_version = decision_record["adapter_version"]
    if adapter_version is not None and (
        type(adapter_version) is not int or adapter_version <= 0
    ):
        raise ValueError("provider capability adapter version is invalid")
    requested = decision_record["requested_semantics"]
    effective = decision_record["effective_semantics"]
    if not isinstance(requested, Mapping) or not isinstance(effective, Mapping):
        raise ValueError("provider capability semantics are invalid")
    decision = ProviderCapabilityDecision(
        feature=feature,
        disposition=disposition,
        provider=_bounded_text(decision_record["provider"], "decision provider"),
        model=_bounded_text(
            decision_record["model"], "decision model", allow_empty=True
        ),
        option=option,
        requested_semantics=requested,
        effective_semantics=effective,
        adapter_version=adapter_version,
        declaration_source=_bounded_text(
            decision_record["declaration_source"], "declaration source"
        ),
        registration_provenance_digest=_bounded_text(
            decision_record["registration_provenance_digest"],
            "decision provenance digest",
            allow_empty=True,
        ),
        code=_bounded_text(decision_record["code"], "decision code"),
        rationale=_bounded_text(decision_record["rationale"], "decision rationale"),
    )
    return WorkflowCapabilityObligation(
        path=_bounded_text(record["path"], "obligation path"),
        route_id=_bounded_text(record["route_id"], "obligation route ID"),
        decision=decision,
    )


def read_workflow_provider_authority_bytes(
    encoded: bytes,
) -> WorkflowProviderAuthority:
    """Decode one exact bounded canonical provider authority snapshot."""
    if not isinstance(encoded, bytes) or len(encoded) > _AUTHORITY_MAX_BYTES:
        raise ValueError("provider authority bytes are invalid")
    try:
        raw = json.loads(encoded)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("provider authority JSON is invalid") from exc
    record = _exact_record(
        raw,
        frozenset({
            "schema_version",
            "resolver_version",
            "config_fingerprint",
            "routes",
            "obligations",
            "warnings",
            "authority_digest",
        }),
        "provider authority",
    )
    if (
        type(record["schema_version"]) is not int
        or record["schema_version"] != _AUTHORITY_SCHEMA_VERSION
        or type(record["resolver_version"]) is not int
        or record["resolver_version"] != _AUTHORITY_RESOLVER_VERSION
    ):
        raise ValueError("provider authority version is unsupported")
    raw_routes = record["routes"]
    raw_obligations = record["obligations"]
    raw_warnings = record["warnings"]
    if (
        not isinstance(raw_routes, list)
        or len(raw_routes) > _AUTHORITY_MAX_ROUTES
        or not isinstance(raw_obligations, list)
        or len(raw_obligations) > _AUTHORITY_MAX_OBLIGATIONS
        or not isinstance(raw_warnings, list)
        or len(raw_warnings) > _AUTHORITY_MAX_WARNINGS
    ):
        raise ValueError("provider authority collection is invalid")
    routes: dict[str, WorkflowResolvedProviderRoute] = {}
    for raw_route in raw_routes:
        route = _read_route(raw_route)
        if route.route_id in routes:
            raise ValueError("provider route ID is duplicated")
        routes[route.route_id] = route
    obligations = tuple(_read_obligation(item) for item in raw_obligations)
    warnings: list[ProviderAuthorityWarning] = []
    for item in raw_warnings:
        warning = _exact_record(
            item,
            frozenset({"path", "code", "rationale"}),
            "provider authority warning",
        )
        warnings.append(
            ProviderAuthorityWarning(
                path=_bounded_text(warning["path"], "warning path"),
                code=_bounded_text(warning["code"], "warning code"),
                rationale=_bounded_text(warning["rationale"], "warning rationale"),
            )
        )
    authority = WorkflowProviderAuthority(
        config_fingerprint=_bounded_text(
            record["config_fingerprint"], "config fingerprint"
        ),
        routes=routes,
        obligations=obligations,
        warnings=tuple(warnings),
        authority_digest=_bounded_text(record["authority_digest"], "authority digest"),
    )
    if any(item.route_id not in authority.routes for item in authority.obligations):
        raise ValueError("provider obligation route is missing")
    expected_digest = _authority_digest(
        config_fingerprint=authority.config_fingerprint,
        routes=authority.routes,
        obligations=authority.obligations,
        warnings=authority.warnings,
    )
    if authority.authority_digest != expected_digest:
        raise ValueError("provider authority digest changed")
    if authority.canonical_bytes() != encoded:
        raise ValueError("provider authority is not canonical")
    return authority


def _workflow_provider_options(options: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if "effort" in options:
        result["effort"] = options["effort"]
    elif "modelReasoningEffort" in options:
        result["effort"] = options["modelReasoningEffort"]
    if "thinking" in options:
        result["thinking"] = options["thinking"]
    if "web_execution" in options:
        result["web_execution"] = options["web_execution"]
    elif "webSearchMode" in options:
        result["web_execution"] = options["webSearchMode"]
    if "service_tier" in options:
        result["service_tier"] = options["service_tier"]
    if "betas" in options:
        result["betas"] = options["betas"]
    return result


def _base_url_for_route(
    model_config: WorkflowModelConfigSnapshot, route: ResolvedModelRoute
) -> str:
    if route.reference_kind == "tier":
        entry = model_config.tiers.get(route.requested_reference.strip())
        return entry.base_url if entry is not None else ""
    if route.reference_kind == "configured_alias":
        name = route.requested_reference.strip()[1:].strip().lower()
        entry = model_config.aliases.get(name)
        return entry.base_url if entry is not None else ""
    return (
        model_config.active_base_url
        if route.provider == model_config.active_provider
        else ""
    )


def _runtime_for_route(
    model_config: WorkflowModelConfigSnapshot, route: ResolvedModelRoute
) -> ExecutionRuntimeCapabilities:
    base_url = _base_url_for_route(model_config, route)
    runtime = classify_execution_runtime(
        provider=route.provider,
        model_config={"provider": route.provider, "default": route.model},
        provider_config={
            "api_mode": route.api_mode,
            **({"base_url": base_url} if base_url else {}),
        },
        target_model=route.model,
    )
    if (
        runtime.api_mode != route.api_mode
        or runtime.model != route.model
        or runtime.base_url_trust_class != route.base_url_trust_class
        or runtime.registration_provenance_digest
        != route.registration_provenance_digest
    ):
        raise WorkflowProviderAuthorityError(
            "provider_capability_drift",
            "provider_resolution",
            "resolved model route no longer matches runtime classification",
        )
    return runtime


def _resolved_route(
    *,
    package: WorkflowPackage,
    model_config: WorkflowModelConfigSnapshot,
    default_runtime: ExecutionRuntimeCapabilities,
    node: object,
    node_id: str,
    node_index: int,
    role: str,
    inline_agent_id: str | None,
    reference: object,
    node_options: Mapping[str, Any],
) -> tuple[
    WorkflowResolvedProviderRoute,
    ExecutionRuntimeCapabilities,
    tuple[ProviderAuthorityWarning, ...],
]:
    if not isinstance(reference, str) or not reference.strip():
        reference = default_runtime.model
    if not isinstance(reference, str) or not reference.strip():
        raise WorkflowProviderAuthorityError(
            "model_reference_missing",
            f"nodes[{node_index}].model",
            "AI workflow route has no concrete model reference",
        )
    workflow_provider = package.definition.options.get("provider")
    node_provider = node_options.get("provider")
    try:
        resolved = resolve_workflow_model_reference(
            model_config,
            reference,
            node_provider=(node_provider if isinstance(node_provider, str) else None),
            workflow_provider=(
                workflow_provider if isinstance(workflow_provider, str) else None
            ),
            node_options=_workflow_provider_options(node_options),
            workflow_options=_workflow_provider_options(package.definition.options),
            catalog_source=package.source,
        )
    except ModelResolutionError as exc:
        raise WorkflowProviderAuthorityError(
            exc.code,
            f"nodes[{node_index}].model",
            str(exc),
        ) from exc
    route_id = f"{node_id}:{role}"
    runtime = _runtime_for_route(model_config, resolved)
    route = WorkflowResolvedProviderRoute(
        route_id=route_id,
        node_id=node_id,
        role=role.split(":", 1)[0],
        inline_agent_id=inline_agent_id,
        reference_kind=resolved.reference_kind,
        requested_reference_sha256=_sha256_text(resolved.requested_reference.strip()),
        provider=resolved.provider,
        model=resolved.model,
        api_mode=resolved.api_mode,
        route_fingerprint=resolved.route_fingerprint,
        registration_provenance_digest=(resolved.registration_provenance_digest),
        provider_options=resolved.provider_options,
        config_scope=resolved.config_scope,
        base_url_trust_class=resolved.base_url_trust_class,
    )
    warning_path = (
        f"nodes[{node_index}].agents.{inline_agent_id}.model"
        if inline_agent_id is not None
        else f"nodes[{node_index}].model"
    )
    warnings = tuple(
        ProviderAuthorityWarning(
            path=warning_path,
            code=warning.code,
            rationale=warning.rationale,
        )
        for warning in resolved.warnings
    )
    return route, runtime, warnings


def _authority_digest(
    *,
    config_fingerprint: str,
    routes: Mapping[str, WorkflowResolvedProviderRoute],
    obligations: tuple[WorkflowCapabilityObligation, ...],
    warnings: tuple[ProviderAuthorityWarning, ...],
) -> str:
    payload = {
        "schema_version": _AUTHORITY_SCHEMA_VERSION,
        "resolver_version": _AUTHORITY_RESOLVER_VERSION,
        "config_fingerprint": config_fingerprint,
        "routes": [route.to_dict() for _, route in sorted(routes.items())],
        "obligations": [item.to_dict() for item in obligations],
        "warnings": [warning.to_dict() for warning in warnings],
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def resolve_workflow_provider_authority(
    package: WorkflowPackage,
    *,
    model_config: WorkflowModelConfigSnapshot,
    default_runtime: ExecutionRuntimeCapabilities,
    environment: ProviderAuthorityEnvironment,
    mcp_execution_preconditions: Mapping[str, bool] | None = None,
) -> WorkflowProviderAuthority:
    """Resolve one complete credential-free v5 provider authority."""
    if not supports_phase5_semantics(
        package.language.effective_profile, package.language.normalizer_version
    ):
        raise WorkflowProviderAuthorityError(
            "provider_authority_version_unsupported",
            "normalizer_version",
            "provider authority requires normalizer v5",
        )
    if not isinstance(model_config, WorkflowModelConfigSnapshot):
        raise TypeError("model_config must be an immutable workflow config snapshot")
    if not isinstance(default_runtime, ExecutionRuntimeCapabilities):
        raise TypeError("default_runtime must be classified runtime capabilities")
    if not isinstance(environment, ProviderAuthorityEnvironment):
        raise TypeError("environment must be provider authority facts")
    config_errors = tuple(
        issue for issue in model_config.issues if issue.severity == "error"
    )
    if config_errors:
        issue = config_errors[0]
        raise WorkflowProviderAuthorityError(issue.code, issue.path, issue.message)

    routes: dict[str, WorkflowResolvedProviderRoute] = {}
    runtimes: dict[str, ExecutionRuntimeCapabilities] = {}
    warnings: list[ProviderAuthorityWarning] = []
    obligations: list[WorkflowCapabilityObligation] = []

    def add(
        path: str,
        route_id: str,
        feature: WorkflowProviderFeature,
        *,
        option: str | None = None,
        requested: Mapping[str, Any] = MappingProxyType({}),
    ) -> None:
        obligations.append(
            WorkflowCapabilityObligation(
                path=path,
                route_id=route_id,
                decision=resolve_provider_capability(
                    runtimes[route_id],
                    feature=feature,
                    option=option,
                    requested_semantics=requested,
                ),
            )
        )

    root = package.definition.options
    for index, node in enumerate(package.definition.nodes):
        approval_rework = (
            node.node_type == "approval"
            and isinstance(node.value, Mapping)
            and isinstance(node.value.get("on_reject"), Mapping)
        )
        if node.node_type not in {"command", "prompt", "loop"} and not approval_rework:
            continue
        node_options = node.options
        primary_reference = node_options.get("model", root.get("model"))
        primary, primary_runtime, route_warnings = _resolved_route(
            package=package,
            model_config=model_config,
            default_runtime=default_runtime,
            node=node,
            node_id=node.id,
            node_index=index,
            role="primary",
            inline_agent_id=None,
            reference=primary_reference,
            node_options=node_options,
        )
        routes[primary.route_id] = primary
        runtimes[primary.route_id] = primary_runtime
        warnings.extend(route_warnings)

        fallback_reference = node_options.get(
            "fallbackModel", root.get("fallbackModel")
        )
        if fallback_reference is not None:
            fallback, fallback_runtime, route_warnings = _resolved_route(
                package=package,
                model_config=model_config,
                default_runtime=default_runtime,
                node=node,
                node_id=node.id,
                node_index=index,
                role="fallback",
                inline_agent_id=None,
                reference=fallback_reference,
                node_options=node_options,
            )
            routes[fallback.route_id] = fallback
            runtimes[fallback.route_id] = fallback_runtime
            warnings.extend(route_warnings)

        agents = node_options.get("agents")
        if isinstance(agents, Mapping):
            for agent_id, raw_agent in sorted(agents.items()):
                if not isinstance(agent_id, str) or not isinstance(raw_agent, Mapping):
                    continue
                inline, inline_runtime, route_warnings = _resolved_route(
                    package=package,
                    model_config=model_config,
                    default_runtime=default_runtime,
                    node=node,
                    node_id=node.id,
                    node_index=index,
                    role=f"inline_agent:{agent_id}",
                    inline_agent_id=agent_id,
                    reference=raw_agent.get("model", primary_reference),
                    node_options=node_options,
                )
                routes[inline.route_id] = inline
                runtimes[inline.route_id] = inline_runtime
                warnings.extend(route_warnings)
                for field_name in ("tools", "disallowedTools"):
                    if field_name in raw_agent:
                        add(
                            f"nodes[{index}].agents.{agent_id}.{field_name}",
                            inline.route_id,
                            WorkflowProviderFeature.TOOL_RESTRICTIONS,
                            option=field_name,
                            requested={
                                "explicit_empty": not bool(raw_agent[field_name]),
                                "hermes_schema_selection": True,
                                "hermes_dispatch": True,
                            },
                        )
                if "skills" in raw_agent:
                    add(
                        f"nodes[{index}].agents.{agent_id}.skills",
                        inline.route_id,
                        WorkflowProviderFeature.SKILLS_INLINE_AGENTS,
                        option="skills",
                        requested={"current_turn_content": True},
                    )

        primary_id = primary.route_id
        output = package.language.structured_outputs.get(node.id)
        if output is not None:
            add(
                f"nodes[{index}].output_format",
                primary_id,
                WorkflowProviderFeature.STRUCTURED_OUTPUT,
                option="json_schema",
                requested={"schema_fingerprint": output.schema_fingerprint},
            )
        if root.get("persist_sessions") is True:
            add(
                "persist_sessions",
                primary_id,
                WorkflowProviderFeature.SESSION_RESUMPTION,
                requested={
                    "session_available": environment.session_store_available,
                    "cache_fingerprint_stable": True,
                    "hermes_conversation_loop": (
                        primary_runtime.hermes_managed_tool_loop
                    ),
                },
            )
        if node_options.get("persist_session") is True:
            add(
                f"nodes[{index}].persist_session",
                primary_id,
                WorkflowProviderFeature.SESSION_RESUMPTION,
                requested={
                    "session_available": environment.session_store_available,
                    "cache_fingerprint_stable": True,
                    "hermes_conversation_loop": (
                        primary_runtime.hermes_managed_tool_loop
                    ),
                },
            )
        for field_name in ("allowed_tools", "denied_tools"):
            if field_name in node_options:
                add(
                    f"nodes[{index}].{field_name}",
                    primary_id,
                    WorkflowProviderFeature.TOOL_RESTRICTIONS,
                    option=field_name,
                    requested={
                        "explicit_empty": not bool(node_options[field_name]),
                        "hermes_schema_selection": True,
                        "hermes_dispatch": True,
                    },
                )
        portability = package.language.node_semantics.get(node.id, {}).get(
            "provider_portability"
        )
        hooks = portability.get("hooks", ()) if isinstance(portability, Mapping) else ()
        for hook_index, hook in enumerate(hooks):
            if not isinstance(hook, Mapping):
                continue
            operations = hook.get("operations", ())
            operation_names = {
                operation.get("name")
                for operation in operations
                if isinstance(operation, Mapping)
            }
            event = str(hook.get("event", ""))
            add(
                f"nodes[{index}].hooks.{event}[{hook_index}]",
                primary_id,
                WorkflowProviderFeature.HOOKS,
                option=event,
                requested={
                    "normalized": True,
                    "events_supported": (
                        event in _SUPPORTED_HOOK_EVENTS
                        and operation_names <= _SUPPORTED_HOOK_OPERATIONS
                    ),
                    "lifecycle_available": environment.hook_lifecycle_available,
                },
            )
        if "mcp" in node_options:
            mcp_precondition = (
                mcp_execution_preconditions is None
                or mcp_execution_preconditions.get(node.id) is True
            )
            add(
                f"nodes[{index}].mcp",
                primary_id,
                WorkflowProviderFeature.MCP,
                option="stdio",
                requested={
                    "sealed_definition": mcp_precondition,
                    "bounded_lifecycle": True,
                    "dependency_available": environment.mcp_available,
                    "teardown_guaranteed": environment.mcp_available,
                    "runtime_identity_digest": (
                        plugin_agent_python_runtime_identity()
                    ),
                    "import_policy_version": (
                        PLUGIN_AGENT_MCP_IMPORT_POLICY_VERSION
                    ),
                },
            )
        if "skills" in node_options:
            add(
                f"nodes[{index}].skills",
                primary_id,
                WorkflowProviderFeature.SKILLS_INLINE_AGENTS,
                option="skills",
                requested={"current_turn_content": True},
            )
        if "agents" in node_options:
            add(
                f"nodes[{index}].agents",
                primary_id,
                WorkflowProviderFeature.SKILLS_INLINE_AGENTS,
                option="inline_agents",
                requested={
                    "declared_worker_tool": environment.inline_agent_available,
                    "shared_limits": environment.inline_agent_available,
                },
            )
        node_route_ids = tuple(
            route_id
            for route_id, route in routes.items()
            if route.node_id == node.id
        )
        raw_allowed_tools = node_options.get("allowed_tools")
        raw_denied_tools = node_options.get("denied_tools", ())
        web_tool_names = {"web_search", "WebSearch"}
        web_tool_reachable = (
            raw_allowed_tools is None
            or bool(web_tool_names.intersection(raw_allowed_tools))
        ) and not bool(web_tool_names.intersection(raw_denied_tools))
        for route_id in node_route_ids:
            effective_options = routes[route_id].provider_options
            for option_name in ("effort", "thinking", "betas", "service_tier"):
                if option_name not in effective_options:
                    continue
                if option_name in node_options:
                    option_path = f"nodes[{index}].{option_name}"
                elif option_name == "effort" and "modelReasoningEffort" in root:
                    option_path = "modelReasoningEffort"
                else:
                    option_path = option_name
                add(
                    option_path,
                    route_id,
                    WorkflowProviderFeature.EFFORT_THINKING,
                    option=option_name,
                    requested={"value": effective_options[option_name]},
                )
            if "web_execution" in effective_options:
                web_option = str(effective_options["web_execution"])
                add(
                    "webSearchMode",
                    route_id,
                    WorkflowProviderFeature.WEB_EXECUTION,
                    option=web_option,
                    requested={
                        "value": web_option,
                        "service_available": (
                            environment.web_service_available
                            and web_tool_reachable
                        ),
                        "explicit_mapping": web_option == "hermes_tool",
                    },
                )
        for root_name, option_name in (
            ("modelReasoningEffort", "effort"),
            ("effort", "effort"),
            ("thinking", "thinking"),
            ("betas", "betas"),
            ("service_tier", "service_tier"),
        ):
            if root_name in root:
                if any(
                    obligation.path == root_name
                    and obligation.route_id == primary_id
                    and obligation.decision.feature
                    is WorkflowProviderFeature.EFFORT_THINKING
                    and obligation.decision.option == option_name
                    for obligation in obligations
                ):
                    continue
                add(
                    root_name,
                    primary_id,
                    WorkflowProviderFeature.EFFORT_THINKING,
                    option=option_name,
                    requested={"value": root[root_name]},
                )
        if "maxBudgetUsd" in node_options:
            available = environment.authoritative_cost_available
            add(
                f"nodes[{index}].maxBudgetUsd",
                primary_id,
                WorkflowProviderFeature.COST_BUDGETS,
                option="maxBudgetUsd",
                requested={
                    "authoritative_settlement": available,
                    "all_billable_routes_observed": available,
                    "single_unsettled": available,
                },
            )

        if "sandbox" in root:
            add(
                "sandbox",
                primary_id,
                WorkflowProviderFeature.PROVIDER_NATIVE_SANDBOX,
                option="sandbox",
                requested={"value": root["sandbox"]},
            )
        if fallback_reference is not None:
            add(
                (
                    f"nodes[{index}].fallbackModel"
                    if "fallbackModel" in node_options
                    else "fallbackModel"
                ),
                primary_id,
                WorkflowProviderFeature.FALLBACK_MODELS,
                requested={
                    "route_resolved": True,
                    "fresh_context": True,
                    "shared_authorities": True,
                },
            )
        if "sandbox" in node_options:
            add(
                f"nodes[{index}].sandbox",
                primary_id,
                WorkflowProviderFeature.PROVIDER_NATIVE_SANDBOX,
                option="sandbox",
                requested={"value": node_options["sandbox"]},
            )

    obligations.sort(
        key=lambda item: (
            item.path,
            item.route_id,
            item.decision.feature.value,
            item.decision.option or "",
        )
    )
    warning_tuple = tuple(
        sorted(
            {
                (warning.path, warning.code, warning.rationale): warning
                for warning in warnings
            }.values(),
            key=lambda warning: (warning.path, warning.code, warning.rationale),
        )
    )
    obligation_tuple = tuple(obligations)
    digest = _authority_digest(
        config_fingerprint=model_config.config_fingerprint,
        routes=routes,
        obligations=obligation_tuple,
        warnings=warning_tuple,
    )
    return WorkflowProviderAuthority(
        config_fingerprint=model_config.config_fingerprint,
        routes=routes,
        obligations=obligation_tuple,
        warnings=warning_tuple,
        authority_digest=digest,
    )


__all__ = [
    "ProviderAuthorityEnvironment",
    "ProviderAuthorityWarning",
    "WorkflowCapabilityObligation",
    "WorkflowProviderAuthority",
    "WorkflowProviderAuthorityError",
    "WorkflowResolvedProviderRoute",
    "public_provider_capability_projection",
    "read_workflow_provider_authority_bytes",
    "resolve_workflow_provider_authority",
]
