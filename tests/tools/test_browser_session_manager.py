"""Tests for acquire()/release() lifecycle, attach-or-launch, daemon hygiene.

The launch path is ported from the confluence-research skill's field-verified
ensure_edge_cdp(): probe CDP first and reuse a live browser, spawn detached, then
wait for readiness. These tests pin all three behaviours — each was a real bug in
the original plan sketch.
"""

import pytest

from tools import browser_profiles, browser_session_manager, browser_session_registry

ENROLLED = browser_profiles.BrowserProfile(
    name="enrolled",
    kind=browser_profiles.KIND_ENROLLED,
    user_data_dir="/tmp/enrolled-profile",
    cdp_port=9222,
    headed=True,
    trusted_origins=("https://wiki.corp.example",),
)


@pytest.fixture(autouse=True)
def _clean():
    browser_session_registry.clear()
    yield
    browser_session_registry.clear()


@pytest.fixture()
def _stub_env(monkeypatch):
    """Stub every external effect; record calls for assertions."""
    calls = {"hygiene": 0, "spawned": [], "attached": [], "alive_probes": 0}

    monkeypatch.setattr(
        browser_profiles, "get_profile",
        lambda n: ENROLLED if n == "enrolled" else browser_profiles.BrowserProfile(name="default"),
    )
    monkeypatch.setattr(browser_profiles, "resolve_executable", lambda p: "/usr/bin/msedge")
    monkeypatch.setattr(
        browser_session_manager, "_run_daemon_hygiene",
        lambda: calls.__setitem__("hygiene", calls["hygiene"] + 1),
    )
    monkeypatch.setattr(
        browser_session_manager, "_spawn_browser",
        lambda exe, args: calls["spawned"].append((exe, args)),
    )
    monkeypatch.setattr(
        browser_session_manager, "_attach_cdp",
        lambda url: calls["attached"].append(url),
    )
    monkeypatch.setattr(browser_session_manager.os, "makedirs", lambda *a, **kw: None)
    monkeypatch.setattr(browser_session_manager.time, "sleep", lambda s: None)
    # Default the pre-launch reuse gate to "nothing is listening" so tests
    # never hit the real network (127.0.0.1:9222 is Chrome's conventional
    # remote-debugging port -- exactly what this feature encourages a
    # developer to have running locally). Tests that specifically exercise
    # the reuse path override this explicitly.
    monkeypatch.setattr(browser_session_manager, "_cdp_browser_identity", lambda url: None)
    # The identity PROOF (H-1) reads <user_data_dir>/DevToolsActivePort off disk
    # and re-probes /json/version -- both external effects this fixture exists to
    # stub out. Reuse and readiness are gated on it, so it defaults to "proven"
    # here; the proof itself is exercised directly in TestEndpointIdentityProof
    # and TestReuseRequiresProof below.
    monkeypatch.setattr(
        browser_session_manager, "_endpoint_is_profile_browser", lambda p, url: True
    )
    return calls


def _cdp_dead_then_alive(calls, alive_after=2):
    """Return a _cdp_alive stub that reports dead until probe `alive_after`."""
    def _probe(url):
        calls["alive_probes"] += 1
        return calls["alive_probes"] > alive_after
    return _probe


class TestAcquireAttachOrLaunch:
    def test_reuses_already_running_browser_without_launching(self, monkeypatch, _stub_env):
        """The common real case: Edge is already listening on the debug port."""
        monkeypatch.setattr(
            browser_session_manager, "_cdp_browser_identity", lambda url: "Chrome/125.0"
        )
        sess = browser_session_manager.acquire(profile="enrolled")
        assert _stub_env["spawned"] == []          # nothing launched
        assert sess.cdp_url == "http://127.0.0.1:9222"
        assert _stub_env["attached"] == ["http://127.0.0.1:9222"]

    def test_launches_when_nothing_is_listening(self, monkeypatch, _stub_env):
        monkeypatch.setattr(
            browser_session_manager, "_cdp_alive", _cdp_dead_then_alive(_stub_env)
        )
        browser_session_manager.acquire(profile="enrolled")
        assert len(_stub_env["spawned"]) == 1
        exe, args = _stub_env["spawned"][0]
        assert exe == "/usr/bin/msedge"
        assert "--remote-debugging-port=9222" in args
        assert "--user-data-dir=/tmp/enrolled-profile" in args

    def test_waits_for_readiness_before_returning(self, monkeypatch, _stub_env):
        """Returning before CDP answers is a race — the caller would attach early."""
        monkeypatch.setattr(
            browser_session_manager, "_cdp_alive", _cdp_dead_then_alive(_stub_env, alive_after=3)
        )
        browser_session_manager.acquire(profile="enrolled")
        # 1 pre-launch probe + polls until alive on probe 4
        assert _stub_env["alive_probes"] >= 4

    def test_raises_when_browser_never_exposes_cdp(self, monkeypatch, _stub_env):
        monkeypatch.setattr(browser_session_manager, "_cdp_alive", lambda url: False)
        with pytest.raises(browser_session_manager.ProfileError, match="did not expose CDP"):
            browser_session_manager.acquire(profile="enrolled")

    def test_headed_profile_launches_visible(self, monkeypatch, _stub_env):
        monkeypatch.setattr(
            browser_session_manager, "_cdp_alive", _cdp_dead_then_alive(_stub_env)
        )
        browser_session_manager.acquire(profile="enrolled")
        _, args = _stub_env["spawned"][0]
        assert "--headless=new" not in args

    def test_explicit_headless_overrides_profile(self, monkeypatch, _stub_env):
        monkeypatch.setattr(
            browser_session_manager, "_cdp_alive", _cdp_dead_then_alive(_stub_env)
        )
        browser_session_manager.acquire(profile="enrolled", headless=True)
        _, args = _stub_env["spawned"][0]
        assert "--headless=new" in args


class TestAcquireContract:
    def test_binds_registry(self, monkeypatch, _stub_env):
        monkeypatch.setattr(browser_session_manager, "_cdp_alive", lambda url: True)
        sess = browser_session_manager.acquire(profile="enrolled")
        assert browser_session_registry.profile_for(sess.session_key) == "enrolled"

    def test_daemon_hygiene_runs(self, monkeypatch, _stub_env):
        monkeypatch.setattr(browser_session_manager, "_cdp_alive", lambda url: True)
        browser_session_manager.acquire(profile="enrolled")
        assert _stub_env["hygiene"] == 1

    def test_unknown_profile_raises(self, monkeypatch):
        monkeypatch.setattr(browser_profiles, "get_profile", lambda n: None)
        with pytest.raises(browser_session_manager.ProfileError, match="unknown browser profile"):
            browser_session_manager.acquire(profile="ghost")

    def test_enrolled_without_executable_raises(self, monkeypatch, _stub_env):
        """Never silently fall back to bundled Chrome — mTLS would fail confusingly."""
        monkeypatch.setattr(browser_session_manager, "_cdp_alive", lambda url: False)
        monkeypatch.setattr(browser_profiles, "resolve_executable", lambda p: None)
        with pytest.raises(browser_session_manager.ProfileError, match="enrolled browser"):
            browser_session_manager.acquire(profile="enrolled")

    def test_ephemeral_profile_does_not_launch(self, monkeypatch, _stub_env):
        sess = browser_session_manager.acquire(profile="default")
        assert _stub_env["spawned"] == []
        assert sess.cdp_url is None

    def test_accepts_caller_session_key(self, monkeypatch, _stub_env):
        """The agent path binds its own task_id rather than profile::<name>."""
        monkeypatch.setattr(browser_session_manager, "_cdp_alive", lambda url: True)
        sess = browser_session_manager.acquire(profile="enrolled", session_key="task-7")
        assert sess.session_key == "task-7"
        assert browser_session_registry.profile_for("task-7") == "enrolled"


class TestRelease:
    @pytest.fixture(autouse=True)
    def _alive(self, monkeypatch, _stub_env):
        monkeypatch.setattr(browser_session_manager, "_cdp_alive", lambda url: True)

    def test_release_unbinds_registry(self):
        sess = browser_session_manager.acquire(profile="enrolled")
        key = sess.session_key
        sess.release()
        assert browser_session_registry.profile_for(key) is None

    def test_release_is_idempotent(self):
        sess = browser_session_manager.acquire(profile="enrolled")
        sess.release()
        sess.release()  # must not raise

    def test_context_manager_releases(self):
        with browser_session_manager.acquire(profile="enrolled") as sess:
            key = sess.session_key
            assert browser_session_registry.profile_for(key) == "enrolled"
        assert browser_session_registry.profile_for(key) is None


class TestSpawnIsDetached:
    """The browser must outlive the parent and not hold its console."""

    def test_posix_uses_start_new_session(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(browser_session_manager.sys, "platform", "darwin")
        monkeypatch.setattr(
            browser_session_manager.subprocess, "Popen",
            lambda cmd, **kw: seen.update(cmd=cmd, kw=kw),
        )
        browser_session_manager._spawn_browser("/usr/bin/msedge", ["--foo"])
        assert seen["kw"].get("start_new_session") is True

    def test_windows_uses_detached_process_flags(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(browser_session_manager.sys, "platform", "win32")
        monkeypatch.setattr(
            browser_session_manager.subprocess, "DETACHED_PROCESS", 0x8, raising=False
        )
        monkeypatch.setattr(
            browser_session_manager.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False
        )
        monkeypatch.setattr(
            browser_session_manager.subprocess, "Popen",
            lambda cmd, **kw: seen.update(cmd=cmd, kw=kw),
        )
        browser_session_manager._spawn_browser("msedge.exe", ["--foo"])
        assert seen["kw"].get("creationflags") == 0x8 | 0x200


class TestEndpointIdentity:
    def test_foreign_listener_is_not_reused(self, monkeypatch):
        """Any HTTP responder on the port must not be trusted as our browser."""
        monkeypatch.setattr(
            browser_session_manager, "_cdp_browser_identity", lambda url: None
        )
        monkeypatch.setattr(browser_session_manager, "_run_daemon_hygiene", lambda: None)
        monkeypatch.setattr(
            browser_profiles, "resolve_executable", lambda p: "/nonexistent/chrome"
        )
        # The identity check must be what gates reuse, not liveness — so the
        # post-launch readiness poll never succeeds (nothing real is
        # launched) and the profile never exposes CDP. Real makedirs/spawn
        # are stubbed out: they are launch-mechanics side effects, not what
        # this test is verifying.
        monkeypatch.setattr(browser_session_manager.os, "makedirs", lambda *a, **kw: None)
        monkeypatch.setattr(browser_session_manager, "_spawn_browser", lambda exe, args: None)
        monkeypatch.setattr(browser_session_manager.time, "sleep", lambda s: None)
        monkeypatch.setattr(browser_session_manager, "_cdp_alive", lambda url: False)
        profile = browser_profiles.BrowserProfile(
            name="corp", kind=browser_profiles.KIND_ENROLLED, cdp_port=9222
        )
        with pytest.raises(browser_session_manager.ProfileError):
            browser_session_manager._ensure_enrolled_cdp(profile, headless=True)


class _Resp:
    """Minimal urlopen context manager returning a fixed body."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self._body


def _stub_version(monkeypatch, body, *, raises=False):
    def _open(url, timeout=None):  # noqa: ARG001 — match urlopen signature
        if raises:
            raise OSError("connection refused")
        return _Resp(body if isinstance(body, bytes) else body.encode())

    monkeypatch.setattr(browser_session_manager.urllib.request, "urlopen", _open)


class TestCdpBrowserIdentityParsing:
    """H-1: `_cdp_browser_identity` had no direct unit coverage at all.

    It is the cheap first gate on the reuse path and must fail CLOSED on every
    malformed answer -- a truthy return from a garbage body would put a stranger
    one step from the enrolled profile's trust.
    """

    URL = "http://127.0.0.1:9333"

    def test_connection_error_is_none(self, monkeypatch):
        _stub_version(monkeypatch, b"", raises=True)
        assert browser_session_manager._cdp_browser_identity(self.URL) is None

    def test_non_json_body_is_none(self, monkeypatch):
        _stub_version(monkeypatch, b"<html>not a CDP endpoint</html>")
        assert browser_session_manager._cdp_browser_identity(self.URL) is None

    def test_json_that_is_not_an_object_is_none(self, monkeypatch):
        _stub_version(monkeypatch, b'["Chrome/125.0"]')
        assert browser_session_manager._cdp_browser_identity(self.URL) is None

    def test_missing_browser_key_is_none(self, monkeypatch):
        _stub_version(monkeypatch, b'{"Protocol-Version": "1.3"}')
        assert browser_session_manager._cdp_browser_identity(self.URL) is None

    def test_empty_browser_value_is_none(self, monkeypatch):
        _stub_version(monkeypatch, b'{"Browser": "   "}')
        assert browser_session_manager._cdp_browser_identity(self.URL) is None

    def test_null_browser_value_is_none(self, monkeypatch):
        _stub_version(monkeypatch, b'{"Browser": null}')
        assert browser_session_manager._cdp_browser_identity(self.URL) is None

    def test_a_real_payload_returns_the_browser_string(self, monkeypatch):
        _stub_version(monkeypatch, b'{"Browser": "Chrome/125.0.6422.60"}')
        assert (
            browser_session_manager._cdp_browser_identity(self.URL)
            == "Chrome/125.0.6422.60"
        )


class TestEndpointIdentityProof:
    """H-1: prove the listener is the browser holding OUR profile directory."""

    TARGET = "/devtools/browser/6c8f0d2e-0000-4000-8000-000000000001"

    def _profile(self, tmp_path, port=9333):
        return browser_profiles.BrowserProfile(
            name="corp",
            kind=browser_profiles.KIND_ENROLLED,
            user_data_dir=str(tmp_path),
            cdp_port=port,
        )

    def _write_port_file(self, tmp_path, port, target):
        (tmp_path / "DevToolsActivePort").write_text(f"{port}\n{target}\n")

    def _version(self, monkeypatch, target):
        _stub_version(
            monkeypatch,
            ('{"Browser": "Chrome/125.0", "webSocketDebuggerUrl": '
             f'"ws://127.0.0.1:9333{target}"}}').encode(),
        )

    def test_matching_port_file_and_endpoint_is_proven(self, tmp_path, monkeypatch):
        self._write_port_file(tmp_path, 9333, self.TARGET)
        self._version(monkeypatch, self.TARGET)
        assert browser_session_manager._endpoint_is_profile_browser(
            self._profile(tmp_path), "http://127.0.0.1:9333"
        ) is True

    def test_no_port_file_is_not_proven(self, tmp_path, monkeypatch):
        """The `/browser connect` throwaway Chrome case: a live CDP endpoint on
        the port, but nothing tying it to this profile directory."""
        self._version(monkeypatch, self.TARGET)
        assert browser_session_manager._endpoint_is_profile_browser(
            self._profile(tmp_path), "http://127.0.0.1:9333"
        ) is False

    def test_different_browser_target_is_not_proven(self, tmp_path, monkeypatch):
        """Our profile dir has a stale record; a DIFFERENT browser answers."""
        self._write_port_file(tmp_path, 9333, self.TARGET)
        self._version(monkeypatch, "/devtools/browser/deadbeef-0000-4000-8000-000000000002")
        assert browser_session_manager._endpoint_is_profile_browser(
            self._profile(tmp_path), "http://127.0.0.1:9333"
        ) is False

    def test_port_mismatch_is_not_proven(self, tmp_path, monkeypatch):
        self._write_port_file(tmp_path, 9999, self.TARGET)
        self._version(monkeypatch, self.TARGET)
        assert browser_session_manager._endpoint_is_profile_browser(
            self._profile(tmp_path), "http://127.0.0.1:9333"
        ) is False

    def test_unreachable_endpoint_is_not_proven(self, tmp_path, monkeypatch):
        self._write_port_file(tmp_path, 9333, self.TARGET)
        _stub_version(monkeypatch, b"", raises=True)
        assert browser_session_manager._endpoint_is_profile_browser(
            self._profile(tmp_path), "http://127.0.0.1:9333"
        ) is False

    def test_endpoint_without_a_websocket_url_is_not_proven(self, tmp_path, monkeypatch):
        self._write_port_file(tmp_path, 9333, self.TARGET)
        _stub_version(monkeypatch, b'{"Browser": "Chrome/125.0"}')
        assert browser_session_manager._endpoint_is_profile_browser(
            self._profile(tmp_path), "http://127.0.0.1:9333"
        ) is False

    def test_truncated_port_file_is_not_proven(self, tmp_path, monkeypatch):
        (tmp_path / "DevToolsActivePort").write_text("9333\n")
        self._version(monkeypatch, self.TARGET)
        assert browser_session_manager._endpoint_is_profile_browser(
            self._profile(tmp_path), "http://127.0.0.1:9333"
        ) is False

    def test_non_numeric_port_file_is_not_proven(self, tmp_path, monkeypatch):
        (tmp_path / "DevToolsActivePort").write_text(f"nine-thousand\n{self.TARGET}\n")
        self._version(monkeypatch, self.TARGET)
        assert browser_session_manager._endpoint_is_profile_browser(
            self._profile(tmp_path), "http://127.0.0.1:9333"
        ) is False


class TestReuseRequiresProof:
    """H-1 end-to-end: a live-but-unproven listener must not be adopted.

    Concrete failure this pins: `/browser connect` then `/browser disconnect`
    leaves a throwaway Chrome running; without the proof, the next trusted
    navigation reuses it and binds `::enrolled` corporate trust to a browser
    with no SSO -- the silent downgrade the ledger says must never happen.
    """

    def test_unproven_listener_is_not_reused(self, monkeypatch, tmp_path):
        monkeypatch.setattr(browser_session_manager, "_run_daemon_hygiene", lambda: None)
        monkeypatch.setattr(
            browser_session_manager, "_cdp_browser_identity", lambda url: "Chrome/125.0"
        )
        monkeypatch.setattr(
            browser_session_manager, "_endpoint_is_profile_browser", lambda p, u: False
        )
        monkeypatch.setattr(browser_profiles, "resolve_executable", lambda p: "/usr/bin/x")
        monkeypatch.setattr(browser_session_manager.os, "makedirs", lambda *a, **kw: None)
        monkeypatch.setattr(browser_session_manager.time, "sleep", lambda s: None)
        spawned = []
        monkeypatch.setattr(
            browser_session_manager, "_spawn_browser",
            lambda exe, args: spawned.append(exe),
        )
        monkeypatch.setattr(browser_session_manager, "_cdp_alive", lambda url: False)
        profile = browser_profiles.BrowserProfile(
            name="corp", kind=browser_profiles.KIND_ENROLLED,
            user_data_dir=str(tmp_path), cdp_port=9333,
        )
        with pytest.raises(browser_session_manager.ProfileError):
            browser_session_manager._ensure_enrolled_cdp(profile, headless=True)
        assert spawned, "an unproven listener was adopted instead of launching our own"

    def test_readiness_poll_also_requires_proof(self, monkeypatch, tmp_path):
        """A squatter holding the port answers /json/version, so liveness alone
        would return the SQUATTER's endpoint as the enrolled browser."""
        monkeypatch.setattr(browser_session_manager, "_run_daemon_hygiene", lambda: None)
        monkeypatch.setattr(browser_session_manager, "_cdp_browser_identity", lambda url: None)
        monkeypatch.setattr(
            browser_session_manager, "_endpoint_is_profile_browser", lambda p, u: False
        )
        monkeypatch.setattr(browser_profiles, "resolve_executable", lambda p: "/usr/bin/x")
        monkeypatch.setattr(browser_session_manager.os, "makedirs", lambda *a, **kw: None)
        monkeypatch.setattr(browser_session_manager.time, "sleep", lambda s: None)
        monkeypatch.setattr(browser_session_manager, "_spawn_browser", lambda exe, args: None)
        monkeypatch.setattr(browser_session_manager, "_cdp_alive", lambda url: True)
        profile = browser_profiles.BrowserProfile(
            name="corp", kind=browser_profiles.KIND_ENROLLED,
            user_data_dir=str(tmp_path), cdp_port=9333,
        )
        with pytest.raises(browser_session_manager.ProfileError, match="did not expose CDP"):
            browser_session_manager._ensure_enrolled_cdp(profile, headless=True)
