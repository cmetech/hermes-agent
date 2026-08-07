"""Pure machine authority for workflow/provider capability decisions.

The resolver consumes an already classified, credential-free execution route.
It never probes a provider, loads credentials, or accepts capability authority
from workflow data. Provider declarations are useful only when they are bound
to the loader-authored registration that classified the route.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping


class CapabilityDisposition(str, Enum):
    NATIVE = "native"
    HERMES_ADAPTER = "hermes_adapter"
    DEGRADED_WITH_EXPLICIT_SEMANTICS = "degraded_with_explicit_semantics"
    UNSUPPORTED = "unsupported"


class WorkflowProviderFeature(str, Enum):
    STRUCTURED_OUTPUT = "structured_output"
    SESSION_RESUMPTION = "session_resumption"
    TOOL_RESTRICTIONS = "tool_restrictions"
    HOOKS = "hooks"
    MCP = "mcp"
    SKILLS_INLINE_AGENTS = "skills_inline_agents"
    EFFORT_THINKING = "effort_thinking"
    FALLBACK_MODELS = "fallback_models"
    WEB_EXECUTION = "web_execution"
    COST_BUDGETS = "cost_budgets"
    PROVIDER_NATIVE_SANDBOX = "provider_native_sandbox"


_RESOLVER_VERSION = 1
_ADAPTER_VERSION = 1
_MAX_TEXT_CHARS = 256
_MAX_SEMANTIC_ITEMS = 32
_MAX_SEMANTIC_DEPTH = 4
_MAX_SEMANTICS_BYTES = 2048
_FORBIDDEN_SEMANTIC_KEY_PARTS = (
    "api_key",
    "credential",
    "secret",
    "token",
    "password",
    "prompt",
    "command",
    "header",
    "environment",
    "base_url",
    "path",
)


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _freeze_json(value: Any, *, depth: int = 0) -> Any:
    if depth > _MAX_SEMANTIC_DEPTH:
        raise ValueError("semantic value exceeds depth limit")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("semantic number must be finite")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_TEXT_CHARS:
            raise ValueError("semantic string exceeds length limit")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_SEMANTIC_ITEMS:
            raise ValueError("semantic mapping exceeds item limit")
        frozen: dict[str, Any] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str) or not raw_key or len(raw_key) > 80:
                raise ValueError("semantic key is invalid")
            normalized_key = raw_key.strip().lower()
            if any(part in normalized_key for part in _FORBIDDEN_SEMANTIC_KEY_PARTS):
                raise ValueError("semantic key is not public-safe")
            frozen[raw_key] = _freeze_json(item, depth=depth + 1)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_SEMANTIC_ITEMS:
            raise ValueError("semantic sequence exceeds item limit")
        return tuple(_freeze_json(item, depth=depth + 1) for item in value)
    raise ValueError("semantic value is not JSON-safe")


def _safe_semantics(value: Mapping[str, Any] | object) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        frozen = _freeze_json(value)
        encoded = json.dumps(
            _thaw_json(frozen),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    if len(encoded) > _MAX_SEMANTICS_BYTES:
        return None
    return frozen


@dataclass(frozen=True, slots=True)
class ProviderCapabilityDecision:
    feature: WorkflowProviderFeature
    disposition: CapabilityDisposition
    provider: str
    model: str
    option: str | None
    requested_semantics: Mapping[str, Any]
    effective_semantics: Mapping[str, Any]
    adapter_version: int | None
    declaration_source: str
    registration_provenance_digest: str
    code: str
    rationale: str

    def __post_init__(self) -> None:
        requested = _safe_semantics(self.requested_semantics)
        effective = _safe_semantics(self.effective_semantics)
        if requested is None or effective is None:
            raise ValueError("capability decision semantics must be bounded and JSON-safe")
        object.__setattr__(self, "requested_semantics", requested)
        object.__setattr__(self, "effective_semantics", effective)

    def to_dict(self) -> dict[str, Any]:
        """Return the closed public JSON projection used for identity/sealing."""

        return {
            "feature": self.feature.value,
            "disposition": self.disposition.value,
            "provider": self.provider,
            "model": self.model,
            "option": self.option,
            "requested_semantics": _thaw_json(self.requested_semantics),
            "effective_semantics": _thaw_json(self.effective_semantics),
            "adapter_version": self.adapter_version,
            "declaration_source": self.declaration_source,
            "registration_provenance_digest": self.registration_provenance_digest,
            "code": self.code,
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class ProviderOptionTransport:
    """Closed transport projection for one sealed workflow model route."""

    reasoning_config: Mapping[str, Any]
    request_overrides: Mapping[str, Any]
    hermes_web_tool: bool = False

    def __post_init__(self) -> None:
        reasoning = _safe_semantics(self.reasoning_config)
        overrides = _safe_semantics(self.request_overrides)
        if reasoning is None or overrides is None:
            raise ValueError("provider_option_encoder_unavailable")
        object.__setattr__(self, "reasoning_config", reasoning)
        object.__setattr__(self, "request_overrides", overrides)


def encode_provider_option_transport(
    route: object,
    obligations: Iterable[object],
) -> ProviderOptionTransport:
    """Encode only capability-reviewed options for an immutable route.

    This is intentionally a small allowlist. A provider declaration is not an
    encoder: any accepted declaration without a reviewed request translation
    fails closed before provider transport.
    """

    route_id = getattr(route, "route_id", None)
    provider = getattr(route, "provider", None)
    model = getattr(route, "model", None)
    provenance = getattr(route, "registration_provenance_digest", None)
    options = getattr(route, "provider_options", None)
    if (
        not isinstance(route_id, str)
        or not isinstance(provider, str)
        or not isinstance(model, str)
        or not isinstance(options, Mapping)
    ):
        raise ValueError("provider_option_encoder_unavailable")

    decisions: list[ProviderCapabilityDecision] = []
    for obligation in obligations:
        if getattr(obligation, "route_id", None) != route_id:
            continue
        decision = getattr(obligation, "decision", None)
        if isinstance(decision, ProviderCapabilityDecision):
            decisions.append(decision)

    reasoning: dict[str, Any] = {}
    overrides: dict[str, Any] = {}
    hermes_web_tool = False
    for option, value in options.items():
        feature = (
            WorkflowProviderFeature.WEB_EXECUTION
            if option == "web_execution"
            else WorkflowProviderFeature.EFFORT_THINKING
        )
        matches = [
            decision
            for decision in decisions
            if decision.feature is feature
            and decision.option == (value if option == "web_execution" else option)
            and decision.requested_semantics.get("value", value) == value
        ]
        accepted = [
            decision
            for decision in matches
            if decision.disposition is not CapabilityDisposition.UNSUPPORTED
        ]
        if not accepted:
            raise ValueError("provider_option_encoder_unavailable")
        decision = accepted[0]
        if (
            any(candidate.to_dict() != decision.to_dict() for candidate in accepted[1:])
            or decision.provider != provider
            or decision.model != model
            or decision.registration_provenance_digest != provenance
        ):
            raise ValueError("provider_option_encoder_unavailable")

        if option == "effort":
            if not isinstance(value, str) or not value:
                raise ValueError("provider_option_encoder_unavailable")
            request_field = decision.effective_semantics.get("request_field")
            reasoning.update({"enabled": True, "effort": value})
            if request_field == "verbosity":
                overrides["verbosity"] = value
            elif request_field == "reasoning.effort":
                overrides["extra_body"] = {"reasoning": {"effort": value}}
            else:
                raise ValueError("provider_option_encoder_unavailable")
        elif option == "web_execution":
            if (
                value != "hermes_tool"
                or decision.effective_semantics.get("mode") != "hermes_web_tool"
            ):
                raise ValueError("provider_option_encoder_unavailable")
            hermes_web_tool = True
        else:
            raise ValueError("provider_option_encoder_unavailable")

    return ProviderOptionTransport(
        reasoning_config=reasoning,
        request_overrides=overrides,
        hermes_web_tool=hermes_web_tool,
    )


@dataclass(frozen=True, slots=True)
class _ParsedDeclaration:
    disposition: CapabilityDisposition
    effective_semantics: Mapping[str, Any]
    adapter_version: int | None
    code: str
    rationale: str


def _text(value: object, fallback: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback[:_MAX_TEXT_CHARS]
    return value.strip()[:_MAX_TEXT_CHARS]


def _decision(
    runtime: object,
    feature: WorkflowProviderFeature,
    disposition: CapabilityDisposition,
    *,
    option: str | None,
    requested: Mapping[str, Any],
    effective: Mapping[str, Any] | None = None,
    adapter_version: int | None = None,
    declaration_source: str = "central_resolver",
    code: str,
    rationale: str,
) -> ProviderCapabilityDecision:
    return ProviderCapabilityDecision(
        feature=feature,
        disposition=disposition,
        provider=_text(getattr(runtime, "effective_provider", ""), "unknown"),
        model=_text(getattr(runtime, "model", ""), ""),
        option=_text(option, "") if option is not None else None,
        requested_semantics=requested,
        effective_semantics=effective or {},
        adapter_version=adapter_version,
        declaration_source=_text(declaration_source, "central_resolver"),
        registration_provenance_digest=_text(
            getattr(runtime, "registration_provenance_digest", ""), ""
        ),
        code=_text(code, "provider_capability_unsupported"),
        rationale=_text(rationale, "provider capability is unsupported"),
    )


def _unsupported(
    runtime: object,
    feature: WorkflowProviderFeature,
    *,
    option: str | None,
    requested: Mapping[str, Any],
    code: str,
    rationale: str,
    declaration_source: str = "central_resolver",
) -> ProviderCapabilityDecision:
    return _decision(
        runtime,
        feature,
        CapabilityDisposition.UNSUPPORTED,
        option=option,
        requested=requested,
        code=code,
        rationale=rationale,
        declaration_source=declaration_source,
    )


def _parse_declaration(
    raw: object,
    *,
    option: str | None,
) -> _ParsedDeclaration | None | bool:
    """Return parsed declaration, None for no matching fact, False if invalid."""

    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        return False
    raw_disposition = raw.get("disposition")
    try:
        disposition = CapabilityDisposition(str(raw_disposition))
    except ValueError:
        return False
    if disposition is CapabilityDisposition.HERMES_ADAPTER:
        return False
    options = raw.get("options")
    if options is not None:
        if (
            not isinstance(options, (list, tuple))
            or len(options) > _MAX_SEMANTIC_ITEMS
            or any(not isinstance(item, str) or not item for item in options)
        ):
            return False
        if option not in options:
            return None
    effective = _safe_semantics(raw.get("effective_semantics", {}))
    if effective is None:
        return False
    raw_adapter_version = raw.get("adapter_version")
    adapter_version = None
    if raw_adapter_version is not None:
        if (
            not isinstance(raw_adapter_version, int)
            or isinstance(raw_adapter_version, bool)
            or not 1 <= raw_adapter_version <= 1_000_000
        ):
            return False
        adapter_version = raw_adapter_version
    if (
        disposition is CapabilityDisposition.DEGRADED_WITH_EXPLICIT_SEMANTICS
        and adapter_version is None
    ):
        return False
    return _ParsedDeclaration(
        disposition=disposition,
        effective_semantics=effective,
        adapter_version=adapter_version,
        code=_text(raw.get("code"), "provider_capability_declared"),
        rationale=_text(
            raw.get("rationale"), "provider profile declares exact support"
        ),
    )


def _profile_declaration(
    runtime: object,
    feature: WorkflowProviderFeature,
    option: str | None,
) -> tuple[_ParsedDeclaration | None | bool, object | None]:
    provider = getattr(runtime, "effective_provider", "")
    try:
        from providers import get_provider_registration

        registration = get_provider_registration(provider)
    except Exception:
        registration = None
    if registration is None:
        return None, None

    runtime_digest = getattr(runtime, "registration_provenance_digest", "")
    registration_digest = registration.provenance.code_closure_digest
    if not runtime_digest and not (
        getattr(runtime, "registration_origin_kind", "")
        == registration.provenance.origin_kind
        and getattr(runtime, "registration_distribution_id", "")
        == registration.provenance.distribution_id
    ):
        # An unbound compatibility/runtime fixture cannot consume provider
        # declarations, but generic Hermes-owned adapters remain available.
        return None, registration
    if runtime_digest != registration_digest:
        return False, registration
    try:
        raw = registration.profile.declare_workflow_capability(
            feature.value,
            model=str(getattr(runtime, "model", "") or ""),
            option=option,
        )
    except Exception:
        return False, registration
    return _parse_declaration(raw, option=option), registration


def _resolve_structured_output(
    runtime: object,
    *,
    option: str | None,
    requested: Mapping[str, Any],
) -> ProviderCapabilityDecision:
    declared = getattr(runtime, "declared_structured_output_strategy", None)
    source = getattr(runtime, "structured_output_declaration_source", None)
    if declared == "unsupported":
        if source == "route_query_unclassified":
            return _unsupported(
                runtime,
                WorkflowProviderFeature.STRUCTURED_OUTPUT,
                option=option,
                requested=requested,
                declaration_source="route_query_unclassified",
                code="structured_output_route_unclassified",
                rationale="provider route query cannot be classified without secrets",
            )
        return _unsupported(
            runtime,
            WorkflowProviderFeature.STRUCTURED_OUTPUT,
            option=option,
            requested=requested,
            declaration_source="explicit_unsupported",
            code="structured_output_explicitly_unsupported",
            rationale="provider explicitly forbids structured-output adaptation",
        )
    if not bool(getattr(runtime, "hermes_managed_tool_loop", False)):
        return _unsupported(
            runtime,
            WorkflowProviderFeature.STRUCTURED_OUTPUT,
            option=option,
            requested=requested,
            declaration_source="delegated_runtime",
            code="structured_output_delegated_runtime_unsupported",
            rationale="delegated runtime has no Hermes structured-output contract",
        )
    native = declared in {"native_json_schema", "native_json_mode"}
    trusted = getattr(runtime, "base_url_trust_class", "") == "trusted_direct"
    if native and trusted:
        if (
            source == "provider_profile"
            and not bool(getattr(runtime, "registration_provenance_complete", False))
        ):
            return _unsupported(
                runtime,
                WorkflowProviderFeature.STRUCTURED_OUTPUT,
                option=option,
                requested=requested,
                code="provider_declaration_provenance_incomplete",
                rationale="native provider declaration has incomplete code provenance",
            )
        return _decision(
            runtime,
            WorkflowProviderFeature.STRUCTURED_OUTPUT,
            CapabilityDisposition.NATIVE,
            option=option,
            requested=requested,
            effective={
                "strategy": declared,
                "schema_fingerprint": requested.get("schema_fingerprint", ""),
            },
            declaration_source=source or "provider_profile",
            code="structured_output_native",
            rationale="trusted direct provider explicitly declares native support",
        )
    return _decision(
        runtime,
        WorkflowProviderFeature.STRUCTURED_OUTPUT,
        CapabilityDisposition.HERMES_ADAPTER,
        option=option,
        requested=requested,
        effective={
            "strategy": "prompt_json_schema",
            "schema_fingerprint": requested.get("schema_fingerprint", ""),
        },
        adapter_version=_ADAPTER_VERSION,
        declaration_source="managed_loop_default",
        code="structured_output_hermes_adapter",
        rationale=(
            "Hermes-managed loop uses prompt schema adaptation without a trusted "
            "direct native declaration"
        ),
    )


_GENERIC_ADAPTER_REQUIREMENTS: dict[
    WorkflowProviderFeature, tuple[str, ...]
] = {
    WorkflowProviderFeature.SESSION_RESUMPTION: (
        "session_available",
        "cache_fingerprint_stable",
        "hermes_conversation_loop",
    ),
    WorkflowProviderFeature.TOOL_RESTRICTIONS: (
        "hermes_schema_selection",
        "hermes_dispatch",
    ),
    WorkflowProviderFeature.HOOKS: (
        "normalized",
        "events_supported",
        "lifecycle_available",
    ),
    WorkflowProviderFeature.MCP: (
        "sealed_definition",
        "bounded_lifecycle",
        "dependency_available",
        "teardown_guaranteed",
    ),
    WorkflowProviderFeature.FALLBACK_MODELS: (
        "route_resolved",
        "fresh_context",
        "shared_authorities",
    ),
}


def _generic_adapter(
    runtime: object,
    feature: WorkflowProviderFeature,
    *,
    option: str | None,
    requested: Mapping[str, Any],
) -> ProviderCapabilityDecision | None:
    if not bool(getattr(runtime, "hermes_managed_tool_loop", False)):
        return _unsupported(
            runtime,
            feature,
            option=option,
            requested=requested,
            code="hermes_managed_loop_required",
            rationale="capability requires the Hermes-managed conversation loop",
        )

    requirements = _GENERIC_ADAPTER_REQUIREMENTS.get(feature)
    if requirements is not None:
        if not all(requested.get(key) is True for key in requirements):
            return _unsupported(
                runtime,
                feature,
                option=option,
                requested=requested,
                code=f"{feature.value}_precondition_unsatisfied",
                rationale="one or more Hermes adapter preconditions are unavailable",
            )
        return _decision(
            runtime,
            feature,
            CapabilityDisposition.HERMES_ADAPTER,
            option=option,
            requested=requested,
            effective={"enforcement": "hermes_managed"},
            adapter_version=_ADAPTER_VERSION,
            code=f"{feature.value}_hermes_adapter",
            rationale="Hermes owns and enforces the requested semantics",
        )

    if feature is WorkflowProviderFeature.SKILLS_INLINE_AGENTS:
        if option == "skills":
            ready = requested.get("current_turn_content") is True
            effective = {"delivery": "current_user_turn"}
        elif option == "inline_agents":
            ready = (
                requested.get("declared_worker_tool") is True
                and requested.get("shared_limits") is True
            )
            effective = {"delegation": "declared_bounded_worker"}
        else:
            ready = False
            effective = {}
        if not ready:
            return _unsupported(
                runtime,
                feature,
                option=option,
                requested=requested,
                code="skills_inline_agents_precondition_unsatisfied",
                rationale="skill or inline-agent adapter preconditions are unavailable",
            )
        return _decision(
            runtime,
            feature,
            CapabilityDisposition.HERMES_ADAPTER,
            option=option,
            requested=requested,
            effective=effective,
            adapter_version=_ADAPTER_VERSION,
            code="skills_inline_agents_hermes_adapter",
            rationale="Hermes applies the declared bounded current-turn semantics",
        )

    if feature is WorkflowProviderFeature.WEB_EXECUTION and option == "hermes_tool":
        if not (
            requested.get("service_available") is True
            and requested.get("explicit_mapping") is True
        ):
            return _unsupported(
                runtime,
                feature,
                option=option,
                requested=requested,
                code="web_execution_precondition_unsatisfied",
                rationale="the explicitly mapped Hermes web service is unavailable",
            )
        return _decision(
            runtime,
            feature,
            CapabilityDisposition.DEGRADED_WITH_EXPLICIT_SEMANTICS,
            option=option,
            requested=requested,
            effective={"mode": "hermes_web_tool"},
            adapter_version=_ADAPTER_VERSION,
            code="web_execution_mapped_to_hermes_tool",
            rationale="web execution uses the explicitly selected Hermes web tool",
        )
    return None


def resolve_provider_capability(
    runtime: object,
    *,
    feature: WorkflowProviderFeature,
    option: str | None = None,
    requested_semantics: Mapping[str, Any] | object = MappingProxyType({}),
) -> ProviderCapabilityDecision:
    """Resolve exactly one capability disposition for one classified route."""

    if not isinstance(feature, WorkflowProviderFeature):
        raise TypeError("feature must be a WorkflowProviderFeature")
    requested = _safe_semantics(requested_semantics)
    if requested is None:
        return _unsupported(
            runtime,
            feature,
            option=option,
            requested={},
            code="provider_capability_request_invalid",
            rationale="requested semantics are not bounded public JSON",
        )
    if requested.get("execution_adapter_available") is False:
        return _unsupported(
            runtime,
            feature,
            option=option,
            requested=requested,
            code=f"{feature.value}_execution_adapter_unavailable",
            rationale="the selected execution path cannot apply this capability",
        )
    if feature is WorkflowProviderFeature.STRUCTURED_OUTPUT:
        return _resolve_structured_output(
            runtime,
            option=option,
            requested=requested,
        )

    declaration, registration = _profile_declaration(runtime, feature, option)
    if declaration is False:
        return _unsupported(
            runtime,
            feature,
            option=option,
            requested=requested,
            code="provider_capability_declaration_invalid",
            rationale="provider capability declaration is malformed or has drifted",
        )
    if isinstance(declaration, _ParsedDeclaration):
        if declaration.disposition is CapabilityDisposition.UNSUPPORTED:
            return _unsupported(
                runtime,
                feature,
                option=option,
                requested=requested,
                code=declaration.code,
                rationale=declaration.rationale,
                declaration_source="provider_profile",
            )
        provenance = registration.provenance
        if not provenance.code_closure_complete:
            return _unsupported(
                runtime,
                feature,
                option=option,
                requested=requested,
                code="provider_declaration_provenance_incomplete",
                rationale="provider declaration code closure is incomplete",
            )
        if feature in {
            WorkflowProviderFeature.COST_BUDGETS,
            WorkflowProviderFeature.PROVIDER_NATIVE_SANDBOX,
        } and provenance.origin_kind != "bundled":
            code = (
                "authoritative_cost_unavailable"
                if feature is WorkflowProviderFeature.COST_BUDGETS
                else "provider_native_sandbox_unavailable"
            )
            return _unsupported(
                runtime,
                feature,
                option=option,
                requested=requested,
                code=code,
                rationale="security-sensitive guarantees require reviewed bundled code",
            )
        if feature in {
            WorkflowProviderFeature.COST_BUDGETS,
            WorkflowProviderFeature.PROVIDER_NATIVE_SANDBOX,
        } and getattr(runtime, "base_url_trust_class", "") != "trusted_direct":
            code = (
                "authoritative_cost_unavailable"
                if feature is WorkflowProviderFeature.COST_BUDGETS
                else "provider_native_sandbox_unavailable"
            )
            return _unsupported(
                runtime,
                feature,
                option=option,
                requested=requested,
                code=code,
                rationale="custom and aggregated routes cannot assert this guarantee",
            )
        if feature is WorkflowProviderFeature.COST_BUDGETS:
            required = (
                "authoritative_settlement",
                "all_billable_routes_observed",
                "single_unsettled",
            )
            if not all(requested.get(key) is True for key in required):
                return _unsupported(
                    runtime,
                    feature,
                    option=option,
                    requested=requested,
                    code="authoritative_cost_unavailable",
                    rationale="authoritative settlement preconditions are incomplete",
                )
            disposition = CapabilityDisposition.HERMES_ADAPTER
            adapter_version = _ADAPTER_VERSION
        else:
            disposition = declaration.disposition
            adapter_version = declaration.adapter_version
        return _decision(
            runtime,
            feature,
            disposition,
            option=option,
            requested=requested,
            effective=declaration.effective_semantics,
            adapter_version=adapter_version,
            declaration_source="provider_profile",
            code=declaration.code,
            rationale=declaration.rationale,
        )

    generic = _generic_adapter(
        runtime,
        feature,
        option=option,
        requested=requested,
    )
    if generic is not None:
        return generic
    code = {
        WorkflowProviderFeature.COST_BUDGETS: "authoritative_cost_unavailable",
        WorkflowProviderFeature.PROVIDER_NATIVE_SANDBOX: (
            "provider_native_sandbox_unavailable"
        ),
    }.get(feature, f"{feature.value}_unsupported")
    return _unsupported(
        runtime,
        feature,
        option=option,
        requested=requested,
        code=code,
        rationale="no exact native declaration or Hermes adapter is available",
    )


def capability_authority_digest(
    decisions: Iterable[ProviderCapabilityDecision],
) -> str:
    """Hash sorted closed decision projections for route/snapshot identity."""

    payload = {
        "resolver_version": _RESOLVER_VERSION,
        "decisions": sorted(
            (decision.to_dict() for decision in decisions),
            key=lambda item: (
                item["feature"],
                item["provider"],
                item["model"],
                item["option"] or "",
            ),
        ),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
