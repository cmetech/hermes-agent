"""Connector-neutral Microsoft Graph identity providers.

This module keeps identity selection and delegated token persistence generic.
Connectors supply explicit scopes and profile-owned cache paths; no tenant or
connector default is embedded here.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

from tools.microsoft_graph_auth import (
    GraphCredentials,
    MicrosoftGraphTokenProvider,
)


class GraphAuthMode(str, Enum):
    """Supported Microsoft Graph authentication modes."""

    APP_ONLY = "app_only"
    DELEGATED_MSAL = "delegated_msal"
    AZURE_CLI = "azure_cli"
    AUTO = "auto"


class MicrosoftGraphIdentityError(RuntimeError):
    """Base class for connector-neutral Graph identity failures."""


class MicrosoftGraphIdentityConfigError(MicrosoftGraphIdentityError):
    """Raised when an identity mode is not fully configured."""


class MicrosoftGraphTokenCacheError(MicrosoftGraphIdentityError):
    """Raised when a delegated token cache cannot be handled safely."""


class MicrosoftGraphInteractiveAuthRequired(MicrosoftGraphIdentityError):
    """Raised when a user-owned interactive setup action is required."""


class MicrosoftGraphDelegatedTokenError(MicrosoftGraphIdentityError):
    """Raised when delegated token acquisition fails."""


class GraphAccessTokenProvider(Protocol):
    """Small protocol consumed by :class:`MicrosoftGraphClient`."""

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        """Return a bearer token, refreshing when requested."""

    def clear_cache(self) -> None:
        """Clear only in-process access-token state."""


@dataclass(frozen=True)
class GraphIdentityConfig:
    """Normalized configuration shared by Graph identity implementations."""

    mode: GraphAuthMode | str = GraphAuthMode.AUTO
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    scopes: tuple[str, ...] = ()
    authority_url: str = "https://login.microsoftonline.com"
    account_id: str = ""
    cache_path: Path | str | None = None
    azure_cli_enabled: bool = False

    def __post_init__(self) -> None:
        raw_mode = self.mode.value if isinstance(self.mode, GraphAuthMode) else str(self.mode)
        try:
            mode = GraphAuthMode(raw_mode.strip().lower())
        except ValueError as exc:
            raise MicrosoftGraphIdentityConfigError(
                f"Unsupported Microsoft Graph authentication mode: {raw_mode.strip()}"
            ) from exc
        scopes = tuple(str(scope).strip() for scope in self.scopes if str(scope).strip())
        cache_path = Path(self.cache_path) if self.cache_path is not None else None
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "tenant_id", str(self.tenant_id or "").strip().strip("/"))
        object.__setattr__(self, "client_id", str(self.client_id or "").strip())
        object.__setattr__(self, "client_secret", str(self.client_secret or "").strip())
        object.__setattr__(self, "scopes", scopes)
        object.__setattr__(
            self,
            "authority_url",
            str(self.authority_url or "https://login.microsoftonline.com").strip().rstrip("/"),
        )
        object.__setattr__(self, "account_id", str(self.account_id or "").strip())
        object.__setattr__(self, "cache_path", cache_path)

    @property
    def authority(self) -> str:
        """Return the tenant-qualified authority used by delegated MSAL."""
        if not self.tenant_id:
            return self.authority_url
        if self.authority_url.rsplit("/", 1)[-1].casefold() == self.tenant_id.casefold():
            return self.authority_url
        return f"{self.authority_url}/{self.tenant_id}"


def _missing_fields(config: GraphIdentityConfig, mode: GraphAuthMode) -> tuple[str, ...]:
    if mode is GraphAuthMode.APP_ONLY:
        required = {
            "tenant_id": config.tenant_id,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "scopes": config.scopes,
        }
    elif mode is GraphAuthMode.DELEGATED_MSAL:
        required = {
            "tenant_id": config.tenant_id,
            "client_id": config.client_id,
            "scopes": config.scopes,
            "cache_path": config.cache_path,
        }
    elif mode is GraphAuthMode.AZURE_CLI:
        required = {"scopes": config.scopes}
    else:
        return ()
    return tuple(name for name, value in required.items() if not value)


def select_auth_mode(config: GraphIdentityConfig) -> GraphAuthMode:
    """Resolve an explicit mode or deterministically choose a complete mode."""
    if config.mode is not GraphAuthMode.AUTO:
        missing = _missing_fields(config, config.mode)
        if missing:
            raise MicrosoftGraphIdentityConfigError(
                "Microsoft Graph identity configuration is incomplete for "
                f"{config.mode.value}: {', '.join(missing)}"
            )
        return config.mode

    for candidate in (GraphAuthMode.DELEGATED_MSAL, GraphAuthMode.APP_ONLY):
        if not _missing_fields(config, candidate):
            return candidate
    if config.azure_cli_enabled and not _missing_fields(config, GraphAuthMode.AZURE_CLI):
        return GraphAuthMode.AZURE_CLI
    raise MicrosoftGraphIdentityConfigError(
        "Microsoft Graph authentication mode 'auto' has no fully configured identity."
    )


@dataclass(frozen=True)
class GraphIdentityReadiness:
    """Credential-free readiness projection for configuration surfaces."""

    status: str
    mode: str | None
    missing_fields: tuple[str, ...] = ()


def inspect_identity_readiness(
    config: GraphIdentityConfig,
    *,
    delegated_cache_has_account: bool = False,
    azure_cli_authenticated: bool = False,
) -> GraphIdentityReadiness:
    """Classify identity readiness without returning or persisting a token."""
    try:
        mode = select_auth_mode(config)
    except MicrosoftGraphIdentityConfigError:
        explicit = config.mode if config.mode is not GraphAuthMode.AUTO else None
        missing = _missing_fields(config, explicit) if explicit else ()
        return GraphIdentityReadiness(
            "configuration_required",
            explicit.value if explicit else None,
            missing,
        )
    if mode is GraphAuthMode.DELEGATED_MSAL and not delegated_cache_has_account:
        return GraphIdentityReadiness("interactive_auth_required", mode.value)
    if mode is GraphAuthMode.AZURE_CLI and not azure_cli_authenticated:
        return GraphIdentityReadiness("authentication_required", mode.value)
    return GraphIdentityReadiness("ready", mode.value)


class GraphTokenCacheStore:
    """Bounded private atomic storage for a profile-scoped MSAL cache."""

    def __init__(self, path: Path | str, *, max_bytes: int = 16 * 1024 * 1024) -> None:
        self.path = Path(path)
        self.max_bytes = max(1, int(max_bytes))

    def read_text(self) -> str | None:
        try:
            payload = self._read_posix() if os.name == "posix" else self._read_portable()
            if payload is None:
                return None
            if len(payload) > self.max_bytes:
                raise ValueError("cache exceeds the configured limit")
            return payload.decode("utf-8")
        except (OSError, UnicodeError, ValueError):
            raise MicrosoftGraphTokenCacheError(
                "Could not read the Microsoft Graph token cache securely."
            ) from None

    def write_text(self, serialized: str) -> None:
        try:
            payload = serialized.encode("utf-8")
            if len(payload) > self.max_bytes:
                raise ValueError("cache exceeds the configured limit")
            if os.name == "posix":
                self._write_posix(payload)
            else:
                self._write_portable(payload)
        except (OSError, UnicodeError, TypeError, ValueError):
            raise MicrosoftGraphTokenCacheError(
                "Could not store the Microsoft Graph token cache securely."
            ) from None

    def _open_posix_parent(self, *, create: bool) -> int:
        if self.path.parent.is_symlink():
            raise OSError("cache parent is a symbolic link")
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        flags |= getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(self.path.parent, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or opened.st_uid != os.geteuid():
            os.close(descriptor)
            raise OSError("cache parent is not private")
        os.fchmod(descriptor, 0o700)
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o700:
            os.close(descriptor)
            raise OSError("cache parent permissions are not private")
        return descriptor

    def _read_posix(self) -> bytes | None:
        try:
            directory_fd = self._open_posix_parent(create=False)
        except FileNotFoundError:
            return None
        descriptor: int | None = None
        try:
            flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            try:
                descriptor = os.open(self.path.name, flags, dir_fd=directory_fd)
            except FileNotFoundError:
                return None
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid():
                raise OSError("cache is not a private regular file")
            os.fchmod(descriptor, 0o600)
            if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
                raise OSError("cache permissions are not private")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(64 * 1024, self.max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > self.max_bytes:
                    raise ValueError("cache exceeds the configured limit")
            return b"".join(chunks)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory_fd)

    def _write_posix(self, payload: bytes) -> None:
        directory_fd = self._open_posix_parent(create=True)
        temporary_name: str | None = None
        temporary_fd: int | None = None
        try:
            try:
                current = os.stat(
                    self.path.name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                current = None
            if current is not None and not stat.S_ISREG(current.st_mode):
                raise OSError("cache destination is not a regular file")

            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            flags |= getattr(os, "O_CLOEXEC", 0)
            for _ in range(8):
                candidate = f".{self.path.name}.{secrets.token_hex(8)}.tmp"
                try:
                    temporary_fd = os.open(
                        candidate,
                        flags,
                        0o600,
                        dir_fd=directory_fd,
                    )
                except FileExistsError:
                    continue
                temporary_name = candidate
                break
            if temporary_fd is None or temporary_name is None:
                raise OSError("could not reserve private cache temporary file")
            self._write_all(temporary_fd, payload)
            os.fchmod(temporary_fd, 0o600)
            temporary_stat = os.fstat(temporary_fd)
            if not stat.S_ISREG(temporary_stat.st_mode) or temporary_stat.st_uid != os.geteuid():
                raise OSError("cache temporary is not a private regular file")
            os.fsync(temporary_fd)
            os.close(temporary_fd)
            temporary_fd = None
            os.replace(
                temporary_name,
                self.path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            temporary_name = None
            os.fsync(directory_fd)
        finally:
            if temporary_fd is not None:
                os.close(temporary_fd)
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
            os.close(directory_fd)

    def _read_portable(self) -> bytes | None:
        if not self.path.exists() and not self.path.is_symlink():
            return None
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise OSError("cache path crosses a symbolic link")
        if not self.path.is_file():
            raise OSError("cache path is not a regular file")
        with self.path.open("rb") as handle:
            return handle.read(self.max_bytes + 1)

    def _write_portable(self, payload: bytes) -> None:
        if self.path.parent.is_symlink():
            raise OSError("cache parent is a symbolic link")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink() or (self.path.exists() and not self.path.is_file()):
            raise OSError("cache path is not a regular file")
        temporary = self.path.parent / f".{self.path.name}.{secrets.token_hex(8)}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            self._write_all(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary, self.path)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short cache write")
            view = view[written:]


def _default_cache_factory() -> Any:
    try:
        import msal
    except ImportError as exc:
        raise MicrosoftGraphIdentityConfigError(
            "MSAL is required for delegated Microsoft Graph authentication."
        ) from exc
    return msal.SerializableTokenCache()


def _default_application_factory(**kwargs: Any) -> Any:
    try:
        import msal
    except ImportError as exc:
        raise MicrosoftGraphIdentityConfigError(
            "MSAL is required for delegated Microsoft Graph authentication."
        ) from exc
    return msal.PublicClientApplication(**kwargs)


class DelegatedMicrosoftGraphTokenProvider:
    """MSAL-backed delegated provider whose operation path is always silent."""

    def __init__(
        self,
        config: GraphIdentityConfig,
        *,
        cache_store: GraphTokenCacheStore | None = None,
        cache_factory: Callable[[], Any] = _default_cache_factory,
        application_factory: Callable[..., Any] = _default_application_factory,
        interactive_allowed: bool = False,
    ) -> None:
        if select_auth_mode(config) is not GraphAuthMode.DELEGATED_MSAL:
            raise MicrosoftGraphIdentityConfigError(
                "Delegated provider requires delegated_msal configuration."
            )
        self.config = config
        self.cache_store = cache_store or GraphTokenCacheStore(config.cache_path)
        self.interactive_allowed = bool(interactive_allowed)
        self._lock = asyncio.Lock()
        self._cache = cache_factory()
        serialized = self.cache_store.read_text()
        if serialized:
            try:
                self._cache.deserialize(serialized)
            except Exception:
                raise MicrosoftGraphTokenCacheError(
                    "The Microsoft Graph token cache is corrupt or unreadable."
                ) from None
        self._application = application_factory(
            client_id=config.client_id,
            authority=config.authority,
            token_cache=self._cache,
        )

    def clear_cache(self) -> None:
        """MSAL owns refresh state; a forced acquisition bypasses access cache."""

    def _selected_account(self) -> Any | None:
        accounts = list(self._application.get_accounts() or [])
        if not accounts:
            return None
        if not self.config.account_id:
            return accounts[0]
        wanted = self.config.account_id.casefold()
        return next(
            (
                account
                for account in accounts
                if str(account.get("home_account_id") or "").casefold() == wanted
                or str(account.get("username") or "").casefold() == wanted
            ),
            None,
        )

    def has_cached_account(self) -> bool:
        return self._selected_account() is not None

    def _persist_if_changed(self) -> None:
        if getattr(self._cache, "has_state_changed", False):
            self.cache_store.write_text(self._cache.serialize())

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        async with self._lock:
            return await asyncio.to_thread(self._get_access_token_sync, force_refresh)

    def _get_access_token_sync(self, force_refresh: bool) -> str:
        account = self._selected_account()
        if account is None:
            raise MicrosoftGraphInteractiveAuthRequired(
                "Microsoft Graph delegated authentication requires interactive setup."
            )
        try:
            result = self._application.acquire_token_silent(
                list(self.config.scopes),
                account,
                force_refresh=bool(force_refresh),
            )
        except Exception:
            raise MicrosoftGraphDelegatedTokenError(
                "Microsoft Graph delegated token acquisition failed."
            ) from None
        finally:
            self._persist_if_changed()
        token = result.get("access_token") if isinstance(result, dict) else None
        if not token:
            raise MicrosoftGraphInteractiveAuthRequired(
                "Microsoft Graph delegated authentication must be renewed interactively."
            )
        return str(token)

    async def authenticate_interactively(self) -> dict[str, Any]:
        """Run the user-invoked setup action and return credential-free status."""
        if not self.interactive_allowed:
            raise MicrosoftGraphInteractiveAuthRequired(
                "Interactive Microsoft Graph authentication is unavailable in this execution context."
            )
        async with self._lock:
            return await asyncio.to_thread(self._authenticate_interactively_sync)

    def _authenticate_interactively_sync(self) -> dict[str, Any]:
        try:
            result = self._application.acquire_token_interactive(list(self.config.scopes))
        except Exception:
            raise MicrosoftGraphDelegatedTokenError(
                "Interactive Microsoft Graph authentication failed."
            ) from None
        finally:
            self._persist_if_changed()
        if not isinstance(result, dict) or not result.get("access_token"):
            raise MicrosoftGraphDelegatedTokenError(
                "Interactive Microsoft Graph authentication did not complete."
            )
        claims = result.get("id_token_claims")
        account = claims.get("preferred_username") if isinstance(claims, dict) else None
        return {"authenticated": True, "account": account}


class AzureCliMicrosoftGraphTokenProvider:
    """Delegated provider backed by Hermes' existing Azure identity adapter."""

    def __init__(
        self,
        scopes: tuple[str, ...],
        *,
        token_provider_factory: Callable[..., Callable[[], str]] | None = None,
    ) -> None:
        normalized = tuple(str(scope).strip() for scope in scopes if str(scope).strip())
        if len(normalized) != 1:
            raise MicrosoftGraphIdentityConfigError(
                "Azure CLI Graph authentication requires exactly one scope."
            )
        if token_provider_factory is None:
            from agent.azure_identity_adapter import build_token_provider

            token_provider_factory = build_token_provider
        self.scope = normalized[0]
        self._provider = token_provider_factory(scope=self.scope)

    def clear_cache(self) -> None:
        """The Azure SDK owns its in-process and operating-system caches."""

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        del force_refresh
        try:
            token = await asyncio.to_thread(self._provider)
        except Exception:
            raise MicrosoftGraphDelegatedTokenError(
                "Azure CLI Microsoft Graph authentication failed."
            ) from None
        if not isinstance(token, str) or not token:
            raise MicrosoftGraphDelegatedTokenError(
                "Azure CLI Microsoft Graph authentication returned no token."
            )
        return token


def create_graph_token_provider(
    config: GraphIdentityConfig,
    *,
    interactive_allowed: bool = False,
) -> GraphAccessTokenProvider:
    """Construct the smallest provider matching a normalized identity config."""
    mode = select_auth_mode(config)
    if mode is GraphAuthMode.APP_ONLY:
        if len(config.scopes) != 1:
            raise MicrosoftGraphIdentityConfigError(
                "App-only Microsoft Graph authentication requires exactly one scope."
            )
        return MicrosoftGraphTokenProvider(
            GraphCredentials(
                tenant_id=config.tenant_id,
                client_id=config.client_id,
                client_secret=config.client_secret,
                scope=config.scopes[0],
                authority_url=config.authority_url,
            )
        )
    if mode is GraphAuthMode.DELEGATED_MSAL:
        return DelegatedMicrosoftGraphTokenProvider(
            config,
            interactive_allowed=interactive_allowed,
        )
    return AzureCliMicrosoftGraphTokenProvider(config.scopes)


__all__ = [
    "AzureCliMicrosoftGraphTokenProvider",
    "DelegatedMicrosoftGraphTokenProvider",
    "GraphAccessTokenProvider",
    "GraphAuthMode",
    "GraphIdentityConfig",
    "GraphIdentityReadiness",
    "GraphTokenCacheStore",
    "MicrosoftGraphDelegatedTokenError",
    "MicrosoftGraphIdentityConfigError",
    "MicrosoftGraphIdentityError",
    "MicrosoftGraphInteractiveAuthRequired",
    "MicrosoftGraphTokenCacheError",
    "create_graph_token_provider",
    "inspect_identity_readiness",
    "select_auth_mode",
]
