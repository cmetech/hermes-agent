"""Pure eligibility policy for verified provider model assignments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from hermes_cli.auth import resolve_api_key_provider_credentials
from hermes_cli.model_capabilities import (
    fetch_model_capability_catalog,
    join_live_model_capabilities,
)
from providers import get_provider_profile


ModelUsageKind = Literal[
    "main",
    "fallback",
    "auxiliary",
    "vision",
    "moa-reference",
    "moa-aggregator",
]

REQUIRED_CAPABILITIES: dict[ModelUsageKind, tuple[str, ...]] = {
    "main": ("completion", "tools"),
    "fallback": ("completion", "tools"),
    "auxiliary": ("completion",),
    "vision": ("completion", "vision"),
    "moa-reference": ("completion",),
    "moa-aggregator": ("completion", "tools"),
}

_CATALOG_STATUS_MESSAGES = {
    "authentication-required": (
        "Gateway authentication is required to verify this model."
    ),
    "gateway-upgrade-required": (
        "The Gateway must be updated before it can verify this model."
    ),
    "gateway-unreachable": (
        "The Gateway is unavailable, so this model cannot be verified."
    ),
    "catalog-empty": (
        "The Gateway did not provide any verified explicit models."
    ),
    "capability-response-invalid": (
        "The Gateway returned an invalid model capability response."
    ),
    "unknown": "The model capability catalog is not ready.",
}

_CAPABILITY_LABELS = {
    "completion": "Completion",
    "tools": "Tool",
    "vision": "Vision",
}


@dataclass(frozen=True)
class ModelEligibility:
    eligible: bool
    reason: str
    message: str
    grandfathered: bool = False


def _ineligible(
    reason: str,
    message: str,
    *,
    exact_existing_assignment: bool,
) -> ModelEligibility:
    if exact_existing_assignment:
        return ModelEligibility(
            eligible=True,
            reason=reason,
            message=(
                "The existing assignment was preserved, but it is not eligible "
                f"for a new assignment. {message}"
            ),
            grandfathered=True,
        )
    return ModelEligibility(
        eligible=False,
        reason=reason,
        message=message,
    )


def evaluate_model_eligibility(
    *,
    capability_contract: bool,
    catalog_status: str,
    model: str,
    usage: ModelUsageKind,
    is_live: bool,
    selection_mode: str,
    verified: Mapping[str, str] | None,
    exact_existing_assignment: bool = False,
) -> ModelEligibility:
    """Evaluate immutable inputs without reading config, environment, or I/O."""
    if not capability_contract:
        return ModelEligibility(
            eligible=True,
            reason="legacy-compatible",
            message=(
                "This provider does not require verified capability filtering."
            ),
        )

    if model == "auto" and usage == "main":
        return ModelEligibility(
            eligible=True,
            reason="automatic-routing",
            message="Automatic routing is allowed for the main model.",
        )

    if catalog_status != "ready":
        return _ineligible(
            catalog_status,
            _CATALOG_STATUS_MESSAGES.get(
                catalog_status,
                _CATALOG_STATUS_MESSAGES["unknown"],
            ),
            exact_existing_assignment=exact_existing_assignment,
        )

    if model == "auto" or selection_mode == "automatic":
        return _ineligible(
            "automatic-not-allowed",
            "Automatic routing is only allowed for the main model.",
            exact_existing_assignment=exact_existing_assignment,
        )

    if not is_live:
        return _ineligible(
            "model-not-live",
            "This model is not present in the provider's live model list.",
            exact_existing_assignment=exact_existing_assignment,
        )

    states = verified or {}
    for capability in REQUIRED_CAPABILITIES[usage]:
        state = states.get(capability, "unknown")
        if state == "supported":
            continue
        # Reaching this loop already proves the catalog is ready and the
        # explicit model is present in the provider's live /models response.
        # That is sufficient evidence that it can perform completion even
        # when the additive capability catalog has not described it yet.
        # Keep every richer capability (tools/vision) fail-closed.
        if capability == "completion" and state == "unknown":
            continue
        normalized_state = "unsupported" if state == "unsupported" else "unknown"
        label = _CAPABILITY_LABELS[capability]
        message = (
            f"{label} support is not available for this model."
            if normalized_state == "unsupported"
            else f"{label} support has not been verified for this model."
        )
        return _ineligible(
            f"{capability}-{normalized_state}",
            message,
            exact_existing_assignment=exact_existing_assignment,
        )

    return ModelEligibility(
        eligible=True,
        reason="eligible",
        message="This model is eligible for the requested assignment.",
    )


def validate_provider_model_selection(
    provider: str,
    model: str,
    usage: ModelUsageKind,
    *,
    exact_existing_assignment: bool = False,
    force_refresh: bool = True,
) -> ModelEligibility:
    """Validate a selection using fresh provider data without writing config."""
    profile = get_provider_profile(provider)
    if profile is None or not profile.model_capabilities_path:
        return evaluate_model_eligibility(
            capability_contract=False,
            catalog_status="unknown",
            model=model,
            usage=usage,
            is_live=False,
            selection_mode="explicit",
            verified=None,
            exact_existing_assignment=exact_existing_assignment,
        )

    api_key = ""
    base_url = profile.base_url
    if profile.auth_type == "api_key":
        credentials = resolve_api_key_provider_credentials(provider)
        api_key = str(credentials.get("api_key") or "")
        base_url = str(credentials.get("base_url") or base_url)

    try:
        live_model_ids = profile.fetch_models(
            api_key=api_key,
            base_url=base_url,
        )
    except Exception:
        live_model_ids = None

    catalog = fetch_model_capability_catalog(
        provider,
        api_key=api_key,
        base_url=base_url,
        force_refresh=force_refresh,
    )
    live_models, verified_models, _mismatch_count = join_live_model_capabilities(
        live_model_ids or [],
        catalog,
    )
    metadata = verified_models.get(model)

    return evaluate_model_eligibility(
        capability_contract=True,
        catalog_status=catalog.status,
        model=model,
        usage=usage,
        is_live=model in live_models,
        selection_mode=(
            metadata.selection_mode if metadata is not None else "explicit"
        ),
        verified=metadata.capabilities if metadata is not None else None,
        exact_existing_assignment=exact_existing_assignment,
    )


__all__ = [
    "ModelEligibility",
    "ModelUsageKind",
    "REQUIRED_CAPABILITIES",
    "evaluate_model_eligibility",
    "validate_provider_model_selection",
]
