"""Per-navigation profile routing: which browser drives which URL.

Design: docs/plans/2026-07-26-per-navigation-browser-profile-design.md
"""

import pytest

from tools import browser_profiles, browser_session_registry, browser_tool


@pytest.fixture(autouse=True)
def _clean():
    browser_session_registry.clear()
    yield
    browser_session_registry.clear()


class TestEnrolledSessionKey:
    def test_enrolled_key_is_recognised(self):
        assert browser_tool._is_enrolled_session_key("task-1::enrolled")

    def test_bare_key_is_not_enrolled(self):
        assert not browser_tool._is_enrolled_session_key("task-1")

    def test_local_sidecar_is_not_enrolled(self):
        assert not browser_tool._is_enrolled_session_key("task-1::local")

    def test_enrolled_key_is_not_a_local_sidecar(self):
        """::local means 'force local Chromium'; an enrolled key must not match."""
        assert not browser_tool._is_local_sidecar_key("task-1::enrolled")


class TestGuardForcedOnForEnrolled:
    """Removing the BROWSER_CDP_URL global must not silently disable the guard."""

    @pytest.fixture(autouse=True)
    def _local_backend(self, monkeypatch):
        # An ordinary local install: upstream considers the guard unnecessary.
        monkeypatch.setattr(browser_tool, "_is_local_backend", lambda: True)
        monkeypatch.setattr(browser_tool, "_allow_private_urls", lambda: False)

    def test_guard_is_forced_on_for_an_enrolled_key(self):
        assert browser_tool._eval_ssrf_guard_active("task-1::enrolled") is True

    def test_bare_key_keeps_upstream_behaviour(self):
        assert browser_tool._eval_ssrf_guard_active("task-1") is False

    def test_local_sidecar_keeps_upstream_behaviour(self):
        assert browser_tool._eval_ssrf_guard_active("task-1::local") is False

    def test_allow_private_urls_still_overrides(self, monkeypatch):
        """The operator's blunt global switch keeps its meaning."""
        monkeypatch.setattr(browser_tool, "_allow_private_urls", lambda: True)
        assert browser_tool._eval_ssrf_guard_active("task-1::enrolled") is False


ENROLLED = browser_profiles.BrowserProfile(
    name="corp",
    kind=browser_profiles.KIND_ENROLLED,
    trusted_origins=("https://wiki.corp.example",),
)


@pytest.fixture()
def _default_enrolled(monkeypatch):
    monkeypatch.setattr(
        browser_profiles, "get_profile",
        lambda n: ENROLLED if n == "corp" else (
            browser_profiles.BrowserProfile(name="default") if n == "default" else None
        ),
    )
    monkeypatch.setattr(browser_session_registry, "default_profile_name", lambda: "corp")


class TestOnlyEnrolledKeysDrive:
    def test_bare_key_never_drives_enrolled(self, _default_enrolled):
        """EBL-002: an unbound session must not get the corporate browser."""
        assert browser_tool._session_browser_profile("task-1") is None
        assert browser_tool._session_uses_enrolled_browser("task-1") is False

    def test_enrolled_key_drives_enrolled(self, _default_enrolled):
        profile = browser_tool._session_browser_profile("task-1::enrolled")
        assert profile is not None and profile.is_enrolled
        assert browser_tool._session_uses_enrolled_browser("task-1::enrolled") is True

    def test_explicit_bind_still_drives(self, _default_enrolled):
        """Scripted callers (confluence CLI, workflow script nodes) bind their key."""
        browser_session_registry.bind("scripted", "corp")
        assert browser_tool._session_uses_enrolled_browser("scripted") is True

    def test_explicit_ephemeral_bind_never_drives_enrolled(self, _default_enrolled):
        browser_session_registry.bind("task-1::enrolled", "default")
        assert browser_tool._session_uses_enrolled_browser("task-1::enrolled") is False

    def test_local_sidecar_never_drives_enrolled(self, _default_enrolled):
        assert browser_tool._session_uses_enrolled_browser("task-1::local") is False


TRUSTED = "https://wiki.corp.example/display/TEAM/Onboarding"
UNTRUSTED_PRIVATE = "https://intranet.other.example/secret"
PUBLIC = "https://example.com/page"


class TestNavigationRouting:
    @pytest.fixture(autouse=True)
    def _plain_local(self, monkeypatch, _default_enrolled):
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: "")
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: None)

    def test_trusted_origin_routes_to_enrolled(self):
        assert browser_tool._navigation_session_key("t", TRUSTED) == "t::enrolled"

    def test_public_origin_stays_on_the_bare_key(self):
        assert browser_tool._navigation_session_key("t", PUBLIC) == "t"

    def test_untrusted_private_origin_stays_on_the_bare_key(self):
        assert browser_tool._navigation_session_key("t", UNTRUSTED_PRIVATE) == "t"

    def test_cdp_override_suppresses_enrolled_routing(self, monkeypatch):
        """/browser connect owns the whole session."""
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: "ws://x")
        assert browser_tool._navigation_session_key("t", TRUSTED) == "t"

    def test_camofox_suppresses_enrolled_routing(self, monkeypatch):
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: True)
        assert browser_tool._navigation_session_key("t", TRUSTED) == "t"

    def test_enrolled_outranks_the_local_sidecar(self, monkeypatch):
        """A trusted origin under a cloud provider gets the corporate browser."""
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: object())
        monkeypatch.setattr(browser_tool, "_auto_local_for_private_urls", lambda: True)
        monkeypatch.setattr(browser_tool, "_url_is_private", lambda u: True)
        assert browser_tool._navigation_session_key("t", TRUSTED) == "t::enrolled"

    def test_untrusted_private_still_gets_the_local_sidecar(self, monkeypatch):
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: object())
        monkeypatch.setattr(browser_tool, "_auto_local_for_private_urls", lambda: True)
        monkeypatch.setattr(browser_tool, "_url_is_private", lambda u: True)
        assert browser_tool._navigation_session_key("t", UNTRUSTED_PRIVATE) == "t::local"

    def test_no_default_profile_never_routes_enrolled(self, monkeypatch):
        monkeypatch.setattr(browser_session_registry, "default_profile_name", lambda: None)
        assert browser_tool._navigation_session_key("t", TRUSTED) == "t"
