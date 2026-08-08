from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
import sys

import pytest

import providers


def _write_plugin(
    root: Path,
    directory_name: str,
    *,
    registered_name: str,
    marker: str,
    version: str = "1.0.0",
    helper_value: str | None = None,
    spoofed_origin: str | None = None,
    aliases: tuple[str, ...] | None = None,
    base_url: str = "",
) -> Path:
    plugin = root / directory_name
    plugin.mkdir(parents=True)
    helper_import = ""
    helper_method = ""
    if helper_value is not None:
        (plugin / "helper.py").write_text(
            f"def declaration_value():\n    return {helper_value!r}\n",
            encoding="utf-8",
        )
        helper_import = "from .helper import declaration_value\n"
        helper_method = (
            "\nclass ProbeProfile(ProviderProfile):\n"
            "    def build_extra_body(self, **context):\n"
            "        return {'declaration': declaration_value()}\n"
        )
        constructor = "ProbeProfile"
    else:
        constructor = "ProviderProfile"
    declared_aliases = (
        aliases if aliases is not None else (registered_name + "-alias",)
    )
    spoof = (
        f"profile.registration_origin = {spoofed_origin!r}\n"
        if spoofed_origin is not None
        else ""
    )
    (plugin / "__init__.py").write_text(
        "from providers import register_provider\n"
        "from providers.base import ProviderProfile\n"
        f"{helper_import}"
        f"{helper_method}"
        f"profile = {constructor}(\n"
        f"    name={registered_name!r},\n"
        f"    description={marker!r},\n"
        f"    aliases={declared_aliases!r},\n"
        f"    base_url={base_url!r},\n"
        ")\n"
        f"{spoof}"
        "register_provider(profile)\n",
        encoding="utf-8",
    )
    (plugin / "plugin.yaml").write_text(
        f"name: {directory_name}-distribution\n"
        "kind: model-provider\n"
        f"version: {version}\n"
        "description: provider precedence test fixture\n",
        encoding="utf-8",
    )
    return plugin


def _write_legacy_module(root: Path, module_name: str, *, registered_name: str, marker: str) -> None:
    (root / f"{module_name}.py").write_text(
        "from providers import register_provider\n"
        "from providers.base import ProviderProfile\n"
        "register_provider(ProviderProfile(\n"
        f"    name={registered_name!r},\n"
        f"    description={marker!r},\n"
        "))\n",
        encoding="utf-8",
    )


def _evict_provider_modules(*prefixes: str) -> None:
    for module_name in tuple(sys.modules):
        if any(module_name == prefix or module_name.startswith(prefix + ".") for prefix in prefixes):
            sys.modules.pop(module_name, None)


@pytest.fixture
def isolated_provider_registry():
    registry = dict(providers._REGISTRY)
    aliases = dict(providers._ALIASES)
    registrations = dict(getattr(providers, "_REGISTRATIONS", {}))
    collisions = list(getattr(providers, "_REGISTRATION_COLLISIONS", []))
    provider_list_cache = providers._PROVIDER_LIST_CACHE
    discovered = providers._discovered

    providers._REGISTRY.clear()
    providers._ALIASES.clear()
    if hasattr(providers, "_REGISTRATIONS"):
        providers._REGISTRATIONS.clear()
    if hasattr(providers, "_REGISTRATION_COLLISIONS"):
        providers._REGISTRATION_COLLISIONS.clear()
    providers._PROVIDER_LIST_CACHE = None
    providers._discovered = True

    yield

    providers._REGISTRY.clear()
    providers._REGISTRY.update(registry)
    providers._ALIASES.clear()
    providers._ALIASES.update(aliases)
    if hasattr(providers, "_REGISTRATIONS"):
        providers._REGISTRATIONS.clear()
        providers._REGISTRATIONS.update(registrations)
    if hasattr(providers, "_REGISTRATION_COLLISIONS"):
        providers._REGISTRATION_COLLISIONS.clear()
        providers._REGISTRATION_COLLISIONS.extend(collisions)
    providers._PROVIDER_LIST_CACHE = provider_list_cache
    providers._discovered = discovered
    _evict_provider_modules(
        "plugins.model_providers.precedence_probe",
        "plugins.model_providers.bundled_only",
        "_hermes_user_provider_precedence_probe",
        "_hermes_user_provider_user_only",
        "_hermes_user_provider_closure_probe",
        "providers.legacy_precedence_probe",
        "providers.legacy_only",
    )


def test_discovery_precedence_is_bundled_then_legacy_then_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_provider_registry: None,
) -> None:
    bundled_root = tmp_path / "bundled"
    user_root = tmp_path / "user"
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    _write_plugin(
        bundled_root,
        "precedence-probe",
        registered_name="precedence-probe",
        marker="bundled",
    )
    _write_plugin(
        user_root,
        "precedence-probe",
        registered_name="precedence-probe",
        marker="user",
    )
    _write_legacy_module(
        legacy_root,
        "legacy_precedence_probe",
        registered_name="precedence-probe",
        marker="legacy",
    )

    monkeypatch.setattr(providers, "_BUNDLED_PLUGINS_DIR", bundled_root)
    monkeypatch.setattr(providers, "_user_plugins_dir", lambda: user_root)
    monkeypatch.setattr(providers, "__path__", [str(legacy_root)])
    providers._discovered = False

    profile = providers.get_provider_profile("precedence-probe")

    assert profile is not None
    assert profile.description == "user"
    assert providers.get_provider_profile("precedence-probe-alias") is profile
    registration = providers.get_provider_registration("precedence-probe")
    assert registration is not None
    assert registration.provenance.origin_kind == "user_plugin"


def test_loader_provenance_distinguishes_origins_and_ignores_profile_spoofing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_provider_registry: None,
) -> None:
    bundled_root = tmp_path / "bundled"
    user_root = tmp_path / "user"
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    _write_plugin(
        bundled_root,
        "bundled-only",
        registered_name="bundled-only",
        marker="bundled",
    )
    _write_plugin(
        user_root,
        "user-only",
        registered_name="user-only",
        marker="user",
        spoofed_origin="bundled",
    )
    _write_legacy_module(
        legacy_root,
        "legacy_only",
        registered_name="legacy-only",
        marker="legacy",
    )

    monkeypatch.setattr(providers, "_BUNDLED_PLUGINS_DIR", bundled_root)
    monkeypatch.setattr(providers, "_user_plugins_dir", lambda: user_root)
    monkeypatch.setattr(providers, "__path__", [str(legacy_root)])
    providers._discovered = False
    providers.list_providers()

    expected = {
        "bundled-only": "bundled",
        "legacy-only": "legacy_compatible",
        "user-only": "user_plugin",
    }
    for name, origin in expected.items():
        registration = providers.get_provider_registration(name)
        assert registration is not None
        assert registration.profile is providers.get_provider_profile(name)
        assert registration.provenance.origin_kind == origin
        assert registration.provenance.code_closure_complete is True
        assert len(registration.provenance.code_closure_digest) == 64
        assert registration.provenance.distribution_id
        with pytest.raises(FrozenInstanceError):
            registration.provenance.origin_kind = "spoofed"


def test_same_version_entry_and_imported_helper_mutations_change_closure_digest(
    tmp_path: Path,
    isolated_provider_registry: None,
) -> None:
    plugin = _write_plugin(
        tmp_path,
        "closure-probe",
        registered_name="closure-probe",
        marker="first-body",
        version="7.4.1",
        helper_value="first-helper",
    )

    providers._import_plugin_dir(plugin, "user")
    first = providers.get_provider_registration("closure-probe")
    assert first is not None
    first_digest = first.provenance.code_closure_digest
    assert first.provenance.distribution_version == "7.4.1"

    (plugin / "helper.py").write_text(
        "def declaration_value():\n    return 'second-helper-with-new-bytes'\n",
        encoding="utf-8",
    )
    _evict_provider_modules("_hermes_user_provider_closure_probe")
    providers._import_plugin_dir(plugin, "user")
    second = providers.get_provider_registration("closure-probe")
    assert second is not None
    assert second.provenance.distribution_version == "7.4.1"
    assert second.provenance.code_closure_digest != first_digest

    entry = plugin / "__init__.py"
    entry.write_text(
        entry.read_text(encoding="utf-8").replace("first-body", "third-body-with-new-bytes"),
        encoding="utf-8",
    )
    _evict_provider_modules("_hermes_user_provider_closure_probe")
    providers._import_plugin_dir(plugin, "user")
    third = providers.get_provider_registration("closure-probe")
    assert third is not None
    assert third.provenance.distribution_version == "7.4.1"
    assert third.provenance.code_closure_digest != second.provenance.code_closure_digest


def test_unhashable_dynamic_registration_is_not_native_eligible(
    isolated_provider_registry: None,
) -> None:
    namespace = {"__name__": "dynamic_provider_probe"}
    exec(
        compile(
            "from providers import register_provider\n"
            "from providers.base import ProviderProfile\n"
            "register_provider(ProviderProfile(name='dynamic-provider'))\n",
            "<dynamic-provider-probe>",
            "exec",
        ),
        namespace,
    )

    registration = providers.get_provider_registration("dynamic-provider")
    assert registration is not None
    assert registration.provenance.origin_kind == "legacy_compatible"
    assert registration.provenance.code_closure_complete is False
    assert registration.provenance.code_closure_digest == ""


def test_collision_diagnostics_are_bounded_and_path_free(
    isolated_provider_registry: None,
) -> None:
    for index in range(200):
        providers.register_provider(
            providers.ProviderProfile(name="collision-probe", description=str(index))
        )

    diagnostics = providers.list_provider_registration_collisions()

    assert 0 < len(diagnostics) <= 128
    for diagnostic in diagnostics:
        assert diagnostic.provider == "collision-probe"
        assert diagnostic.code
        assert "/" not in diagnostic.code
        assert "\\" not in diagnostic.code


def test_discovery_resolves_canonical_alias_collisions_in_both_directions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_provider_registry: None,
) -> None:
    bundled_root = tmp_path / "bundled"
    user_root = tmp_path / "user"
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    _write_plugin(
        bundled_root,
        "anthropic-probe",
        registered_name="anthropic",
        marker="bundled-anthropic",
        aliases=("claude",),
    )
    _write_plugin(
        bundled_root,
        "openrouter-probe",
        registered_name="openrouter",
        marker="bundled-openrouter",
        aliases=(),
    )
    _write_plugin(
        user_root,
        "claude-probe",
        registered_name="claude",
        marker="user-claude",
        aliases=(),
    )
    _write_plugin(
        user_root,
        "shadow-probe",
        registered_name="shadow",
        marker="user-shadow",
        aliases=("openrouter",),
    )

    monkeypatch.setattr(providers, "_BUNDLED_PLUGINS_DIR", bundled_root)
    monkeypatch.setattr(providers, "_user_plugins_dir", lambda: user_root)
    monkeypatch.setattr(providers, "__path__", [str(legacy_root)])
    providers._discovered = False

    assert providers.get_provider_profile("openrouter").description == (
        "bundled-openrouter"
    )
    assert providers.get_provider_profile("claude").description == "user-claude"
    diagnostics = {
        (diagnostic.provider, diagnostic.code)
        for diagnostic in providers.list_provider_registration_collisions()
    }
    assert diagnostics == {
        ("claude", "provider_alias_displaced_by_canonical"),
        ("openrouter", "provider_alias_rejected_canonical"),
    }


def test_auth_resolution_consumes_registry_canonical_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_provider_registry: None,
) -> None:
    bundled_root = tmp_path / "bundled"
    user_root = tmp_path / "user"
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    _write_plugin(
        bundled_root,
        "openrouter-probe",
        registered_name="openrouter",
        marker="bundled-openrouter",
        aliases=("or",),
    )
    _write_plugin(
        user_root,
        "or-probe",
        registered_name="or",
        marker="user-or",
        aliases=(),
    )
    monkeypatch.setattr(providers, "_BUNDLED_PLUGINS_DIR", bundled_root)
    monkeypatch.setattr(providers, "_user_plugins_dir", lambda: user_root)
    monkeypatch.setattr(providers, "__path__", [str(legacy_root)])
    providers._discovered = False

    from hermes_cli.auth import resolve_provider

    registration = providers.get_provider_registration("or")
    assert registration is not None
    assert registration.profile.description == "user-or"
    assert registration.provenance.origin_kind == "user_plugin"
    assert resolve_provider("or") == "or"


def test_credential_free_runtime_uses_registry_winner_not_hardcoded_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_provider_registry: None,
) -> None:
    bundled_root = tmp_path / "bundled"
    user_root = tmp_path / "user"
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    _write_plugin(
        bundled_root,
        "bedrock-probe",
        registered_name="bedrock",
        marker="bundled-bedrock",
        aliases=("aws",),
    )
    _write_plugin(
        user_root,
        "aws-probe",
        registered_name="aws",
        marker="user-aws",
        aliases=(),
        base_url="https://user-aws.example/v1",
    )
    monkeypatch.setattr(providers, "_BUNDLED_PLUGINS_DIR", bundled_root)
    monkeypatch.setattr(providers, "_user_plugins_dir", lambda: user_root)
    monkeypatch.setattr(providers, "__path__", [str(legacy_root)])
    providers._discovered = False

    from hermes_cli.runtime_provider import (
        classify_execution_runtime,
        execution_endpoint_sha256,
    )

    runtime = classify_execution_runtime(
        provider="aws",
        model_config={"provider": "aws", "default": "user-model"},
        provider_config={"api_mode": "chat_completions", "region": "eu-west-1"},
    )

    assert runtime.effective_provider == "aws"
    assert runtime.registration_origin_kind == "user_plugin"
    assert runtime.base_url_trust_class == "unknown"
    assert runtime.endpoint_sha256 == execution_endpoint_sha256(
        provider="aws",
        api_mode="chat_completions",
        base_url="https://user-aws.example/v1",
    )


@pytest.mark.parametrize(
    ("provider", "bundled_aliases", "profile_base_url", "provider_config"),
    [
        (
            "bedrock",
            ("aws",),
            "https://user-bedrock.example/v1",
            {"api_mode": "chat_completions", "region": "eu-west-1"},
        ),
        (
            "vertex",
            ("google-vertex",),
            "https://user-vertex.example/v1",
            {"api_mode": "chat_completions"},
        ),
    ],
    ids=("bedrock-user-override", "vertex-user-override"),
)
def test_same_name_user_cloud_provider_uses_registered_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_provider_registry: None,
    provider: str,
    bundled_aliases: tuple[str, ...],
    profile_base_url: str,
    provider_config: dict[str, str],
) -> None:
    bundled_root = tmp_path / "bundled"
    user_root = tmp_path / "user"
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    _write_plugin(
        bundled_root,
        f"{provider}-bundled-probe",
        registered_name=provider,
        marker=f"bundled-{provider}",
        aliases=bundled_aliases,
    )
    _write_plugin(
        user_root,
        f"{provider}-user-probe",
        registered_name=provider,
        marker=f"user-{provider}",
        aliases=(),
        base_url=profile_base_url,
    )
    monkeypatch.setattr(providers, "_BUNDLED_PLUGINS_DIR", bundled_root)
    monkeypatch.setattr(providers, "_user_plugins_dir", lambda: user_root)
    monkeypatch.setattr(providers, "__path__", [str(legacy_root)])
    providers._discovered = False

    from hermes_cli.runtime_provider import classify_execution_runtime

    runtime = classify_execution_runtime(
        provider=provider,
        model_config={"provider": provider, "default": "user-model"},
        provider_config=provider_config,
    )
    expected_endpoint = hashlib.sha256(
        b"hermes-execution-endpoint-v1\0" + profile_base_url.encode("utf-8")
    ).hexdigest()

    assert runtime.effective_provider == provider
    assert runtime.registration_origin_kind == "user_plugin"
    assert runtime.base_url_trust_class == "unknown"
    assert runtime.endpoint_sha256 == expected_endpoint
    assert runtime.endpoint_identity_error is None


@pytest.mark.parametrize(
    ("provider", "provider_config", "legacy_endpoint"),
    [
        (
            "bedrock",
            {"api_mode": "bedrock_converse", "region": "eu-central-1"},
            "https://bedrock-runtime.eu-central-1.amazonaws.com",
        ),
        (
            "vertex",
            {
                "api_mode": "chat_completions",
                "project_id": "legacy-project",
                "region": "europe-west4",
            },
            "https://europe-west4-aiplatform.googleapis.com/v1beta1/projects/"
            "legacy-project/locations/europe-west4/endpoints/openapi",
        ),
    ],
    ids=("bedrock-unregistered", "vertex-unregistered"),
)
def test_unregistered_legacy_cloud_provider_keeps_endpoint_synthesis(
    isolated_provider_registry: None,
    provider: str,
    provider_config: dict[str, str],
    legacy_endpoint: str,
) -> None:
    from hermes_cli.runtime_provider import classify_execution_runtime

    runtime = classify_execution_runtime(
        provider=provider,
        model_config={"provider": provider, "default": "legacy-model"},
        provider_config=provider_config,
    )
    expected_endpoint = hashlib.sha256(
        b"hermes-execution-endpoint-v1\0" + legacy_endpoint.encode("utf-8")
    ).hexdigest()

    assert runtime.registration_origin_kind == ""
    assert runtime.endpoint_sha256 == expected_endpoint
    assert runtime.endpoint_identity_error is None


def test_legacy_aws_alias_uses_user_bedrock_canonical_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_provider_registry: None,
) -> None:
    bundled_root = tmp_path / "bundled"
    user_root = tmp_path / "user"
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    _write_plugin(
        bundled_root,
        "bedrock-legacy-alias-bundled",
        registered_name="bedrock",
        marker="bundled-bedrock",
        aliases=("aws",),
    )
    _write_plugin(
        user_root,
        "bedrock-legacy-alias-user",
        registered_name="bedrock",
        marker="user-bedrock",
        aliases=(),
        base_url="https://user-bedrock-alias.example/v1",
    )
    monkeypatch.setattr(providers, "_BUNDLED_PLUGINS_DIR", bundled_root)
    monkeypatch.setattr(providers, "_user_plugins_dir", lambda: user_root)
    monkeypatch.setattr(providers, "__path__", [str(legacy_root)])
    providers._discovered = False

    from hermes_cli.runtime_provider import classify_execution_runtime

    runtime = classify_execution_runtime(
        provider="aws",
        model_config={"provider": "aws", "default": "user-model"},
        provider_config={"api_mode": "chat_completions", "region": "eu-west-1"},
    )
    expected_endpoint = hashlib.sha256(
        b"hermes-execution-endpoint-v1\0https://user-bedrock-alias.example/v1"
    ).hexdigest()

    assert runtime.effective_provider == "bedrock"
    assert runtime.registration_origin_kind == "user_plugin"
    assert runtime.base_url_trust_class == "unknown"
    assert runtime.endpoint_sha256 == expected_endpoint
    assert runtime.endpoint_identity_error is None


def test_registered_vertex_alias_uses_user_vertex_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_provider_registry: None,
) -> None:
    bundled_root = tmp_path / "bundled"
    user_root = tmp_path / "user"
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    _write_plugin(
        bundled_root,
        "vertex-registry-alias-bundled",
        registered_name="vertex",
        marker="bundled-vertex",
        aliases=("google-vertex",),
    )
    _write_plugin(
        user_root,
        "vertex-registry-alias-user",
        registered_name="vertex",
        marker="user-vertex",
        aliases=("google-vertex",),
        base_url="https://user-vertex-alias.example/v1",
    )
    monkeypatch.setattr(providers, "_BUNDLED_PLUGINS_DIR", bundled_root)
    monkeypatch.setattr(providers, "_user_plugins_dir", lambda: user_root)
    monkeypatch.setattr(providers, "__path__", [str(legacy_root)])
    providers._discovered = False

    from hermes_cli.runtime_provider import classify_execution_runtime

    runtime = classify_execution_runtime(
        provider="google-vertex",
        model_config={"provider": "google-vertex", "default": "user-model"},
        provider_config={"api_mode": "chat_completions"},
    )
    expected_endpoint = hashlib.sha256(
        b"hermes-execution-endpoint-v1\0https://user-vertex-alias.example/v1"
    ).hexdigest()

    assert runtime.effective_provider == "vertex"
    assert runtime.registration_origin_kind == "user_plugin"
    assert runtime.base_url_trust_class == "unknown"
    assert runtime.endpoint_sha256 == expected_endpoint
    assert runtime.endpoint_identity_error is None
