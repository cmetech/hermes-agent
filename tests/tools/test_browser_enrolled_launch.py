"""The agent's browser tools must LAUNCH the enrolled browser, not merely trust it.

The trust seam (test_browser_profile_trust_seam.py) decided which origins an
enrolled session may reach. Nothing, however, ever called
``browser_session_manager.acquire()`` — so with ``browser.default_profile:
enrolled`` a session was granted internal-origin trust while still driving
agent-browser's bundled Chrome for Testing, which holds none of the corporate
certificates. The guard opened the door and the wrong browser walked through.

These tests pin the wiring: an enrolled session drives the acquired browser's
CDP endpoint, every other session keeps today's ``_get_cdp_override()``
behaviour byte for byte, the acquire happens exactly once per session, and a
failed acquire is an error rather than a silent fall back to the bundled
browser.

Design: docs/plans/2026-07-26-consolidated-browser-automation-design.md
"""

import pytest

from tools import (
    browser_profiles,
    browser_session_manager,
    browser_session_registry,
    browser_tool,
)

CDP = "http://127.0.0.1:9222"
OVERRIDE = "ws://cdp.example:9222/devtools/browser/abc"

ENROLLED = browser_profiles.BrowserProfile(
    name="enrolled",
    kind=browser_profiles.KIND_ENROLLED,
    trusted_origins=("https://wiki.corp.example",),
    headed=True,
    # A launchable profile needs a real user_data_dir -- see EBL-006's
    # default_profile_launchable() chain validation in TestAvailabilityGate.
    user_data_dir="/tmp/otto-enrolled-profile",
)
EPHEMERAL = browser_profiles.BrowserProfile(name="default")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    browser_session_registry.clear()
    browser_tool._reset_session_cdp_cache()
    # _resolve_cdp_override performs an HTTP discovery call; identity keeps
    # these tests hermetic without weakening what they assert.
    monkeypatch.setattr(browser_tool, "_resolve_cdp_override", lambda url: url)
    yield
    browser_session_registry.clear()
    browser_tool._reset_session_cdp_cache()


class _AcquireSpy:
    """Records acquire() calls and hands back a session with a fixed CDP URL."""

    def __init__(self, cdp_url=CDP, error=None):
        self.calls = []
        self._cdp_url = cdp_url
        self._error = error

    def __call__(self, profile, headless=None, session_key=None, attach_global=True):
        self.calls.append((profile, session_key))
        if self._error is not None:
            raise self._error
        return browser_session_manager.BrowserSession(
            session_key=session_key or "", profile=ENROLLED, cdp_url=self._cdp_url
        )


@pytest.fixture()
def _default_enrolled(monkeypatch):
    monkeypatch.setattr(
        browser_profiles, "get_profile",
        lambda n: ENROLLED if n == "enrolled" else (EPHEMERAL if n == "default" else None),
    )
    monkeypatch.setattr(
        browser_session_registry, "default_profile_name", lambda: "enrolled"
    )
    monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: OVERRIDE)


@pytest.fixture()
def _no_profile(monkeypatch):
    monkeypatch.setattr(browser_session_registry, "default_profile_name", lambda: None)
    monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: OVERRIDE)


class TestSessionCdpUrl:
    def test_enrolled_session_drives_the_acquired_browser(self, monkeypatch, _default_enrolled):
        spy = _AcquireSpy()
        monkeypatch.setattr(browser_session_manager, "acquire", spy)
        assert browser_tool._session_cdp_url("task-1::enrolled") == CDP
        assert spy.calls == [("enrolled", "task-1::enrolled")]

    def test_bare_key_no_longer_drives_enrolled(self, monkeypatch, _default_enrolled):
        """Superseded by per-navigation routing: see EBL-002."""
        spy = _AcquireSpy()
        monkeypatch.setattr(browser_session_manager, "acquire", spy)
        assert browser_tool._session_cdp_url("task-1") == OVERRIDE
        assert spy.calls == []

    def test_session_without_a_profile_keeps_the_cdp_override(self, monkeypatch, _no_profile):
        spy = _AcquireSpy()
        monkeypatch.setattr(browser_session_manager, "acquire", spy)
        assert browser_tool._session_cdp_url("task-1") == OVERRIDE
        assert spy.calls == []

    def test_explicit_ephemeral_binding_never_launches_enrolled(
        self, monkeypatch, _default_enrolled
    ):
        """An explicitly ephemeral session must not inherit the default profile."""
        spy = _AcquireSpy()
        monkeypatch.setattr(browser_session_manager, "acquire", spy)
        browser_session_registry.bind("task-1", "default")
        assert browser_tool._session_cdp_url("task-1") == OVERRIDE
        assert spy.calls == []

    def test_missing_session_key_keeps_the_cdp_override(self, monkeypatch, _default_enrolled):
        spy = _AcquireSpy()
        monkeypatch.setattr(browser_session_manager, "acquire", spy)
        assert browser_tool._session_cdp_url(None) == OVERRIDE
        assert spy.calls == []

    def test_acquire_runs_once_per_session(self, monkeypatch, _default_enrolled):
        """acquire() runs `close --all` daemon hygiene, so calling it per tool
        invocation would tear the browser down between navigate and click."""
        spy = _AcquireSpy()
        monkeypatch.setattr(browser_session_manager, "acquire", spy)
        for _ in range(4):
            assert browser_tool._session_cdp_url("task-1::enrolled") == CDP
        assert len(spy.calls) == 1

    def test_each_session_acquires_separately(self, monkeypatch, _default_enrolled):
        spy = _AcquireSpy()
        monkeypatch.setattr(browser_session_manager, "acquire", spy)
        browser_tool._session_cdp_url("task-1::enrolled")
        browser_tool._session_cdp_url("task-2::enrolled")
        assert [key for _, key in spy.calls] == ["task-1::enrolled", "task-2::enrolled"]

    def test_failed_acquire_raises_instead_of_falling_back(self, monkeypatch, _default_enrolled):
        """Silent fallback to the bundled browser is the failure mode this exists
        to prevent: it would fail corporate mTLS with a confusing error."""
        spy = _AcquireSpy(error=browser_session_manager.ProfileError("no Chrome"))
        monkeypatch.setattr(browser_session_manager, "acquire", spy)
        with pytest.raises(browser_session_manager.ProfileError):
            browser_tool._session_cdp_url("task-1::enrolled")

    def test_failed_acquire_is_not_cached(self, monkeypatch, _default_enrolled):
        """A transient launch failure must be able to recover on the next call."""
        spy = _AcquireSpy(error=browser_session_manager.ProfileError("no Chrome"))
        monkeypatch.setattr(browser_session_manager, "acquire", spy)
        for _ in range(2):
            with pytest.raises(browser_session_manager.ProfileError):
                browser_tool._session_cdp_url("task-1::enrolled")
        assert len(spy.calls) == 2

        ok = _AcquireSpy()
        monkeypatch.setattr(browser_session_manager, "acquire", ok)
        assert browser_tool._session_cdp_url("task-1::enrolled") == CDP

    def test_empty_acquired_url_is_an_error_not_a_fallback(self, monkeypatch, _default_enrolled):
        monkeypatch.setattr(browser_session_manager, "acquire", _AcquireSpy(cdp_url=None))
        with pytest.raises(browser_session_manager.ProfileError):
            browser_tool._session_cdp_url("task-1::enrolled")

    def test_reaping_a_session_drops_its_memoized_url(self, monkeypatch, _default_enrolled):
        """The browser behind a reaped session may be gone; reusing its URL
        would drive a dead endpoint instead of acquiring a fresh one."""
        spy = _AcquireSpy()
        monkeypatch.setattr(browser_session_manager, "acquire", spy)
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "_stop_cdp_supervisor", lambda t: None)

        browser_tool._session_cdp_url("task-1::enrolled")
        browser_tool._cleanup_single_browser_session("task-1::enrolled")
        browser_tool._session_cdp_url("task-1::enrolled")
        assert len(spy.calls) == 2

    def test_reaping_one_session_leaves_others_memoized(self, monkeypatch, _default_enrolled):
        spy = _AcquireSpy()
        monkeypatch.setattr(browser_session_manager, "acquire", spy)
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "_stop_cdp_supervisor", lambda t: None)

        browser_tool._session_cdp_url("task-1::enrolled")
        browser_tool._session_cdp_url("task-2::enrolled")
        browser_tool._cleanup_single_browser_session("task-1::enrolled")
        browser_tool._session_cdp_url("task-2::enrolled")
        assert len(spy.calls) == 2


class TestSessionCreation:
    """_get_session_info is the launch decision — it must build the session on
    the enrolled browser's endpoint, not on the bundled browser."""

    @pytest.fixture(autouse=True)
    def _quiet_session_machinery(self, monkeypatch):
        monkeypatch.setattr(browser_tool, "_start_browser_cleanup_thread", lambda: None)
        monkeypatch.setattr(browser_tool, "_ensure_cdp_supervisor", lambda t: None)
        with browser_tool._cleanup_lock:
            browser_tool._active_sessions.pop("task-1", None)
            browser_tool._active_sessions.pop("task-1::enrolled", None)
        yield
        with browser_tool._cleanup_lock:
            browser_tool._active_sessions.pop("task-1", None)
            browser_tool._active_sessions.pop("task-1::enrolled", None)

    def test_enrolled_session_is_built_on_the_acquired_endpoint(
        self, monkeypatch, _default_enrolled
    ):
        monkeypatch.setattr(browser_session_manager, "acquire", _AcquireSpy())
        monkeypatch.setattr(
            browser_tool, "_create_local_session",
            lambda t: pytest.fail("enrolled session fell back to the bundled browser"),
        )
        info = browser_tool._get_session_info("task-1::enrolled")
        assert info["cdp_url"] == CDP

    def test_unprofiled_session_still_uses_the_local_browser(self, monkeypatch, _no_profile):
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: "")
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: None)
        monkeypatch.setattr(
            browser_tool, "_create_local_session",
            lambda t: {"session_name": "local", "bb_session_id": None, "cdp_url": None},
        )
        info = browser_tool._get_session_info("task-1")
        assert info["session_name"] == "local"


class TestChromiumGate:
    """_run_browser_command must not fail an enrolled session for missing Chromium."""

    @pytest.fixture(autouse=True)
    def _no_bundled_chromium(self, monkeypatch):
        monkeypatch.setattr(browser_tool, "_find_agent_browser", lambda **kw: "agent-browser")
        monkeypatch.setattr(browser_tool, "_is_local_mode", lambda: True)
        monkeypatch.setattr(browser_tool, "_chromium_installed", lambda: False)
        monkeypatch.setattr(browser_tool, "_maybe_autoinstall_chromium", lambda: False)
        monkeypatch.setattr(browser_tool, "_get_browser_engine", lambda: "chrome")
        monkeypatch.setattr(browser_tool, "_running_in_docker", lambda: False)

        def _sentinel(task_id):
            raise RuntimeError("REACHED_SESSION_CREATION")

        monkeypatch.setattr(browser_tool, "_get_session_info", _sentinel)

    def test_enrolled_session_passes_the_chromium_gate(self, monkeypatch, _default_enrolled):
        monkeypatch.setattr(browser_session_manager, "acquire", _AcquireSpy())
        out = browser_tool._run_browser_command(
            "task-1::enrolled", "navigate", ["https://x.example"]
        )
        assert "REACHED_SESSION_CREATION" in out["error"]

    def test_unprofiled_session_still_blocked_without_chromium(self, monkeypatch, _no_profile):
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: "")
        out = browser_tool._run_browser_command("task-1", "navigate", ["https://x.example"])
        assert "Chromium browser is missing" in out["error"]

    def test_unresolvable_enrolled_browser_reports_the_reason(
        self, monkeypatch, _default_enrolled
    ):
        """The real session path must surface the ProfileError, never fall back
        to the bundled browser that internal sites would refuse."""
        monkeypatch.undo()  # restore the real _get_session_info
        monkeypatch.setattr(browser_tool, "_find_agent_browser", lambda **kw: "agent-browser")
        monkeypatch.setattr(browser_tool, "_is_local_mode", lambda: True)
        monkeypatch.setattr(browser_tool, "_chromium_installed", lambda: False)
        monkeypatch.setattr(browser_tool, "_start_browser_cleanup_thread", lambda: None)
        monkeypatch.setattr(
            browser_profiles, "get_profile",
            lambda n: ENROLLED if n == "enrolled" else None,
        )
        monkeypatch.setattr(
            browser_session_registry, "default_profile_name", lambda: "enrolled"
        )
        monkeypatch.setattr(
            browser_session_manager, "acquire",
            _AcquireSpy(error=browser_session_manager.ProfileError("could not resolve Chrome")),
        )
        out = browser_tool._run_browser_command(
            "task-1::enrolled", "navigate", ["https://x.example"]
        )
        assert out["success"] is False
        assert "could not resolve Chrome" in out["error"]
        assert "Chromium browser is missing" not in out["error"]


class TestAvailabilityGate:
    """check_browser_requirements must not withhold every browser tool from an
    enrolled profile just because agent-browser's bundled Chromium is absent."""

    @pytest.fixture(autouse=True)
    def _local_mode_without_chromium(self, monkeypatch):
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: "")
        monkeypatch.setattr(browser_tool, "_find_agent_browser", lambda **kw: "agent-browser")
        monkeypatch.setattr(
            browser_tool, "_requires_real_termux_browser_install", lambda cmd: False
        )
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: None)
        monkeypatch.setattr(browser_tool, "_using_lightpanda_engine", lambda: False)
        monkeypatch.setattr(browser_tool, "_chromium_installed", lambda: False)

    def test_available_when_the_enrolled_browser_resolves(self, monkeypatch, tmp_path):
        exe = tmp_path / "chrome"
        exe.write_text("")
        monkeypatch.setattr(browser_profiles, "get_profile", lambda n: ENROLLED)
        monkeypatch.setattr(browser_profiles, "resolve_executable", lambda p: str(exe))
        monkeypatch.setattr(
            browser_session_registry, "default_profile_name", lambda: "enrolled"
        )
        assert browser_tool.check_browser_requirements() is True

    def test_unavailable_when_the_enrolled_browser_does_not_resolve(self, monkeypatch):
        """Never advertise a tool that hangs until the command timeout on first use."""
        monkeypatch.setattr(browser_profiles, "get_profile", lambda n: ENROLLED)
        monkeypatch.setattr(browser_profiles, "resolve_executable", lambda p: None)
        monkeypatch.setattr(
            browser_session_registry, "default_profile_name", lambda: "enrolled"
        )
        assert browser_tool.check_browser_requirements() is False

    def test_unavailable_when_the_toggle_is_off(self, monkeypatch):
        monkeypatch.setattr(browser_session_registry, "default_profile_name", lambda: None)
        assert browser_tool.check_browser_requirements() is False

    def test_unavailable_for_an_ephemeral_default_profile(self, monkeypatch):
        """browser.default_profile naming the disposable browser changes nothing."""
        monkeypatch.setattr(browser_profiles, "get_profile", lambda n: EPHEMERAL)
        monkeypatch.setattr(
            browser_session_registry, "default_profile_name", lambda: "default"
        )
        assert browser_tool.check_browser_requirements() is False

    def test_unavailable_without_the_agent_browser_cli(self, monkeypatch, tmp_path):
        """The enrolled path still drives the browser THROUGH agent-browser."""
        exe = tmp_path / "chrome"
        exe.write_text("")
        exe.chmod(0o755)
        monkeypatch.setattr(browser_profiles, "get_profile", lambda n: ENROLLED)
        monkeypatch.setattr(browser_profiles, "resolve_executable", lambda p: str(exe))
        monkeypatch.setattr(
            browser_session_registry, "default_profile_name", lambda: "enrolled"
        )

        def _missing(**kw):
            raise FileNotFoundError("agent-browser CLI not found")

        monkeypatch.setattr(browser_tool, "_find_agent_browser", _missing)
        assert browser_tool.check_browser_requirements() is False

    def test_non_executable_file_does_not_resolve(self, tmp_path):
        exe = tmp_path / "chrome"
        exe.write_text("")
        exe.chmod(0o644)
        profile = browser_profiles.BrowserProfile(
            name="enrolled", kind=browser_profiles.KIND_ENROLLED, executable=str(exe)
        )
        assert browser_profiles.resolve_executable(profile) is None

    def test_directory_does_not_resolve(self, tmp_path):
        profile = browser_profiles.BrowserProfile(
            name="enrolled", kind=browser_profiles.KIND_ENROLLED, executable=str(tmp_path)
        )
        assert browser_profiles.resolve_executable(profile) is None
