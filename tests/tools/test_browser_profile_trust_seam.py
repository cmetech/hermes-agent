"""Tests for the per-session, origin-scoped SSRF trust seam.

Upstream v0.19.0 gates private-network access via _eval_ssrf_guard_active(),
which returns False for any CDP override — and an enrolled profile attaches
over CDP. These tests pin the narrow exemption: a private URL is permitted
ONLY when the session's bound profile explicitly trusts that origin.
"""

import pytest

from tools import browser_profiles, browser_session_registry, browser_tool

PRIVATE_TRUSTED = "https://wiki.corp.example/rest/api/content"
PRIVATE_UNTRUSTED = "https://intranet.other.example/secret"

ENROLLED = browser_profiles.BrowserProfile(
    name="enrolled",
    kind=browser_profiles.KIND_ENROLLED,
    trusted_origins=("https://wiki.corp.example",),
)


@pytest.fixture(autouse=True)
def _clean_registry():
    browser_session_registry.clear()
    yield
    browser_session_registry.clear()


@pytest.fixture()
def _enrolled_bound(monkeypatch):
    monkeypatch.setattr(
        browser_profiles, "get_profile", lambda name: ENROLLED if name == "enrolled" else None
    )
    browser_session_registry.bind("task-1", "enrolled")


class TestRegistry:
    def test_bind_and_lookup(self):
        browser_session_registry.bind("task-1", "enrolled")
        assert browser_session_registry.profile_for("task-1") == "enrolled"

    def test_unbind_removes(self):
        browser_session_registry.bind("task-1", "enrolled")
        browser_session_registry.unbind("task-1")
        assert browser_session_registry.profile_for("task-1") is None

    def test_unbound_session_trusts_nothing(self):
        assert not browser_session_registry.session_trusts_url("task-x", PRIVATE_TRUSTED)


class TestSessionTrustsUrl:
    def test_trusted_origin_is_permitted(self, _enrolled_bound):
        assert browser_session_registry.session_trusts_url("task-1", PRIVATE_TRUSTED)

    def test_untrusted_origin_is_denied(self, _enrolled_bound):
        assert not browser_session_registry.session_trusts_url("task-1", PRIVATE_UNTRUSTED)

    def test_other_session_does_not_inherit_trust(self, _enrolled_bound):
        """Trust is per-session; a sibling task must not borrow it."""
        assert not browser_session_registry.session_trusts_url("task-2", PRIVATE_TRUSTED)


class TestExpressionPreScan:
    """browser_tool._expression_targets_private_url must honour session trust."""

    @pytest.fixture(autouse=True)
    def _force_private(self, monkeypatch):
        # Treat every URL as private so we isolate the trust decision.
        monkeypatch.setattr(browser_tool, "_is_safe_url", lambda url: False)
        monkeypatch.setattr(browser_tool, "_is_always_blocked_url", lambda url: False)

    def test_blocks_private_literal_without_trust(self):
        expr = f"fetch('{PRIVATE_TRUSTED}')"
        assert browser_tool._expression_targets_private_url(expr) == PRIVATE_TRUSTED

    def test_permits_private_literal_for_trusted_session(self, _enrolled_bound):
        expr = f"fetch('{PRIVATE_TRUSTED}')"
        assert browser_tool._expression_targets_private_url(expr, session_key="task-1") is None

    def test_still_blocks_untrusted_origin_for_trusted_session(self, _enrolled_bound):
        expr = f"fetch('{PRIVATE_UNTRUSTED}')"
        assert (
            browser_tool._expression_targets_private_url(expr, session_key="task-1")
            == PRIVATE_UNTRUSTED
        )

    def test_always_blocked_url_is_never_trusted(self, monkeypatch, _enrolled_bound):
        """Cloud-metadata floor must survive profile trust."""
        monkeypatch.setattr(browser_tool, "_is_always_blocked_url", lambda url: True)
        expr = f"fetch('{PRIVATE_TRUSTED}')"
        assert (
            browser_tool._expression_targets_private_url(expr, session_key="task-1")
            == PRIVATE_TRUSTED
        )

    def test_default_arg_preserves_upstream_behaviour(self):
        """Calling without session_key must behave exactly as before."""
        expr = f"fetch('{PRIVATE_TRUSTED}')"
        assert browser_tool._expression_targets_private_url(expr) == PRIVATE_TRUSTED
