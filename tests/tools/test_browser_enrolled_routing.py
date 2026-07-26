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
