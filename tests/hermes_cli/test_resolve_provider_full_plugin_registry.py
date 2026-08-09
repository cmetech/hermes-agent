"""Regression test: resolve_provider_full must resolve plugin-registry providers.

Symptom A (docs/2026-07-16-hermes-provider-registry-fixes.md): a brand gateway
provider (e.g. ``loop24``) is registered ONLY as a plugin, via
``register_provider(ProviderProfile(...))`` in
``plugins/model-providers/<brand>/__init__.py``. The in-chat ``/model`` selector
and CLI setup resolve providers through ``resolve_provider_full()``, which
historically consulted only config.yaml ``providers:`` / ``custom_providers:``
plus built-ins (models.dev + overlays) — never the plugin registry — so picking
a brand-gateway model raised ``Unknown provider '<brand>'``.

``resolve_provider_full()`` must fall back to the plugin registry and map the
``ProviderProfile`` into the same ``ProviderDef`` return contract as the
config-provider branch, so downstream routing is unchanged.
"""

from pathlib import Path

import providers as provider_registry
from providers.base import ProviderProfile

from hermes_cli.providers import resolve_provider_full


def _register_user_provider(
    root: Path,
    *,
    name: str,
    base_url: str,
) -> None:
    plugin = root / f"phase5-public-{name}"
    plugin.mkdir()
    (plugin / "__init__.py").write_text(
        "from providers import register_provider\n"
        "from providers.base import ProviderProfile\n"
        "register_provider(ProviderProfile(\n"
        f"    name={name!r},\n"
        f"    base_url={base_url!r},\n"
        "    env_vars=('PHASE5_PUBLIC_PROVIDER_API_KEY',),\n"
        "))\n",
        encoding="utf-8",
    )
    (plugin / "plugin.yaml").write_text(
        f"name: phase5-public-{name}\n"
        "kind: model-provider\n"
        "version: 1.0.0\n",
        encoding="utf-8",
    )
    provider_registry.get_provider_profile("anthropic")
    provider_registry._import_plugin_dir(plugin, "user")


def test_resolve_provider_full_finds_plugin_only_provider():
    """A provider registered only in the plugin registry (no config.yaml entry)
    must resolve, with routing fields populated from the profile."""
    # Unique slug: not a built-in alias, not in models.dev, not in any config.
    slug = "otto-parity-gw"
    profile = ProviderProfile(
        name=slug,
        display_name=f"{slug} Gateway",
        env_vars=(f"{slug.upper().replace('-', '_')}_API_KEY",
                  f"{slug.upper().replace('-', '_')}_BASE_URL"),
        base_url="http://127.0.0.1:18080/v1",
        auth_type="api_key",
        supports_unauthenticated=True,
        model_capabilities_path="model-capabilities",
    )
    # Trigger lazy plugin discovery, THEN register the profile so discovery
    # cannot clobber the application-level fixture.
    provider_registry.get_provider_profile("anthropic")
    provider_registry.register_provider(profile)

    # Plugin registry is the ONLY source — empty config providers/custom lists.
    pdef = resolve_provider_full(slug, {}, {})

    assert pdef is not None, "resolve_provider_full ignored the plugin registry"
    assert pdef.id == slug
    assert pdef.base_url == "http://127.0.0.1:18080/v1"
    assert pdef.auth_type == "api_key"
    assert pdef.transport == "openai_chat"  # api_mode chat_completions → openai_chat
    # The *_BASE_URL var is split out of the key list (mirrors the auth.py
    # PROVIDER_REGISTRY auto-merge) so it becomes base_url_env_var.
    assert "OTTO_PARITY_GW_API_KEY" in pdef.api_key_env_vars
    assert "OTTO_PARITY_GW_BASE_URL" not in pdef.api_key_env_vars
    assert pdef.base_url_env_var == "OTTO_PARITY_GW_BASE_URL"


def test_resolve_provider_full_plugin_provider_resolves_via_alias():
    """Plugin providers declare aliases; resolving by alias must also work
    (get_provider_profile resolves aliases → canonical name)."""
    slug = "otto-parity-gw"
    profile = ProviderProfile(
        name=slug,
        aliases=(f"{slug}-alias",),
        display_name=f"{slug} Gateway",
        env_vars=(f"{slug.upper().replace('-', '_')}_API_KEY",),
        base_url="http://127.0.0.1:18080/v1",
        auth_type="api_key",
    )
    provider_registry.get_provider_profile("anthropic")
    provider_registry.register_provider(profile)

    pdef = resolve_provider_full(f"{slug}-alias", {}, {})

    assert pdef is not None
    assert pdef.id == slug
    assert pdef.base_url == "http://127.0.0.1:18080/v1"


def test_resolve_provider_full_honors_user_canonical_over_static_alias(
    tmp_path: Path,
) -> None:
    _register_user_provider(
        tmp_path,
        name="claude",
        base_url="https://user-claude.example/v1",
    )

    pdef = resolve_provider_full("claude", {}, {})

    assert pdef is not None
    assert pdef.id == "claude"
    assert pdef.base_url == "https://user-claude.example/v1"
    assert pdef.source == "plugin"


def test_resolve_provider_full_honors_user_override_of_builtin_provider(
    tmp_path: Path,
) -> None:
    _register_user_provider(
        tmp_path,
        name="openrouter",
        base_url="https://user-openrouter.example/v1",
    )

    pdef = resolve_provider_full("openrouter", {}, {})

    assert pdef is not None
    assert pdef.id == "openrouter"
    assert pdef.base_url == "https://user-openrouter.example/v1"
    assert pdef.source == "plugin"
