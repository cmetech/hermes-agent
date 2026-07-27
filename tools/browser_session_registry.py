"""Session → profile bindings and the origin-scoped trust decision.

Lives outside ``browser_tool.py`` on purpose: this module holds the mutable
session state so the footprint inside that large shared upstream file stays a
single delegating helper plus one-line guards at each private-URL check. Every
line added there is a line a future upstream merge can silently revert (see the
design's §6a), so we keep it minimal.

Trust is evaluated per ``(session_key, url)``. There is no blanket session
exemption: binding a session to an enrolled profile grants access ONLY to the
origins that profile explicitly lists.

Design: docs/plans/2026-07-20-persistent-enrolled-browser-session-design.md
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_session_profiles: Dict[str, str] = {}  # session_key -> profile name


def bind(session_key: str, profile_name: str) -> None:
    """Record that ``session_key`` is driving ``profile_name``."""
    with _lock:
        _session_profiles[str(session_key)] = str(profile_name)


def unbind(session_key: str) -> None:
    """Forget a session's profile binding (call on release)."""
    with _lock:
        _session_profiles.pop(str(session_key), None)


def profile_for(session_key: str) -> Optional[str]:
    """Return the profile name bound to ``session_key``, or None."""
    with _lock:
        return _session_profiles.get(str(session_key))


def bound_session_keys() -> list:
    """Return every session key currently bound to a profile.

    Daemon hygiene consults this. ``acquire()`` binds here BEFORE its caller
    publishes into ``browser_tool._active_sessions``, and ``release()`` unbinds,
    so a binding covers the whole life of a session -- including the window
    where a browser has launched but nothing has been published yet, which is
    where a second acquire's process-global ``close --all`` used to tear it down
    (review finding HIGH-003).
    """
    with _lock:
        return list(_session_profiles)


def clear() -> None:
    """Drop all bindings. Test helper; also usable on interpreter teardown."""
    with _lock:
        _session_profiles.clear()


def _read_config() -> Dict[str, Any]:
    """Read raw config. Separate function so tests can monkeypatch it."""
    try:
        from hermes_cli.config import read_raw_config

        return read_raw_config() or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("browser_session_registry: could not read config: %s", exc)
        return {}


def default_profile_name() -> Optional[str]:
    """Return ``browser.default_profile``, or None when unset.

    This is what lets the AGENT's own browser tools reach enterprise pages: the
    agent keys sessions on its bare ``task_id``, which nothing binds explicitly.
    Rather than hooking session creation inside the heavily-churned
    ``_get_session_info``, an unbound session falls back to this config value —
    keeping all the new logic in this module.

    Trust stays origin-scoped: the named profile's ``trusted_origins`` still
    decide which hosts are reachable, so this grants enterprise access without
    opening private addresses at large.
    """
    cfg = _read_config()
    browser_cfg = cfg.get("browser") if isinstance(cfg, dict) else None
    if not isinstance(browser_cfg, dict):
        return None
    name = str(browser_cfg.get("default_profile", "") or "").strip()
    return name or None


def default_profile_trusts_url(url: str) -> bool:
    """Return True when ``browser.default_profile`` is an enrolled profile that
    explicitly trusts ``url``'s origin.

    This is the ROUTING question -- "should this navigation use the real
    installed browser?" -- and is deliberately separate from
    ``session_trusts_url``, which answers the GUARD question for an existing
    session. Both consult the same ``is_origin_trusted`` matcher, so routing and
    trust can never disagree about an origin. Fails CLOSED.
    """
    name = default_profile_name()
    if not name:
        return False
    try:
        from tools.browser_profiles import get_profile, is_origin_trusted

        return is_origin_trusted(get_profile(name), url)
    except Exception as exc:  # noqa: BLE001
        logger.debug("default_profile_trusts_url: denying after error: %s", exc)
        return False


def default_profile_launchable() -> bool:
    """Return True when ``browser.default_profile`` names an ENROLLED profile
    whose browser executable actually resolves on this machine, AND whose
    ``user_data_dir``/``cdp_port`` are usable.

    Gates the browser tools' availability check: such a session drives the
    user's real installed browser over CDP and needs no bundled Chromium, so
    withholding every browser tool for a missing Chromium would be wrong.

    Validates the whole launch chain, not merely the toggle, so we never
    advertise a tool that would fail (PermissionError on a non-executable
    file, or hang until the command timeout) on first use — the same
    reasoning the Termux branch applies (review finding EBL-006). Fails
    CLOSED.
    """
    name = default_profile_name()
    if not name:
        return False
    try:
        from tools.browser_profiles import (
            data_dir_problem,
            get_profile,
            resolve_executable,
            resolve_user_data_dir,
        )

        profile = get_profile(name)
        if profile is None or not profile.is_enrolled:
            return False
        if not resolve_executable(profile):
            return False
        # A launch that cannot create its user-data-dir, or whose port is out of
        # range, fails on every acquire -- do not advertise the tools for it.
        # resolve_user_data_dir, not expandvars: HERMES_HOME is unset on the CLI
        # and `hermes serve` paths, where expandvars leaves the literal
        # "${HERMES_HOME}/..." in place -- non-empty, so this check passed while
        # the directory it described was CWD-relative (review finding M-3).
        # data_dir_problem, not os.path.isabs: an absolute string can still be a
        # path under a regular file, which passes an isabs check and then fails
        # at first acquire with NotADirectoryError (review finding MED-005).
        data_dir = resolve_user_data_dir(profile)
        problem = data_dir_problem(data_dir)
        if problem:
            logger.debug(
                "browser profile %r is not launchable: user_data_dir %s %s",
                name, data_dir, problem,
            )
            return False
        if not (1 <= int(profile.cdp_port) <= 65535):
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("default_profile_launchable: reporting unavailable after error: %s", exc)
        return False


def session_trusts_url(session_key: str, url: str) -> bool:
    """Return True when ``session_key``'s profile explicitly trusts ``url``.

    Resolution order: an explicit ``bind()`` wins; otherwise
    ``browser.default_profile`` applies ONLY to a key that per-navigation
    routing created for a trusted origin (suffixed ``::enrolled``). A bare
    (ephemeral) key inherits nothing -- routing already sends any origin the
    default profile trusts to the enrolled key, so the ephemeral session has
    no reason to reach internal origins. An explicitly-bound ephemeral
    session therefore does NOT silently inherit enrolled trust either.

    SECURITY: gates private-network access. Denies on any unexpected input —
    no profile at all, unknown profile, ephemeral profile, or unparseable URL.
    """
    name = profile_for(session_key)
    if not name:
        # The default profile applies only to a key routing created for a
        # trusted origin. A bare (ephemeral) key inherits nothing.
        if not str(session_key).endswith("::enrolled"):
            return False
        name = default_profile_name()
    if not name:
        return False
    try:
        from tools.browser_profiles import get_profile, is_origin_trusted

        return is_origin_trusted(get_profile(name), url)
    except Exception as exc:  # noqa: BLE001
        logger.debug("session_trusts_url: denying after error: %s", exc)
        return False
