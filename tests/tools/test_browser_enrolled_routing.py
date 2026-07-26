"""Per-navigation profile routing: which browser drives which URL.

Design: docs/plans/2026-07-26-per-navigation-browser-profile-design.md
"""

import pytest

from tools import browser_profiles, browser_session_manager, browser_session_registry, browser_tool


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


class TestGuardTrustScoping:
    def test_enrolled_key_is_trusted(self, _default_enrolled):
        assert browser_session_registry.session_trusts_url("t::enrolled", TRUSTED)

    def test_bare_key_is_not_trusted(self, _default_enrolled):
        """Routing sends trusted origins to the enrolled key, so the ephemeral
        session has no reason to reach internal origins."""
        assert not browser_session_registry.session_trusts_url("t", TRUSTED)

    def test_enrolled_key_is_still_origin_scoped(self, _default_enrolled):
        assert not browser_session_registry.session_trusts_url("t::enrolled", UNTRUSTED_PRIVATE)


import os


class TestNoGlobalEndpointLeak:
    """EBL-001: an ephemeral task must never inherit the corporate endpoint."""

    def test_agent_acquire_does_not_write_the_global(self, monkeypatch, _default_enrolled):
        monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
        monkeypatch.setattr(browser_tool, "_resolve_cdp_override", lambda u: u)

        def _fake_acquire(profile, headless=None, session_key=None, attach_global=True):
            assert attach_global is False, "agent path must not mutate os.environ"
            return browser_session_manager.BrowserSession(
                session_key=session_key, profile=ENROLLED, cdp_url="http://127.0.0.1:9222"
            )

        monkeypatch.setattr(browser_session_manager, "acquire", _fake_acquire)
        assert browser_tool._session_cdp_url("t::enrolled") == "http://127.0.0.1:9222"
        assert os.environ.get("BROWSER_CDP_URL") is None

    def test_ephemeral_task_never_inherits_the_corporate_endpoint(
        self, monkeypatch, _default_enrolled
    ):
        """The exact EBL-001 reproduction. This test must fail if the global returns.

        An enrolled task acquires; an explicitly ephemeral task must then still
        resolve to its own override, not to the corporate endpoint -- before AND
        after the enrolled task is cleaned up.
        """
        monkeypatch.setattr(browser_tool, "_resolve_cdp_override", lambda u: u)
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: "")
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "_stop_cdp_supervisor", lambda t: None)
        browser_tool._reset_session_cdp_cache()

        def _acq(profile, headless=None, session_key=None, attach_global=True):
            return browser_session_manager.BrowserSession(
                session_key=session_key, profile=ENROLLED,
                cdp_url="http://127.0.0.1:9222/corporate",
            )

        monkeypatch.setattr(browser_session_manager, "acquire", _acq)
        browser_session_registry.bind("external", "default")

        assert browser_tool._session_cdp_url("corp-task::enrolled") == \
            "http://127.0.0.1:9222/corporate"
        assert browser_tool._session_cdp_url("external") == ""

        browser_tool._cleanup_single_browser_session("corp-task::enrolled")
        assert browser_tool._session_cdp_url("external") == ""

    def test_scripted_callers_keep_the_global(self, monkeypatch):
        """The confluence CLI and workflow script nodes rely on _attach_cdp."""
        monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
        monkeypatch.setattr(browser_profiles, "get_profile", lambda n: ENROLLED)
        monkeypatch.setattr(
            browser_session_manager, "_ensure_enrolled_cdp",
            lambda p, h: "http://127.0.0.1:9333",
        )
        monkeypatch.setattr(browser_session_manager, "_run_daemon_hygiene", lambda: None)
        browser_session_manager.acquire("corp", session_key="scripted")
        assert os.environ["BROWSER_CDP_URL"] == "http://127.0.0.1:9333"
