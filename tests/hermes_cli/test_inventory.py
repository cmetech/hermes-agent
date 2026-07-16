"""Behavior tests for hermes_cli.inventory.

Locks the invariants the three migrated consumers (web_server.py
/api/model/options, tui_gateway model.options, tui_gateway model.save_key)
depend on:

- load_picker_context() reproduces the inline 17-LOC config-slice exactly.
- with_overrides() is truthy-only (empty agent attrs must not clobber).
- build_models_payload() returns a stable {providers, model, provider}
  shape and delegates curation to list_authenticated_providers (does not
  call provider_model_ids per row).
- canonical_order keys on slug membership, not is_user_defined — section
  3 of list_authenticated_providers sets is_user_defined=True for
  canonical slugs in the providers: dict, and that flag must NOT demote
  them to the tail.
- picker_hints adds authenticated/auth_type/key_env/warning per row,
  matching the TUI ModelPickerDialog shape.
"""

from __future__ import annotations

import json
from unittest.mock import patch


from hermes_cli.inventory import (
    ConfigContext,
    build_models_payload,
    load_picker_context,
)


# ─── load_picker_context ───────────────────────────────────────────────


def _cfg(model=None, providers=None, custom_providers=None) -> dict:
    return {
        "model": model if model is not None else {},
        "providers": providers if providers is not None else {},
        "custom_providers": custom_providers if custom_providers is not None else [],
    }


def test_load_picker_context_full_dict():
    cfg = _cfg(
        model={
            "default": "anthropic/claude-sonnet-4.6",
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
        },
        providers={"openrouter": {}},
        custom_providers=[{"name": "Ollama", "base_url": "http://localhost:11434/v1"}],
    )
    with patch("hermes_cli.config.load_config", return_value=cfg):
        ctx = load_picker_context()
    assert ctx.current_model == "anthropic/claude-sonnet-4.6"
    assert ctx.current_provider == "openrouter"
    assert ctx.current_base_url == "https://openrouter.ai/api/v1"
    assert "openrouter" in ctx.user_providers
    # custom_providers comes from get_compatible_custom_providers, which
    # merges legacy list + v12+ keyed providers — both present here means
    # at least one row.
    assert isinstance(ctx.custom_providers, list)


def test_load_picker_context_normalizes_list_of_dict_models():
    cfg = _cfg(
        providers={
            "static-gateway": {
                "name": "Static Gateway",
                "api": "https://router.example.com/v1",
                "default_model": "claude-3-7-sonnet",
                "models": [
                    {"id": "claude-3-7-sonnet"},
                    {"id": "claude-sonnet-4", "context_length": 200000},
                ],
                "discover_models": False,
            }
        },
    )
    with patch("hermes_cli.config.load_config", return_value=cfg):
        ctx = load_picker_context()

    assert len(ctx.custom_providers) == 1
    assert ctx.custom_providers[0]["models"] == {
        "claude-3-7-sonnet": {},
        "claude-sonnet-4": {"context_length": 200000},
    }
    assert ctx.custom_providers[0]["discover_models"] is False


def test_load_picker_context_falls_back_to_name_when_default_missing():
    cfg = _cfg(model={"name": "gpt-5.4", "provider": "openai"})
    with patch("hermes_cli.config.load_config", return_value=cfg):
        ctx = load_picker_context()
    assert ctx.current_model == "gpt-5.4"
    assert ctx.current_provider == "openai"


def test_load_picker_context_string_model_legacy_shape():
    """config.model can be a bare string in older configs."""
    cfg = {"model": "some-model", "providers": {}, "custom_providers": []}
    with patch("hermes_cli.config.load_config", return_value=cfg):
        ctx = load_picker_context()
    assert ctx.current_model == "some-model"
    assert ctx.current_provider == ""
    assert ctx.current_base_url == ""


def test_load_picker_context_empty_config():
    cfg = _cfg()
    with patch("hermes_cli.config.load_config", return_value=cfg):
        ctx = load_picker_context()
    assert ctx.current_provider == ""
    assert ctx.current_model == ""
    assert ctx.current_base_url == ""
    assert ctx.user_providers == {}
    assert ctx.custom_providers == []


# ─── with_overrides ────────────────────────────────────────────────────


def _empty_ctx(provider="orig", model="orig-model", base_url="orig-url"):
    return ConfigContext(
        current_provider=provider,
        current_model=model,
        current_base_url=base_url,
        user_providers={},
        custom_providers=[],
    )


def test_with_overrides_truthy_only_strings():
    """Empty strings must NOT clobber disk config — TUI calls this with
    empty getattr(agent, 'provider', '') when no agent is spawned yet."""
    ctx = _empty_ctx()
    overlaid = ctx.with_overrides(
        current_provider="",
        current_model="",
        current_base_url="",
    )
    assert overlaid.current_provider == "orig"
    assert overlaid.current_model == "orig-model"
    assert overlaid.current_base_url == "orig-url"


def test_with_overrides_truthy_value_replaces():
    ctx = _empty_ctx()
    overlaid = ctx.with_overrides(current_provider="anthropic")
    assert overlaid.current_provider == "anthropic"
    assert overlaid.current_model == "orig-model"  # untouched


def test_with_overrides_no_args_returns_self_or_equivalent():
    ctx = _empty_ctx()
    assert ctx.with_overrides() == ctx


# ─── build_models_payload ──────────────────────────────────────────────


def _list_auth_returning(rows: list[dict]):
    """Patch list_authenticated_providers to return a fixed row list."""
    return patch(
        "hermes_cli.model_switch.list_authenticated_providers",
        return_value=rows,
    )


def _nous_row(model: str = "openai/gpt-5.5") -> dict:
    return {
        "slug": "nous",
        "name": "Nous",
        "models": [model],
        "total_models": 1,
        "is_current": True,
        "is_user_defined": False,
        "source": "built-in",
    }


def test_build_models_payload_returns_expected_shape():
    rows = [
        {"slug": "openrouter", "name": "OpenRouter", "models": ["m1"],
         "total_models": 1, "is_current": True, "is_user_defined": False,
         "source": "built-in"},
    ]
    ctx = _empty_ctx(provider="openrouter", model="m1", base_url="")
    with _list_auth_returning(rows):
        payload = build_models_payload(ctx)
    assert set(payload.keys()) == {"providers", "model", "provider"}
    assert payload["model"] == "m1"
    assert payload["provider"] == "openrouter"
    assert payload["providers"][0]["slug"] == "moa"
    assert payload["providers"][0]["models"] == ["default"]
    assert payload["providers"][1:] == rows


def test_build_models_payload_does_not_call_provider_model_ids():
    """``build_models_payload`` is a thin shape adapter — it delegates the
    actual curation to ``list_authenticated_providers`` (which DOES call
    ``cached_provider_model_ids`` internally for live discovery, with disk
    caching). ``build_models_payload`` itself must not call the live fetcher
    directly; the test pins that boundary.
    """
    rows = [{"slug": "nous", "name": "Nous", "models": ["hermes-4-405b"],
             "total_models": 1, "is_current": False, "is_user_defined": False,
             "source": "built-in"}]
    ctx = _empty_ctx()
    with _list_auth_returning(rows), \
         patch("hermes_cli.models.provider_model_ids") as mock_pm:
        build_models_payload(ctx)
    mock_pm.assert_not_called()


def test_build_models_payload_uses_cached_nous_tier_by_default():
    """Picker payloads should not force fresh Nous account checks.

    Desktop/status picker opens are request/response UI paths. They can hit
    the short free-tier cache; explicit model/auth flows can still opt into a
    fresh account check when needed.
    """
    ctx = _empty_ctx(provider="nous", model="openai/gpt-5.5")
    rows = [_nous_row()]
    with patch(
        "hermes_cli.model_switch.list_authenticated_providers",
        return_value=rows,
    ) as mock_list:
        build_models_payload(ctx)

    mock_list.assert_called_once()
    assert mock_list.call_args.kwargs["force_fresh_nous_tier"] is False


def test_build_models_payload_can_force_fresh_nous_tier():
    ctx = _empty_ctx(provider="nous", model="openai/gpt-5.5")
    rows = [_nous_row()]
    with patch(
        "hermes_cli.model_switch.list_authenticated_providers",
        return_value=rows,
    ) as mock_list:
        build_models_payload(ctx, force_fresh_nous_tier=True)

    mock_list.assert_called_once()
    assert mock_list.call_args.kwargs["force_fresh_nous_tier"] is True


def test_build_models_payload_can_skip_custom_provider_probes():
    ctx = _empty_ctx()
    rows = []
    with patch(
        "hermes_cli.model_switch.list_authenticated_providers",
        return_value=rows,
    ) as mock_list:
        build_models_payload(ctx, probe_custom_providers=False)

    mock_list.assert_called_once()
    assert mock_list.call_args.kwargs["probe_custom_providers"] is False


def test_build_models_payload_can_probe_only_current_custom_provider():
    ctx = _empty_ctx()
    rows = []
    with patch(
        "hermes_cli.model_switch.list_authenticated_providers",
        return_value=rows,
    ) as mock_list:
        build_models_payload(
            ctx,
            probe_custom_providers=False,
            probe_current_custom_provider=True,
        )

    mock_list.assert_called_once()
    assert mock_list.call_args.kwargs["probe_custom_providers"] is False
    assert mock_list.call_args.kwargs["probe_current_custom_provider"] is True


def test_list_authenticated_providers_force_fresh_is_keyword_only():
    """``force_fresh_nous_tier`` must be keyword-only on the public listing API.

    It was inserted between ``custom_providers`` and ``max_models``; making it
    keyword-only ensures no positional caller passing ``max_models`` as the 5th
    arg silently mis-binds it to the tier-refresh flag. Pin the contract so a
    future signature edit that drops the ``*`` separator is caught.
    """
    import inspect

    from hermes_cli.model_switch import list_authenticated_providers

    sig = inspect.signature(list_authenticated_providers)
    param = sig.parameters["force_fresh_nous_tier"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is False


def test_pricing_uses_cached_nous_tier_by_default():
    rows = [_nous_row()]
    ctx = _empty_ctx(provider="nous", model="openai/gpt-5.5")
    with (
        _list_auth_returning(rows),
        patch(
            "hermes_cli.models.get_pricing_for_provider",
            return_value={
                "openai/gpt-5.5": {
                    "prompt": "0.000001",
                    "completion": "0.000002",
                },
            },
        ),
        patch("hermes_cli.models.check_nous_free_tier", return_value=False) as mock_free,
    ):
        build_models_payload(ctx, pricing=True)

    mock_free.assert_called_once_with(force_fresh=False)


def test_pricing_can_force_fresh_nous_tier():
    rows = [_nous_row()]
    ctx = _empty_ctx(provider="nous", model="openai/gpt-5.5")
    with (
        _list_auth_returning(rows),
        patch(
            "hermes_cli.models.get_pricing_for_provider",
            return_value={
                "openai/gpt-5.5": {
                    "prompt": "0.000001",
                    "completion": "0.000002",
                },
            },
        ),
        patch("hermes_cli.models.check_nous_free_tier", return_value=False) as mock_free,
    ):
        build_models_payload(ctx, pricing=True, force_fresh_nous_tier=True)

    mock_free.assert_called_once_with(force_fresh=True)


def test_include_unconfigured_appends_canonical_skeletons():
    """include_unconfigured=True adds CANONICAL_PROVIDERS rows that
    list_authenticated_providers didn't emit. Skeleton rows have empty
    models and source='canonical'."""
    rows = [
        {"slug": "openrouter", "name": "OpenRouter", "models": ["m1"],
         "total_models": 1, "is_current": True, "is_user_defined": False,
         "source": "built-in"},
    ]
    ctx = _empty_ctx(provider="openrouter")
    with _list_auth_returning(rows):
        payload = build_models_payload(ctx, include_unconfigured=True)
    # All canonical providers other than openrouter should appear as
    # skeleton rows.
    from hermes_cli.models import CANONICAL_PROVIDERS

    seen_slugs = {r["slug"] for r in payload["providers"]}
    for entry in CANONICAL_PROVIDERS:
        assert entry.slug in seen_slugs, f"missing {entry.slug}"
    # Skeletons have empty models and source='canonical'.
    skeletons = [r for r in payload["providers"]
                 if r.get("source") == "canonical"]
    assert all(r["models"] == [] for r in skeletons)
    assert all(r["total_models"] == 0 for r in skeletons)


def test_include_unconfigured_skips_already_present_slugs():
    """If list_authenticated_providers already returned a row for a
    canonical slug, include_unconfigured must NOT duplicate it."""
    rows = [
        {"slug": "openrouter", "name": "OpenRouter", "models": ["m1"],
         "total_models": 1, "is_current": True, "is_user_defined": False,
         "source": "built-in"},
    ]
    ctx = _empty_ctx()
    with _list_auth_returning(rows):
        payload = build_models_payload(ctx, include_unconfigured=True)
    or_rows = [r for r in payload["providers"] if r["slug"] == "openrouter"]
    assert len(or_rows) == 1
    assert or_rows[0]["models"] == ["m1"]  # the authenticated row, not skeleton


def test_explicit_only_filters_ambient_credentials_but_keeps_current_and_custom_rows():
    rows = [
        {"slug": "openai-codex", "name": "OpenAI Codex", "models": ["gpt-5.4"],
         "total_models": 1, "is_current": True, "is_user_defined": False,
         "source": "hermes"},
        {"slug": "gemini", "name": "Gemini", "models": ["gemini-2.5-pro"],
         "total_models": 1, "is_current": False, "is_user_defined": False,
         "source": "built-in"},
        {"slug": "copilot", "name": "Copilot", "models": ["gpt-5.4"],
         "total_models": 1, "is_current": False, "is_user_defined": False,
         "source": "hermes"},
        {"slug": "nous", "name": "Nous", "models": ["anthropic/claude-sonnet-5"],
         "total_models": 1, "is_current": False, "is_user_defined": False,
         "source": "hermes"},
        {"slug": "custom:lab", "name": "Lab", "models": ["lab-1"],
         "total_models": 1, "is_current": False, "is_user_defined": True,
         "source": "user-config"},
        {"slug": "moa", "name": "MoA", "models": ["default"],
         "total_models": 1, "is_current": False, "is_user_defined": False,
         "source": "virtual"},
    ]
    ctx = _empty_ctx(provider="openai-codex", model="gpt-5.4")
    with (
        _list_auth_returning(rows),
        patch("hermes_cli.config.read_raw_config", return_value={}),
        patch(
            "hermes_cli.auth.is_provider_explicitly_configured",
            side_effect=lambda slug: slug == "gemini",
        ),
    ):
        payload = build_models_payload(ctx, explicit_only=True)

    assert [row["slug"] for row in payload["providers"]] == [
        "openai-codex",
        "gemini",
        "custom:lab",
    ]


def test_explicit_only_keeps_unauthenticated_current_provider_visible():
    """Desktop's configured-only picker must retain its saved provider row."""
    ctx = _empty_ctx(provider="deepseek", model="deepseek-v4-pro")
    with _list_auth_returning([]):
        payload = build_models_payload(
            ctx,
            explicit_only=True,
            picker_hints=True,
        )

    assert [row["slug"] for row in payload["providers"]] == ["deepseek"]
    row = payload["providers"][0]
    assert row["source"] == "configured-current"
    assert row["authenticated"] is False
    assert row["models"] == ["deepseek-v4-pro"]
    assert "DEEPSEEK_API_KEY" in row["warning"]
def test_include_unconfigured_keeps_current_provider_visible_without_credentials():
    """If the saved provider is currently unauthenticated, keep a visible row
    with the saved model so GUI pickers don't silently jump to another
    authenticated provider."""
    ctx = _empty_ctx(provider="deepseek", model="deepseek-v4-pro")
    with _list_auth_returning([]):
        payload = build_models_payload(
            ctx, include_unconfigured=True, picker_hints=True,
        )

    deepseek = next(r for r in payload["providers"] if r["slug"] == "deepseek")
    assert deepseek["source"] == "configured-current"
    assert deepseek["is_current"] is True
    assert deepseek["authenticated"] is False
    assert deepseek["models"] == ["deepseek-v4-pro"]
    assert deepseek["total_models"] == 1
    assert deepseek["auth_type"] == "api_key"
    assert "DEEPSEEK_API_KEY" in deepseek["warning"]
    assert "saved model only" in deepseek["warning"]


def test_include_unconfigured_does_not_duplicate_configured_current_row():
    ctx = _empty_ctx(provider="deepseek", model="deepseek-v4-pro")
    with _list_auth_returning([]):
        payload = build_models_payload(
            ctx,
            explicit_only=True,
            include_unconfigured=True,
            picker_hints=True,
        )

    assert sum(row["slug"] == "deepseek" for row in payload["providers"]) == 1

def test_explicit_only_keeps_moa_when_raw_config_has_enabled_preset():
    rows = [
        {"slug": "moa", "name": "MoA", "models": ["review"],
         "total_models": 1, "is_current": False, "is_user_defined": False,
         "source": "virtual"},
    ]
    ctx = _empty_ctx(provider="openrouter", model="anthropic/claude-opus-4.8")
    raw_config = {
        "moa": {
            "active_preset": "review",
            "presets": {
                "review": {
                    "enabled": True,
                    "reference_models": [
                        {"provider": "openai-codex", "model": "gpt-5.5"},
                    ],
                    "aggregator": {
                        "provider": "openrouter",
                        "model": "anthropic/claude-opus-4.8",
                    },
                },
            },
        },
    }

    with (
        _list_auth_returning(rows),
        patch("hermes_cli.config.load_config", return_value=raw_config),
        patch("hermes_cli.config.read_raw_config", return_value=raw_config),
        patch("hermes_cli.auth.is_provider_explicitly_configured", return_value=False),
    ):
        payload = build_models_payload(ctx, explicit_only=True)

    assert [row["slug"] for row in payload["providers"]] == ["moa", "openrouter"]
    assert payload["providers"][0]["models"] == ["review"]
    assert payload["providers"][1]["source"] == "configured-current"
    assert payload["providers"][1]["authenticated"] is False
# ─── picker_hints ──────────────────────────────────────────────────────


def test_picker_hints_marks_authed_rows_authenticated():
    rows = [
        {"slug": "openrouter", "name": "OpenRouter", "models": ["m1"],
         "total_models": 1, "is_current": True, "is_user_defined": False,
         "source": "built-in"},
    ]
    ctx = _empty_ctx()
    with _list_auth_returning(rows):
        payload = build_models_payload(ctx, picker_hints=True)
    assert payload["providers"][0]["authenticated"] is True


def test_picker_hints_adds_warning_to_skeleton_rows():
    """Skeleton rows (unconfigured canonical providers) must carry the
    setup hint the picker UI displays."""
    rows = []
    ctx = _empty_ctx()
    with _list_auth_returning(rows):
        payload = build_models_payload(
            ctx, include_unconfigured=True, picker_hints=True,
        )
    skeleton_rows = [r for r in payload["providers"]
                     if r.get("source") == "canonical"]
    assert skeleton_rows, "test setup: expected at least one skeleton row"
    for row in skeleton_rows:
        assert row["authenticated"] is False
        assert "auth_type" in row
        assert "warning" in row
        # api_key providers get "paste X to activate" / others get the
        # hermes model fallback.
        assert (
            row["warning"].startswith("paste ")
            or row["warning"].startswith("run `hermes model`")
        )


def test_picker_hints_api_key_warning_format():
    """For api_key providers with a defined env var, the warning must
    point to that env var."""
    rows = []
    ctx = _empty_ctx()
    with _list_auth_returning(rows):
        payload = build_models_payload(
            ctx, include_unconfigured=True, picker_hints=True,
        )
    # anthropic uses api_key + ANTHROPIC_API_KEY.
    anthropic = next(
        r for r in payload["providers"] if r["slug"] == "anthropic"
    )
    assert "ANTHROPIC_API_KEY" in anthropic["warning"]
    assert anthropic["warning"].startswith("paste ")


# ─── canonical_order ───────────────────────────────────────────────────


def test_canonical_order_uses_slug_not_is_user_defined_flag():
    """Section 3 of list_authenticated_providers sets is_user_defined=True
    for canonical slugs that appear in the providers: config dict.
    canonical_order MUST key on slug membership, not the flag — otherwise
    canonical providers configured via the keyed schema get demoted to
    the tail.
    """
    from hermes_cli.models import CANONICAL_PROVIDERS

    canonical_slug = CANONICAL_PROVIDERS[2].slug  # any canonical
    rows = [
        # A truly-custom row (correct: is_user_defined=True)
        {"slug": "custom:Ollama", "name": "Ollama", "models": [],
         "total_models": 0, "is_current": False, "is_user_defined": True,
         "source": "user-config"},
        # A canonical row that the substrate flagged as user-defined
        # because the user configured it via providers: dict.
        {"slug": canonical_slug, "name": "x", "models": ["m1"],
         "total_models": 1, "is_current": False, "is_user_defined": True,
         "source": "built-in"},
    ]
    ctx = _empty_ctx()
    with _list_auth_returning(rows):
        payload = build_models_payload(ctx, canonical_order=True)
    slugs = [r["slug"] for r in payload["providers"]]
    # Canonical-slug row must come BEFORE truly-custom rows, regardless
    # of is_user_defined.
    canonical_idx = slugs.index(canonical_slug)
    custom_idx = slugs.index("custom:Ollama")
    assert canonical_idx < custom_idx, (
        f"canonical {canonical_slug} demoted to tail "
        f"(canonical_idx={canonical_idx} > custom_idx={custom_idx})"
    )


def test_canonical_order_with_unconfigured_preserves_full_universe():
    """Combined picker call: include_unconfigured + picker_hints +
    canonical_order is the production TUI shape. Verify the result
    has CANONICAL_PROVIDERS in declaration order, hints applied,
    custom rows trailing.
    """
    from hermes_cli.models import CANONICAL_PROVIDERS

    rows = [
        {"slug": "custom:Ollama", "name": "Ollama", "models": [],
         "total_models": 0, "is_current": False, "is_user_defined": True,
         "source": "user-config"},
    ]
    ctx = _empty_ctx()
    with _list_auth_returning(rows):
        payload = build_models_payload(
            ctx,
            include_unconfigured=True,
            picker_hints=True,
            canonical_order=True,
        )
    slugs = [r["slug"] for r in payload["providers"]]
    # First row: first canonical provider in declaration order.
    assert slugs[0] == CANONICAL_PROVIDERS[0].slug
    # Custom row trails canonical universe.
    assert slugs.index("custom:Ollama") >= len(CANONICAL_PROVIDERS)


# ─── Integration: end-to-end through real load_picker_context ──────────


def test_end_to_end_with_real_context_no_credentials_leak(monkeypatch):
    """Full pipeline: real load_picker_context + real
    list_authenticated_providers. Verify no credential string ever
    appears in the returned payload, even with picker_hints=True."""
    canary = "sk-canary-XYZ-must-not-appear"
    monkeypatch.setenv("OPENROUTER_API_KEY", canary)
    monkeypatch.setenv("ANTHROPIC_API_KEY", canary)
    cfg = _cfg(model={"provider": "openrouter"})
    with patch("hermes_cli.config.load_config", return_value=cfg):
        ctx = load_picker_context()
    payload = build_models_payload(
        ctx, include_unconfigured=True, picker_hints=True,
    )
    import json as _json

    assert canary not in _json.dumps(payload)


def test_payload_shape_compatible_with_modelpickerdialog_frontend():
    """Frontend (web/src/components/ModelPickerDialog.tsx) reads:
    name, slug, models, total_models, is_current, warning, authenticated.
    Verify every authenticated/skeleton row exposes those keys.
    """
    rows = [
        {"slug": "openrouter", "name": "OpenRouter", "models": ["m1"],
         "total_models": 1, "is_current": True, "is_user_defined": False,
         "source": "built-in"},
    ]
    ctx = _empty_ctx()
    with _list_auth_returning(rows):
        payload = build_models_payload(
            ctx, include_unconfigured=True, picker_hints=True,
        )
    required_keys = {"name", "slug", "models", "total_models", "is_current",
                     "authenticated"}
    for row in payload["providers"]:
        missing = required_keys - row.keys()
        assert not missing, f"row {row['slug']} missing keys: {missing}"


# ─── Aggregator dedup (issue #45954) ───────────────────────────────────


def _user_provider_row(slug: str, models: list[str]) -> dict:
    return {
        "slug": slug,
        "name": slug.title(),
        "models": models,
        "total_models": len(models),
        "is_current": False,
        "is_user_defined": True,
        "source": "user-config",
    }


def _aggregator_row(slug: str, models: list[str]) -> dict:
    return {
        "slug": slug,
        "name": slug.title(),
        "models": models,
        "total_models": len(models),
        "is_current": False,
        "is_user_defined": False,
        "source": "built-in",
    }


def test_aggregator_dedup_removes_overlapping_models():
    """Models served by a user-defined provider are removed from
    aggregator rows so the picker doesn't show them under the wrong
    provider.  (#45954)"""
    rows = [
        _user_provider_row("litellm-proxy", [
            "nvidia/nim/minimax-m3",
            "nvidia/nim/kimi-k2.6",
        ]),
        _aggregator_row("openrouter", [
            "minimax/minimax-m3",
            "nvidia/nim/minimax-m3",  # overlaps with litellm-proxy
            "anthropic/claude-sonnet-4.6",
        ]),
    ]
    ctx = _empty_ctx()
    with _list_auth_returning(rows):
        payload = build_models_payload(ctx)

    or_row = next(r for r in payload["providers"] if r["slug"] == "openrouter")
    proxy_row = next(r for r in payload["providers"] if r["slug"] == "litellm-proxy")

    # User-defined provider keeps all its models
    assert proxy_row["models"] == ["nvidia/nim/minimax-m3", "nvidia/nim/kimi-k2.6"]

    # Aggregator lost the overlapping model but kept the rest
    assert "nvidia/nim/minimax-m3" not in or_row["models"]
    assert "minimax/minimax-m3" in or_row["models"]
    assert "anthropic/claude-sonnet-4.6" in or_row["models"]
    assert or_row["total_models"] == 2


def test_aggregator_dedup_case_insensitive():
    """Dedup uses case-insensitive matching.  (#45954)"""
    rows = [
        _user_provider_row("my-proxy", ["NVIDIA/NIM/MiniMax-M3"]),
        _aggregator_row("openrouter", ["nvidia/nim/minimax-m3", "other/model"]),
    ]
    ctx = _empty_ctx()
    with _list_auth_returning(rows):
        payload = build_models_payload(ctx)

    or_row = next(r for r in payload["providers"] if r["slug"] == "openrouter")
    assert "nvidia/nim/minimax-m3" not in or_row["models"]
    assert or_row["total_models"] == 1


def test_aggregator_dedup_no_overlap_unchanged():
    """When there's no overlap, aggregator models are untouched.  (#45954)"""
    rows = [
        _user_provider_row("litellm-proxy", ["custom/model-a"]),
        _aggregator_row("openrouter", ["anthropic/claude-sonnet-4.6"]),
    ]
    ctx = _empty_ctx()
    with _list_auth_returning(rows):
        payload = build_models_payload(ctx)

    or_row = next(r for r in payload["providers"] if r["slug"] == "openrouter")
    assert or_row["models"] == ["anthropic/claude-sonnet-4.6"]
    assert or_row["total_models"] == 1


def test_aggregator_dedup_no_user_providers_unchanged():
    """When there are no user-defined providers, nothing is filtered.
    (#45954)"""
    rows = [
        _aggregator_row("openrouter", [
            "nvidia/nim/minimax-m3",
            "anthropic/claude-sonnet-4.6",
        ]),
    ]
    ctx = _empty_ctx()
    with _list_auth_returning(rows):
        payload = build_models_payload(ctx)

    or_row = next(r for r in payload["providers"] if r["slug"] == "openrouter")
    assert len(or_row["models"]) == 2


def test_aggregator_dedup_multiple_user_providers():
    """Models from all user-defined providers are excluded from aggregators.
    (#45954)"""
    rows = [
        _user_provider_row("proxy-a", ["model-x"]),
        _user_provider_row("proxy-b", ["model-y"]),
        _aggregator_row("openrouter", ["model-x", "model-y", "model-z"]),
    ]
    ctx = _empty_ctx()
    with _list_auth_returning(rows):
        payload = build_models_payload(ctx)

    or_row = next(r for r in payload["providers"] if r["slug"] == "openrouter")
    assert or_row["models"] == ["model-z"]
    assert or_row["total_models"] == 1


def test_aggregator_dedup_does_not_empty_user_defined_custom_provider():
    """A named custom provider has slug ``custom:<name>``, which makes it
    *both* ``is_user_defined=True`` *and* ``is_aggregator()==True``
    (is_aggregator reports True for every ``custom:*`` slug).  The dedup
    must skip user-defined rows: their models populate ``user_models``, so
    filtering them against that set would strip the row's entire catalog and
    hide the provider from the picker.  Regression for the #45954 dedup
    emptying ``custom:*`` providers (e.g. a local llama.cpp endpoint or an
    Anthropic-compatible proxy)."""
    rows = [
        _user_provider_row("custom:my-proxy", ["my-model-a", "my-model-b"]),
        _aggregator_row("openrouter", ["my-model-a", "other/model"]),
    ]
    ctx = _empty_ctx()
    with _list_auth_returning(rows):
        payload = build_models_payload(ctx)

    proxy_row = next(
        r for r in payload["providers"] if r["slug"] == "custom:my-proxy"
    )
    or_row = next(r for r in payload["providers"] if r["slug"] == "openrouter")

    # The user's own custom provider keeps all of its models.
    assert proxy_row["models"] == ["my-model-a", "my-model-b"]
    assert proxy_row["total_models"] == 2

    # A genuine aggregator is still deduped against the user's models.
    assert "my-model-a" not in or_row["models"]
    assert "other/model" in or_row["models"]
    assert or_row["total_models"] == 1


def test_flat_namespace_reseller_keeps_first_party_models_overlapping_user_proxy():
    """opencode-go / opencode-zen are flagged ``is_aggregator=True`` (their
    flat ``/v1/models`` returns bare IDs the model-switch resolver searches),
    but they are NOT routing aggregators — every model they list is a
    first-party model under the user's subscription. When a user also runs a
    custom proxy that happens to serve a same-named model, the picker dedup
    must NOT strip the reseller's own catalog. Regression for #47077, where
    opencode-go showed only 13 of 19 models because minimax-m3/m2.7/m2.5,
    glm-5/5.1, and deepseek-v4-flash were deduped against an overlapping
    custom provider.
    """
    rows = [
        _user_provider_row("custom:my-proxy", [
            "minimax-m3", "minimax-m2.7", "glm-5", "deepseek-v4-flash",
        ]),
        _aggregator_row("opencode-go", [
            "kimi-k2.6", "minimax-m3", "minimax-m2.7", "glm-5",
            "deepseek-v4-flash", "qwen3.7-max",
        ]),
        _aggregator_row("openrouter", ["minimax-m3", "anthropic/claude-sonnet-4.6"]),
    ]
    ctx = _empty_ctx()
    with _list_auth_returning(rows):
        payload = build_models_payload(ctx)

    go_row = next(r for r in payload["providers"] if r["slug"] == "opencode-go")
    or_row = next(r for r in payload["providers"] if r["slug"] == "openrouter")

    # The reseller keeps ALL of its first-party models — nothing stripped.
    assert go_row["models"] == [
        "kimi-k2.6", "minimax-m3", "minimax-m2.7", "glm-5",
        "deepseek-v4-flash", "qwen3.7-max",
    ]
    assert go_row["total_models"] == 6

    # A TRUE routing aggregator is still deduped against the user's models.
    assert "minimax-m3" not in or_row["models"]
    assert "anthropic/claude-sonnet-4.6" in or_row["models"]


def test_two_custom_providers_with_overlap_both_survive():
    """Two user-defined custom endpoints that happen to expose an
    overlapping model must each keep their full catalog. Neither is the
    aggregator the dedup exists to trim, so cross-filtering between two
    user-defined rows must not happen.
    """
    rows = [
        _user_provider_row("custom:proxy-a", ["shared/model", "a/only"]),
        _user_provider_row("custom:proxy-b", ["shared/model", "b/only"]),
    ]
    ctx = _empty_ctx()
    with _list_auth_returning(rows):
        payload = build_models_payload(ctx)

    a_row = next(r for r in payload["providers"] if r["slug"] == "custom:proxy-a")
    b_row = next(r for r in payload["providers"] if r["slug"] == "custom:proxy-b")
    assert a_row["models"] == ["shared/model", "a/only"]
    assert b_row["models"] == ["shared/model", "b/only"]
    assert a_row["total_models"] == 2
    assert b_row["total_models"] == 2


def test_build_models_payload_keeps_static_provider_models_from_providers_dict():
    """The inventory payload must keep configured static models from a
    ``providers:`` entry even when the same endpoint also appears via the
    compatibility ``custom_providers`` view and live discovery would fail."""
    cfg = _cfg(
        model={
            "provider": "static-gateway",
            "default": "claude-3-7-sonnet",
        },
        providers={
            "static-gateway": {
                "name": "Static Gateway",
                "api": "https://router.example.com/v1",
                "api_key": "sk-test",
                "default_model": "claude-3-7-sonnet",
                "models": [
                    {"id": "claude-3-7-sonnet"},
                    {"id": "claude-sonnet-4"},
                ],
                "discover_models": False,
            }
        },
    )
    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("agent.models_dev.fetch_models_dev", return_value={}),
        patch("hermes_cli.providers.HERMES_OVERLAYS", {}),
        patch(
            "hermes_cli.models.fetch_api_models",
            side_effect=AssertionError("fetch_api_models must not be called"),
        ),
    ):
        ctx = load_picker_context()
        payload = build_models_payload(ctx)

    rows = [
        row
        for row in payload["providers"]
        if row.get("api_url") == "https://router.example.com/v1"
    ]
    assert len(rows) == 1
    assert rows[0]["slug"] == "static-gateway"
    assert rows[0]["models"] == ["claude-3-7-sonnet", "claude-sonnet-4"]
    assert rows[0]["total_models"] == 2


def test_build_models_payload_no_max_models_returns_full_list():
    """When max_models is not passed (None), build_models_payload must
    return the full model list — not truncate to the old default of 50.
    Regression for #48279: Kilo Gateway picker was capped at 50 of 336
    models, making most models undiscoverable via search."""
    full_models = [f"model-{i}" for i in range(100)]
    rows = [
        {
            "slug": "kilocode",
            "name": "Kilo Code",
            "models": full_models,
            "total_models": len(full_models),
            "is_current": False,
            "is_user_defined": False,
            "source": "built-in",
        },
    ]
    ctx = _empty_ctx()
    with _list_auth_returning(rows):
        # No max_models argument — should return all 100 models
        payload = build_models_payload(ctx)

    kilo_row = next(r for r in payload["providers"] if r["slug"] == "kilocode")
    assert kilo_row["models"] == full_models
    assert kilo_row["total_models"] == 100
    assert len(kilo_row["models"]) == 100


# ─── refresh flag (cache-bust) ─────────────────────────────────────────


def test_build_models_payload_forwards_refresh_flag():
    """build_models_payload must forward refresh= to list_authenticated_providers.

    The desktop picker's "Refresh Models" control passes refresh=True; the
    flag has to reach list_authenticated_providers so the per-provider
    model-id cache gets busted. Default opens pass refresh=False.
    """
    captured: dict = {}

    def _capture(*args, **kwargs):
        captured["refresh"] = kwargs.get("refresh")
        return []

    with patch("hermes_cli.model_switch.list_authenticated_providers", side_effect=_capture):
        build_models_payload(_empty_ctx())
    assert captured["refresh"] is False

    with patch("hermes_cli.model_switch.list_authenticated_providers", side_effect=_capture):
        build_models_payload(_empty_ctx(), refresh=True)
    assert captured["refresh"] is True


def test_list_authenticated_providers_refresh_busts_cache():
    """refresh=True clears the provider-model disk cache exactly once;
    refresh=False leaves it untouched (so normal picker opens stay snappy)."""
    from hermes_cli import model_switch

    with patch("hermes_cli.models.clear_provider_models_cache") as clear:
        model_switch.list_authenticated_providers(refresh=False)
        assert clear.call_count == 0
        model_switch.list_authenticated_providers(refresh=True)
        assert clear.call_count == 1


# ─── Verified provider capabilities ────────────────────────────────────


def _install_inventory_gateway_profile(
    monkeypatch,
    slug="test-inventory-gateway",
    *,
    aliases=(),
):
    import providers as provider_registry
    from hermes_cli.auth import PROVIDER_REGISTRY, ProviderConfig
    from providers.base import ProviderProfile

    profile = ProviderProfile(
        name=slug,
        aliases=aliases,
        display_name="Test Inventory Gateway",
        env_vars=("OTTO_API_KEY", "OTTO_BASE_URL"),
        base_url="http://127.0.0.1:18080/v1",
        auth_type="api_key",
        supports_unauthenticated=True,
        model_capabilities_path="model-capabilities",
    )
    monkeypatch.setitem(provider_registry._REGISTRY, slug, profile)
    for alias in aliases:
        monkeypatch.setitem(provider_registry._ALIASES, alias, slug)
    monkeypatch.setitem(
        PROVIDER_REGISTRY,
        slug,
        ProviderConfig(
            id=slug,
            name="Test Inventory Gateway",
            auth_type="api_key",
            inference_base_url=profile.base_url,
            api_key_env_vars=("OTTO_API_KEY",),
            base_url_env_var="OTTO_BASE_URL",
        ),
    )
    monkeypatch.delenv("OTTO_API_KEY", raising=False)
    monkeypatch.delenv("OTTO_BASE_URL", raising=False)
    return profile


def _verified_model(
    model_id: str,
    *,
    reasoning: str,
    selection_mode: str = "explicit",
    evidence: dict | None = None,
):
    from hermes_cli.model_capabilities import VerifiedModelCapability

    return VerifiedModelCapability(
        id=model_id,
        name=model_id,
        selection_mode=selection_mode,
        capabilities={
            "completion": "supported",
            "tools": "supported",
            "vision": "unsupported",
            "reasoning": reasoning,
        },
        evidence=evidence or {},
    )


def test_noauth_gateway_inventory_is_visible_and_enriched_when_other_provider_is_current(
    monkeypatch,
):
    from hermes_cli.model_capabilities import ModelCapabilityCatalog

    slug = "test-inventory-gateway"
    _install_inventory_gateway_profile(monkeypatch, slug)
    rows = [
        {
            "slug": slug,
            "name": "Test Inventory Gateway",
            "models": [
                "model-reasoning",
                "auto",
                "model-fast",
                "model-no-reasoning",
                "model-without-metadata",
                "auto",
            ],
            "total_models": 6,
            "is_current": False,
            "is_user_defined": False,
            "source": "canonical",
        }
    ]
    catalog = ModelCapabilityCatalog(
        status="ready",
        models={
            "auto": _verified_model(
                "auto",
                reasoning="unknown",
                selection_mode="automatic",
            ),
            "model-reasoning": _verified_model(
                "model-reasoning",
                reasoning="supported",
                evidence={
                    "reasoning": {
                        "source": "gateway_registry",
                        "reference": "public-model-record",
                    }
                },
            ),
            "model-fast": _verified_model(
                "model-fast",
                reasoning="supported",
            ),
            "model-no-reasoning": _verified_model(
                "model-no-reasoning",
                reasoning="unsupported",
            ),
            "capability-only-model": _verified_model(
                "capability-only-model",
                reasoning="supported",
            ),
        },
    )
    captured = {}

    def _fetch(provider, **kwargs):
        captured["provider"] = provider
        captured.update(kwargs)
        return catalog

    ctx = _empty_ctx(provider="openrouter", model="openrouter/auto")
    with (
        _list_auth_returning(rows),
        patch(
            "hermes_cli.model_capabilities.fetch_model_capability_catalog",
            side_effect=_fetch,
        ),
        patch(
            "hermes_cli.models.model_supports_fast_mode",
            side_effect=lambda model: model == "model-fast",
        ),
        patch(
            "agent.models_dev.get_model_capabilities",
            return_value=None,
        ),
        patch(
            "hermes_cli.auth.is_provider_explicitly_configured",
            return_value=False,
        ),
    ):
        payload = build_models_payload(
            ctx,
            explicit_only=True,
            capabilities=True,
        )

    gateway = next(row for row in payload["providers"] if row["slug"] == slug)
    assert gateway["models"] == [
        "auto",
        "model-reasoning",
        "model-fast",
        "model-no-reasoning",
        "model-without-metadata",
    ]
    assert "capability-only-model" not in gateway["models"]
    assert gateway["capability_status"] == "ready"
    assert gateway["capability_mismatch_count"] == 2
    assert captured == {
        "provider": slug,
        "api_key": "dummy-lm-api-key",
        "base_url": "http://127.0.0.1:18080/v1",
        "force_refresh": False,
    }

    capabilities = gateway["capabilities"]
    assert capabilities["model-reasoning"] == {
        "fast": False,
        "reasoning": True,
        "verified": {
            "completion": "supported",
            "tools": "supported",
            "vision": "unsupported",
            "reasoning": "supported",
        },
        "selection_mode": "explicit",
        "evidence": {
            "reasoning": {
                "source": "gateway_registry",
                "reference": "public-model-record",
            }
        },
    }
    assert capabilities["model-fast"]["fast"] is True
    assert capabilities["model-no-reasoning"]["reasoning"] is False
    assert capabilities["model-without-metadata"] == {
        "fast": False,
        "reasoning": False,
        "verified": {
            "completion": "unknown",
            "tools": "unknown",
            "vision": "unknown",
            "reasoning": "unknown",
        },
        "selection_mode": "explicit",
        "evidence": {},
    }


def test_contract_provider_failure_statuses_keep_live_and_saved_models_unknown(
    monkeypatch,
):
    from hermes_cli.model_capabilities import ModelCapabilityCatalog

    slug = "test-inventory-gateway-failure"
    _install_inventory_gateway_profile(monkeypatch, slug)
    statuses = (
        "gateway-upgrade-required",
        "gateway-unreachable",
        "authentication-required",
        "capability-response-invalid",
    )
    for status in statuses:
        row = {
            "slug": slug,
            "name": "Test Inventory Gateway",
            "models": ["live-model"],
            "total_models": 1,
            "is_current": True,
            "is_user_defined": False,
            "source": "canonical",
        }
        with (
            _list_auth_returning([row]),
            patch(
                "hermes_cli.model_capabilities.fetch_model_capability_catalog",
                return_value=ModelCapabilityCatalog(status=status, models={}),
            ),
            patch(
                "hermes_cli.models.model_supports_fast_mode",
                return_value=False,
            ),
            patch(
                "agent.models_dev.get_model_capabilities",
                return_value=None,
            ),
        ):
            payload = build_models_payload(
                _empty_ctx(
                    provider=slug,
                    model="saved-out-of-catalog-model",
                ),
                capabilities=True,
            )

        gateway = next(
            provider
            for provider in payload["providers"]
            if provider["slug"] == slug
        )
        assert gateway["capability_status"] == status
        assert gateway["models"] == [
            "auto",
            "live-model",
            "saved-out-of-catalog-model",
        ]
        assert gateway["capability_mismatch_count"] == 1
        for model_id in ("live-model", "saved-out-of-catalog-model"):
            assert gateway["capabilities"][model_id]["reasoning"] is False
            assert gateway["capabilities"][model_id]["verified"] == {
                "completion": "unknown",
                "tools": "unknown",
                "vision": "unknown",
                "reasoning": "unknown",
            }
        assert (
            gateway["capabilities"]["saved-out-of-catalog-model"]["live"]
            is False
        )


def test_contract_provider_alias_keeps_saved_current_model_visible(monkeypatch):
    from hermes_cli.model_capabilities import ModelCapabilityCatalog

    slug = "test-inventory-gateway-canonical"
    alias = f"{slug}-alias"
    _install_inventory_gateway_profile(
        monkeypatch,
        slug,
        aliases=(alias,),
    )
    row = {
        "slug": slug,
        "name": "Test Inventory Gateway",
        "models": ["live-model"],
        "total_models": 1,
        "is_current": True,
        "is_user_defined": False,
        "source": "canonical",
    }
    with (
        _list_auth_returning([row]),
        patch(
            "hermes_cli.model_capabilities.fetch_model_capability_catalog",
            return_value=ModelCapabilityCatalog(
                status="gateway-unreachable",
                models={},
            ),
        ),
    ):
        payload = build_models_payload(
            _empty_ctx(
                provider=alias,
                model="saved-out-of-catalog-model",
            ),
            capabilities=True,
        )

    gateway = next(
        provider
        for provider in payload["providers"]
        if provider["slug"] == slug
    )
    assert gateway["models"] == [
        "auto",
        "live-model",
        "saved-out-of-catalog-model",
    ]
    assert gateway["capabilities"]["saved-out-of-catalog-model"]["verified"] == {
        "completion": "unknown",
        "tools": "unknown",
        "vision": "unknown",
        "reasoning": "unknown",
    }


def test_saved_contract_model_is_not_joined_as_live_catalog_evidence(monkeypatch):
    from hermes_cli.model_capabilities import ModelCapabilityCatalog

    slug = "test-inventory-gateway-stale-saved"
    saved_model = "saved-out-of-catalog-model"
    _install_inventory_gateway_profile(monkeypatch, slug)
    row = {
        "slug": slug,
        "name": "Test Inventory Gateway",
        "models": ["live-model"],
        "total_models": 1,
        "is_current": True,
        "is_user_defined": False,
        "source": "canonical",
    }
    catalog = ModelCapabilityCatalog(
        status="ready",
        models={
            "live-model": _verified_model(
                "live-model",
                reasoning="supported",
            ),
            saved_model: _verified_model(
                saved_model,
                reasoning="supported",
                evidence={
                    "tools": {
                        "source": "stale-capability-catalog",
                        "reference": "must-not-survive",
                    }
                },
            ),
        },
    )

    with (
        _list_auth_returning([row]) as list_authenticated,
        patch(
            "hermes_cli.model_capabilities.fetch_model_capability_catalog",
            return_value=catalog,
        ),
        patch(
            "hermes_cli.models.model_supports_fast_mode",
            return_value=False,
        ),
        patch(
            "agent.models_dev.get_model_capabilities",
            return_value=None,
        ),
    ):
        payload = build_models_payload(
            _empty_ctx(provider=slug, model=saved_model),
            capabilities=True,
        )

    assert list_authenticated.call_args.kwargs["inject_current_model"] is False
    gateway = next(
        provider
        for provider in payload["providers"]
        if provider["slug"] == slug
    )
    assert gateway["models"] == ["auto", "live-model", saved_model]
    assert gateway["capability_mismatch_count"] == 1
    assert gateway["capabilities"][saved_model] == {
        "fast": False,
        "reasoning": False,
        "verified": {
            "completion": "unknown",
            "tools": "unknown",
            "vision": "unknown",
            "reasoning": "unknown",
        },
        "selection_mode": "explicit",
        "evidence": {},
        "live": False,
    }


def test_saved_contract_model_helper_uses_resolvable_context_annotation():
    from typing import get_type_hints

    from hermes_cli.inventory import ConfigContext, _append_saved_contract_model

    assert get_type_hints(_append_saved_contract_model)["ctx"] is ConfigContext


def test_explicit_only_does_not_globally_retain_noncurrent_lmstudio():
    rows = [
        {
            "slug": "lmstudio",
            "name": "LM Studio",
            "models": ["local-model"],
            "total_models": 1,
            "is_current": False,
            "is_user_defined": False,
            "source": "canonical",
        }
    ]
    ctx = _empty_ctx(provider="openrouter", model="openrouter/auto")
    with (
        _list_auth_returning(rows),
        patch(
            "hermes_cli.auth.is_provider_explicitly_configured",
            return_value=False,
        ),
    ):
        payload = build_models_payload(ctx, explicit_only=True)

    assert all(row["slug"] != "lmstudio" for row in payload["providers"])


def test_provider_without_capability_contract_keeps_legacy_capability_shape():
    rows = [
        {
            "slug": "openrouter",
            "name": "OpenRouter",
            "models": ["model-a", "model-b"],
            "total_models": 2,
            "is_current": True,
            "is_user_defined": False,
            "source": "built-in",
        }
    ]
    with (
        _list_auth_returning(rows),
        patch(
            "hermes_cli.models.model_supports_fast_mode",
            side_effect=lambda model: model == "model-b",
        ),
        patch(
            "agent.models_dev.get_model_capabilities",
            return_value=None,
        ),
    ):
        payload = build_models_payload(
            _empty_ctx(provider="openrouter", model="model-a"),
            capabilities=True,
        )

    openrouter = next(
        row for row in payload["providers"] if row["slug"] == "openrouter"
    )
    assert openrouter["capabilities"] == {
        "model-a": {"fast": False, "reasoning": True},
        "model-b": {"fast": True, "reasoning": True},
    }
    assert "capability_status" not in openrouter
    assert "capability_mismatch_count" not in openrouter


def test_inventory_refresh_bypasses_model_and_capability_caches(monkeypatch):
    from hermes_cli.model_capabilities import ModelCapabilityCatalog

    slug = "test-inventory-gateway-refresh"
    _install_inventory_gateway_profile(monkeypatch, slug)
    row = {
        "slug": slug,
        "name": "Test Inventory Gateway",
        "models": ["live-model"],
        "total_models": 1,
        "is_current": True,
        "is_user_defined": False,
        "source": "canonical",
    }
    with (
        patch(
            "hermes_cli.model_switch.list_authenticated_providers",
            return_value=[row],
        ) as list_providers,
        patch(
            "hermes_cli.model_capabilities.fetch_model_capability_catalog",
            return_value=ModelCapabilityCatalog(status="ready", models={}),
        ) as fetch_capabilities,
    ):
        build_models_payload(
            _empty_ctx(provider=slug, model="live-model"),
            capabilities=True,
            refresh=True,
        )

    assert list_providers.call_args.kwargs["refresh"] is True
    assert fetch_capabilities.call_args.kwargs["force_refresh"] is True


def test_verified_inventory_serialization_omits_credentials_and_authorization(
    monkeypatch,
):
    from hermes_cli.model_capabilities import ModelCapabilityCatalog

    slug = "test-inventory-gateway-secret"
    _install_inventory_gateway_profile(monkeypatch, slug)
    secret = "sk-inventory-canary-must-not-serialize"
    monkeypatch.setenv("OTTO_API_KEY", secret)
    row = {
        "slug": slug,
        "name": "Test Inventory Gateway",
        "models": ["live-model"],
        "total_models": 1,
        "is_current": True,
        "is_user_defined": False,
        "source": "canonical",
    }
    with (
        _list_auth_returning([row]),
        patch(
            "hermes_cli.model_capabilities.fetch_model_capability_catalog",
            return_value=ModelCapabilityCatalog(status="ready", models={}),
        ),
    ):
        payload = build_models_payload(
            _empty_ctx(provider=slug, model="live-model"),
            capabilities=True,
        )

    serialized = json.dumps(payload)
    assert secret not in serialized
    assert "Authorization" not in serialized
