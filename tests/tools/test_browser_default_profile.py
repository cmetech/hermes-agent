"""The agent's own browser tools inherit the configured default profile.

Without this, only scripts calling acquire() get the enrolled browser; the agent
answering in natural language stays blocked on internal pages, because it keys
sessions on a bare task_id that nothing binds.
"""

import pytest

from tools import browser_profiles, browser_session_registry, browser_tool

INTERNAL = "https://wiki.corp.example/display/TEAM/Onboarding"
OTHER_INTERNAL = "https://intranet.other.example/secret"

ENROLLED = browser_profiles.BrowserProfile(
    name="enrolled",
    kind=browser_profiles.KIND_ENROLLED,
    trusted_origins=("https://wiki.corp.example",),
)


@pytest.fixture(autouse=True)
def _clean():
    browser_session_registry.clear()
    yield
    browser_session_registry.clear()


@pytest.fixture()
def _default_enrolled(monkeypatch):
    monkeypatch.setattr(
        browser_profiles, "get_profile", lambda n: ENROLLED if n == "enrolled" else None
    )
    monkeypatch.setattr(
        browser_session_registry, "default_profile_name", lambda: "enrolled"
    )


class TestDefaultProfileFallback:
    def test_enrolled_key_uses_default_profile(self, _default_enrolled):
        """A key routing created for a trusted origin has no explicit binding
        — config must supply the profile via the enrolled suffix."""
        assert browser_session_registry.session_trusts_url("default::enrolled", INTERNAL)

    def test_bare_key_no_longer_inherits_trust(self, _default_enrolled):
        """Superseded by per-navigation routing: trusted origins get their own key."""
        assert not browser_session_registry.session_trusts_url("default", INTERNAL)

    def test_default_profile_is_still_origin_scoped(self, _default_enrolled):
        """Falling back must not grant blanket private-network access."""
        assert not browser_session_registry.session_trusts_url("default::enrolled", OTHER_INTERNAL)

    def test_no_default_configured_trusts_nothing(self, monkeypatch):
        monkeypatch.setattr(browser_session_registry, "default_profile_name", lambda: None)
        assert not browser_session_registry.session_trusts_url("default::enrolled", INTERNAL)

    def test_explicit_binding_wins_over_default(self, monkeypatch, _default_enrolled):
        """An acquired ephemeral session must NOT inherit enrolled trust."""
        monkeypatch.setattr(
            browser_profiles, "get_profile",
            lambda n: ENROLLED if n == "enrolled"
            else browser_profiles.BrowserProfile(name="default"),
        )
        browser_session_registry.bind("default", "default")  # ephemeral
        assert not browser_session_registry.session_trusts_url("default", INTERNAL)

    def test_default_profile_name_reads_config(self, monkeypatch):
        monkeypatch.setattr(
            browser_session_registry, "_read_config",
            lambda: {"browser": {"default_profile": "enrolled"}},
        )
        assert browser_session_registry.default_profile_name() == "enrolled"

    def test_default_profile_name_absent_is_none(self, monkeypatch):
        monkeypatch.setattr(browser_session_registry, "_read_config", lambda: {})
        assert browser_session_registry.default_profile_name() is None

    def test_default_profile_name_blank_is_none(self, monkeypatch):
        monkeypatch.setattr(
            browser_session_registry, "_read_config",
            lambda: {"browser": {"default_profile": "   "}},
        )
        assert browser_session_registry.default_profile_name() is None

    def test_malformed_config_is_none(self, monkeypatch):
        monkeypatch.setattr(
            browser_session_registry, "_read_config", lambda: {"browser": "garbage"}
        )
        assert browser_session_registry.default_profile_name() is None


class TestSnapshotGuardHelper:
    """_snapshot_blocked_url is shared by browser_snapshot and browser_vision."""

    @pytest.fixture(autouse=True)
    def _guard_on(self, monkeypatch):
        monkeypatch.setattr(browser_tool, "_is_safe_url", lambda url: False)
        monkeypatch.setattr(browser_tool, "_is_always_blocked_url", lambda url: False)

    def test_trusted_internal_page_is_permitted(self, _default_enrolled):
        assert browser_tool._snapshot_blocked_url("default::enrolled", INTERNAL) is None

    def test_untrusted_internal_page_is_blocked(self, _default_enrolled):
        assert (
            browser_tool._snapshot_blocked_url("default::enrolled", OTHER_INTERNAL)
            == OTHER_INTERNAL
        )

    def test_blocked_without_any_profile(self, monkeypatch):
        monkeypatch.setattr(browser_session_registry, "default_profile_name", lambda: None)
        assert browser_tool._snapshot_blocked_url("default::enrolled", INTERNAL) == INTERNAL

    def test_metadata_floor_outranks_trust(self, monkeypatch, _default_enrolled):
        monkeypatch.setattr(browser_tool, "_is_always_blocked_url", lambda url: True)
        assert browser_tool._snapshot_blocked_url("default::enrolled", INTERNAL) == INTERNAL

    def test_empty_url_is_not_blocked(self, _default_enrolled):
        assert browser_tool._snapshot_blocked_url("default", "") is None

    def test_public_url_is_not_blocked(self, monkeypatch, _default_enrolled):
        monkeypatch.setattr(browser_tool, "_is_safe_url", lambda url: True)
        assert browser_tool._snapshot_blocked_url("default", "https://example.com") is None


class TestNavigateGuard:
    """browser_navigate must permit a trusted internal origin."""

    @pytest.fixture(autouse=True)
    def _patches(self, monkeypatch, _default_enrolled):
        monkeypatch.setattr(browser_tool, "_is_local_backend", lambda: False)
        monkeypatch.setattr(browser_tool, "_allow_private_urls", lambda: False)
        monkeypatch.setattr(browser_tool, "_is_safe_url", lambda url: False)
        monkeypatch.setattr(browser_tool, "_is_always_blocked_url", lambda url: False)
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "check_website_access", lambda url: None)
        monkeypatch.setattr(
            browser_tool, "_get_session_info",
            lambda task_id: {"session_name": "s", "bb_session_id": None,
                             "cdp_url": None, "features": {}, "_first_nav": False},
        )
        monkeypatch.setattr(
            browser_tool, "_run_browser_command",
            lambda *a, **kw: {"success": True, "data": {"title": "OK", "url": INTERNAL}},
        )

    def test_trusted_internal_url_is_permitted(self):
        out = browser_tool.browser_navigate(INTERNAL, task_id="default")
        assert "private or internal address" not in out

    def test_untrusted_internal_url_is_still_blocked(self):
        out = browser_tool.browser_navigate(OTHER_INTERNAL, task_id="default")
        assert "private or internal address" in out

    def test_metadata_floor_still_fires(self, monkeypatch):
        """The always-blocked cloud-metadata floor outranks profile trust."""
        monkeypatch.setattr(browser_tool, "_is_always_blocked_url", lambda url: True)
        out = browser_tool.browser_navigate(INTERNAL, task_id="default")
        assert "cloud metadata endpoint" in out
