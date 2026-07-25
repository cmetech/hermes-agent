"""Named browser profiles — a pure registry over ``browser.profiles`` config.

A *profile* is a named browser identity. Two kinds exist:

``ephemeral``
    Today's behaviour: agent-browser's bundled Chrome for Testing, disposable,
    no persistent user-data-dir. This is the ``default`` profile and is never
    origin-trusted.

``enrolled``
    A corporate-enrolled browser (OS cert store) launched with a persistent
    user-data-dir so it can hold a live SSO/mTLS session, attached over CDP.

This module is intentionally pure: it parses and validates config and resolves
executables. It launches nothing and holds no session state — that is
``tools.browser_session_manager``. Keeping it pure keeps it unit-testable
without a browser.

Design: docs/plans/2026-07-20-persistent-enrolled-browser-session-design.md
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_NAME = "default"

KIND_EPHEMERAL = "ephemeral"
KIND_ENROLLED = "enrolled"
_VALID_KINDS = frozenset({KIND_EPHEMERAL, KIND_ENROLLED})

DEFAULT_CDP_PORT = 9222

# Sentinel used while normalizing a ``*.`` wildcard pattern. Must be a valid
# hostname label so urlsplit still parses the pattern.
_WILDCARD_LABEL = "wildcard-placeholder"


@dataclass(frozen=True)
class BrowserProfile:
    """An immutable, validated browser identity."""

    name: str
    kind: str = KIND_EPHEMERAL
    executable: str = "auto"
    user_data_dir: str = ""
    cdp_port: int = DEFAULT_CDP_PORT
    trusted_origins: Tuple[str, ...] = field(default_factory=tuple)
    headed: bool = False

    @property
    def is_enrolled(self) -> bool:
        return self.kind == KIND_ENROLLED


def _read_config() -> Dict[str, Any]:
    """Read the raw config. Separate function so tests can monkeypatch it."""
    try:
        from hermes_cli.config import read_raw_config

        return read_raw_config() or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("browser_profiles: could not read config: %s", exc)
        return {}


def _builtin_default() -> BrowserProfile:
    """The always-present ephemeral profile — today's exact OOB behaviour."""
    return BrowserProfile(name=DEFAULT_PROFILE_NAME, kind=KIND_EPHEMERAL)


def _parse_one(name: str, raw: Any) -> Optional[BrowserProfile]:
    """Parse a single profile entry, returning None when unusable."""
    if not isinstance(raw, dict):
        logger.debug("browser_profiles: profile %r is not a mapping; ignoring", name)
        return None

    kind = str(raw.get("kind", KIND_EPHEMERAL)).strip().lower()
    if kind not in _VALID_KINDS:
        logger.debug("browser_profiles: profile %r has unknown kind %r; ignoring", name, kind)
        return None

    # Hard rule: an ephemeral profile is NEVER origin-trusted. A hostile
    # external page must not be able to reach private addresses just because
    # someone added trusted_origins to the disposable profile.
    origins: Tuple[str, ...] = ()
    if kind == KIND_ENROLLED:
        raw_origins = raw.get("trusted_origins") or []
        if isinstance(raw_origins, (list, tuple)):
            origins = tuple(str(o).strip() for o in raw_origins if str(o).strip())
        elif raw_origins:
            # Loud, because the failure is otherwise invisible: `hermes config
            # set` coerces only bool/int/float, so
            # `config set ...trusted_origins "https://a,https://b"` stores a
            # STRING. We refuse to trust it (fail closed), which would look
            # like "the profile just doesn't work" with nothing to debug.
            logger.warning(
                "browser profile %r: trusted_origins must be a LIST of origins, got %s "
                "— no origins are trusted. Use `hermes config edit` and write:\n"
                "  trusted_origins:\n    - \"https://host.example\"\n"
                "(`hermes config set` cannot create a list.)",
                name, type(raw_origins).__name__,
            )

    try:
        port = int(raw.get("cdp_port", DEFAULT_CDP_PORT))
    except (TypeError, ValueError):
        port = DEFAULT_CDP_PORT

    return BrowserProfile(
        name=name,
        kind=kind,
        executable=str(raw.get("executable", "auto") or "auto"),
        user_data_dir=str(raw.get("user_data_dir", "") or ""),
        cdp_port=port,
        trusted_origins=origins,
        headed=bool(raw.get("headed", False)),
    )


def load_profiles() -> Dict[str, BrowserProfile]:
    """Return all valid profiles, always including the builtin ``default``.

    Fail-open: any malformed input degrades to the builtin default so a bad
    config can never break ``/browser``.
    """
    profiles: Dict[str, BrowserProfile] = {DEFAULT_PROFILE_NAME: _builtin_default()}

    cfg = _read_config()
    browser_cfg = cfg.get("browser") if isinstance(cfg, dict) else None
    raw_profiles = browser_cfg.get("profiles") if isinstance(browser_cfg, dict) else None
    if not isinstance(raw_profiles, dict):
        return profiles

    for name, raw in raw_profiles.items():
        key = str(name).strip()
        if not key:
            continue
        parsed = _parse_one(key, raw)
        if parsed is None:
            continue
        if key == DEFAULT_PROFILE_NAME:
            # Config may tune the default profile but never grant it trust,
            # and never promote it to enrolled.
            parsed = BrowserProfile(
                name=DEFAULT_PROFILE_NAME,
                kind=KIND_EPHEMERAL,
                executable=parsed.executable,
                user_data_dir=parsed.user_data_dir,
                cdp_port=parsed.cdp_port,
                trusted_origins=(),
                headed=parsed.headed,
            )
        profiles[key] = parsed
    return profiles


def get_profile(name: str) -> Optional[BrowserProfile]:
    """Return a profile by name, or None when it does not exist."""
    return load_profiles().get(str(name).strip())


def _normalize_origin(url: str) -> Optional[Tuple[str, str, Optional[int]]]:
    """Return ``(scheme, hostname, port)`` for a URL, or None when unparseable.

    Uses ``urlsplit`` so userinfo spoofs (``https://good.example@evil.test``)
    resolve to the REAL host (``evil.test``) rather than the decoy.
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url.strip())
    except (ValueError, AttributeError):
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None  # malformed port → deny
    return (parts.scheme.lower(), parts.hostname.lower(), port)


def is_origin_trusted(profile: Optional[BrowserProfile], url: str) -> bool:
    """Return True when ``url``'s origin is explicitly trusted by ``profile``.

    SECURITY: this predicate gates private/internal network access, so every
    unexpected input must DENY. Only ``enrolled`` profiles can grant trust.

    A pattern is an origin string, optionally with a leading ``*.`` wildcard on
    the host: ``https://wiki.corp.example`` or ``https://*.corp.example``. The
    wildcard matches strict subdomains only — never the apex, never a
    suffix-lookalike (``evilcorp.example``). Scheme and port must match exactly.
    """
    if profile is None or not profile.is_enrolled or not profile.trusted_origins:
        return False

    target = _normalize_origin(url)
    if target is None:
        return False
    scheme, host, port = target

    for pattern in profile.trusted_origins:
        allowed = _normalize_origin(pattern.replace("*.", f"{_WILDCARD_LABEL}.", 1))
        if allowed is None:
            continue
        a_scheme, a_host, a_port = allowed
        if a_scheme != scheme or a_port != port:
            continue
        if a_host.startswith(f"{_WILDCARD_LABEL}."):
            suffix = a_host[len(_WILDCARD_LABEL):]  # ".corp.example"
            # Strict subdomain: must END with ".corp.example" AND have at least
            # one label before it. Excludes the apex ("corp.example") and
            # suffix-lookalikes ("evilcorp.example").
            if host.endswith(suffix) and len(host) > len(suffix):
                return True
            continue
        if a_host == host:
            return True
    return False


def _enrolled_candidates() -> List[str]:
    """Return likely enrolled-browser executable paths for this platform.

    Edge is listed first: on a managed corporate device it is the browser
    enrolled with the OS certificate store, which is what makes mTLS work.
    We deliberately do NOT include agent-browser's bundled Chrome for Testing —
    it is the UNMANAGED browser that fails corporate mTLS/SSO.
    """
    if sys.platform == "win32":
        program_files = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        program_files64 = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        return [
            os.path.join(program_files, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(program_files64, "Microsoft", "Edge", "Application", "msedge.exe"),
        ]
    if sys.platform == "darwin":
        return [
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    return [
        "/usr/bin/microsoft-edge",
        "/usr/bin/microsoft-edge-stable",
        "/usr/bin/google-chrome",
    ]


def resolve_executable(profile: BrowserProfile) -> Optional[str]:
    """Return the enrolled browser executable for ``profile``, or None.

    Ephemeral profiles always return None — they use agent-browser's bundled
    Chrome for Testing via the normal ``--session`` path.
    """
    if not profile.is_enrolled:
        return None

    configured = (profile.executable or "auto").strip()
    if configured and configured.lower() != "auto":
        return configured if os.path.exists(configured) else None

    for candidate in _enrolled_candidates():
        if os.path.exists(candidate):
            return candidate
    return None
