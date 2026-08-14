"""Validated connector configuration and safe public projections."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlsplit


_TENANT_HOST = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+sharepoint\.com\Z"
)
_PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_AUTH_MODES = frozenset({"auto", "delegated_msal", "app_only", "azure_cli"})
_SETTING_FIELDS = (
    "tenant_host",
    "auth_mode",
    "tenant_id",
    "client_id",
    "scopes",
    "authority_url",
    "account_id",
    "azure_cli_enabled",
    "max_pages",
    "max_items",
    "max_bytes",
    "timeout_seconds",
    "download_root",
    "upload_root",
    "browser_profile",
)


class SharePointConfigurationError(ValueError):
    """Raised when connector settings cannot form a safe bounded contract."""


def _profile_home(value: Path | str | None) -> Path:
    if value is not None:
        return Path(value).expanduser().resolve()
    configured = os.environ.get("HERMES_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home()).resolve()
    except ImportError:
        return Path.home() / ".hermes"


def _bounded_int(value: Any, *, name: str, default: int, maximum: int) -> int:
    if value is None or value == "":
        value = default
    if isinstance(value, bool):
        raise SharePointConfigurationError(f"{name} must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise SharePointConfigurationError(f"{name} must be an integer") from None
    if not 1 <= normalized <= maximum:
        raise SharePointConfigurationError(f"{name} is outside the supported range")
    return normalized


def _bounded_float(value: Any, *, name: str, default: float, maximum: float) -> float:
    if value is None or value == "":
        value = default
    if isinstance(value, bool):
        raise SharePointConfigurationError(f"{name} must be numeric")
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        raise SharePointConfigurationError(f"{name} must be numeric") from None
    if not 1 <= normalized <= maximum:
        raise SharePointConfigurationError(f"{name} is outside the supported range")
    return normalized


def _local_root(value: Any, *, home: Path, default: str) -> Path:
    raw = str(value or default).strip()
    if not raw or "\x00" in raw:
        raise SharePointConfigurationError("local root is invalid")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = home / candidate
    # Preserve the lexical path so the operation boundary can reject any
    # symlink component before resolving it. Resolving here would erase the
    # evidence that a configured root itself is a symlink.
    return Path(os.path.abspath(os.path.normpath(candidate)))


@dataclass(frozen=True, slots=True)
class SharePointConfiguration:
    tenant_host: str
    auth_mode: str
    tenant_id: str
    client_id: str
    client_secret: str = field(repr=False)
    scopes: tuple[str, ...]
    authority_url: str
    account_id: str
    azure_cli_enabled: bool
    max_pages: int
    max_items: int
    max_bytes: int
    timeout_seconds: float
    download_root: Path
    upload_root: Path
    browser_profile: str
    cache_path: Path

    @classmethod
    def from_runtime(cls, configuration: Any) -> "SharePointConfiguration":
        """Resolve only descriptor-authorized fields from an opaque host accessor."""
        if isinstance(configuration, Mapping):
            return cls.from_mapping(configuration)
        values: dict[str, Any] = {}
        for field_id in _SETTING_FIELDS:
            try:
                values[field_id] = configuration.setting(field_id)
            except (KeyError, TypeError, ValueError):
                continue
        try:
            values["client_secret"] = configuration.secret("client_secret")
        except (KeyError, TypeError, ValueError):
            pass
        return cls.from_mapping(values)

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any],
        *,
        profile_home: Path | str | None = None,
    ) -> "SharePointConfiguration":
        home = _profile_home(profile_home)
        host = str(values.get("tenant_host") or "").strip().lower().rstrip(".")
        if _TENANT_HOST.fullmatch(host) is None:
            raise SharePointConfigurationError(
                "tenant_host must be an exact *.sharepoint.com host"
            )
        mode = str(values.get("auth_mode") or "auto").strip().lower()
        if mode not in _AUTH_MODES:
            raise SharePointConfigurationError("auth_mode is unsupported")
        raw_scopes = str(values.get("scopes") or "")
        scopes = tuple(part for part in re.split(r"[\s,]+", raw_scopes.strip()) if part)
        if not scopes or len(scopes) > 64 or any(len(scope) > 512 for scope in scopes):
            raise SharePointConfigurationError("scopes must be a bounded non-empty list")
        authority = str(
            values.get("authority_url") or "https://login.microsoftonline.com"
        ).strip().rstrip("/")
        parsed_authority = urlsplit(authority)
        if (
            parsed_authority.scheme != "https"
            or not parsed_authority.hostname
            or parsed_authority.username is not None
            or parsed_authority.password is not None
            or parsed_authority.query
            or parsed_authority.fragment
        ):
            raise SharePointConfigurationError("authority_url must be a safe HTTPS URL")
        browser_profile = str(values.get("browser_profile") or "").strip()
        if _PROFILE_NAME.fullmatch(browser_profile) is None:
            raise SharePointConfigurationError("browser_profile is invalid")

        return cls(
            tenant_host=host,
            auth_mode=mode,
            tenant_id=str(values.get("tenant_id") or "").strip(),
            client_id=str(values.get("client_id") or "").strip(),
            client_secret=str(values.get("client_secret") or "").strip(),
            scopes=scopes,
            authority_url=authority,
            account_id=str(values.get("account_id") or "").strip(),
            azure_cli_enabled=values.get("azure_cli_enabled") is True,
            max_pages=_bounded_int(
                values.get("max_pages"), name="max_pages", default=25, maximum=1000
            ),
            max_items=_bounded_int(
                values.get("max_items"), name="max_items", default=1000, maximum=100_000
            ),
            max_bytes=_bounded_int(
                values.get("max_bytes"),
                name="max_bytes",
                default=512 * 1024 * 1024,
                maximum=1024**4,
            ),
            timeout_seconds=_bounded_float(
                values.get("timeout_seconds"),
                name="timeout_seconds",
                default=60,
                maximum=3600,
            ),
            download_root=_local_root(
                values.get("download_root"),
                home=home,
                default="artifacts/sharepoint/downloads",
            ),
            upload_root=_local_root(
                values.get("upload_root"),
                home=home,
                default="artifacts/sharepoint/uploads",
            ),
            browser_profile=browser_profile,
            cache_path=home / "auth" / "ericsson-sharepoint" / "msal-cache.json",
        )

    @property
    def tenant_origin(self) -> str:
        return f"https://{self.tenant_host}"

    def public_status(self, *, delegated_cache_configured: bool = False) -> dict[str, Any]:
        return {
            "tenant_host": self.tenant_host,
            "auth_mode": self.auth_mode,
            "browser_profile": self.browser_profile,
            "delegated_cache": "configured" if delegated_cache_configured else "not_configured",
        }


class SharePointSetupError(RuntimeError):
    """Credential-free setup-action failure."""


class SharePointResolutionError(RuntimeError):
    """Raised when a URL or explicit ID does not resolve unambiguously."""


class SharePointCancelledError(RuntimeError):
    """Raised when cooperative cancellation stops a connector operation."""


class SharePointDeadlineError(RuntimeError):
    """Raised when a connector operation exceeds its monotonic deadline."""


class SharePointFileBoundaryError(RuntimeError):
    """Raised when a local path is outside the admitted operation boundary."""


class SharePointAuditError(RuntimeError):
    """Raised when a browser-backed audit cannot preserve its authority contract."""


class SharePointWriteError(RuntimeError):
    """Raised when a SharePoint mutation request is invalid or unsafe."""


class SharePointAmbiguousWriteError(SharePointWriteError):
    """Raised when a mutation may have completed and must be reconciled."""
