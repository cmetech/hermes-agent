from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path

import pytest

import hermes_cli.workflow_model_resolution as model_resolution
import hermes_cli.runtime_provider as runtime_provider
from hermes_cli.workflow_model_resolution import (
    ModelResolutionError,
    load_workflow_model_config_snapshot,
    parse_workflow_model_config,
    resolve_workflow_model_reference,
)


def test_route_fingerprint_uses_version2_normalized_endpoint_identity() -> None:
    assert model_resolution.WORKFLOW_MODEL_RESOLVER_VERSION == 2
    first = _config()
    first["model_aliases"]["review"]["base_url"] = (
        "https://OPENROUTER.ai:443/api/v1/"
    )
    second = _config()
    second["model_aliases"]["review"]["base_url"] = (
        "https://openrouter.ai/api/v1"
    )

    first_route = resolve_workflow_model_reference(
        parse_workflow_model_config(first), "@review"
    )
    second_route = resolve_workflow_model_reference(
        parse_workflow_model_config(second), "@review"
    )

    assert first_route.endpoint_sha256 == second_route.endpoint_sha256


@pytest.mark.parametrize(
    ("provider", "provider_config", "expected_url"),
    [
        (
            "bedrock",
            {"region": "ap-southeast-2"},
            "https://bedrock-runtime.ap-southeast-2.amazonaws.com",
        ),
        (
            "vertex",
            {"project_id": "private-project-9", "region": "us-central1"},
            "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/"
            "private-project-9/locations/us-central1/endpoints/openapi",
        ),
    ],
)
def test_workflow_snapshot_binds_dynamic_provider_endpoint_privately(
    provider, provider_config, expected_url
) -> None:
    config = {
        "model": {"provider": provider, "default": "test-model"},
        provider: provider_config,
    }
    snapshot = parse_workflow_model_config(config)
    route = resolve_workflow_model_reference(snapshot, "test-model")

    expected = hashlib.sha256(
        b"hermes-execution-endpoint-v1\0" + expected_url.encode("utf-8")
    ).hexdigest()
    assert route.endpoint_sha256 == expected
    assert expected == runtime_provider.classify_credential_free_execution_runtime(
        config,
        requested_provider=provider,
        target_model="test-model",
    ).endpoint_sha256
    public_snapshot = json.dumps(snapshot.to_dict(), sort_keys=True)
    assert provider_config["region"] not in public_snapshot
    assert provider_config.get("project_id", "private-project-9") not in public_snapshot


def test_live_alias_route_selects_one_private_constraint_by_fingerprint() -> None:
    config = {
        "model": {"provider": "openrouter", "default": "other-model"},
        "model_aliases": {
            "review": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "base_url": "https://private-alias.example/anthropic",
            }
        },
    }
    route = resolve_workflow_model_reference(
        parse_workflow_model_config(config), "@review"
    )

    constraint = runtime_provider.select_credential_free_execution_route(
        config,
        requested_provider=route.provider,
        target_model=route.model,
        route_fingerprint=route.route_fingerprint,
        expected_runtime_identity={
            "provider": "anthropic",
            "model": route.model,
            "api_mode": route.api_mode,
            "base_url_trust_class": route.base_url_trust_class,
            "endpoint_sha256": route.endpoint_sha256,
            "registration_provenance_digest": (
                route.registration_provenance_digest
            ),
        },
    )

    assert constraint is not None
    assert constraint.route_fingerprint == route.route_fingerprint
    assert constraint.execution_runtime_identity().endpoint_sha256 == (
        route.endpoint_sha256
    )
    assert "private-alias.example" not in repr(constraint)


def test_live_alias_route_rejects_mismatched_requested_provider() -> None:
    config = {
        "model": {"provider": "openrouter", "default": "other-model"},
        "model_aliases": {
            "review": {"provider": "anthropic", "model": "claude-sonnet-4-6"}
        },
    }
    route = resolve_workflow_model_reference(
        parse_workflow_model_config(config), "@review"
    )

    constraint = runtime_provider.select_credential_free_execution_route(
        config,
        requested_provider="openrouter",
        target_model=route.model,
        route_fingerprint=route.route_fingerprint,
        expected_runtime_identity={
            "provider": route.provider,
            "model": route.model,
            "api_mode": route.api_mode,
            "base_url_trust_class": route.base_url_trust_class,
            "endpoint_sha256": route.endpoint_sha256,
            "registration_provenance_digest": (
                route.registration_provenance_digest
            ),
        },
    )

    assert constraint is None


def test_live_alias_route_rejects_fingerprint_after_endpoint_change() -> None:
    admitted_config = {
        "model": {"provider": "openrouter", "default": "other-model"},
        "model_aliases": {
            "review": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "base_url": "https://admitted.example/anthropic",
            }
        },
    }
    route = resolve_workflow_model_reference(
        parse_workflow_model_config(admitted_config), "@review"
    )
    changed_config = {
        **admitted_config,
        "model_aliases": {
            "review": {
                **admitted_config["model_aliases"]["review"],
                "base_url": "https://changed.example/anthropic",
            }
        },
    }

    constraint = runtime_provider.select_credential_free_execution_route(
        changed_config,
        requested_provider=route.provider,
        target_model=route.model,
        route_fingerprint=route.route_fingerprint,
        expected_runtime_identity={
            "provider": route.provider,
            "model": route.model,
            "api_mode": route.api_mode,
            "base_url_trust_class": route.base_url_trust_class,
            "endpoint_sha256": route.endpoint_sha256,
            "registration_provenance_digest": (
                route.registration_provenance_digest
            ),
        },
    )

    assert constraint is None


def test_live_alias_route_replays_node_option_precedence() -> None:
    config = {
        "model": {"provider": "openrouter", "default": "other-model"},
        "model_aliases": {
            "review": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "options": {"effort": "medium"},
            }
        },
    }
    route = resolve_workflow_model_reference(
        parse_workflow_model_config(config),
        "@review",
        node_options={"effort": "high"},
    )

    constraint = runtime_provider.select_credential_free_execution_route(
        config,
        requested_provider=route.provider,
        target_model=route.model,
        route_fingerprint=route.route_fingerprint,
        expected_runtime_identity={
            "provider": route.provider,
            "model": route.model,
            "api_mode": route.api_mode,
            "base_url_trust_class": route.base_url_trust_class,
            "endpoint_sha256": route.endpoint_sha256,
            "registration_provenance_digest": (
                route.registration_provenance_digest
            ),
        },
        provider_options=route.provider_options,
    )

    assert constraint is not None


def test_live_tier_route_selects_dynamic_vertex_constraint() -> None:
    config = {
        "model": {"provider": "openrouter", "default": "other-model"},
        "model_tiers": {
            "small": {"provider": "vertex", "model": "gemini-3.6-flash"}
        },
        "vertex": {"project_id": "private-tier-project", "region": "asia-east1"},
    }
    route = resolve_workflow_model_reference(
        parse_workflow_model_config(config), "small"
    )

    constraint = runtime_provider.select_credential_free_execution_route(
        config,
        requested_provider=route.provider,
        target_model=route.model,
        route_fingerprint=route.route_fingerprint,
        expected_runtime_identity={
            "provider": route.provider,
            "model": route.model,
            "api_mode": route.api_mode,
            "base_url_trust_class": route.base_url_trust_class,
            "endpoint_sha256": route.endpoint_sha256,
            "registration_provenance_digest": (
                route.registration_provenance_digest
            ),
        },
    )

    assert constraint is not None
    assert constraint.route_fingerprint == route.route_fingerprint
    assert "private-tier-project" not in repr(constraint)


def test_live_active_literal_selects_nondefault_bedrock_constraint() -> None:
    config = {
        "model": {"provider": "bedrock", "default": "amazon.nova-pro-v1:0"},
        "bedrock": {"region": "eu-west-2"},
    }
    route = resolve_workflow_model_reference(
        parse_workflow_model_config(config), "amazon.nova-pro-v1:0"
    )

    constraint = runtime_provider.select_credential_free_execution_route(
        config,
        requested_provider=route.provider,
        target_model=route.model,
        route_fingerprint=route.route_fingerprint,
        expected_runtime_identity={
            "provider": route.provider,
            "model": route.model,
            "api_mode": route.api_mode,
            "base_url_trust_class": route.base_url_trust_class,
            "endpoint_sha256": route.endpoint_sha256,
            "registration_provenance_digest": (
                route.registration_provenance_digest
            ),
        },
    )

    assert constraint is not None
    assert constraint.api_mode == "bedrock_converse"
    assert "eu-west-2" not in repr(constraint)


def test_live_node_provider_literal_selects_configured_bedrock_constraint() -> None:
    config = {
        "model": {"provider": "openrouter", "default": "other-model"},
        "bedrock": {"region": "ap-southeast-2"},
    }
    snapshot = parse_workflow_model_config(config)
    route = resolve_workflow_model_reference(
        snapshot,
        "amazon.nova-pro-v1:0",
        node_provider="bedrock",
    )

    constraint = runtime_provider.select_credential_free_execution_route(
        config,
        requested_provider=route.provider,
        target_model=route.model,
        route_fingerprint=route.route_fingerprint,
        expected_runtime_identity={
            "provider": route.provider,
            "model": route.model,
            "api_mode": route.api_mode,
            "base_url_trust_class": route.base_url_trust_class,
            "endpoint_sha256": route.endpoint_sha256,
            "registration_provenance_digest": (
                route.registration_provenance_digest
            ),
        },
    )

    assert constraint is not None
    assert constraint.route_fingerprint == route.route_fingerprint
    assert "ap-southeast-2" not in repr(constraint)


def _config() -> dict[str, object]:
    return {
        "model": {
            "provider": "anthropic",
            "default": "claude-sonnet-4.6",
            "aliases": {
                "legacy": "openrouter/anthropic/claude-haiku-4.5",
                "shadowed": "openrouter/old-model",
            },
        },
        "model_tiers": {
            "small": {
                "provider": "openrouter",
                "model": "google/gemini-3.6-flash",
                "options": {"effort": "low"},
            },
            "medium": {
                "provider": "anthropic",
                "model": "claude-sonnet-4.6",
            },
            "large": {
                "provider": "openrouter",
                "model": "anthropic/claude-opus-4.6",
            },
        },
        "model_aliases": {
            "review": {
                "provider": "openrouter",
                "model": "anthropic/claude-opus-4.6",
                "base_url": "https://openrouter.ai/api/v1",
                "options": {"effort": "high", "thinking": "adaptive"},
            },
            "shadowed": {
                "provider": "anthropic",
                "model": "claude-opus-4.6",
            },
        },
    }


def test_parser_accepts_tiers_rich_aliases_and_legacy_aliases_with_top_precedence() -> None:
    snapshot = parse_workflow_model_config(_config())

    assert set(snapshot.tiers) == {"small", "medium", "large"}
    assert set(snapshot.aliases) == {"legacy", "review", "shadowed"}
    assert snapshot.aliases["legacy"].provider == "openrouter"
    assert snapshot.aliases["legacy"].model == "anthropic/claude-haiku-4.5"
    assert snapshot.aliases["shadowed"].provider == "anthropic"
    assert snapshot.aliases["shadowed"].model == "claude-opus-4.6"
    assert snapshot.aliases["review"].base_url == "https://openrouter.ai/api/v1"
    assert not snapshot.issues


@pytest.mark.parametrize(
    ("reference", "kind", "provider", "model"),
    [
        ("small", "tier", "openrouter", "google/gemini-3.6-flash"),
        ("@review", "configured_alias", "openrouter", "anthropic/claude-opus-4.6"),
        ("vendor/new-model:preview", "literal", "anthropic", "vendor/new-model:preview"),
    ],
)
def test_reference_grammar_is_exact_and_literals_pass_through(
    reference, kind, provider, model
) -> None:
    route = resolve_workflow_model_reference(
        parse_workflow_model_config(_config()),
        reference,
    )
    assert route.reference_kind == kind
    assert route.provider == provider
    assert route.model == model


def test_resolved_route_pins_the_provider_api_mode() -> None:
    route = resolve_workflow_model_reference(
        parse_workflow_model_config(_config()),
        "medium",
    )
    assert route.api_mode == "anthropic_messages"


@pytest.mark.parametrize("reference", ["small", "@missing"])
def test_missing_configured_reference_fails_with_stable_code(reference: str) -> None:
    with pytest.raises(ModelResolutionError) as exc_info:
        resolve_workflow_model_reference(parse_workflow_model_config({}), reference)
    expected = (
        "model_tier_unconfigured" if reference == "small" else "model_alias_unconfigured"
    )
    assert exc_info.value.code == expected


def test_tier_or_alias_provider_wins_with_exactly_one_warning() -> None:
    snapshot = parse_workflow_model_config(_config())
    route = resolve_workflow_model_reference(
        snapshot,
        "@review",
        node_provider="anthropic",
        workflow_provider="custom",
    )

    assert route.provider == "openrouter"
    assert [warning.code for warning in route.warnings] == [
        "model_reference_provider_overridden"
    ]


@pytest.mark.parametrize(
    ("node_provider", "workflow_provider", "expected"),
    [
        ("openrouter", "custom", "openrouter"),
        ("auto", "custom", "custom"),
        (None, "openrouter", "openrouter"),
        (None, None, "anthropic"),
    ],
)
def test_literal_provider_precedence_is_node_then_workflow_then_snapshot(
    node_provider, workflow_provider, expected
) -> None:
    route = resolve_workflow_model_reference(
        parse_workflow_model_config(_config()),
        "literal-model",
        node_provider=node_provider,
        workflow_provider=workflow_provider,
    )
    assert route.provider == expected


def test_option_precedence_is_node_then_workflow_then_reference_defaults() -> None:
    route = resolve_workflow_model_reference(
        parse_workflow_model_config(_config()),
        "@review",
        workflow_options={"effort": "medium", "web_execution": "hermes_tool"},
        node_options={"effort": "low"},
    )

    assert dict(route.provider_options) == {
        "effort": "low",
        "thinking": "adaptive",
        "web_execution": "hermes_tool",
    }


def test_unresolved_auto_provider_blocks() -> None:
    snapshot = parse_workflow_model_config({"model": {"provider": "auto"}})
    with pytest.raises(ModelResolutionError) as exc_info:
        resolve_workflow_model_reference(snapshot, "literal-model")
    assert exc_info.value.code == "model_provider_unresolved"


def test_managed_leaf_precedence_and_provenance_are_exact() -> None:
    profile = {
        "model_tiers": {
            "large": {
                "provider": "openrouter",
                "model": "anthropic/claude-opus-4.6",
                "options": {"effort": "medium", "thinking": "adaptive"},
            }
        },
        "model_aliases": {
            "review": {
                "provider": "openrouter",
                "model": "anthropic/claude-sonnet-4.6",
                "options": {"effort": "medium"},
            }
        },
    }
    managed = {
        "model_tiers": {
            "large": {
                "model": "openai/gpt-5.6-sol",
                "options": {"effort": "high"},
            }
        }
    }
    snapshot = parse_workflow_model_config(profile, managed_config=managed)

    assert snapshot.tiers["large"].provider == "openrouter"
    assert snapshot.tiers["large"].model == "openai/gpt-5.6-sol"
    assert dict(snapshot.tiers["large"].options) == {
        "effort": "high",
        "thinking": "adaptive",
    }
    assert snapshot.tiers["large"].config_scope == "managed"
    assert snapshot.aliases["review"].config_scope == "profile"


@pytest.mark.parametrize("catalog_source", ["project", "showcase"])
def test_profile_local_reference_warns_when_package_distributed(
    catalog_source: str,
) -> None:
    route = resolve_workflow_model_reference(
        parse_workflow_model_config(_config()),
        "small",
        catalog_source=catalog_source,
    )
    assert "model_reference_not_globally_portable" in {
        warning.code for warning in route.warnings
    }


def test_invented_project_and_package_alias_sources_are_rejected() -> None:
    snapshot = parse_workflow_model_config(
        {
            "project_model_aliases": {
                "hidden": {"provider": "custom", "model": "project-model"}
            },
            "package_model_aliases": {
                "hidden": {"provider": "custom", "model": "package-model"}
            },
        }
    )
    assert "hidden" not in snapshot.aliases
    assert {issue.code for issue in snapshot.issues} == {
        "model_reference_source_unsupported"
    }


@pytest.mark.parametrize(
    "entry",
    [
        {"provider": "custom", "model": "m", "options": {"api_key": "secret"}},
        {"provider": "custom", "model": "m", "options": {"token": "secret"}},
        {
            "provider": "custom",
            "model": "m",
            "options": {f"option_{index}": index for index in range(20)},
        },
    ],
)
def test_secret_like_and_unbounded_options_are_rejected(entry) -> None:
    snapshot = parse_workflow_model_config({"model_aliases": {"bad": entry}})
    assert "bad" not in snapshot.aliases
    assert {issue.code for issue in snapshot.issues} == {
        "model_reference_options_invalid"
    }


def test_fingerprint_is_deterministic_credential_free_and_route_sensitive() -> None:
    config = _config()
    config["model_aliases"]["review"]["api_key"] = "credential-value"
    first = parse_workflow_model_config(config)
    reordered = json.loads(json.dumps(config, sort_keys=True))
    second = parse_workflow_model_config(reordered)

    assert first.config_fingerprint == second.config_fingerprint
    serialized = json.dumps(first.to_dict(), sort_keys=True)
    assert "credential-value" not in serialized
    assert "https://openrouter.ai" not in serialized

    changed = _config()
    changed["model_aliases"]["review"]["model"] = "anthropic/claude-opus-4.7"
    assert (
        parse_workflow_model_config(changed).config_fingerprint
        != first.config_fingerprint
    )

    route_changed = _config()
    route_changed["model"]["base_url"] = "https://private-route.example/v1"
    route_snapshot = parse_workflow_model_config(route_changed)
    assert route_snapshot.config_fingerprint != parse_workflow_model_config(_config()).config_fingerprint
    assert "https://private-route.example" not in json.dumps(route_snapshot.to_dict())


def test_resolution_does_not_consult_live_catalog_or_global_alias_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("workflow model resolution used mutable/global discovery")

    monkeypatch.setattr("agent.models_dev.get_model_info", forbidden)
    monkeypatch.setattr("agent.models_dev.list_provider_models", forbidden)
    monkeypatch.setattr("hermes_cli.model_switch.resolve_alias", forbidden)
    monkeypatch.setattr("hermes_cli.model_switch.DIRECT_ALIASES", {"review": object()})

    route = resolve_workflow_model_reference(
        parse_workflow_model_config(_config()),
        "@review",
    )
    assert route.model == "anthropic/claude-opus-4.6"


def test_snapshot_and_routes_are_frozen() -> None:
    snapshot = parse_workflow_model_config(_config())
    route = resolve_workflow_model_reference(snapshot, "small")
    with pytest.raises(TypeError):
        snapshot.tiers["new"] = snapshot.tiers["small"]
    with pytest.raises(TypeError):
        route.provider_options["effort"] = "high"
    with pytest.raises((FrozenInstanceError, AttributeError)):
        route.provider = "mutated"


def test_explicit_profile_and_managed_files_load_without_environment_expansion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "profile.yaml"
    managed = tmp_path / "managed.yaml"
    profile.write_text(
        "model:\n  provider: anthropic\n"
        "model_aliases:\n"
        "  review:\n"
        "    provider: openrouter\n"
        "    model: anthropic/claude-sonnet-4.6\n"
        "  env-check:\n"
        "    provider: custom\n"
        "    model: ${MODEL_FROM_ENV}\n"
        "model_tiers:\n"
        "  small:\n"
        "    provider: custom\n"
        "    model: ${MODEL_FROM_ENV}\n",
        encoding="utf-8",
    )
    managed.write_text(
        "model_tiers:\n  small:\n    model: pinned-model\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MODEL_FROM_ENV", "environment-model")

    snapshot = load_workflow_model_config_snapshot(profile, managed)

    assert snapshot.tiers["small"].model == "pinned-model"
    assert snapshot.tiers["small"].provider == "custom"
    assert snapshot.tiers["small"].config_scope == "managed"
    assert snapshot.aliases["env-check"].model == "${MODEL_FROM_ENV}"
