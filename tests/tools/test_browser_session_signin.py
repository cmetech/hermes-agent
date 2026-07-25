"""Tests for the auth probe, the sign-in wait loop, and eval encoding."""

import pytest

from tools import browser_profiles, browser_session_manager, browser_session_registry

ENROLLED = browser_profiles.BrowserProfile(
    name="enrolled", kind=browser_profiles.KIND_ENROLLED,
    user_data_dir="/tmp/p", cdp_port=9222, headed=True,
    trusted_origins=("https://wiki.corp.example",),
)
PROBE = "document.querySelector('#user-menu') !== null"


@pytest.fixture(autouse=True)
def _clean():
    browser_session_registry.clear()
    yield
    browser_session_registry.clear()


def _session():
    return browser_session_manager.BrowserSession(
        session_key="task-1", profile=ENROLLED, cdp_url="http://127.0.0.1:9222"
    )


class TestIsAuthenticated:
    def test_true_when_probe_returns_true(self, monkeypatch):
        monkeypatch.setattr(browser_session_manager.BrowserSession, "eval",
                            lambda self, expr: {"success": True, "result": True})
        assert _session().is_authenticated(PROBE) is True

    def test_false_when_probe_returns_false(self, monkeypatch):
        monkeypatch.setattr(browser_session_manager.BrowserSession, "eval",
                            lambda self, expr: {"success": True, "result": False})
        assert _session().is_authenticated(PROBE) is False

    def test_false_when_eval_fails(self, monkeypatch):
        """A failed probe must read as NOT authenticated, never as success."""
        monkeypatch.setattr(browser_session_manager.BrowserSession, "eval",
                            lambda self, expr: {"success": False, "error": "boom"})
        assert _session().is_authenticated(PROBE) is False

    def test_truthy_string_is_not_authenticated(self, monkeypatch):
        """Only a real boolean True counts — avoid 'false' string truthiness."""
        monkeypatch.setattr(browser_session_manager.BrowserSession, "eval",
                            lambda self, expr: {"success": True, "result": "false"})
        assert _session().is_authenticated(PROBE) is False

    def test_missing_result_is_not_authenticated(self, monkeypatch):
        monkeypatch.setattr(browser_session_manager.BrowserSession, "eval",
                            lambda self, expr: {"success": True})
        assert _session().is_authenticated(PROBE) is False


class TestSignin:
    def test_returns_immediately_when_already_authenticated(self, monkeypatch):
        navs, sleeps = [], []
        monkeypatch.setattr(browser_session_manager.BrowserSession, "navigate",
                            lambda self, url: navs.append(url))
        monkeypatch.setattr(browser_session_manager.BrowserSession, "is_authenticated",
                            lambda self, probe: True)
        monkeypatch.setattr(browser_session_manager.time, "sleep", lambda s: sleeps.append(s))
        assert _session().signin("https://wiki.corp.example", PROBE) is True
        assert navs == ["https://wiki.corp.example"]
        assert sleeps == []  # no polling needed

    def test_polls_until_authenticated(self, monkeypatch):
        states = [False, False, True]
        monkeypatch.setattr(browser_session_manager.BrowserSession, "navigate",
                            lambda self, url: None)
        monkeypatch.setattr(browser_session_manager.BrowserSession, "is_authenticated",
                            lambda self, probe: states.pop(0))
        monkeypatch.setattr(browser_session_manager.time, "sleep", lambda s: None)
        monkeypatch.setattr(browser_session_manager.time, "monotonic", lambda: 0.0)
        assert _session().signin("https://wiki.corp.example", PROBE) is True
        assert states == []

    def test_times_out_and_returns_false(self, monkeypatch):
        clock = {"t": 0.0}
        monkeypatch.setattr(browser_session_manager.BrowserSession, "navigate",
                            lambda self, url: None)
        monkeypatch.setattr(browser_session_manager.BrowserSession, "is_authenticated",
                            lambda self, probe: False)
        monkeypatch.setattr(browser_session_manager.time, "sleep",
                            lambda s: clock.__setitem__("t", clock["t"] + 10))
        monkeypatch.setattr(browser_session_manager.time, "monotonic", lambda: clock["t"])
        assert _session().signin("https://wiki.corp.example", PROBE, timeout=30) is False


class TestEvalEncoding:
    def test_eval_uses_base64_and_never_stdin(self, monkeypatch):
        """Design §2: base64 IIFE, never --stdin (that path wedged the daemon)."""
        seen = {}

        def fake_run(task_id, command, args, **kw):
            seen["command"], seen["args"] = command, args
            return {"success": True, "data": {"result": 42}}

        monkeypatch.setattr(browser_session_manager, "_run_browser_command", fake_run)
        result = _session().eval("1 + 41")
        assert result["success"] is True
        assert result["result"] == 42
        assert "--stdin" not in seen["args"]
        assert any("atob" in str(a) for a in seen["args"])

    def test_eval_surfaces_failure(self, monkeypatch):
        monkeypatch.setattr(
            browser_session_manager, "_run_browser_command",
            lambda *a, **kw: {"success": False, "error": "JS exception"},
        )
        assert _session().eval("boom()") == {"success": False, "error": "JS exception"}

    def test_eval_payload_round_trips(self, monkeypatch):
        """The base64 payload must decode to the original IIFE-wrapped expression."""
        import base64 as _b64

        seen = {}
        monkeypatch.setattr(
            browser_session_manager, "_run_browser_command",
            lambda t, c, args, **kw: (seen.update(args=args),
                                      {"success": True, "data": {"result": None}})[1],
        )
        _session().eval("document.title")
        payload = seen["args"][0].split("atob('")[1].split("')")[0]
        assert _b64.b64decode(payload).decode() == "(() => (document.title))()"
