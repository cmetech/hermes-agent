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

        An enrolled task acquires through the REAL ``acquire()``/``attach_global``
        path -- only its browser-launch internals (``_ensure_enrolled_cdp``,
        ``_run_daemon_hygiene``) are mocked, exactly like
        ``test_scripted_callers_keep_the_global`` below. ``_get_cdp_override`` is
        deliberately left UNMOCKED: stubbing it to ``""`` would make the "not the
        corporate endpoint" assertions vacuously true regardless of whether
        ``_attach_cdp`` ever ran, which is what let this test through review
        without ever exercising ``attach_global``. An explicitly ephemeral task
        must then still resolve to its own (real) override, not to the corporate
        endpoint -- before AND after the enrolled task is cleaned up.
        """
        monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
        monkeypatch.setattr(browser_tool, "_resolve_cdp_override", lambda u: u)
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "_stop_cdp_supervisor", lambda t: None)
        monkeypatch.setattr(
            browser_session_manager, "_ensure_enrolled_cdp",
            lambda p, h: "http://127.0.0.1:9222/corporate",
        )
        monkeypatch.setattr(browser_session_manager, "_run_daemon_hygiene", lambda: None)
        browser_tool._reset_session_cdp_cache()

        browser_session_registry.bind("external", "default")

        assert browser_tool._session_cdp_url("corp-task::enrolled") == \
            "http://127.0.0.1:9222/corporate"
        # The real _attach_cdp path just ran (or didn't): assert directly on the
        # process-global, not through a session key whose resolution never
        # depended on it.
        assert os.environ.get("BROWSER_CDP_URL") is None
        assert browser_tool._session_cdp_url("external") == ""

        browser_tool._cleanup_single_browser_session("corp-task::enrolled")
        assert os.environ.get("BROWSER_CDP_URL") is None
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


import threading


class TestAcquireLifecycle:
    def test_concurrent_misses_acquire_once(self, monkeypatch, _default_enrolled):
        """EBL-003: acquire() runs `close --all`; a second one tears down the first."""
        monkeypatch.setattr(browser_tool, "_resolve_cdp_override", lambda u: u)
        browser_tool._reset_session_cdp_cache()
        barrier, calls, lock = threading.Barrier(2, timeout=5), [], threading.Lock()

        def _slow_acquire(profile, headless=None, session_key=None, attach_global=True):
            with lock:
                calls.append(session_key)
                n = len(calls)
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return browser_session_manager.BrowserSession(
                session_key=session_key, profile=ENROLLED,
                cdp_url=f"http://127.0.0.1:922{n}",
            )

        monkeypatch.setattr(browser_session_manager, "acquire", _slow_acquire)
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(
                browser_tool._session_cdp_url("t::enrolled")))
            for _ in range(2)
        ]
        for t in threads:
            t.start()
        barrier.abort()
        for t in threads:
            t.join(timeout=5)

        assert len(calls) == 1, f"acquire ran {len(calls)} times"
        assert len(set(results)) == 1, f"threads got different endpoints: {results}"

    def test_cleanup_releases_the_binding(self, monkeypatch, _default_enrolled):
        """EBL-005: acquire() binds the registry; nothing released it."""
        # Isolate from any endpoint memoized under this same key by an earlier
        # test in this file (run_tests.sh isolates per-file, not per-test, so
        # module-level caches persist across tests unless reset).
        browser_tool._reset_session_cdp_cache()
        monkeypatch.setattr(browser_tool, "_resolve_cdp_override", lambda u: u)
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "_stop_cdp_supervisor", lambda t: None)

        def _binding_acquire(profile, headless=None, session_key=None, attach_global=True):
            browser_session_registry.bind(session_key, profile)
            return browser_session_manager.BrowserSession(
                session_key=session_key, profile=ENROLLED, cdp_url="http://127.0.0.1:9222"
            )

        monkeypatch.setattr(browser_session_manager, "acquire", _binding_acquire)
        browser_tool._session_cdp_url("t::enrolled")
        assert browser_session_registry.profile_for("t::enrolled") == "corp"

        browser_tool._cleanup_single_browser_session("t::enrolled")
        assert browser_session_registry.profile_for("t::enrolled") is None

        monkeypatch.setattr(browser_session_registry, "default_profile_name", lambda: None)
        assert not browser_session_registry.session_trusts_url("t::enrolled", TRUSTED)


class TestCleanupReapsEnrolledSidecar:
    """Fix round 1, Important finding: cleanup_browser() must expand a bare
    task id to its ``::enrolled`` sidecar the same way it already does for
    ``::local``. Without this, the PRIMARY end-of-task path
    (agent/run_agent.py -> cleanup_browser(bare_task_id)) never calls
    _cleanup_single_browser_session on the enrolled key at all, so the
    registry binding, the CDP memo, AND the BrowserSession handle all survive
    normal task completion -- exactly the residual internal-origin trust this
    task exists to eliminate.
    """

    def test_bare_cleanup_reaps_the_enrolled_binding(self, monkeypatch, _default_enrolled):
        # Isolate from module-level state left by sibling tests in this file
        # (run_tests.sh isolates per-file, not per-test).
        browser_tool._reset_session_cdp_cache()
        browser_tool._active_sessions.pop("t::enrolled", None)
        try:
            monkeypatch.setattr(browser_tool, "_resolve_cdp_override", lambda u: u)
            monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
            monkeypatch.setattr(browser_tool, "_stop_cdp_supervisor", lambda t: None)
            monkeypatch.setattr(browser_tool, "_maybe_stop_recording", lambda t: None)
            monkeypatch.setattr(
                browser_tool, "_run_browser_command",
                lambda *a, **k: {"success": True},
            )
            monkeypatch.setattr(browser_tool.os.path, "exists", lambda p: False)

            def _binding_acquire(profile, headless=None, session_key=None, attach_global=True):
                browser_session_registry.bind(session_key, profile)
                return browser_session_manager.BrowserSession(
                    session_key=session_key, profile=ENROLLED, cdp_url="http://127.0.0.1:9222"
                )

            monkeypatch.setattr(browser_session_manager, "acquire", _binding_acquire)

            browser_tool._session_cdp_url("t::enrolled")
            # Mirror the real navigation path (_get_session_info): a navigation
            # routed to the enrolled key also registers an agent-browser
            # session under that same key. This is what a real task looks
            # like right before end-of-task cleanup runs.
            browser_tool._active_sessions["t::enrolled"] = {
                "session_name": "", "bb_session_id": None, "cdp_url": "http://127.0.0.1:9222",
            }

            assert browser_session_registry.profile_for("t::enrolled") == "corp"
            assert "t::enrolled" in browser_tool._session_cdp_urls
            assert "t::enrolled" in browser_tool._session_handles

            # This is the exact call agent/run_agent.py makes at task
            # completion: the BARE task id, never the enrolled key itself.
            browser_tool.cleanup_browser("t")

            assert browser_session_registry.profile_for("t::enrolled") is None, \
                "registry binding leaked past end-of-task cleanup (EBL-005 regression)"
            assert "t::enrolled" not in browser_tool._session_cdp_urls, \
                "CDP endpoint memo leaked past end-of-task cleanup"
            assert "t::enrolled" not in browser_tool._session_handles, \
                "BrowserSession handle leaked past end-of-task cleanup"

            monkeypatch.setattr(browser_session_registry, "default_profile_name", lambda: None)
            assert not browser_session_registry.session_trusts_url("t::enrolled", TRUSTED)
        finally:
            browser_tool._active_sessions.pop("t::enrolled", None)
            browser_tool._reset_session_cdp_cache()


class TestBareTaskIdStripsEnrolledSuffix:
    """Fix round 2, Important finding: _bare_task_id_for_session_key stripped
    _LOCAL_SUFFIX but not _ENROLLED_SUFFIX, so an enrolled session's recorded
    owner_task_id was "<task>::enrolled" instead of "<task>". A later
    non-navigation call (browser_click/type/snapshot -> _last_session_key)
    then saw owner_task_id != the caller's bare task id, treated the binding
    as stale, and fell back to the ephemeral throwaway browser instead of the
    enrolled one the page is actually open in -- silently breaking every
    non-navigation tool call after an enrolled navigate.
    """

    def test_non_nav_call_resolves_to_the_enrolled_session_key(self):
        # Isolate from module-level state left by sibling tests in this file
        # (run_tests.sh isolates per-file, not per-test).
        browser_tool._reset_session_cdp_cache()
        browser_tool._active_sessions.pop("t::enrolled", None)
        browser_tool._last_active_session_key.pop("t", None)
        try:
            session_key = "t::enrolled"
            # Built the same way _get_session_info (tools/browser_tool.py
            # ~line 2334-2337) builds a session record: session_key is the
            # full routing key, owner_task_id is derived by the function
            # under test rather than hand-computed here.
            session_info: dict = {"session_name": "sess-enrolled", "bb_session_id": None}
            session_info.setdefault("session_key", session_key)
            session_info.setdefault(
                "owner_task_id", browser_tool._bare_task_id_for_session_key(session_key)
            )
            browser_tool._active_sessions[session_key] = session_info
            # Mirrors what browser_navigate records after a successful
            # enrolled navigation made under the bare task id "t".
            browser_tool._last_active_session_key["t"] = session_key

            resolved = browser_tool._last_session_key("t")

            assert resolved == session_key, (
                f"non-navigation call resolved to {resolved!r} instead of "
                f"{session_key!r} -- it would act on the wrong browser"
            )
        finally:
            browser_tool._active_sessions.pop("t::enrolled", None)
            browser_tool._last_active_session_key.pop("t", None)
            browser_tool._reset_session_cdp_cache()


class TestRedirectParity:
    @pytest.fixture(autouse=True)
    def _nav(self, monkeypatch, _default_enrolled):
        monkeypatch.setattr(browser_tool, "_is_local_backend", lambda: False)
        monkeypatch.setattr(browser_tool, "_allow_private_urls", lambda: False)
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "_is_safe_url", lambda u: "corp.example" not in u)
        monkeypatch.setattr(browser_tool, "_is_always_blocked_url", lambda u: "169.254" in u)
        monkeypatch.setattr(browser_tool, "check_website_access", lambda u: None)
        monkeypatch.setattr(
            browser_tool, "_get_session_info",
            lambda k: {"session_name": "s", "bb_session_id": None, "cdp_url": None,
                       "features": {}, "_first_nav": False},
        )

    def _navigate_landing_on(self, monkeypatch, final_url):
        monkeypatch.setattr(
            browser_tool, "_run_browser_command",
            lambda *a, **kw: {"success": True, "data": {"title": "T", "url": final_url}},
        )
        return browser_tool.browser_navigate(TRUSTED, task_id="t")

    def test_redirect_to_a_trusted_origin_is_permitted(self, monkeypatch):
        out = self._navigate_landing_on(monkeypatch, "https://wiki.corp.example/home")
        # The redirect-block error text is "private/internal address" (see
        # test_browser_ssrf_local.py:271, a protected suite this task must not
        # edit) -- distinct from the pre-nav guard's "private or internal
        # address" wording. Assert against the real production string.
        assert "private/internal address" not in out

    def test_redirect_to_an_unlisted_private_origin_is_blocked(self, monkeypatch):
        out = self._navigate_landing_on(monkeypatch, "https://other.corp.example/x")
        assert "private/internal address" in out

    def test_redirect_to_cloud_metadata_is_blocked(self, monkeypatch):
        out = self._navigate_landing_on(monkeypatch, "http://169.254.169.254/latest")
        assert "cloud metadata endpoint" in out


class TestDeadEndpointRecovery:
    def test_connection_failure_permits_one_reacquire(self, monkeypatch, _default_enrolled):
        monkeypatch.setattr(browser_tool, "_resolve_cdp_override", lambda u: u)
        browser_tool._reset_session_cdp_cache()
        calls = []

        def _acq(profile, headless=None, session_key=None, attach_global=True):
            calls.append(session_key)
            return browser_session_manager.BrowserSession(
                session_key=session_key, profile=ENROLLED,
                cdp_url=f"http://127.0.0.1:922{len(calls)}",
            )

        monkeypatch.setattr(browser_session_manager, "acquire", _acq)
        first = browser_tool._session_cdp_url("t::enrolled")
        browser_tool._evict_dead_enrolled_session("t::enrolled")
        second = browser_tool._session_cdp_url("t::enrolled")
        assert len(calls) == 2
        assert first != second

    def test_eviction_is_a_noop_for_a_bare_key(self, _default_enrolled):
        browser_tool._evict_dead_enrolled_session("t")  # must not raise
