import pytest

from providers import ProviderProfile
import providers


@pytest.fixture(autouse=True)
def isolate_provider_registry():
    registry = providers._REGISTRY.copy()
    aliases = providers._ALIASES.copy()
    registrations = providers._REGISTRATIONS.copy()
    collisions = list(providers._REGISTRATION_COLLISIONS)
    provider_list_cache = (
        None
        if providers._PROVIDER_LIST_CACHE is None
        else list(providers._PROVIDER_LIST_CACHE)
    )
    discovered = providers._discovered

    yield

    providers._REGISTRY.clear()
    providers._REGISTRY.update(registry)
    providers._ALIASES.clear()
    providers._ALIASES.update(aliases)
    providers._REGISTRATIONS.clear()
    providers._REGISTRATIONS.update(registrations)
    providers._REGISTRATION_COLLISIONS.clear()
    providers._REGISTRATION_COLLISIONS.extend(collisions)
    providers._PROVIDER_LIST_CACHE = provider_list_cache
    providers._discovered = discovered


def _profile(name: str, *aliases: str) -> ProviderProfile:
    return ProviderProfile(name=name, aliases=aliases)


def _reset_registry() -> None:
    providers._REGISTRY.clear()
    providers._ALIASES.clear()
    providers._REGISTRATIONS.clear()
    providers._REGISTRATION_COLLISIONS.clear()
    providers._PROVIDER_LIST_CACHE = None
    providers._discovered = True


def _register_at_origin(
    profile: ProviderProfile,
    origin_kind: str,
) -> None:
    context = providers._RegistrationContext(
        origin_kind=origin_kind,
        distribution_id=f"{origin_kind}-fixture",
        distribution_version="1",
        package_root=None,
    )
    token = providers._REGISTRATION_CONTEXT.set(context)
    try:
        providers.register_provider(profile)
    finally:
        providers._REGISTRATION_CONTEXT.reset(token)


def _collision_codes() -> list[str]:
    return [
        diagnostic.code
        for diagnostic in providers.list_provider_registration_collisions()
    ]


def test_list_providers_reuses_cached_snapshot_until_registration_changes():
    _reset_registry()
    first = _profile("alpha")
    providers.register_provider(first)

    listed = providers.list_providers()
    listed.clear()

    assert providers.list_providers() == [first]

    # Hit-path copy guard: mutating a CACHED return must not corrupt the
    # module-level snapshot for later callers (aliasing bug class).
    providers.list_providers().clear()
    assert providers.list_providers() == [first]

    second = _profile("beta")
    providers.register_provider(second)

    assert providers.list_providers() == [first, second]


def test_list_providers_dedupes_aliases_in_cached_snapshot():
    _reset_registry()
    profile = _profile("kimi", "moonshot", "kimi-k2")
    providers.register_provider(profile)

    assert providers.get_provider_profile("moonshot") is profile
    assert providers.list_providers() == [profile]


def test_canonical_name_always_outranks_user_alias_collision():
    _reset_registry()
    canonical = _profile("openrouter")
    shadow = _profile("shadow", "openrouter")

    _register_at_origin(canonical, "bundled")
    _register_at_origin(shadow, "user_plugin")

    assert providers.get_provider_profile("openrouter") is canonical
    registration = providers.get_provider_registration("openrouter")
    assert registration is not None
    assert registration.profile is canonical
    assert _collision_codes() == ["provider_alias_rejected_canonical"]


def test_user_canonical_name_displaces_bundled_alias_collision():
    _reset_registry()
    anthropic = _profile("anthropic", "claude")
    canonical_claude = _profile("claude")

    _register_at_origin(anthropic, "bundled")
    _register_at_origin(canonical_claude, "user_plugin")

    assert providers.get_provider_profile("claude") is canonical_claude
    registration = providers.get_provider_registration("claude")
    assert registration is not None
    assert registration.profile is canonical_claude
    assert _collision_codes() == ["provider_alias_displaced_by_canonical"]


@pytest.mark.parametrize(
    ("first_origin", "second_origin", "expected_winner", "expected_code"),
    [
        (
            "bundled",
            "legacy_compatible",
            "second",
            "provider_alias_higher_precedence_replaced",
        ),
        (
            "legacy_compatible",
            "bundled",
            "first",
            "provider_alias_lower_precedence_ignored",
        ),
        (
            "legacy_compatible",
            "user_plugin",
            "second",
            "provider_alias_higher_precedence_replaced",
        ),
        (
            "user_plugin",
            "legacy_compatible",
            "first",
            "provider_alias_lower_precedence_ignored",
        ),
        (
            "user_plugin",
            "user_plugin",
            "second",
            "provider_alias_same_precedence_replaced",
        ),
    ],
)
def test_alias_collisions_use_loader_origin_precedence(
    first_origin: str,
    second_origin: str,
    expected_winner: str,
    expected_code: str,
):
    _reset_registry()
    first = _profile("first", "shared-token")
    second = _profile("second", "shared-token")

    _register_at_origin(first, first_origin)
    _register_at_origin(second, second_origin)

    expected = first if expected_winner == "first" else second
    assert providers.get_provider_profile("shared-token") is expected
    registration = providers.get_provider_registration("shared-token")
    assert registration is not None
    assert registration.profile is expected
    assert _collision_codes() == [expected_code]


def test_replacing_canonical_removes_stale_alias_owner_and_provenance():
    _reset_registry()
    original = _profile("replaceable", "stale-alias")
    replacement = _profile("replaceable", "current-alias")

    _register_at_origin(original, "legacy_compatible")
    providers.list_providers()
    _register_at_origin(replacement, "legacy_compatible")

    assert providers.get_provider_profile("stale-alias") is None
    assert providers.get_provider_profile("current-alias") is replacement
    registration = providers.get_provider_registration("replaceable")
    assert registration is not None
    alias_registration = providers._ALIASES["current-alias"]
    assert isinstance(alias_registration, providers._ProviderAliasRegistration)
    assert alias_registration.canonical_name == "replaceable"
    assert alias_registration.provenance == registration.provenance
    assert providers._PROVIDER_LIST_CACHE is None
