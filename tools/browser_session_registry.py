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
from typing import Dict, Optional

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


def session_trusts_url(session_key: str, url: str) -> bool:
    """Return True when ``session_key``'s profile explicitly trusts ``url``.

    SECURITY: gates private-network access. Denies on any unexpected input —
    unbound session, unknown profile, ephemeral profile, or unparseable URL.
    """
    name = profile_for(session_key)
    if not name:
        return False
    try:
        from tools.browser_profiles import get_profile, is_origin_trusted

        return is_origin_trusted(get_profile(name), url)
    except Exception as exc:  # noqa: BLE001
        logger.debug("session_trusts_url: denying after error: %s", exc)
        return False
