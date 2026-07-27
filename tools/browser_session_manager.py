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
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from tools import browser_profiles, browser_session_registry

logger = logging.getLogger(__name__)

# Bounded so a wedged daemon surfaces as an error instead of hanging the agent.
HYGIENE_TIMEOUT_S = 15
CDP_PROBE_TIMEOUT_S = 2

# Readiness polling after a launch: 30 x 0.5s ≈ 15s, matching the proven skill.
READY_POLL_ATTEMPTS = 30
READY_POLL_INTERVAL_S = 0.5

# ...but the ITERATION count alone is not a time bound. Each iteration also does
# a liveness probe and an identity probe, each of which can approach
# CDP_PROBE_TIMEOUT_S against a slow or stateful listener, so 30 iterations could
# run ~135s -- and the whole acquire happens under browser_tool's per-key lock,
# so a same-key caller waited all of it before its own 30s browser command even
# started (review finding MED-007). A wall-clock deadline caps the pathological
# case without shortening the intended ~15s of polling.
ENROLLED_READY_DEADLINE_S = 30

# The documented worst case for one acquisition, for operators and for the
# regression that keeps it honest: hygiene + the pre-launch identity probe pair
# + the readiness deadline.
ACQUIRE_WORST_CASE_S = (
    HYGIENE_TIMEOUT_S + (2 * CDP_PROBE_TIMEOUT_S) + ENROLLED_READY_DEADLINE_S
)


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


def _live_browser_session_keys() -> List[str]:
    """Return the session keys browser_tool currently has open. Best-effort."""
    from tools.browser_tool import _active_sessions, _cleanup_lock

    with _cleanup_lock:
        return list(_active_sessions)


# Acquisitions that have passed hygiene but have not yet bound their session.
# A process-global `close --all` must not run while one of these is launching a
# browser (review finding HIGH-003).
_inflight_lock = threading.Lock()
_inflight_acquires: Dict[str, int] = {}


def _reserve_acquire(session_key: str) -> List[str]:
    """Register an in-flight acquire; return the OTHER keys already in flight.

    Registering happens under the same lock that reads the others, so two
    concurrent acquires can never both observe an empty table and both decide
    to run process-global hygiene.
    """
    with _inflight_lock:
        others = [k for k in _inflight_acquires if k != session_key]
        _inflight_acquires[session_key] = _inflight_acquires.get(session_key, 0) + 1
        return others


def _release_acquire(session_key: str) -> None:
    """Drop one in-flight reservation. Idempotent-safe for the failure path."""
    with _inflight_lock:
        remaining = _inflight_acquires.get(session_key, 0) - 1
        if remaining > 0:
            _inflight_acquires[session_key] = remaining
        else:
            _inflight_acquires.pop(session_key, None)


def _run_daemon_hygiene() -> None:
    """Close every agent-browser session so a wedged daemon can't poison us.

    ``close --all`` is unscoped and CANNOT be narrowed to the session being
    acquired: at this point that session does not exist yet (acquire() runs
    before ``_get_or_create_session`` registers anything) and agent-browser
    session names are freshly-generated UUIDs, so there is no name to close.
    What CAN be scoped is WHEN it runs: if this process already has live browser
    sessions, ``close --all`` would tear down other tasks' in-flight browsers,
    which is strictly worse than skipping the hygiene (review finding H-2). With
    nothing live, the only thing ``close --all`` can reach is an orphaned or
    wedged daemon -- exactly what it is for.

    "Live" is THREE things, not one (review finding HIGH-003 -- the first
    remediation only checked the first, which left two windows open):

    1. ``browser_tool._active_sessions`` -- sessions the tool has published.
    2. ``browser_session_registry`` bindings -- a session that has ACQUIRED but
       whose caller has not published it yet. ``acquire()`` binds before
       returning and ``release()`` unbinds, so this covers the whole lifetime.
    3. Other in-flight acquires -- checked by ``acquire()`` BEFORE it calls
       this, because only it knows its own key. Nothing else can see those: the
       browser exists but no record of it does.

    Residual: a daemon that wedges WHILE another session is live is not cleared
    here. That surfaces as a bounded command failure on this acquire rather than
    as a silent teardown of someone else's session, which is the trade we want.
    Errors reading any table fail toward SKIPPING.
    """
    try:
        live = _live_browser_session_keys()
    except Exception as exc:  # noqa: BLE001
        logger.debug("daemon hygiene skipped (session table unreadable): %s", exc)
        return
    if live:
        logger.debug(
            "daemon hygiene skipped: %d live browser session(s) would be torn "
            "down by `close --all` (%s)", len(live), live,
        )
        return
    try:
        bound = browser_session_registry.bound_session_keys()
    except Exception as exc:  # noqa: BLE001
        logger.debug("daemon hygiene skipped (registry unreadable): %s", exc)
        return
    if bound:
        logger.debug(
            "daemon hygiene skipped: %d acquired session(s) still bound (%s)",
            len(bound), bound,
        )
        return
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


def _cdp_version_payload(cdp_url: str) -> Optional[Dict[str, Any]]:
    """Return the parsed ``/json/version`` document, or None.

    Fails CLOSED: connection error, timeout, non-JSON body, or a JSON body that
    is not an object all return None.
    """
    try:
        with urllib.request.urlopen(
            f"{cdp_url}/json/version", timeout=CDP_PROBE_TIMEOUT_S
        ) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) else None


def _cdp_browser_identity(cdp_url: str) -> Optional[str]:
    """Return the ``Browser`` string from /json/version, or None.

    LIVENESS + shape only. Every CDP endpoint answers with a non-empty
    ``Browser`` string, so this ALONE cannot tell the enrolled browser apart
    from Chrome for Testing, ``/browser connect``'s throwaway Chrome in
    ``$HERMES_HOME/chrome-debug``, or any other Chromium on the port. It is the
    cheap first gate; ``_endpoint_is_profile_browser`` is the one that
    establishes identity, and BOTH must pass before a listener is reused.

    Fails CLOSED: any connection error, timeout, non-JSON body, missing
    ``Browser`` key, or empty value returns None, which must lead to
    launching our own browser, never to reusing the unknown listener.
    """
    payload = _cdp_version_payload(cdp_url)
    if payload is None:
        return None
    browser = str(payload.get("Browser") or "").strip()
    return browser or None


# Chromium writes this file into its --user-data-dir as soon as the DevTools
# HTTP server is listening: line 1 is the port, line 2 the browser target path
# ("/devtools/browser/<uuid>"). It is removed on a clean exit.
_DEVTOOLS_PORT_FILE = "DevToolsActivePort"


def _devtools_active_target(user_data_dir: str) -> Optional[Tuple[int, str]]:
    """Return ``(port, ws_target_path)`` from a profile dir's DevToolsActivePort.

    Fails CLOSED: a missing/unreadable file, a short file, a non-numeric port,
    or a target path that is not absolute all return None.
    """
    try:
        raw = open(
            os.path.join(user_data_dir, _DEVTOOLS_PORT_FILE), encoding="utf-8"
        ).read()
    except OSError:
        return None
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    try:
        port = int(lines[0])
    except ValueError:
        return None
    target = lines[1]
    if not target.startswith("/"):
        return None
    return (port, target)


def _endpoint_is_profile_browser(
    profile: browser_profiles.BrowserProfile, cdp_url: str
) -> bool:
    """Return True only when ``cdp_url`` is the browser using THIS profile's dir.

    The identity proof (review finding H-1). A non-empty ``Browser`` string
    proves nothing -- every CDP endpoint has one -- so before this check, a
    ``/browser connect`` throwaway Chrome left running in
    ``$HERMES_HOME/chrome-debug``, a stray Chrome for Testing, or any other
    listener on the port was indistinguishable from the enrolled browser, and
    the ``::enrolled`` key's corporate origin trust would be bound to a browser
    with no SSO and no client certificate: a SILENT downgrade.

    The proof is anchored on the profile's own ``user_data_dir``, which only the
    enrolled browser ever uses: Chromium writes ``DevToolsActivePort`` there
    (port + ``/devtools/browser/<uuid>`` target) when its DevTools server comes
    up, and the running endpoint reports the same target in
    ``webSocketDebuggerUrl``. Matching the two proves the listener on this port
    is the browser holding this profile directory. It works across process
    restarts, so a browser a PREVIOUS run left holding the profile dir and port
    is still legitimately reusable -- the attach-or-launch behaviour this module
    is built on.

    Fails CLOSED on every unexpected input. A false negative costs a relaunch;
    a false positive costs the corporate session.
    """
    try:
        target = _devtools_active_target(
            browser_profiles.resolve_user_data_dir(profile)
        )
        if target is None:
            return False
        recorded_port, ws_path = target
        if recorded_port != int(profile.cdp_port):
            return False
        payload = _cdp_version_payload(cdp_url)
        if payload is None:
            return False
        ws_url = str(payload.get("webSocketDebuggerUrl") or "")
        if not ws_url:
            return False
        from urllib.parse import urlsplit

        return urlsplit(ws_url).path == ws_path
    except Exception as exc:  # noqa: BLE001
        logger.debug("endpoint identity check failed for %s: %s", cdp_url, exc)
        return False


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
    if identity and _endpoint_is_profile_browser(profile, cdp_url):
        logger.info(
            "browser profile %r: reusing CDP listener on %s (%s)",
            profile.name, cdp_url, identity,
        )
        return cdp_url
    if identity:
        logger.warning(
            "browser profile %r: a CDP endpoint (%s) is listening on %s but is NOT "
            "the browser holding this profile's user-data-dir; not reusing it",
            profile.name, identity, cdp_url,
        )

    executable = browser_profiles.resolve_executable(profile)
    if not executable:
        raise ProfileError(
            f"could not resolve the enrolled browser for profile {profile.name!r}. "
            "Set browser.profiles.<name>.executable to an absolute path."
        )

    user_data_dir = browser_profiles.resolve_user_data_dir(profile)
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

    # Bounded by BOTH the attempt count and a wall-clock deadline: slow probes
    # make the attempt count a poor proxy for elapsed time (MED-007).
    deadline = time.monotonic() + ENROLLED_READY_DEADLINE_S
    for _ in range(READY_POLL_ATTEMPTS):
        if time.monotonic() >= deadline:
            break
        time.sleep(READY_POLL_INTERVAL_S)
        # Identity is checked here too, not just on the reuse path: if a foreign
        # listener holds the port, our launch cannot bind it, yet _cdp_alive
        # would happily report the SQUATTER as "ready" and we would return its
        # endpoint as the enrolled browser.
        if _cdp_alive(cdp_url) and _endpoint_is_profile_browser(profile, cdp_url):
            return cdp_url

    raise ProfileError(
        f"browser for profile {profile.name!r} did not expose CDP on {cdp_url} within "
        f"{ENROLLED_READY_DEADLINE_S}s (if something else is listening on that port, "
        "give the profile its own cdp_port)"
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

    # Reserve BEFORE hygiene, and hold the reservation until the session is
    # bound: between those two points the browser may exist while no record of
    # it does, and another key's `close --all` would tear it down (HIGH-003).
    others_in_flight = _reserve_acquire(key)
    try:
        if others_in_flight:
            # Another acquire may be launching a browser right now, and
            # `close --all` is process-global. Skipping is strictly safer than
            # tearing down a browser nothing has recorded yet (HIGH-003).
            logger.debug(
                "daemon hygiene skipped: %d acquire(s) in flight (%s)",
                len(others_in_flight), others_in_flight,
            )
        else:
            _run_daemon_hygiene()

        cdp_url: Optional[str] = None
        if prof.is_enrolled:
            cdp_url = _ensure_enrolled_cdp(prof, effective_headless)
            if attach_global:
                _attach_cdp(cdp_url)

        browser_session_registry.bind(key, prof.name)
    finally:
        _release_acquire(key)
    return BrowserSession(session_key=key, profile=prof, cdp_url=cdp_url)
