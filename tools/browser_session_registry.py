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
    whose browser executable actually resolves on this machine.

    Gates the browser tools' availability check: such a session drives the
    user's real installed browser over CDP and needs no bundled Chromium, so
    withholding every browser tool for a missing Chromium would be wrong.

    Requires the executable to resolve, not merely the toggle to be on, so we
    never advertise a tool that would hang until the command timeout on first
    use — the same reasoning the Termux branch applies. Fails CLOSED.
    """
    name = default_profile_name()
    if not name:
        return False
    try:
        from tools.browser_profiles import get_profile, resolve_executable

        profile = get_profile(name)
        if profile is None or not profile.is_enrolled:
            return False
        return bool(resolve_executable(profile))
    except Exception as exc:  # noqa: BLE001
        logger.debug("default_profile_launchable: reporting unavailable after error: %s", exc)
        return False


def session_trusts_url(session_key: str, url: str) -> bool:
    """Return True when ``session_key``'s profile explicitly trusts ``url``.

    Resolution order: an explicit ``bind()`` wins; otherwise
    ``browser.default_profile`` applies. An explicitly-bound ephemeral session
    therefore does NOT silently inherit enrolled trust.

    SECURITY: gates private-network access. Denies on any unexpected input —
    no profile at all, unknown profile, ephemeral profile, or unparseable URL.
    """
    name = profile_for(session_key) or default_profile_name()
    if not name:
        return False
    try:
        from tools.browser_profiles import get_profile, is_origin_trusted

        return is_origin_trusted(get_profile(name), url)
    except Exception as exc:  # noqa: BLE001
        logger.debug("session_trusts_url: denying after error: %s", exc)
        return False
