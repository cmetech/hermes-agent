"""Behavior tests for generic Microsoft Graph identities."""

from __future__ import annotations

import importlib
import os
import stat

import pytest

from tools import microsoft_graph_identity as identity


def test_graph_auth_modes_are_typed_and_connector_neutral():
    identity = importlib.import_module("tools.microsoft_graph_identity")

    assert {mode.value for mode in identity.GraphAuthMode} == {
        "app_only",
        "delegated_msal",
        "azure_cli",
        "auto",
    }


def test_identity_config_normalizes_authority_scopes_and_account(tmp_path):
    config = identity.GraphIdentityConfig(
        mode=" delegated_msal ",
        tenant_id=" tenant-id ",
        client_id=" client-id ",
        scopes=(" User.Read ", "", "Sites.Read.All"),
        authority_url="https://login.microsoftonline.com/",
        account_id=" user@example.com ",
        cache_path=tmp_path / "cache.json",
    )

    assert config.mode is identity.GraphAuthMode.DELEGATED_MSAL
    assert config.scopes == ("User.Read", "Sites.Read.All")
    assert config.authority == "https://login.microsoftonline.com/tenant-id"
    assert config.account_id == "user@example.com"


def test_auto_selects_only_complete_modes_without_exposing_secret(tmp_path):
    delegated = identity.GraphIdentityConfig(
        mode="auto",
        tenant_id="tenant",
        client_id="public-client",
        scopes=("Sites.Read.All",),
        cache_path=tmp_path / "cache.json",
    )
    assert identity.select_auth_mode(delegated) is identity.GraphAuthMode.DELEGATED_MSAL

    app_only = identity.GraphIdentityConfig(
        mode="auto",
        tenant_id="tenant",
        client_id="confidential-client",
        client_secret="must-not-leak",
        scopes=("https://graph.microsoft.com/.default",),
    )
    assert identity.select_auth_mode(app_only) is identity.GraphAuthMode.APP_ONLY

    incomplete = identity.GraphIdentityConfig(
        mode="auto",
        tenant_id="tenant",
        client_secret="must-not-leak",
        scopes=("Sites.Read.All",),
    )
    with pytest.raises(identity.MicrosoftGraphIdentityConfigError) as caught:
        identity.select_auth_mode(incomplete)
    assert "must-not-leak" not in str(caught.value)


def test_readiness_distinguishes_configuration_interactive_and_ready(tmp_path):
    missing = identity.GraphIdentityConfig(mode="delegated_msal")
    assert identity.inspect_identity_readiness(missing).status == "configuration_required"

    delegated = identity.GraphIdentityConfig(
        mode="delegated_msal",
        tenant_id="tenant",
        client_id="client",
        scopes=("Sites.Read.All",),
        cache_path=tmp_path / "cache.json",
    )
    assert identity.inspect_identity_readiness(delegated).status == "interactive_auth_required"
    assert identity.inspect_identity_readiness(
        delegated, delegated_cache_has_account=True
    ).status == "ready"

    cli = identity.GraphIdentityConfig(
        mode="azure_cli", scopes=("https://graph.microsoft.com/.default",)
    )
    assert identity.inspect_identity_readiness(cli).status == "authentication_required"
    assert identity.inspect_identity_readiness(
        cli, azure_cli_authenticated=True
    ).status == "ready"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_cache_store_persists_privately_and_rejects_symlink(tmp_path):
    path = tmp_path / "profile" / "graph-cache.json"
    store = identity.GraphTokenCacheStore(path, max_bytes=128)

    store.write_text('{"refresh_token":"secret"}')

    assert store.read_text() == '{"refresh_token":"secret"}'
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    path.unlink()
    victim = tmp_path / "victim"
    victim.write_text("victim-secret", encoding="utf-8")
    path.symlink_to(victim)
    with pytest.raises(identity.MicrosoftGraphTokenCacheError) as caught:
        store.read_text()
    assert "victim-secret" not in str(caught.value)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_cache_store_read_repairs_existing_profile_permissions(tmp_path):
    parent = tmp_path / "profile"
    parent.mkdir(mode=0o755)
    path = parent / "graph-cache.json"
    path.write_text("cached-account", encoding="utf-8")
    path.chmod(0o644)
    store = identity.GraphTokenCacheStore(path)

    assert store.read_text() == "cached-account"
    assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_cache_store_rejects_oversize_without_leaking_content(tmp_path):
    path = tmp_path / "graph-cache.json"
    path.write_text("sensitive-cache-content", encoding="utf-8")
    store = identity.GraphTokenCacheStore(path, max_bytes=4)

    with pytest.raises(identity.MicrosoftGraphTokenCacheError) as caught:
        store.read_text()

    assert "sensitive-cache-content" not in str(caught.value)


@pytest.mark.skipif(os.name != "posix", reason="POSIX parent-boundary contract")
def test_cache_store_rejects_symlinked_parent_without_touching_victim(tmp_path):
    victim_dir = tmp_path / "victim-profile"
    victim_dir.mkdir(mode=0o755)
    victim = victim_dir / "graph-cache.json"
    victim.write_text("victim-cache", encoding="utf-8")
    linked_parent = tmp_path / "linked-profile"
    linked_parent.symlink_to(victim_dir, target_is_directory=True)
    store = identity.GraphTokenCacheStore(linked_parent / "graph-cache.json")

    with pytest.raises(identity.MicrosoftGraphTokenCacheError):
        store.read_text()

    assert victim.read_text(encoding="utf-8") == "victim-cache"
    assert stat.S_IMODE(victim_dir.stat().st_mode) == 0o755


class _FakeCache:
    def __init__(self):
        self.has_state_changed = False
        self.loaded = None

    def deserialize(self, serialized):
        self.loaded = serialized

    def serialize(self):
        return "updated-cache"


class _FakeApplication:
    def __init__(self, *, accounts, silent_result=None, interactive_result=None):
        self.accounts = accounts
        self.silent_result = silent_result
        self.interactive_result = interactive_result
        self.silent_calls = []
        self.interactive_calls = []

    def get_accounts(self):
        return self.accounts

    def acquire_token_silent(self, scopes, account, **kwargs):
        self.silent_calls.append((scopes, account, kwargs))
        return self.silent_result

    def acquire_token_interactive(self, scopes, **kwargs):
        self.interactive_calls.append((scopes, kwargs))
        return self.interactive_result


@pytest.mark.anyio
async def test_delegated_provider_uses_silent_cache_before_interactive(tmp_path):
    cache = _FakeCache()
    app = _FakeApplication(
        accounts=[{"home_account_id": "account-1", "username": "user@example.com"}],
        silent_result={"access_token": "delegated-token", "expires_in": 3600},
    )
    provider = identity.DelegatedMicrosoftGraphTokenProvider(
        identity.GraphIdentityConfig(
            mode="delegated_msal",
            tenant_id="tenant",
            client_id="client",
            scopes=("Sites.Read.All",),
            account_id="account-1",
            cache_path=tmp_path / "cache.json",
        ),
        cache_store=identity.GraphTokenCacheStore(tmp_path / "cache.json"),
        cache_factory=lambda: cache,
        application_factory=lambda **kwargs: app,
        interactive_allowed=False,
    )

    assert await provider.get_access_token() == "delegated-token"
    assert len(app.silent_calls) == 1
    assert app.interactive_calls == []


@pytest.mark.anyio
async def test_delegated_provider_never_opens_browser_during_unattended_token_get(tmp_path):
    app = _FakeApplication(accounts=[], silent_result=None)
    provider = identity.DelegatedMicrosoftGraphTokenProvider(
        identity.GraphIdentityConfig(
            mode="delegated_msal",
            tenant_id="tenant",
            client_id="client",
            scopes=("Sites.Read.All",),
            cache_path=tmp_path / "cache.json",
        ),
        cache_store=identity.GraphTokenCacheStore(tmp_path / "cache.json"),
        cache_factory=_FakeCache,
        application_factory=lambda **kwargs: app,
        interactive_allowed=False,
    )

    with pytest.raises(identity.MicrosoftGraphInteractiveAuthRequired):
        await provider.get_access_token()
    with pytest.raises(identity.MicrosoftGraphInteractiveAuthRequired):
        await provider.authenticate_interactively()
    assert app.interactive_calls == []


@pytest.mark.anyio
async def test_interactive_setup_persists_cache_but_never_returns_token(tmp_path):
    cache = _FakeCache()
    cache.has_state_changed = True
    app = _FakeApplication(
        accounts=[],
        interactive_result={
            "access_token": "must-not-return",
            "id_token_claims": {"preferred_username": "user@example.com"},
        },
    )
    store = identity.GraphTokenCacheStore(tmp_path / "cache.json")
    provider = identity.DelegatedMicrosoftGraphTokenProvider(
        identity.GraphIdentityConfig(
            mode="delegated_msal",
            tenant_id="tenant",
            client_id="client",
            scopes=("Sites.Read.All",),
            cache_path=tmp_path / "cache.json",
        ),
        cache_store=store,
        cache_factory=lambda: cache,
        application_factory=lambda **kwargs: app,
        interactive_allowed=True,
    )

    result = await provider.authenticate_interactively()

    assert result == {"authenticated": True, "account": "user@example.com"}
    assert "must-not-return" not in repr(result)
    assert store.read_text() == "updated-cache"


@pytest.mark.anyio
async def test_azure_cli_provider_reuses_existing_identity_adapter():
    scopes = []
    calls = []

    def provider_factory(*, scope):
        scopes.append(scope)

        def provide():
            calls.append(1)
            return "azure-cli-token"

        return provide

    provider = identity.AzureCliMicrosoftGraphTokenProvider(
        ("https://graph.microsoft.com/.default",),
        token_provider_factory=provider_factory,
    )

    assert await provider.get_access_token() == "azure-cli-token"
    assert scopes == ["https://graph.microsoft.com/.default"]
    assert calls == [1]
