from __future__ import annotations

from types import MappingProxyType

from hermes_cli.provider_capabilities import (
    CapabilityDisposition,
    ProviderCapabilityDecision,
    WorkflowProviderFeature,
)
from plugins.workflow.evidence import EvidenceReader
from plugins.workflow.provider_authority import (
    ProviderAuthorityWarning,
    WorkflowCapabilityObligation,
    WorkflowProviderAuthority,
    WorkflowResolvedProviderRoute,
    public_provider_capability_projection,
)
from plugins.workflow.sanitize import public_display_identifier
from plugins.workflow.sanitize import sanitize_projection


def _authority(*, provider: str = "openrouter", model: str = "openai/gpt-5.4"):
    route = WorkflowResolvedProviderRoute(
        route_id="ask:primary",
        node_id="ask",
        role="primary",
        inline_agent_id=None,
        reference_kind="configured_alias",
        requested_reference_sha256="1" * 64,
        provider=provider,
        model=model,
        api_mode="chat_completions",
        route_fingerprint="2" * 64,
        endpoint_sha256="6" * 64,
        registration_provenance_digest="3" * 64,
        provider_options=MappingProxyType({"effort": "high"}),
        config_scope="profile",
        base_url_trust_class="known_provider",
    )
    decision = ProviderCapabilityDecision(
        feature=WorkflowProviderFeature.EFFORT_THINKING,
        disposition=CapabilityDisposition.NATIVE,
        provider=provider,
        model=model,
        option="effort",
        requested_semantics={"value": "high"},
        effective_semantics={"request_field": "reasoning.effort", "value": "high"},
        adapter_version=1,
        declaration_source="builtin",
        registration_provenance_digest="3" * 64,
        code="provider_capability_native",
        rationale="not public",
    )
    return WorkflowProviderAuthority(
        config_fingerprint="4" * 64,
        routes={route.route_id: route},
        obligations=(WorkflowCapabilityObligation("nodes[0].effort", route.route_id, decision),),
        warnings=(
            ProviderAuthorityWarning(
                "nodes[0].model", "model_reference_not_globally_portable", "private"
            ),
        ),
        authority_digest="5" * 64,
    )


def test_display_identifiers_keep_slugs_and_redact_unsafe_values_wholesale() -> None:
    assert public_display_identifier("anthropic/claude-sonnet-4.5") == (
        "anthropic/claude-sonnet-4.5"
    )
    unsafe = [
        "https://user:pass@example.test/model?token=secret#fragment",
        "/private/tmp/model",
        "../private/model",
        "sk-secret-pasted-here",
        "model\x00escape",
        "a" * 96,
        "0123456789abcdef" * 4,
    ]
    for value in unsafe:
        projected = public_display_identifier(value)
        assert projected.startswith("redacted:")
        assert value not in projected
        assert not any(part in projected for part in ("secret", "private", "example"))
        assert projected == public_display_identifier(value)


def test_provider_capability_summary_and_detail_are_closed_and_bounded() -> None:
    authority = _authority()

    summary = public_provider_capability_projection(authority)
    detail = public_provider_capability_projection(authority, include_details=True)

    assert summary == {
        "schema_version": 1,
        "level": "portable",
        "resolved_route_count": 1,
        "mixed_provider": False,
        "unsupported_count": 0,
        "degraded_count": 0,
        "warning_codes": ["model_reference_not_globally_portable"],
        "authority_digest": "5" * 64,
    }
    assert detail == {
        **summary,
        "routes": [
            {
                "node_id": "ask",
                "role": "primary",
                "inline_agent_id": None,
                "reference_kind": "configured_alias",
                "provider": "openrouter",
                "model": "openai/gpt-5.4",
            }
        ],
        "decisions": [
            {
                "path": "nodes[0].effort",
                "feature": "effort_thinking",
                "disposition": "native",
                "provider": "openrouter",
                "model": "openai/gpt-5.4",
                "option": "effort",
                "effective_semantics": {
                    "request_field": "reasoning.effort",
                    "value": "high",
                },
                "code": "provider_capability_native",
            }
        ],
    }
    forbidden = {
        "rationale",
        "requested_semantics",
        "provider_options",
        "api_mode",
        "base_url",
        "route_fingerprint",
        "registration_provenance_digest",
        "config_fingerprint",
    }
    assert not forbidden.intersection(str(detail))


def test_provider_projection_redacts_unsafe_provider_and_model_identically() -> None:
    unsafe_provider = "https://user:password@provider.test/api?token=secret"
    unsafe_model = "/tmp/../" + "a" * 96
    detail = public_provider_capability_projection(
        _authority(provider=unsafe_provider, model=unsafe_model),
        include_details=True,
    )

    route = detail["routes"][0]
    decision = detail["decisions"][0]
    assert route["provider"] == decision["provider"] == public_display_identifier(
        unsafe_provider
    )
    assert route["model"] == decision["model"] == public_display_identifier(
        unsafe_model
    )
    assert unsafe_provider not in str(detail)
    assert unsafe_model not in str(detail)


def test_public_sanitizer_redacts_private_runtime_fields_and_identifiers() -> None:
    projected = sanitize_projection({
        "provider": "https://user:pass@example.test/api?key=value",
        "model": "sk-pasted-model-secret",
        "base_url": "https://private.example/api",
        "provider_response": {"body": "private"},
        "feedback": "private",
        "command": "curl private",
        "mcp_stderr": "private",
    })

    assert projected["provider"].startswith("redacted:")
    assert projected["model"].startswith("redacted:")
    assert list(projected.values()).count("[REDACTED]") == 5
    assert "private" not in str(projected)


def test_rest_provider_projection_models_reject_open_or_unknown_shapes() -> None:
    from pydantic import ValidationError
    import pytest

    from plugins.workflow.dashboard.plugin_api import (
        WorkflowProviderCapabilityProjection,
    )

    detail = public_provider_capability_projection(_authority(), include_details=True)
    assert WorkflowProviderCapabilityProjection.model_validate(detail)
    for invalid in (
        {**detail, "level": "future"},
        {**detail, "authority_digest": "not-a-digest"},
        {**detail, "provider_response": "private"},
        {**detail, "decisions": [{**detail["decisions"][0], "feature": "future"}]},
        {
            **detail,
            "routes": [{**detail["routes"][0], "model": "https://user:pass/model"}],
        },
        {
            **detail,
            "decisions": [
                {
                    **detail["decisions"][0],
                    "effective_semantics": {"value": "https://private/model"},
                }
            ],
        },
    ):
        with pytest.raises(ValidationError):
            WorkflowProviderCapabilityProjection.model_validate(invalid)


def test_rest_language_projection_accepts_the_explicit_v5_reader() -> None:
    from plugins.workflow.dashboard.plugin_api import WorkflowDetailLanguageStatus

    projected = WorkflowDetailLanguageStatus.model_validate({
        "declared_profile": "archon-2026-07",
        "effective_profile": "archon-2026-07",
        "legacy": False,
        "normalizer_version": 5,
        "normalized_definition_digest": "a" * 64,
    })

    assert projected.normalizer_version == 5


class _AttemptStore:
    def get_run_status(self, _run_id: str, *, operator_scope=None):
        return {
            "provider_resolution_sha256": "6" * 64,
            "nodes": {
                "ask": {
                    "attempts": [
                        {
                            "attempt_id": "attempt-1",
                            "state": "failed",
                            "error_code": "cost_budget_exhausted",
                            "error_message": "budget exhausted",
                            "metadata": {
                                "intended_authority_digest": "5" * 64,
                                "audit": {
                                    "cost_budget": {
                                        "max_budget_usd": "1",
                                        "settled_cost_usd": "1.25",
                                        "remaining_usd": "0",
                                        "overage_usd": "0.25",
                                        "settlement_count": 1,
                                        "provider_response": "private",
                                        "arbitrary_nested": {"secret": "leak"},
                                    },
                                    "prompt": "private prompt",
                                    "command": "private command",
                                    "mcp_stderr": "private stderr",
                                },
                            },
                        }
                    ]
                }
            },
        }


def test_attempt_evidence_projects_manifest_and_closed_budget_totals_only() -> None:
    page = EvidenceReader(_AttemptStore()).query("run-1", kind="attempts")

    assert page["items"] == [
        {
            "node_id": "ask",
            "attempt_id": "attempt-1",
            "state": "failed",
            "provider_authority": {
                "authority_digest": "5" * 64,
                "manifest_digest": "6" * 64,
            },
            "cost_budget": {
                "max_budget_usd": "1",
                "settled_cost_usd": "1.25",
                "remaining_usd": "0",
                "overage_usd": "0.25",
                "settlement_count": 1,
            },
            "error": {
                "code": "cost_budget_exhausted",
                "message": "budget exhausted",
            },
        }
    ]
    assert "private" not in str(page)
