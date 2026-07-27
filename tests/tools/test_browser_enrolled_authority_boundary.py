"""The enrolled browser's authority must not leak through a second door.

Second-pass adversarial review (2026-07-27,
``docs/reviews/2026-07-27-per-navigation-browser-profile-remediation-adversarial-review-gpt-5.md``)
found four ways the per-navigation trust boundary could still be bypassed after
the first remediation round. Each class gets a test here, and each test was
confirmed to FAIL against the pre-fix tree:

* CRIT-001 -- ``browser.cdp_url``/``BROWSER_CDP_URL`` pointing at a local
  enrolled listener made ``_navigation_session_key`` return the BARE key for
  every URL, so untrusted public pages were driven by the corporate browser with
  all six enrolled guard-forcing disjuncts inactive.
* CRIT-002 -- ``find_free_debug_port`` skipped reserved enrolled ports inside its
  search loop and then returned ``preferred + 1`` unchecked on exhaustion, which
  can be the very port it just refused.
* HIGH-003 -- daemon hygiene (``close --all``) is process-global, and its
  liveness gate only saw ``_active_sessions``. A different key's acquire could
  therefore run it after another acquire had already launched its browser.
* HIGH-004 -- end-of-task cleanup only reaped an enrolled sidecar already present
  in ``_active_sessions``, so an acquire still in flight republished the memo,
  handle and registry binding AFTER its task's reaper had run.

Design: docs/plans/2026-07-26-per-navigation-browser-profile-design.md
"""

import threading

import pytest

from hermes_cli import browser_connect
from tools import (
    browser_profiles,
    browser_session_manager,
    browser_session_registry,
    browser_tool,
)


ENROLLED_PORT = 9333


def _enrolled_config(port=ENROLLED_PORT, cdp_url=None):
    browser_cfg = {
        "default_profile": "corp",
        "profiles": {
            "corp": {
                "kind": "enrolled",
                "executable": "auto",
                "cdp_port": port,
                "trusted_origins": ["https://wiki.corp.example"],
            }
        },
    }
    if cdp_url is not None:
        browser_cfg["cdp_url"] = cdp_url
    return {"browser": browser_cfg}


def _patch_config(monkeypatch, cfg):
    """Patch every seam that reads raw config, including the override reader.

    ``_get_cdp_override`` imports ``hermes_cli.config.read_raw_config`` inside
    the function body, so that module attribute is the seam it actually uses.
    """
    import hermes_cli.config as hermes_config

    monkeypatch.setattr(browser_profiles, "_read_config", lambda: cfg)
    monkeypatch.setattr(browser_session_registry, "_read_config", lambda: cfg)
    monkeypatch.setattr(hermes_config, "read_raw_config", lambda *a, **k: cfg)


@pytest.fixture(autouse=True)
def _clean():
    browser_tool._reset_session_cdp_cache()
    browser_session_registry.clear()
    yield
    browser_tool._reset_session_cdp_cache()
    browser_session_registry.clear()


@pytest.fixture()
def _no_cdp_discovery(monkeypatch):
    """Keep ``_resolve_cdp_override`` off the network; it is not under test."""
    monkeypatch.setattr(
        browser_tool, "_resolve_cdp_override", lambda raw: str(raw or "").strip()
    )


# ── CRIT-001 ────────────────────────────────────────────────────────────────


class TestConfiguredOverrideCannotReachTheEnrolledBrowser:
    """A global CDP override naming a local enrolled listener must be refused.

    ``browser.cdp_url`` is a real, seeded field -- the OpenClaw migration copies
    ``browser.cdpUrl`` into it (``openclaw_to_hermes.py``) -- so "nothing seeds
    it" was not an adequate boundary.
    """

    @pytest.fixture(autouse=True)
    def _config(self, monkeypatch, _no_cdp_discovery):
        cfg = _enrolled_config(cdp_url=f"http://127.0.0.1:{ENROLLED_PORT}")
        _patch_config(monkeypatch, cfg)
        monkeypatch.delenv("BROWSER_CDP_URL", raising=False)

    def test_override_naming_the_enrolled_port_is_discarded(self):
        assert browser_tool._get_cdp_override() == ""

    def test_public_url_does_not_get_the_corporate_endpoint(self):
        key = browser_tool._navigation_session_key("victim", "https://attacker.example/")
        assert not browser_tool._is_enrolled_session_key(key)
        assert browser_tool._session_cdp_url(key) == "", (
            "a bare/ephemeral session received the corporate CDP endpoint"
        )

    def test_trusted_origin_still_routes_to_the_enrolled_key(self):
        """The override must not defeat per-navigation routing either."""
        key = browser_tool._navigation_session_key(
            "victim", "https://wiki.corp.example/page"
        )
        assert key == "victim::enrolled"

    def test_env_override_naming_the_enrolled_port_is_discarded(self, monkeypatch):
        monkeypatch.setenv("BROWSER_CDP_URL", f"ws://127.0.0.1:{ENROLLED_PORT}/devtools/browser/x")
        assert browser_tool._get_cdp_override() == ""

    def test_ipv6_loopback_form_is_discarded(self, monkeypatch):
        monkeypatch.setenv("BROWSER_CDP_URL", f"http://[::1]:{ENROLLED_PORT}")
        assert browser_tool._get_cdp_override() == ""


class TestOrdinaryOverridesStillWork:
    """The refusal must be surgical: only LOCAL enrolled endpoints are blocked."""

    @pytest.fixture(autouse=True)
    def _config(self, monkeypatch, _no_cdp_discovery):
        cfg = _enrolled_config()
        _patch_config(monkeypatch, cfg)
        monkeypatch.delenv("BROWSER_CDP_URL", raising=False)

    def test_throwaway_local_debug_port_is_untouched(self, monkeypatch):
        monkeypatch.setenv("BROWSER_CDP_URL", "http://127.0.0.1:9222")
        assert browser_tool._get_cdp_override() == "http://127.0.0.1:9222"

    def test_remote_endpoint_on_the_enrolled_port_is_untouched(self, monkeypatch):
        """A remote CDP service cannot be the local enrolled listener."""
        monkeypatch.setenv("BROWSER_CDP_URL", f"wss://cdp.vendor.example:{ENROLLED_PORT}/x")
        assert browser_tool._get_cdp_override() == (
            f"wss://cdp.vendor.example:{ENROLLED_PORT}/x"
        )


# ── CRIT-002 ────────────────────────────────────────────────────────────────


class TestFreePortSearchNeverReturnsAReservedPort:
    @pytest.fixture(autouse=True)
    def _config(self, monkeypatch):
        monkeypatch.setattr(
            browser_profiles, "_read_config", lambda: _enrolled_config(port=9223)
        )

    def test_exhausted_search_does_not_fall_back_onto_the_enrolled_port(self):
        """The old code returned ``preferred + 1`` unchecked after exhaustion."""
        picked = browser_connect.find_free_debug_port(9222, attempts=1)
        assert picked != 9223, (
            "the exhausted-search fallback returned the enrolled port it skipped"
        )
        assert browser_connect.enrolled_port_refusal(picked) is None

    def test_exhausted_default_search_also_avoids_reserved_ports(self, monkeypatch):
        """Not an attempts=1 artefact: the same fallback ends the 10-port search.

        Every port in the near range is unbindable, so the search is exhausted;
        an ephemeral port is still available.
        """
        near = set(range(9223, 9233))
        monkeypatch.setattr(
            browser_connect, "_dual_stack_bindable", lambda port: port not in near
        )
        picked = browser_connect.find_free_debug_port(9222)
        assert picked not in near
        assert browser_connect.enrolled_port_refusal(picked) is None

    def test_no_safe_port_raises_instead_of_returning_a_reserved_one(self, monkeypatch):
        """When nothing is bindable there is no safe integer to return."""
        monkeypatch.setattr(browser_connect, "_dual_stack_bindable", lambda port: False)
        with pytest.raises(RuntimeError, match="could not find a free debug port"):
            browser_connect.find_free_debug_port(9222)


class TestPortRefusalIsHostnameAware:
    """MED-006: a remote CDP endpoint cannot collide with the local listener."""

    @pytest.fixture(autouse=True)
    def _config(self, monkeypatch):
        monkeypatch.setattr(browser_profiles, "_read_config", lambda: _enrolled_config())

    @pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", None])
    def test_local_forms_are_refused(self, host):
        assert browser_connect.enrolled_port_refusal(ENROLLED_PORT, host) is not None

    def test_remote_host_on_the_enrolled_port_is_allowed(self):
        assert (
            browser_connect.enrolled_port_refusal(ENROLLED_PORT, "cdp.vendor.example")
            is None
        )


# ── HIGH-003 ────────────────────────────────────────────────────────────────


class TestHygieneRespectsInFlightAcquires:
    """``close --all`` is process-global; it must not run beside another launch."""

    def test_second_key_does_not_run_close_all_after_the_first_launch(
        self, monkeypatch
    ):
        monkeypatch.setattr(browser_profiles, "_read_config", lambda: _enrolled_config())

        hygiene_calls = []
        launch_started = threading.Event()
        may_finish = threading.Event()
        who = threading.local()

        def _fake_run(cmd, **kwargs):
            if cmd[-2:] == ["close", "--all"]:
                hygiene_calls.append(getattr(who, "name", "?"))

            class _R:
                returncode = 0

            return _R()

        def _fake_ensure(profile, headless):
            if getattr(who, "name", "") == "A":
                launch_started.set()
                may_finish.wait(5)
            return f"http://127.0.0.1:{profile.cdp_port}"

        monkeypatch.setattr(browser_session_manager, "_agent_browser_cmd", lambda: ["/bin/true"])
        monkeypatch.setattr(browser_session_manager.subprocess, "run", _fake_run)
        monkeypatch.setattr(browser_session_manager, "_ensure_enrolled_cdp", _fake_ensure)

        def _worker(name, key):
            who.name = name
            browser_session_manager.acquire(
                "corp", session_key=key, attach_global=False
            )

        a = threading.Thread(target=_worker, args=("A", "taskA::enrolled"))
        a.start()
        assert launch_started.wait(5), "thread A never reached its launch"
        b = threading.Thread(target=_worker, args=("B", "taskB::enrolled"))
        b.start()
        b.join(5)
        may_finish.set()
        a.join(5)

        assert hygiene_calls == ["A"], (
            f"a second acquire ran process-global `close --all` while another "
            f"launch was in flight: {hygiene_calls}"
        )

    def test_hygiene_still_runs_when_nothing_is_in_flight(self, monkeypatch):
        """The fix must not disable hygiene for the ordinary single-acquire case."""
        monkeypatch.setattr(browser_profiles, "_read_config", lambda: _enrolled_config())
        calls = []

        def _fake_run(cmd, **kwargs):
            if cmd[-2:] == ["close", "--all"]:
                calls.append(cmd)

            class _R:
                returncode = 0

            return _R()

        monkeypatch.setattr(browser_session_manager, "_agent_browser_cmd", lambda: ["/bin/true"])
        monkeypatch.setattr(browser_session_manager.subprocess, "run", _fake_run)
        monkeypatch.setattr(
            browser_session_manager,
            "_ensure_enrolled_cdp",
            lambda p, h: f"http://127.0.0.1:{p.cdp_port}",
        )
        browser_session_manager.acquire(
            "corp", session_key="solo::enrolled", attach_global=False
        )
        assert len(calls) == 1

    def test_hygiene_is_skipped_while_an_acquired_session_is_still_bound(
        self, monkeypatch
    ):
        """A session that acquired but has not yet published counts as live."""
        monkeypatch.setattr(browser_profiles, "_read_config", lambda: _enrolled_config())
        calls = []

        def _fake_run(cmd, **kwargs):
            if cmd[-2:] == ["close", "--all"]:
                calls.append(cmd)

            class _R:
                returncode = 0

            return _R()

        monkeypatch.setattr(browser_session_manager, "_agent_browser_cmd", lambda: ["/bin/true"])
        monkeypatch.setattr(browser_session_manager.subprocess, "run", _fake_run)
        monkeypatch.setattr(
            browser_session_manager,
            "_ensure_enrolled_cdp",
            lambda p, h: f"http://127.0.0.1:{p.cdp_port}",
        )
        browser_session_manager.acquire("corp", session_key="first::enrolled", attach_global=False)
        browser_session_manager.acquire("corp", session_key="second::enrolled", attach_global=False)
        assert len(calls) == 1, (
            "the second acquire tore down the first session's browser with "
            "process-global `close --all`"
        )


# ── HIGH-004 ────────────────────────────────────────────────────────────────


class TestCleanupBeatsAnInFlightAcquire:
    def test_racing_acquire_does_not_republish_after_cleanup(
        self, monkeypatch, _no_cdp_discovery
    ):
        cfg = _enrolled_config()
        _patch_config(monkeypatch, cfg)

        entered = threading.Event()
        may_finish = threading.Event()

        monkeypatch.setattr(browser_session_manager, "_run_daemon_hygiene", lambda *a, **k: None)

        def _blocking_ensure(profile, headless):
            entered.set()
            may_finish.wait(5)
            return f"http://127.0.0.1:{profile.cdp_port}"

        monkeypatch.setattr(browser_session_manager, "_ensure_enrolled_cdp", _blocking_ensure)

        errors = []

        def _racer():
            try:
                browser_tool._session_cdp_url("race::enrolled")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t = threading.Thread(target=_racer)
        t.start()
        assert entered.wait(5), "the racing acquire never started"

        # The task ends while the acquire is still in flight.
        browser_tool.cleanup_browser("race")
        may_finish.set()
        t.join(5)

        assert "race::enrolled" not in browser_tool._session_cdp_urls, (
            "a cleaned-up task's enrolled endpoint was republished after cleanup"
        )
        assert "race::enrolled" not in browser_tool._session_handles, (
            "a cleaned-up task's browser handle was republished after cleanup"
        )
        assert browser_session_registry.profile_for("race::enrolled") is None, (
            "enrolled origin trust survived the task that owned it"
        )

    def test_bare_cleanup_reaps_an_unpublished_enrolled_sidecar(self, monkeypatch):
        """Memo/handle can exist before ``_active_sessions`` does."""
        released = []

        class _Handle:
            def release(self):
                released.append(True)
                browser_session_registry.unbind("solo::enrolled")

        browser_session_registry.bind("solo::enrolled", "corp")
        with browser_tool._session_cdp_lock:
            browser_tool._session_cdp_urls["solo::enrolled"] = "http://127.0.0.1:9333"
            browser_tool._session_handles["solo::enrolled"] = _Handle()

        browser_tool.cleanup_browser("solo")

        assert released == [True]
        assert "solo::enrolled" not in browser_tool._session_cdp_urls
        assert browser_session_registry.profile_for("solo::enrolled") is None

    def test_per_turn_cleanup_still_spares_the_enrolled_session(self, monkeypatch):
        """``keep_enrolled=True`` must keep its meaning (review finding H-2)."""

        class _Handle:
            def release(self):  # pragma: no cover - must not be called
                raise AssertionError("per-turn cleanup released the enrolled session")

        browser_session_registry.bind("turn::enrolled", "corp")
        with browser_tool._session_cdp_lock:
            browser_tool._session_cdp_urls["turn::enrolled"] = "http://127.0.0.1:9333"
            browser_tool._session_handles["turn::enrolled"] = _Handle()

        browser_tool.cleanup_browser("turn", keep_enrolled=True)

        assert "turn::enrolled" in browser_tool._session_cdp_urls
        assert browser_session_registry.profile_for("turn::enrolled") == "corp"


# ── MED-005 ─────────────────────────────────────────────────────────────────


class TestEnrolledDataDirectoryIsUsableAndStable:
    """The availability gate must not advertise a profile whose first acquire
    is guaranteed to fail, and a configured relative path must not depend on
    the process CWD (review finding MED-005)."""

    def _profile(self, user_data_dir):
        return browser_profiles.BrowserProfile(
            name="corp",
            kind=browser_profiles.KIND_ENROLLED,
            executable="auto",
            user_data_dir=user_data_dir,
            cdp_port=ENROLLED_PORT,
            trusted_origins=("https://wiki.corp.example",),
        )

    def _launchable(self, monkeypatch, user_data_dir, tmp_path):
        profile = self._profile(user_data_dir)
        monkeypatch.setattr(
            browser_profiles, "get_profile", lambda n: profile if n == "corp" else None
        )
        monkeypatch.setattr(
            browser_profiles, "resolve_executable", lambda p: str(tmp_path / "browser")
        )
        monkeypatch.setattr(browser_session_registry, "default_profile_name", lambda: "corp")
        return browser_session_registry.default_profile_launchable()

    def test_relative_path_is_anchored_not_cwd_relative(self, monkeypatch, tmp_path):
        """Same config, two CWDs, one directory."""
        monkeypatch.setattr(
            browser_profiles, "get_profile", lambda n: self._profile("relative-profile")
        )
        import hermes_constants

        monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
        first = browser_profiles.resolve_user_data_dir(self._profile("relative-profile"))
        monkeypatch.chdir(tmp_path)
        second = browser_profiles.resolve_user_data_dir(self._profile("relative-profile"))
        assert first == second, "a configured relative user_data_dir moved with the CWD"
        assert str(tmp_path) in first

    def test_target_under_a_regular_file_is_not_launchable(self, monkeypatch, tmp_path):
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        assert self._launchable(monkeypatch, str(blocker / "profile"), tmp_path) is False

    def test_existing_non_directory_target_is_not_launchable(self, monkeypatch, tmp_path):
        target = tmp_path / "profile-file"
        target.write_text("x", encoding="utf-8")
        assert self._launchable(monkeypatch, str(target), tmp_path) is False

    def test_ordinary_creatable_directory_is_launchable(self, monkeypatch, tmp_path):
        assert self._launchable(monkeypatch, str(tmp_path / "profile"), tmp_path) is True

    def test_existing_directory_is_launchable(self, monkeypatch, tmp_path):
        existing = tmp_path / "already"
        existing.mkdir()
        assert self._launchable(monkeypatch, str(existing), tmp_path) is True


# ── MED-007 ─────────────────────────────────────────────────────────────────


class TestAcquisitionIsBoundedByWallClock:
    """A slow/stateful listener must not make a same-key caller wait minutes
    before a 30s browser command even starts (review finding MED-007)."""

    def test_readiness_wait_honours_a_wall_clock_deadline(self, monkeypatch):
        slept = []
        clock = {"now": 1000.0}

        monkeypatch.setattr(browser_session_manager.time, "monotonic", lambda: clock["now"])

        def _sleep(seconds):
            slept.append(seconds)
            clock["now"] += seconds

        monkeypatch.setattr(browser_session_manager.time, "sleep", _sleep)
        # Every probe is slow AND never proves identity: the pathological case.
        def _slow_alive(url):
            clock["now"] += browser_session_manager.CDP_PROBE_TIMEOUT_S
            return True

        monkeypatch.setattr(browser_session_manager, "_cdp_alive", _slow_alive)
        monkeypatch.setattr(browser_session_manager, "_cdp_browser_identity", lambda url: None)
        monkeypatch.setattr(
            browser_session_manager, "_endpoint_is_profile_browser", lambda p, u: False
        )
        monkeypatch.setattr(browser_session_manager, "_spawn_browser", lambda e, a: None)
        monkeypatch.setattr(browser_profiles, "resolve_executable", lambda p: "/bin/true")
        monkeypatch.setattr(browser_profiles, "resolve_user_data_dir", lambda p: "/tmp/x")
        monkeypatch.setattr(browser_session_manager.os, "makedirs", lambda *a, **k: None)

        profile = browser_profiles.BrowserProfile(
            name="corp", kind=browser_profiles.KIND_ENROLLED, cdp_port=ENROLLED_PORT
        )
        start = clock["now"]
        with pytest.raises(browser_session_manager.ProfileError):
            browser_session_manager._ensure_enrolled_cdp(profile, headless=True)
        elapsed = clock["now"] - start
        assert elapsed <= browser_session_manager.ENROLLED_READY_DEADLINE_S + 10, (
            f"readiness wait ran {elapsed:.0f}s, unbounded by the deadline"
        )

    def test_documented_bound_is_well_under_two_minutes(self):
        assert browser_session_manager.ACQUIRE_WORST_CASE_S < 90


# ── LOW-009 ─────────────────────────────────────────────────────────────────


class TestKeyLockTableIsBounded:
    def test_completed_keys_do_not_accumulate(self, monkeypatch, _no_cdp_discovery):
        cfg = _enrolled_config()
        _patch_config(monkeypatch, cfg)
        monkeypatch.setattr(browser_session_manager, "_run_daemon_hygiene", lambda: None)
        monkeypatch.setattr(
            browser_session_manager,
            "_ensure_enrolled_cdp",
            lambda p, h: f"http://127.0.0.1:{p.cdp_port}",
        )
        for i in range(50):
            key = f"task-{i}::enrolled"
            browser_tool._session_cdp_url(key)
            browser_tool.cleanup_browser(f"task-{i}")
        assert len(browser_tool._session_cdp_keylocks) == 0, (
            "per-key locks accumulate without bound in a long-running server"
        )

    def test_single_flight_still_holds(self, monkeypatch, _no_cdp_discovery):
        """Pruning must not reintroduce the race the locks exist to prevent."""
        cfg = _enrolled_config()
        _patch_config(monkeypatch, cfg)
        monkeypatch.setattr(browser_session_manager, "_run_daemon_hygiene", lambda: None)

        acquires = []
        gate = threading.Event()

        def _slow_ensure(profile, headless):
            acquires.append(profile.name)
            gate.wait(2)
            return f"http://127.0.0.1:{profile.cdp_port}"

        monkeypatch.setattr(browser_session_manager, "_ensure_enrolled_cdp", _slow_ensure)
        threads = [
            threading.Thread(target=browser_tool._session_cdp_url, args=("shared::enrolled",))
            for _ in range(4)
        ]
        for t in threads:
            t.start()
        gate.set()
        for t in threads:
            t.join(5)
        assert len(acquires) == 1, f"single-flight broken: {len(acquires)} acquires"
