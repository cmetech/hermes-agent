"""Acquire/release browser sessions for a named profile.

The importable seam (design §3): a workflow ``script`` node, the confluence CLI,
and the agent's own ``/browser`` tools all drive this same code, so there is one
consistent way to manage browsers.

The enrolled launch path is PORTED from the confluence-research skill's
``ensure_edge_cdp`` (``skills/ericsson/confluence-research/scripts/backends.py``),
which was field-verified against corporate Edge on 2026-07-19 — CF Access + SSO
+ mTLS all carried. Three behaviours from there are load-bearing and must not be
"simplified" away:

1. **Attach-or-launch** — probe CDP first and reuse a browser that is already
   listening (the user may have opened Edge themselves, or a prior run's browser
   may still hold the profile directory and port).
2. **Detached spawn** — else the browser dies with the parent or holds its
   console.
3. **Readiness wait** — returning before CDP answers races the caller.

Daemon hygiene is likewise a first-class requirement, not an afterthought
(design §2): agent-browser's client-daemon wedged on Windows during testing
(``eval`` hung until a full restart), so ``acquire()`` always runs ``close --all``
first and every subprocess call is bounded.

Design: docs/plans/2026-07-20-persistent-enrolled-browser-session-design.md
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional

from tools import browser_profiles, browser_session_registry

logger = logging.getLogger(__name__)

# Bounded so a wedged daemon surfaces as an error instead of hanging the agent.
HYGIENE_TIMEOUT_S = 15
CDP_PROBE_TIMEOUT_S = 2

# Readiness polling after a launch: 30 x 0.5s ≈ 15s, matching the proven skill.
READY_POLL_ATTEMPTS = 30
READY_POLL_INTERVAL_S = 0.5


class ProfileError(RuntimeError):
    """Raised when a profile is unknown or cannot be launched."""


def _agent_browser_cmd() -> List[str]:
    """Return the agent-browser CLI invocation, reusing browser_tool's resolver.

    ``_find_agent_browser()`` raises FileNotFoundError when the CLI is missing.
    The ``npx agent-browser`` special case mirrors browser_tool's own idiom: on
    Windows npx is ``npx.cmd``, so ``shutil.which`` is required for
    CreateProcessW to execute the batch shim.
    """
    from tools.browser_tool import _find_agent_browser

    browser_cmd = _find_agent_browser()
    if browser_cmd == "npx agent-browser":
        return [shutil.which("npx") or "npx", "agent-browser"]
    return [browser_cmd]


def _run_daemon_hygiene() -> None:
    """Close every agent-browser session so a wedged daemon can't poison us."""
    try:
        subprocess.run(
            _agent_browser_cmd() + ["close", "--all"],
            capture_output=True, timeout=HYGIENE_TIMEOUT_S, check=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("daemon hygiene skipped: %s", exc)


def _cdp_url_for(profile: browser_profiles.BrowserProfile) -> str:
    return f"http://127.0.0.1:{profile.cdp_port}"


def _cdp_alive(cdp_url: str) -> bool:
    """Return True when something is already serving CDP at ``cdp_url``.

    Liveness only -- no identity check. Used for the POST-launch readiness
    poll, where we just spawned the browser ourselves and identity is already
    known; do not use this to decide whether to reuse a PRE-existing listener.
    """
    try:
        urllib.request.urlopen(f"{cdp_url}/json/version", timeout=CDP_PROBE_TIMEOUT_S)
        return True
    except Exception:  # noqa: BLE001
        return False


def _cdp_browser_identity(cdp_url: str) -> Optional[str]:
    """Return the ``Browser`` string from /json/version, or None.

    Reusing whatever answers on the port lets an unrelated listener -- or
    another profile's browser -- inherit this profile's trust (EBL-004).
    Fails CLOSED: any connection error, timeout, non-JSON body, missing
    ``Browser`` key, or empty value returns None, which must lead to
    launching our own browser, never to reusing the unknown listener.
    """
    try:
        with urllib.request.urlopen(
            f"{cdp_url}/json/version", timeout=CDP_PROBE_TIMEOUT_S
        ) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        browser = str(payload.get("Browser") or "").strip()
        return browser or None
    except Exception:  # noqa: BLE001
        return None


def _spawn_browser(executable: str, args: List[str]) -> None:
    """Spawn the browser DETACHED so it outlives us and holds no console."""
    kwargs: Dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [executable, *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


def _ensure_enrolled_cdp(profile: browser_profiles.BrowserProfile, headless: bool) -> str:
    """Attach to, or launch, the enrolled browser. Returns its CDP URL.

    Uses the OS-installed browser (cert store → working mTLS), NEVER
    agent-browser's bundled Chrome for Testing.
    """
    cdp_url = _cdp_url_for(profile)

    identity = _cdp_browser_identity(cdp_url)
    if identity:
        logger.info(
            "browser profile %r: reusing CDP listener on %s (%s)",
            profile.name, cdp_url, identity,
        )
        return cdp_url

    executable = browser_profiles.resolve_executable(profile)
    if not executable:
        raise ProfileError(
            f"could not resolve the enrolled browser for profile {profile.name!r}. "
            "Set browser.profiles.<name>.executable to an absolute path."
        )

    user_data_dir = os.path.expandvars(profile.user_data_dir)
    os.makedirs(user_data_dir, exist_ok=True)

    args = [
        f"--remote-debugging-port={profile.cdp_port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        args.append("--headless=new")

    logger.info(
        "browser profile %r: launching %s (%s)",
        profile.name, os.path.basename(executable), "headless" if headless else "visible",
    )
    _spawn_browser(executable, args)

    for _ in range(READY_POLL_ATTEMPTS):
        time.sleep(READY_POLL_INTERVAL_S)
        if _cdp_alive(cdp_url):
            return cdp_url

    raise ProfileError(
        f"browser for profile {profile.name!r} did not expose CDP on {cdp_url} in time"
    )


def _attach_cdp(cdp_url: str) -> None:
    """Point the browser tool at ``cdp_url`` for subsequent commands."""
    os.environ["BROWSER_CDP_URL"] = cdp_url


def _run_browser_command(task_id, command, args, **kwargs):
    """Indirection over browser_tool's command runner (patchable in tests)."""
    from tools.browser_tool import _run_browser_command as _run

    return _run(task_id, command, args, **kwargs)


class BrowserSession:
    """A live session bound to one profile. Profile is fixed at acquire time.

    The hard isolation rule (design §5): an untrusted external site must never be
    driven through an enrolled profile, so there is deliberately no API to change
    ``profile`` after construction.
    """

    def __init__(self, session_key: str, profile: browser_profiles.BrowserProfile,
                 cdp_url: Optional[str]):
        self.session_key = session_key
        self.profile = profile
        self.cdp_url = cdp_url
        self._released = False

    def __enter__(self) -> "BrowserSession":
        return self

    def __exit__(self, *_exc) -> None:
        self.release()

    def release(self) -> None:
        """Detach. The on-disk profile persists so SSO survives. Idempotent."""
        if self._released:
            return
        self._released = True
        browser_session_registry.unbind(self.session_key)

    # ── page operations ──────────────────────────────────────────────────────

    def navigate(self, url: str) -> dict:
        """Navigate this session's browser to ``url``."""
        return _run_browser_command(self.session_key, "navigate", [url])

    def eval(self, expression: str) -> dict:
        """Evaluate JS in the page and return ``{"success", "result"|"error"}``.

        The expression is wrapped in a base64-encoded IIFE and passed as an
        ARGUMENT — never over ``--stdin``. Design §2: the stdin path is what
        wedged agent-browser's daemon on Windows.
        """
        payload = base64.b64encode(f"(() => ({expression}))()".encode()).decode()
        wrapped = f"eval(atob('{payload}'))"
        result = _run_browser_command(self.session_key, "eval", [wrapped])
        if not result.get("success"):
            return {"success": False, "error": result.get("error", "eval failed")}
        return {"success": True, "result": result.get("data", {}).get("result")}

    def is_authenticated(self, probe_js: str) -> bool:
        """Return True only when ``probe_js`` evaluates to boolean ``True``.

        Fails CLOSED: an eval error, a missing result, or a truthy non-boolean
        (e.g. the string ``"false"``) all read as NOT authenticated.
        """
        result = self.eval(probe_js)
        return result.get("success") is True and result.get("result") is True

    def signin(self, url: str, probe_js: str, timeout: int = 300,
               poll_s: float = 2.0) -> bool:
        """Navigate to ``url`` and wait for the user to complete sign-in.

        Returns True as soon as ``probe_js`` reports an authenticated session, or
        False on timeout. Cookies land in the profile's persistent user-data-dir,
        so a later headless acquire can reuse the session.

        NOTE (design §8 open question): enrolled-browser headless reuse vs
        Conditional Access re-checks and cookie lifetime are UNMEASURED. Validate
        before relying on this unattended.
        """
        self.navigate(url)
        if self.is_authenticated(probe_js):
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(poll_s)
            if self.is_authenticated(probe_js):
                return True
        return False


def acquire(profile: str = browser_profiles.DEFAULT_PROFILE_NAME,
            headless: Optional[bool] = None,
            session_key: Optional[str] = None,
            attach_global: bool = True) -> BrowserSession:
    """Acquire a session for ``profile``.

    ``headless`` defaults to the inverse of the profile's ``headed`` flag.
    ``session_key`` lets a caller bind the session under its own key — the agent
    passes its ``task_id`` so the trust seam in ``browser_tool`` matches.

    ``attach_global`` controls whether the resolved endpoint is exported to the
    process-global ``BROWSER_CDP_URL``. Scripted callers default to True and rely
    on it. The AGENT passes False: a process-global endpoint cannot model
    concurrent per-task state, and an explicitly ephemeral task would otherwise
    read it back through ``_get_cdp_override()`` and drive the corporate browser
    (review finding EBL-001).

    Raises ``ProfileError`` for an unknown profile, an unresolvable enrolled
    browser, or a browser that never exposes CDP. Never silently falls back to
    the unmanaged bundled Chrome, which would fail corporate mTLS confusingly.
    """
    prof = browser_profiles.get_profile(profile)
    if prof is None:
        raise ProfileError(f"unknown browser profile: {profile!r}")

    key = session_key or f"profile::{prof.name}"
    effective_headless = (not prof.headed) if headless is None else bool(headless)

    _run_daemon_hygiene()

    cdp_url: Optional[str] = None
    if prof.is_enrolled:
        cdp_url = _ensure_enrolled_cdp(prof, effective_headless)
        if attach_global:
            _attach_cdp(cdp_url)

    browser_session_registry.bind(key, prof.name)
    return BrowserSession(session_key=key, profile=prof, cdp_url=cdp_url)
