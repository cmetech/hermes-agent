"""Resolve Hermes' opaque per-profile configuration into safe Artifactory auth.

Origin validation is ported from ericsson-jira/auth.py. The mTLS and
certificate-expiry handling is new and exists because of an observed
failure: this instance sits behind Cloudflare Access, which authenticates
the caller by client certificate. When that certificate expires, Access
returns 302 to cloudflareaccess.com with
`auth_status: FAILED:FAILED:certificate has expired`, and every consumer
that does not check the redirect reports something unrelated instead --
"No files found", "Failed to parse response as JSON", "AQL query failed".
Reading notAfter here turns that into one accurate sentence.
"""

from __future__ import annotations

import os
import ssl
import time
from urllib.parse import urlsplit

if __package__:
    from .models import ArmAuth, ArmError, certificate_expiry_remediation
else:
    from models import ArmAuth, ArmError, certificate_expiry_remediation


API_ROOT = "/artifactory/"

_MAX_ORIGIN = 2048
_MAX_SECRET = 4096
_MAX_PATH = 4096

_AUTH_HEADERS = {
    "bearer": "Authorization",
    "api_key": "X-JFrog-Art-Api",
}


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
        raise ArmError("invalid_configuration")
    return value.strip()


def _origin(value) -> str:
    """Validate scheme + host and nothing else."""
    if not isinstance(value, str):
        raise ArmError("invalid_configuration")
    value = value.strip().rstrip("/")
    if (
        not value
        or len(value) > _MAX_ORIGIN
        or "\\" in value
        or any(character.isspace() for character in value)
    ):
        raise ArmError("invalid_configuration")
    if "://" not in value:
        value = f"https://{value}"
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ArmError("invalid_configuration") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/")
        or (port is not None and not 0 < port < 65536)
    ):
        raise ArmError("invalid_configuration")
    return value


def _bounded_integer(value, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ArmError("invalid_configuration")
    return value


def _path_setting(configuration, field_id: str) -> str | None:
    value = _setting(configuration, field_id, None)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > _MAX_PATH or "\x00" in value:
        raise ArmError("invalid_configuration")
    return value


def certificate_not_after(cert_path: str) -> float:
    """Read a PEM certificate's notAfter as Unix seconds."""
    try:
        decoded = ssl._ssl._test_decode_cert(cert_path)  # noqa: SLF001
        return float(ssl.cert_time_to_seconds(decoded["notAfter"]))
    except Exception:
        raise ArmError("certificate_invalid") from None


def _tls_context(
    cert_path: str | None, key_path: str | None, now: float
) -> tuple[object | None, float | None]:
    """Build the client-certificate SSL context, or (None, None)."""
    if cert_path is None and key_path is None:
        return None, None
    if cert_path is None or key_path is None:
        raise ArmError("invalid_configuration")
    for path in (cert_path, key_path):
        if not os.path.isfile(path):
            raise ArmError("certificate_invalid")

    not_after = certificate_not_after(cert_path)
    if now >= not_after:
        expired_on = time.strftime("%Y-%m-%d", time.gmtime(not_after))
        raise ArmError(
            "certificate_invalid",
            remediation=certificate_expiry_remediation(expired_on),
        )

    context = ssl.create_default_context()
    try:
        context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    except (ssl.SSLError, OSError):
        raise ArmError("certificate_invalid") from None
    return context, not_after


def authentication_from_configuration(configuration, *, now=None) -> ArmAuth:
    """Build one redacted, validated runtime identity for an Artifactory call."""
    current_time = time.time() if now is None else float(now)

    origin = _origin(_setting(configuration, "base_url", None))

    auth_mode = _setting(configuration, "auth_mode", "bearer")
    if type(auth_mode) is not str or auth_mode not in _AUTH_HEADERS:
        raise ArmError("invalid_configuration")

    token = _secret(configuration, "token")
    if not token:
        raise ArmError("invalid_configuration")

    context, not_after = _tls_context(
        _path_setting(configuration, "client_cert_path"),
        _path_setting(configuration, "client_key_path"),
        current_time,
    )

    deploy_root = _path_setting(configuration, "deploy_root")
    if deploy_root is not None:
        deploy_root = os.path.realpath(deploy_root)

    return ArmAuth(
        origin=origin,
        api_root=API_ROOT,
        auth_header_name=_AUTH_HEADERS[auth_mode],
        auth_header_value=(
            f"Bearer {token}" if auth_mode == "bearer" else token
        ),
        token=token,
        tls_context=context,
        certificate_not_after=not_after,
        request_timeout_seconds=_bounded_integer(
            _setting(configuration, "request_timeout_seconds", 60), 1, 300
        ),
        default_max_results=_bounded_integer(
            _setting(configuration, "default_max_results", 25), 1, 100
        ),
        max_deploy_bytes=_bounded_integer(
            _setting(configuration, "max_deploy_megabytes", 2048), 1, 16384
        ) * 1024 * 1024,
        deploy_root=deploy_root,
    )


ArmAuth.from_configuration = staticmethod(  # type: ignore[attr-defined]
    authentication_from_configuration
)
