"""Translate Hermes' opaque runtime configuration into GitLab transport auth."""

from __future__ import annotations

import os
import ssl
import stat
from pathlib import Path
from urllib.parse import urlsplit

if __package__:
    from .models import GitLabAuth, GitLabError
else:  # Standalone source tests import modules directly from the plugin root.
    from models import GitLabAuth, GitLabError


_DEFAULT_CERT = "~/.config/edpctl/auth/client.pem"
_DEFAULT_KEY = "~/.config/edpctl/auth/client-key.pem"
_MAX_ORIGIN = 2048
_MAX_PATH = 4096
_MAX_TOKEN = 4096
_MAX_PEM_BYTES = 1024 * 1024


def _safe_setting(configuration, field_id: str, default: str) -> str:
    try:
        value = configuration.setting(field_id)
    except Exception:
        value = default
    if not isinstance(value, str):
        raise GitLabError("invalid_configuration")
    return value.strip()


def _expand_path(value: str, home: Path) -> Path:
    if not value or len(value) > _MAX_PATH or "\x00" in value:
        raise GitLabError("invalid_configuration")
    if value == "~":
        return home
    if value.startswith("~/"):
        return home / value[2:]
    return Path(value).expanduser()


def _usable_regular_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and 0 < metadata.st_size <= _MAX_PEM_BYTES
        and os.access(path, os.R_OK)
    )


def _certificate_pair(cert_value: str, key_value: str, home: Path):
    if not cert_value and not key_value:
        return None
    if not cert_value or not key_value:
        raise GitLabError("invalid_configuration")
    cert = _expand_path(cert_value, home)
    key = _expand_path(key_value, home)
    cert_ok = _usable_regular_file(cert)
    key_ok = _usable_regular_file(key)
    if not cert_ok and not key_ok:
        if cert_value == _DEFAULT_CERT and key_value == _DEFAULT_KEY:
            return None
        raise GitLabError("invalid_configuration")
    if not cert_ok or not key_ok:
        raise GitLabError("invalid_configuration")
    try:
        context = ssl.create_default_context()
        context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    except (OSError, ssl.SSLError, ValueError):
        raise GitLabError("invalid_configuration") from None
    return (cert, key), context


def _from_configuration(configuration, *, home: Path | None = None) -> GitLabAuth:
    try:
        origin = configuration.setting("origin")
        pat = configuration.secret("pat")
    except Exception as exc:
        raise GitLabError("invalid_configuration") from None
    if not isinstance(origin, str) or not isinstance(pat, str):
        raise GitLabError("invalid_configuration")
    origin = origin.strip().rstrip("/")
    pat = pat.strip()
    if not origin or len(origin) > _MAX_ORIGIN or not pat or len(pat) > _MAX_TOKEN:
        raise GitLabError("invalid_configuration")
    parsed = urlsplit(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise GitLabError("invalid_configuration")
    cert_value = _safe_setting(configuration, "client_certificate_path", _DEFAULT_CERT)
    key_value = _safe_setting(configuration, "client_key_path", _DEFAULT_KEY)
    selected_home = Path.home() if home is None else Path(home)
    certificate_pair = _certificate_pair(cert_value, key_value, selected_home)
    return GitLabAuth(
        origin=origin,
        pat=pat,
        certificate_pair=certificate_pair[0] if certificate_pair else None,
        tls_context=certificate_pair[1] if certificate_pair else None,
    )


GitLabAuth.from_configuration = staticmethod(_from_configuration)  # type: ignore[attr-defined]
