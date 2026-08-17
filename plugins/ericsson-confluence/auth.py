"""Resolve Hermes' opaque per-profile configuration into safe Confluence auth.

Origin validation is ported from ericsson-jira/auth.py. API-base derivation
is ported from skills/ericsson/confluence-research/scripts/confluence_api.py,
which handles the Cloud-versus-Data-Center split that super-cli does not:
Cloud serves the REST API under /wiki/rest/api, Server/DC under /rest/api.
"""

from __future__ import annotations

from urllib.parse import urlsplit

if __package__:
    from .models import ConfluenceAuth, ConfluenceError
else:
    from models import ConfluenceAuth, ConfluenceError

_MAX_ORIGIN = 2048
_MAX_SECRET = 4096


def _setting(configuration, field_id: str, default):
    try:
        value = configuration.setting(field_id)
    except Exception:
        return default
    return default if value is None else value


def _secret(configuration, field_id: str) -> str:
    try:
        value = configuration.secret(field_id)
    except Exception:
        return ""
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > _MAX_SECRET:
        raise ConfluenceError("invalid_configuration")
    return value.strip()


def derive_api_base(url: str, override: str | None = None) -> str:
    """Cloud lives under /wiki/rest/api; Server/DC under /rest/api."""
    if override:
        return override.rstrip("/")
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    path = parts.path.rstrip("/")
    if "/wiki/" in parts.path or path.endswith("/wiki"):
        return f"{origin}/wiki/rest/api"
    return f"{origin}/rest/api"


def _origin(value) -> str:
    """Validate scheme + host (+ optional /wiki path) and nothing else.

    A path segment is allowed here where the Jira connector forbids one,
    because Confluence Cloud legitimately lives at <site>/wiki.
    """
    if not isinstance(value, str):
        raise ConfluenceError("invalid_configuration")
    value = value.strip().rstrip("/")
    if (
        not value
        or len(value) > _MAX_ORIGIN
        or "\\" in value
        or any(character.isspace() for character in value)
    ):
        raise ConfluenceError("invalid_configuration")
    if "://" not in value:
        value = f"https://{value}"
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ConfluenceError("invalid_configuration") from None
    path = parsed.path.rstrip("/")
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and not 0 < port < 65536)
        # Only an empty path or a /wiki mount point is meaningful.
        or path not in {"", "/wiki"}
    ):
        raise ConfluenceError("invalid_configuration")
    return value


def _bounded_integer(value, minimum: int, maximum: int) -> int:
    # type(...) is not int, not isinstance: bool subclasses int, so True
    # would otherwise satisfy a range check.
    if type(value) is not int or not minimum <= value <= maximum:
        raise ConfluenceError("invalid_configuration")
    return value


def authentication_from_configuration(configuration) -> ConfluenceAuth:
    """Build one redacted, validated runtime identity for a Confluence call."""
    origin = _origin(_setting(configuration, "base_url", None))
    override = _setting(configuration, "api_base_override", None)
    if override is not None and not isinstance(override, str):
        raise ConfluenceError("invalid_configuration")
    timeout = _bounded_integer(
        _setting(configuration, "request_timeout_seconds", 30), 1, 120
    )
    default_max_results = _bounded_integer(
        _setting(configuration, "default_max_results", 25), 1, 100
    )
    pat = _secret(configuration, "pat")
    if not pat:
        raise ConfluenceError("invalid_configuration")
    return ConfluenceAuth(
        origin=origin,
        api_base=derive_api_base(origin, override or None),
        authorization=f"Bearer {pat}",
        request_timeout_seconds=timeout,
        default_max_results=default_max_results,
    )


ConfluenceAuth.from_configuration = staticmethod(  # type: ignore[attr-defined]
    authentication_from_configuration
)
