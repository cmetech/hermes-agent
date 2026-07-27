"""Per-navigation profile routing: which browser drives which URL.

Design: docs/plans/2026-07-26-per-navigation-browser-profile-design.md
"""

import json

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
        """Assert SUCCESS, not the absence of one error string.

        The redirect-block text is "private/internal address"; the PRE-NAV guard
        says "private or internal address". A negative assertion on the former
        stayed green even when the navigation never happened because the pre-nav
        guard blocked it, so it could not tell "permitted" from "blocked for a
        different reason" (review finding L-3).
        """
        out = self._navigate_landing_on(monkeypatch, "https://wiki.corp.example/home")
        payload = json.loads(out)
        assert payload["success"] is True, payload
        assert payload["url"] == "https://wiki.corp.example/home"

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


class TestDeadEndpointHookIntegration:
    """Fix round 1, Important finding #3: the two TestDeadEndpointRecovery
    tests above call _evict_dead_enrolled_session directly, bypassing both
    the marker matching and the `if not result.get("success")` gate inside
    _run_browser_command -- deleting the 8-line hook there fails nothing in
    that class. These drive _run_browser_command itself with a mocked
    subprocess pipeline, so the hook's actual wiring is under test.

    Also covers Important finding #1's required regression test: a benign
    error that merely CONTAINS the substring "websocket" must not evict.
    """

    @staticmethod
    def _seed_session_state():
        browser_tool._reset_session_cdp_cache()
        browser_tool._active_sessions.pop("t::enrolled", None)
        browser_tool._session_cdp_urls["t::enrolled"] = "http://127.0.0.1:9222"
        browser_tool._session_handles["t::enrolled"] = object()
        browser_tool._active_sessions["t::enrolled"] = {
            "session_name": "sess", "bb_session_id": None,
            "cdp_url": "http://127.0.0.1:9222",
        }

    @staticmethod
    def _drive_run_browser_command(monkeypatch, error_text):
        import json as _json
        from unittest.mock import MagicMock
        import tools.interrupt as interrupt_mod

        # engine="auto" (not "lightpanda") keeps this test clear of the
        # Lightpanda-fallback branch entirely -- that branch is exercised
        # separately in test_browser_lightpanda.py and by the Important #2
        # fix's own reasoning, not here.
        monkeypatch.setattr(browser_tool, "_get_browser_engine", lambda: "auto")
        monkeypatch.setattr(browser_tool, "_is_local_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "_find_agent_browser", lambda: "/usr/bin/agent-browser")
        monkeypatch.setattr(
            browser_tool, "_get_session_info",
            lambda task_id: {"session_name": "sess", "cdp_url": "http://127.0.0.1:9222"},
        )
        monkeypatch.setattr(browser_tool, "_write_owner_pid", lambda *a, **k: None)
        monkeypatch.setattr(interrupt_mod, "is_interrupted", lambda: False)

        mock_proc = MagicMock()
        mock_proc.wait.return_value = None
        mock_proc.returncode = 0
        monkeypatch.setattr("subprocess.Popen", lambda *a, **k: mock_proc)
        monkeypatch.setattr("os.open", lambda *a, **k: 99)
        monkeypatch.setattr("os.close", lambda *a, **k: None)
        monkeypatch.setattr("os.unlink", lambda *a, **k: None)
        monkeypatch.setattr("os.makedirs", lambda *a, **k: None)

        stdout_text = _json.dumps({"success": False, "error": error_text})
        monkeypatch.setattr(
            "builtins.open",
            MagicMock(return_value=MagicMock(
                __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=stdout_text))),
                __exit__=MagicMock(return_value=False),
            )),
        )

        return browser_tool._run_browser_command("t::enrolled", "click", ["e1"])

    def test_genuine_dead_endpoint_error_is_evicted_and_result_unchanged(self, monkeypatch):
        self._seed_session_state()
        error_text = "WebSocket connection closed unexpectedly"

        result = self._drive_run_browser_command(monkeypatch, error_text)

        # The hook must never turn a failure into a success or alter the payload.
        assert result == {"success": False, "error": error_text}
        assert "t::enrolled" not in browser_tool._session_cdp_urls
        assert "t::enrolled" not in browser_tool._session_handles
        assert "t::enrolled" not in browser_tool._active_sessions

    def test_benign_websocket_mention_does_not_evict(self, monkeypatch):
        """IMPORTANT #1 regression test: bare "websocket" must not evict."""
        self._seed_session_state()
        error_text = "Failed to open a websocket devtools inspector stream; retrying navigation"

        result = self._drive_run_browser_command(monkeypatch, error_text)

        assert result == {"success": False, "error": error_text}
        assert browser_tool._session_cdp_urls.get("t::enrolled") == "http://127.0.0.1:9222"
        assert "t::enrolled" in browser_tool._session_handles
        assert "t::enrolled" in browser_tool._active_sessions

    def test_eviction_is_a_noop_for_a_bare_key(self, _default_enrolled):
        browser_tool._evict_dead_enrolled_session("t")  # must not raise


# ---------------------------------------------------------------------------
# Review finding (CRITICAL): five of the six guard-forcing disjuncts had NO
# coverage.
#
# `tools/browser_tool.py` amends six SSRF-guard sites with the same
# conditional:
#
#     (not _is_local_backend() or _is_enrolled_session_key(<session key>))
#
# Only the first of the six (`_eval_ssrf_guard_active`) is a shared helper;
# the other five are independent inline duplicates. `TestGuardForcedOnForEnrolled`
# above calls the helper directly, so removing the disjunct from any of the
# other five failed nothing at all -- while an enrolled browser would have been
# free to reach ANY private origin instead of only its trusted ones.
#
# The classes below drive each remaining inline site through its real public
# entry point with `_is_local_backend()` forced True -- the exact condition
# under which the disjunct is the ONLY thing keeping the guard on -- and assert
# both directions:
#
#   * enrolled key  -> the guard FIRES (the disjunct is load-bearing), and
#   * bare key      -> the guard does NOT fire (upstream behaviour preserved,
#                      so a mutant that forces the guard on unconditionally is
#                      also caught).
#
# `browser_tool` keeps module-level caches and `scripts/run_tests.sh` isolates
# per FILE, not per test, so every class here resets them first.
# ---------------------------------------------------------------------------

SENSITIVE = "https://wiki.corp.example/page?token=abc123"
METADATA = "http://169.254.169.254/latest/meta-data/"


@pytest.fixture()
def _reset_module_caches():
    """run_tests.sh isolates per FILE; sibling tests leave module state behind."""
    def _wipe():
        browser_tool._reset_session_cdp_cache()
        browser_tool._active_sessions.clear()
        browser_tool._last_active_session_key.clear()

    _wipe()
    yield
    _wipe()


@pytest.fixture()
def _local_backend_guard_env(monkeypatch, _reset_module_caches, _default_enrolled):
    """An ordinary LOCAL install: `_is_local_backend()` is True, so upstream
    would switch the SSRF guard off entirely. Only the enrolled disjunct keeps
    it on."""
    monkeypatch.setattr(browser_tool, "_is_local_backend", lambda: True)
    monkeypatch.setattr(browser_tool, "_allow_private_urls", lambda: False)
    monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
    # Only public example.com is "safe"; wiki.corp.example (trusted by the
    # enrolled profile) and intranet.other.example (trusted by nobody) are not.
    monkeypatch.setattr(browser_tool, "_is_safe_url", lambda u: "example.com" in u)
    monkeypatch.setattr(browser_tool, "_is_always_blocked_url", lambda u: "169.254" in u)


def _route_to(monkeypatch, session_key):
    """Pin the navigation routing decision so the URL under test can be held
    constant while only the SESSION KEY varies -- which is exactly what the
    disjunct switches on."""
    monkeypatch.setattr(browser_tool, "_navigation_session_key", lambda t, u: session_key)


class TestNavigateGuardsForcedOnForEnrolled:
    """Sites 2-4: the three inline disjuncts inside `browser_navigate`
    (sensitive-query-param check, pre-navigation private-address block,
    post-redirect block)."""

    @pytest.fixture(autouse=True)
    def _nav(self, monkeypatch, _local_backend_guard_env):
        monkeypatch.setattr(browser_tool, "check_website_access", lambda u: None)
        monkeypatch.setattr(
            browser_tool, "_get_session_info",
            lambda k: {"session_name": "s", "bb_session_id": None, "cdp_url": None,
                       "features": {}, "_first_nav": False},
        )

    @staticmethod
    def _navigate(monkeypatch, url, final_url=None):
        monkeypatch.setattr(
            browser_tool, "_run_browser_command",
            lambda *a, **kw: {"success": True,
                              "data": {"title": "T", "url": final_url or url}},
        )
        return browser_tool.browser_navigate(url, task_id="t")

    # -- Site 2: sensitive query parameter (browser_tool.py ~3125) -----------

    def test_sensitive_query_param_blocked_for_an_enrolled_key(self, monkeypatch):
        _route_to(monkeypatch, "t::enrolled")
        assert "credential-like query parameter" in self._navigate(monkeypatch, SENSITIVE)

    def test_sensitive_query_param_bare_key_keeps_upstream_behaviour(self, monkeypatch):
        _route_to(monkeypatch, "t")
        assert "credential-like query parameter" not in self._navigate(monkeypatch, SENSITIVE)

    # -- Site 3: pre-navigation private-address block (~3152) ---------------

    def test_untrusted_private_url_blocked_for_an_enrolled_key(self, monkeypatch):
        _route_to(monkeypatch, "t::enrolled")
        out = self._navigate(monkeypatch, UNTRUSTED_PRIVATE)
        assert "private or internal address" in out

    def test_pre_nav_bare_key_keeps_upstream_behaviour(self, monkeypatch):
        _route_to(monkeypatch, "t")
        out = self._navigate(monkeypatch, UNTRUSTED_PRIVATE)
        assert "private or internal address" not in out

    def test_pre_nav_guard_stays_origin_scoped_for_an_enrolled_key(self, monkeypatch):
        """Forcing the guard on must not break the enrolled use case itself:
        an origin the profile explicitly trusts is still reachable."""
        _route_to(monkeypatch, "t::enrolled")
        out = self._navigate(monkeypatch, TRUSTED)
        assert "private or internal address" not in out

    def test_metadata_floor_outranks_enrolled_trust(self, monkeypatch):
        """The always-blocked cloud-metadata floor is checked first and is
        never trusted -- for either key."""
        _route_to(monkeypatch, "t::enrolled")
        assert "cloud metadata endpoint" in self._navigate(monkeypatch, METADATA)
        _route_to(monkeypatch, "t")
        assert "cloud metadata endpoint" in self._navigate(monkeypatch, METADATA)

    # -- Site 4: post-redirect block (~3232) --------------------------------

    def test_redirect_to_untrusted_private_blocked_for_an_enrolled_key(self, monkeypatch):
        _route_to(monkeypatch, "t::enrolled")
        out = self._navigate(monkeypatch, TRUSTED, final_url=UNTRUSTED_PRIVATE)
        assert "private/internal address" in out

    def test_redirect_bare_key_keeps_upstream_behaviour(self, monkeypatch):
        _route_to(monkeypatch, "t")
        out = self._navigate(monkeypatch, TRUSTED, final_url=UNTRUSTED_PRIVATE)
        assert "private/internal address" not in out

    def test_redirect_to_a_trusted_origin_is_still_permitted(self, monkeypatch):
        _route_to(monkeypatch, "t::enrolled")
        out = self._navigate(monkeypatch, TRUSTED, final_url="https://wiki.corp.example/home")
        assert "private/internal address" not in out

    def test_redirect_metadata_floor_outranks_enrolled_trust(self, monkeypatch):
        _route_to(monkeypatch, "t::enrolled")
        out = self._navigate(monkeypatch, TRUSTED, final_url=METADATA)
        assert "cloud metadata endpoint" in out


class TestSnapshotGuardForcedOnForEnrolled:
    """Site 5: the inline disjunct in `browser_snapshot`'s current-URL recheck
    (browser_tool.py ~3354). `browser_snapshot` derives its key from
    `_last_session_key(task_id)`, which returns an unrecorded key unchanged --
    so passing the routing key straight in exercises the real production
    shape."""

    @pytest.fixture(autouse=True)
    def _snap(self, monkeypatch, _local_backend_guard_env):
        pass

    @staticmethod
    def _snapshot(monkeypatch, task_id, current_url):
        def _cmd(session_key, command, args=None, **kw):
            if command == "eval":
                return {"success": True, "data": {"result": current_url}}
            return {"success": True, "data": {"snapshot": "- page", "refs": {}}}

        monkeypatch.setattr(browser_tool, "_run_browser_command", _cmd)
        return browser_tool.browser_snapshot(task_id=task_id)

    def test_private_page_snapshot_blocked_for_an_enrolled_key(self, monkeypatch):
        out = self._snapshot(monkeypatch, "t::enrolled", UNTRUSTED_PRIVATE)
        assert "private or internal address" in out

    def test_snapshot_bare_key_keeps_upstream_behaviour(self, monkeypatch):
        out = self._snapshot(monkeypatch, "t", UNTRUSTED_PRIVATE)
        assert "private or internal address" not in out

    def test_snapshot_of_a_trusted_origin_is_still_permitted(self, monkeypatch):
        out = self._snapshot(monkeypatch, "t::enrolled", TRUSTED)
        assert "private or internal address" not in out

    def test_snapshot_metadata_floor_outranks_enrolled_trust(self, monkeypatch):
        """`_snapshot_blocked_url` checks the always-blocked floor FIRST.

        Forced over a TRUSTED url, mirroring
        test_browser_default_profile.py::TestSnapshotGuardHelper::
        test_metadata_floor_outranks_trust. Asserting on METADATA instead would
        be untestable here: the profile never trusts 169.254.169.254, so the
        ordinary private-address branch emits the same message and deleting the
        floor branch entirely would leave this green.
        """
        monkeypatch.setattr(browser_tool, "_is_always_blocked_url", lambda u: True)
        out = self._snapshot(monkeypatch, "t::enrolled", TRUSTED)
        assert "private or internal address" in out


class TestVisionGuardForcedOnForEnrolled:
    """Site 6: the inline disjunct in `browser_vision`'s screenshot recheck
    (browser_tool.py ~4416)."""

    @pytest.fixture(autouse=True)
    def _vision(self, monkeypatch, tmp_path, _local_backend_guard_env):
        import hermes_constants

        monkeypatch.setattr(hermes_constants, "get_hermes_dir", lambda *a, **k: tmp_path)
        monkeypatch.setattr(browser_tool, "_get_browser_engine", lambda: "auto")

    @staticmethod
    def _vision_call(monkeypatch, task_id, current_url):
        def _cmd(session_key, command, args=None, **kw):
            if command == "eval":
                return {"success": True, "data": {"result": current_url}}
            # Stop the bare-key path before any real screenshot/model work: it
            # must fail for an unrelated reason, never with the guard's message.
            return {"success": False, "error": "no browser in this test"}

        monkeypatch.setattr(browser_tool, "_run_browser_command", _cmd)
        return browser_tool.browser_vision("what is on the page?", task_id=task_id)

    def test_private_page_screenshot_blocked_for_an_enrolled_key(self, monkeypatch):
        out = self._vision_call(monkeypatch, "t::enrolled", UNTRUSTED_PRIVATE)
        assert "private or internal address" in out

    def test_vision_bare_key_keeps_upstream_behaviour(self, monkeypatch):
        out = self._vision_call(monkeypatch, "t", UNTRUSTED_PRIVATE)
        assert "private or internal address" not in out

    def test_vision_of_a_trusted_origin_is_still_permitted(self, monkeypatch):
        out = self._vision_call(monkeypatch, "t::enrolled", TRUSTED)
        assert "private or internal address" not in out

    def test_vision_metadata_floor_outranks_enrolled_trust(self, monkeypatch):
        """Forced over a TRUSTED url -- see the snapshot twin for why METADATA
        cannot distinguish the floor branch from the ordinary private branch."""
        monkeypatch.setattr(browser_tool, "_is_always_blocked_url", lambda u: True)
        out = self._vision_call(monkeypatch, "t::enrolled", TRUSTED)
        assert "private or internal address" in out


class TestLightpandaPreRouteIsGatedOffCdpSessions:
    """M-1: `browser_vision`'s screenshot pre-route was never gated.

    `_should_inject_engine` is session-blind. With a global
    `browser.engine: lightpanda` alongside an enrolled profile, the pre-route
    called `_chrome_fallback_screenshot("t::enrolled", ...)`, which reads the
    internal URL off the enrolled session and then spawns the BUNDLED browser
    and navigates it there -- a silent fallback to the throwaway browser for a
    trusted origin, and an internal URL opened in an unmanaged profile. Site one
    (inside `_run_browser_command`) was gated on `session_info["cdp_url"]`; this
    is the same gate for a call site that only has the session key.
    """

    @pytest.fixture(autouse=True)
    def _vision(self, monkeypatch, tmp_path, _reset_module_caches, _default_enrolled):
        import hermes_constants

        monkeypatch.setattr(hermes_constants, "get_hermes_dir", lambda *a, **k: tmp_path)
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "_is_local_backend", lambda: True)
        monkeypatch.setattr(browser_tool, "_allow_private_urls", lambda: True)
        # A LOCAL install with lightpanda configured globally: this is exactly
        # the combination that makes _should_inject_engine() return True.
        monkeypatch.setattr(browser_tool, "_get_browser_engine", lambda: "lightpanda")
        monkeypatch.setattr(browser_tool, "_is_local_mode", lambda: True)
        monkeypatch.setattr(
            browser_tool, "_run_browser_command",
            lambda *a, **kw: {"success": False, "error": "no browser in this test"},
        )

    @staticmethod
    def _capture_preroute(monkeypatch):
        calls = []

        def _fallback(task_id, args, timeout):
            calls.append(task_id)
            return {"success": False, "error": "stubbed"}

        monkeypatch.setattr(browser_tool, "_chrome_fallback_screenshot", _fallback)
        return calls

    def test_enrolled_session_is_never_pre_routed_to_the_bundled_browser(
        self, monkeypatch
    ):
        calls = self._capture_preroute(monkeypatch)
        browser_tool.browser_vision("what is on the page?", task_id="t::enrolled")
        assert calls == [], (
            "an enrolled session's screenshot was pre-routed to the bundled "
            f"browser: {calls}"
        )

    def test_cdp_attached_session_is_never_pre_routed(self, monkeypatch):
        """Any CDP-attached session, not just enrolled ones (parity with the
        `session_info['cdp_url']` gate on site one)."""
        calls = self._capture_preroute(monkeypatch)
        browser_tool._active_sessions["t"] = {
            "session_name": "s", "cdp_url": "ws://127.0.0.1:9333/devtools/browser/x",
        }
        try:
            browser_tool.browser_vision("what is on the page?", task_id="t")
        finally:
            browser_tool._active_sessions.pop("t", None)
        assert calls == []

    def test_ordinary_lightpanda_session_still_pre_routes(self, monkeypatch):
        """The gate must not disable the feature for the sessions it exists for."""
        calls = self._capture_preroute(monkeypatch)
        browser_tool.browser_vision("what is on the page?", task_id="t")
        assert calls == ["t"]


class TestPerTurnCleanupSparesTheEnrolledSession:
    """H-2: the per-turn hook ran a bare-id cleanup_browser EVERY turn.

    Its headed-mode skip reads the GLOBAL `browser.headed`; the seeded enrolled
    profile sets `headed` at PROFILE level, so the skip never fired. Every turn
    dropped the enrolled memo/handle/binding while the real browser stayed
    alive, so the next turn's first trusted navigation re-acquired -- and
    `acquire()` runs process-wide `close --all` hygiene, which can tear down a
    CONCURRENT conversation's in-flight session.
    """

    @pytest.fixture(autouse=True)
    def _sessions(self, monkeypatch, _reset_module_caches):
        reaped = []
        monkeypatch.setattr(
            browser_tool, "_cleanup_single_browser_session", reaped.append
        )
        browser_tool._active_sessions["t"] = {"session_name": "bare"}
        browser_tool._active_sessions["t::enrolled"] = {"session_name": "corp"}
        browser_tool._active_sessions["t::local"] = {"session_name": "side"}
        browser_tool._last_active_session_key["t"] = "t::enrolled"
        self.reaped = reaped

    def test_per_turn_cleanup_keeps_the_enrolled_sidecar(self):
        browser_tool.cleanup_browser("t", keep_enrolled=True)
        assert "t::enrolled" not in self.reaped, (
            "the live enrolled session was torn down by per-turn cleanup"
        )
        # everything else is still reaped exactly as before
        assert "t" in self.reaped and "t::local" in self.reaped

    def test_per_turn_cleanup_keeps_the_last_active_binding(self):
        browser_tool.cleanup_browser("t", keep_enrolled=True)
        assert browser_tool._last_active_session_key.get("t") == "t::enrolled", (
            "the next click/snapshot would be routed to a different browser"
        )

    def test_end_of_task_cleanup_still_reaps_it(self):
        """Task 6 deliberately made end-of-task reaping work -- keep it."""
        browser_tool.cleanup_browser("t")
        assert "t::enrolled" in self.reaped
        assert browser_tool._last_active_session_key.get("t") is None


class TestPerTurnHookPassesKeepEnrolled:
    def test_cleanup_task_resources_spares_enrolled_sessions(self, monkeypatch):
        from unittest.mock import patch
        from types import SimpleNamespace

        from agent.chat_completion_helpers import cleanup_task_resources

        with (
            patch("tools.browser_tool._is_headed_mode", return_value=False),
            patch("run_agent.cleanup_vm"),
            patch("run_agent.cleanup_browser") as mock_cb,
            patch("agent.chat_completion_helpers.is_persistent_env", return_value=False),
        ):
            cleanup_task_resources(SimpleNamespace(verbose_logging=False), "task-x")
        mock_cb.assert_called_once_with("task-x", keep_enrolled=True)


class TestDaemonHygieneIsScoped:
    """H-2, second half: `acquire()`'s `close --all` is process-wide."""

    @pytest.fixture(autouse=True)
    def _no_subprocess(self, monkeypatch):
        ran = []
        monkeypatch.setattr(
            browser_session_manager, "_agent_browser_cmd", lambda: ["agent-browser"]
        )
        monkeypatch.setattr(
            browser_session_manager.subprocess, "run",
            lambda *a, **kw: ran.append(a[0] if a else kw.get("args")),
        )
        self.ran = ran

    def test_hygiene_runs_when_nothing_else_is_open(self, monkeypatch):
        monkeypatch.setattr(browser_session_manager, "_live_browser_session_keys", list)
        browser_session_manager._run_daemon_hygiene()
        assert self.ran, "a wedged daemon would never be cleared"

    def test_hygiene_is_skipped_while_another_session_is_live(self, monkeypatch):
        monkeypatch.setattr(
            browser_session_manager, "_live_browser_session_keys",
            lambda: ["other-conversation"],
        )
        browser_session_manager._run_daemon_hygiene()
        assert self.ran == [], (
            "`close --all` tore down another conversation's live session"
        )

    def test_unreadable_session_table_fails_toward_skipping(self, monkeypatch):
        def _boom():
            raise RuntimeError("no session table")

        monkeypatch.setattr(
            browser_session_manager, "_live_browser_session_keys", _boom
        )
        browser_session_manager._run_daemon_hygiene()
        assert self.ran == []
