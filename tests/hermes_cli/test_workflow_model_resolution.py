from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

import hermes_cli.workflow_model_resolution as model_resolution
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
