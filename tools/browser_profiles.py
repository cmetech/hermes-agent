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

import functools
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

# NOT 9222. 9222 is the port `/browser connect` defaults to and auto-launches a
# throwaway debug Chrome on (hermes_cli.browser_connect.DEFAULT_BROWSER_CDP_PORT),
# and it is the conventional port for every ad-hoc `--remote-debugging-port`
# recipe on the internet. An enrolled profile sharing it means `/browser connect`
# discovers the user's REAL corporate browser, sets the process-global
# BROWSER_CDP_URL, and from then on every navigation -- including attacker-
# controlled public pages -- is driven by the browser holding live SSO cookies
# and the machine client certificate. Keep the enrolled default on its own port.
# `hermes_cli.browser_connect` refuses to connect to any port in
# ``enrolled_cdp_ports()``, which always contains this value.
DEFAULT_CDP_PORT = 9333

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
    used_ports: Dict[int, str] = {}

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
            # and never promote it to enrolled. Do this BEFORE the port-
            # collision check below: the reserved default profile is always
            # ephemeral and never launches a browser on cdp_port, so it must
            # never participate in (or be dropped by) that check.
            parsed = BrowserProfile(
                name=DEFAULT_PROFILE_NAME,
                kind=KIND_EPHEMERAL,
                executable=parsed.executable,
                user_data_dir=parsed.user_data_dir,
                cdp_port=parsed.cdp_port,
                trusted_origins=(),
                headed=parsed.headed,
            )
        if parsed.is_enrolled:
            if parsed.cdp_port in used_ports:
                logger.warning(
                    "browser profile %r reuses cdp_port %d (already used by %r); "
                    "ignoring it. Two profiles on one port make one profile's "
                    "trust apply to the other's browser. Give each a unique port.",
                    key, parsed.cdp_port, used_ports[parsed.cdp_port],
                )
                continue
            used_ports[parsed.cdp_port] = key
        profiles[key] = parsed
    return profiles


def get_profile(name: str) -> Optional[BrowserProfile]:
    """Return a profile by name, or None when it does not exist."""
    return load_profiles().get(str(name).strip())


def enrolled_cdp_ports() -> Dict[int, str]:
    """Return ``{port: profile_name}`` for every port an enrolled browser owns.

    ``DEFAULT_CDP_PORT`` is ALWAYS present (mapped to ``""``) even when no
    enrolled profile is configured yet: the port is reserved for the enrolled
    browser by construction, and a debug browser parked there would later be
    reused -- and origin-trusted -- by the enrolled profile the first time the
    user activates it.

    Used by ``/browser connect`` to refuse pointing the process-global
    ``BROWSER_CDP_URL`` at the user's real corporate browser. Fail-open on a
    config error (the reserved default is still returned), so a broken config
    cannot break ``/browser connect`` entirely.
    """
    ports: Dict[int, str] = {DEFAULT_CDP_PORT: ""}
    try:
        for name, profile in load_profiles().items():
            if profile.is_enrolled:
                ports[profile.cdp_port] = name
    except Exception as exc:  # noqa: BLE001
        logger.debug("browser_profiles: could not enumerate enrolled ports: %s", exc)
    return ports


_LOCAL_HOST_LITERALS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "::"})


@functools.lru_cache(maxsize=256)
def _resolves_to_loopback(hostname: str) -> bool:
    """Cached DNS check. Only reached for a non-literal host on a RESERVED port.

    ``enrolled_endpoint_owner`` checks the port first, so this is rare -- but
    when it does apply it sits on the per-navigation path, where an uncached
    blocking ``getaddrinfo`` would cost every browser action. A hosts-file edit
    therefore needs a process restart to be seen, which is an acceptable trade
    for a check whose literal-loopback cases never reach here at all.
    """
    import socket

    try:
        addr_info = socket.getaddrinfo(
            hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
    except OSError:
        return False

    import ipaddress

    for *_, sockaddr in addr_info:
        try:
            if ipaddress.ip_address(sockaddr[0]).is_loopback:
                return True
        except ValueError:
            continue
    return False


def is_local_host(host: Optional[str]) -> bool:
    """Return True when ``host`` names a listener on THIS machine.

    ``None``/empty means the caller could not determine a host (e.g. the
    free-port search, which is always about a local bind), so it reads as LOCAL
    -- the fail-closed direction for the enrolled-port refusal.

    A non-literal hostname is resolved, because ``corp-browser.internal`` in
    ``/etc/hosts`` pointing at ``127.0.0.1`` is still the local enrolled
    listener. DNS failure reads as NOT local: a remote CDP endpoint whose name
    does not resolve here must fail with its own connection error, not with a
    confusing enrolled-port refusal (review finding MED-006).
    """
    if host is None:
        return True
    normalized = host.strip().lower().strip("[]")
    if not normalized:
        return True
    if normalized in _LOCAL_HOST_LITERALS or normalized.endswith(".localhost"):
        return True

    import ipaddress

    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        pass  # a real hostname — resolve it below

    return _resolves_to_loopback(normalized)


def enrolled_endpoint_owner(url: str) -> Optional[str]:
    """Return the enrolled profile owning the LOCAL CDP listener ``url`` names.

    Returns the profile name (``""`` for the reserved default port, which is
    owned by construction even with no profile configured), or ``None`` when
    ``url`` cannot be the local enrolled browser.

    This is the ONE classifier for "does this endpoint reach the corporate
    browser?". Every surface that can point the agent at a CDP endpoint --
    ``/browser connect``, the ``browser.manage`` RPC, ``BROWSER_CDP_URL``, and
    ``browser.cdp_url`` -- must consult it, or the authority boundary has a
    second door (review findings CRIT-001, MED-006).

    Fails CLOSED for a malformed URL on a reserved port; fails OPEN (``None``)
    only when the URL names no port we reserve.
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url.strip() if "://" in url else f"http://{url.strip()}")
    except (ValueError, AttributeError):
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None:
        port = 443 if parts.scheme in {"https", "wss"} else 80
    owners = enrolled_cdp_ports()
    if port not in owners:
        return None
    if not is_local_host(parts.hostname):
        return None
    return owners[port]


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


_HERMES_HOME_TOKENS = ("${HERMES_HOME}", "$HERMES_HOME", "%HERMES_HOME%")


def resolve_user_data_dir(profile: BrowserProfile) -> str:
    """Return ``profile``'s persistent profile directory as an ABSOLUTE path.

    ``${HERMES_HOME}`` is expanded through ``hermes_constants.get_hermes_home()``
    rather than ``os.path.expandvars``. ``HERMES_HOME`` is only exported into
    ``os.environ`` on the ``--profile`` override path: on the plain CLI and on
    ``hermes serve`` it is unset, so ``expandvars`` returns the literal
    ``${HERMES_HOME}/browser-profiles/enrolled`` and ``os.makedirs`` creates that
    directory RELATIVE TO THE CWD. The persistent SSO profile this whole feature
    exists for would then live in whatever directory the agent happened to be in
    and would not survive a CWD change (review finding M-3).

    Going through the resolver is also strictly more correct than the env var:
    it honours the context-local home override, which ``expandvars`` cannot see.

    An empty ``user_data_dir`` falls back to ``<home>/browser-profiles/<name>``
    so a hand-written profile without one still gets a persistent directory
    instead of failing in ``os.makedirs("")``.

    A configured RELATIVE path is anchored under the Hermes home, not the CWD.
    ``os.path.abspath`` made such a value syntactically absolute while still
    resolving differently under the classic CLI, ``hermes serve``, Desktop, and
    any launcher with a different working directory -- so the persistent SSO
    profile this feature exists for would silently split into several
    directories (review finding MED-005). Absolute values are untouched.
    """
    from hermes_constants import get_hermes_home

    home = str(get_hermes_home())
    raw = (profile.user_data_dir or "").strip()
    if not raw:
        return os.path.abspath(os.path.join(home, "browser-profiles", profile.name))
    for token in _HERMES_HOME_TOKENS:
        raw = raw.replace(token, home)
    expanded = os.path.expandvars(os.path.expanduser(raw))
    if not os.path.isabs(expanded):
        expanded = os.path.join(home, expanded)
    return os.path.abspath(expanded)


def data_dir_problem(path: str) -> Optional[str]:
    """Return why ``path`` cannot serve as a profile directory, or None.

    Proving the string is absolute says nothing about whether ``os.makedirs``
    can succeed: a path under a REGULAR FILE, or an existing target that is a
    file, passes that test and then fails deterministically at first acquire
    with ``NotADirectoryError`` (review finding MED-005). We check what the
    filesystem actually says, walking up to the nearest existing ancestor.

    The filesystem can still change between this check and the launch, so
    acquire must stay loud; this only stops us ADVERTISING a profile that
    cannot work.
    """
    if not path or not os.path.isabs(path):
        return "is not an absolute path"
    if os.path.exists(path):
        if not os.path.isdir(path):
            return "exists but is not a directory"
        if not os.access(path, os.W_OK | os.X_OK):
            return "is not writable"
        return None
    ancestor = os.path.dirname(path)
    seen = set()
    while ancestor and ancestor not in seen and not os.path.exists(ancestor):
        seen.add(ancestor)
        ancestor = os.path.dirname(ancestor)
    if not ancestor or not os.path.exists(ancestor):
        return "has no existing parent directory"
    if not os.path.isdir(ancestor):
        return f"cannot be created: {ancestor} is not a directory"
    if not os.access(ancestor, os.W_OK | os.X_OK):
        return f"cannot be created: {ancestor} is not writable"
    return None


def _enrolled_candidates() -> List[str]:
    """Return likely enrolled-browser executable paths for this platform.

    Chrome is listed first, Edge second. Both are policy-managed on a corporate
    device and both read the OS certificate store, which is what makes mTLS
    work; confirmed on the target fleet 2026-07-26 that Chrome reaches internal
    sites, so it is the one the user actually browses with.

    We deliberately do NOT include agent-browser's bundled Chrome for Testing —
    a different binary, not merely a different profile, and the one browser that
    cannot present machine certificates. That exclusion is the point of this
    list, not an incidental detail.
    """
    if sys.platform == "win32":
        program_files = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        program_files64 = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        # Per-user installs are common where the fleet blocks machine-wide ones.
        local_app_data = os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local")
        return [
            os.path.join(program_files64, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(program_files, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(local_app_data, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(program_files, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(program_files64, "Microsoft", "Edge", "Application", "msedge.exe"),
        ]
    if sys.platform == "darwin":
        return [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    return [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/opt/google/chrome/chrome",
        "/usr/bin/microsoft-edge",
        "/usr/bin/microsoft-edge-stable",
    ]


def _is_runnable(path: str) -> bool:
    """True when ``path`` is a regular file we could actually execute.

    ``os.path.exists`` alone accepts a directory or a mode-0644 file, which then
    passes the availability gate and fails at first launch with PermissionError
    (review finding EBL-006). Windows has no execute bit, so the X_OK check is
    POSIX-only.
    """
    if not os.path.isfile(path):
        return False
    if sys.platform == "win32":
        return True
    return os.access(path, os.X_OK)


def resolve_executable(profile: BrowserProfile) -> Optional[str]:
    """Return the enrolled browser executable for ``profile``, or None.

    Ephemeral profiles always return None — they use agent-browser's bundled
    Chrome for Testing via the normal ``--session`` path.
    """
    if not profile.is_enrolled:
        return None

    configured = (profile.executable or "auto").strip()
    if configured and configured.lower() != "auto":
        return configured if _is_runnable(configured) else None

    for candidate in _enrolled_candidates():
        if _is_runnable(candidate):
            return candidate
    return None
