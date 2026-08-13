"""Resolve Hermes' opaque per-profile configuration into safe Jira auth."""

from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import urlsplit

if __package__:
    from .models import JiraAuth, JiraError
else:
    from models import JiraAuth, JiraError


_MAX_ORIGIN = 2048
_MAX_SECRET = 4096
_MAX_EMAIL = 320


def _setting(configuration, field_id: str, default):
    try:
        return configuration.setting(field_id)
    except Exception:
        return default


def _secret(configuration, field_id: str) -> str:
    try:
        value = configuration.secret(field_id)
    except Exception:
        return ""
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > _MAX_SECRET:
        raise JiraError("invalid_configuration")
    return value.strip()


def _origin(value) -> str:
    if not isinstance(value, str):
        raise JiraError("invalid_configuration")
    value = value.strip().rstrip("/")
    if not value or len(value) > _MAX_ORIGIN or "\\" in value or any(c.isspace() for c in value):
        raise JiraError("invalid_configuration")
    if "://" not in value:
        value = f"https://{value}"
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise JiraError("invalid_configuration") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (port is not None and not 0 < port < 65536)
    ):
        raise JiraError("invalid_configuration")
    return value


def _bounded_integer(value, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise JiraError("invalid_configuration")
    return value


def authentication_from_configuration(configuration) -> JiraAuth:
    """Build one redacted, validated runtime identity for a Jira operation."""

    origin = _origin(_setting(configuration, "base_url", None))
    auth_mode = _setting(configuration, "auth_mode", "bearer")
    rest_api_version = _setting(configuration, "rest_api_version", "auto")
    transport = _setting(configuration, "transport", "auto")
    curl_executable = _setting(configuration, "curl_executable", "/usr/bin/curl")
    timeout = _bounded_integer(
        _setting(configuration, "request_timeout_seconds", 30), 1, 120
    )
    default_max_results = _bounded_integer(
        _setting(configuration, "default_max_results", 25), 1, 100
    )
    if auth_mode not in {"bearer", "basic"}:
        raise JiraError("invalid_configuration")
    if rest_api_version not in {"auto", "3", "2"}:
        raise JiraError("invalid_configuration")
    if transport not in {"auto", "native", "curl"}:
        raise JiraError("invalid_configuration")
    if (
        not isinstance(curl_executable, str)
        or len(curl_executable) > 4096
        or "\x00" in curl_executable
        or not Path(curl_executable).is_absolute()
    ):
        raise JiraError("invalid_configuration")

    pat = _secret(configuration, "pat")
    api_token = _secret(configuration, "api_token")
    if auth_mode == "bearer":
        if not pat or api_token:
            raise JiraError("invalid_configuration")
        authorization = f"Bearer {pat}"
    else:
        email = _setting(configuration, "email", "")
        if (
            not isinstance(email, str)
            or not email.strip()
            or len(email) > _MAX_EMAIL
            or pat
            or not api_token
        ):
            raise JiraError("invalid_configuration")
        encoded = base64.b64encode(
            f"{email.strip()}:{api_token}".encode("utf-8")
        ).decode("ascii")
        authorization = f"Basic {encoded}"

    return JiraAuth(
        origin=origin,
        authorization=authorization,
        auth_mode=auth_mode,
        rest_api_version=rest_api_version,
        transport=transport,
        curl_executable=curl_executable,
        request_timeout_seconds=timeout,
        default_max_results=default_max_results,
    )


JiraAuth.from_configuration = staticmethod(authentication_from_configuration)  # type: ignore[attr-defined]
