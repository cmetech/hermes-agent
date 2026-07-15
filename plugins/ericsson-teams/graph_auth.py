"""MSAL device-code auth for Microsoft Graph (Teams tools).

- Public client (Azure CLI's well-known client id — no app registration),
  scope https://graph.microsoft.com/.default, authority organizations.
- Serializable token cache at $HERMES_HOME/ericsson/msal_token_cache.json.
- msal is imported LAZILY so the plugin loads even if msal is absent.
- Device flow is two-step for chat UX: start_device_flow() returns the code
  message immediately (module-level pending flow survives because the plugin
  lives in the persistent Hermes process); complete_device_flow() polls.
"""
from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

CLIENT_ID = os.environ.get("ERICSSON_GRAPH_CLIENT_ID",
                           "04b07795-8ddb-461a-bbee-02f9e1bf7b46")  # Azure CLI public client
AUTHORITY = "https://login.microsoftonline.com/organizations"
SCOPES = ["https://graph.microsoft.com/.default"]

_PENDING_FLOW = None


class AuthRequired(RuntimeError):
    pass


def cache_path() -> Path:
    home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
    return home / "ericsson" / "msal_token_cache.json"


def _app():
    import msal
    cache = msal.SerializableTokenCache()
    serialized = _read_cache_text()
    if serialized is not None:
        cache.deserialize(serialized)
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY,
                                        token_cache=cache)
    return app, cache


def _read_cache_text() -> str | None:
    try:
        if os.name == "posix":
            return _read_cache_posix()
        path = cache_path()
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
    except (OSError, UnicodeError, ValueError):
        raise AuthRequired(
            "could not read the Microsoft Graph sign-in cache securely"
        ) from None


def _read_cache_posix() -> str | None:
    path = cache_path()
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise OSError("secure no-follow opens are unavailable")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | nofollow
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        directory_fd = os.open(path.parent, directory_flags)
    except FileNotFoundError:
        return None
    descriptor: int | None = None
    try:
        directory_stat = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise OSError("cache parent is not a directory")
        if directory_stat.st_uid != os.geteuid():
            raise OSError("cache parent is not owned by the current user")
        os.fchmod(directory_fd, 0o700)
        if stat.S_IMODE(os.fstat(directory_fd).st_mode) != 0o700:
            raise OSError("cache parent permissions are not private")
        file_flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(path.name, file_flags, dir_fd=directory_fd)
        except FileNotFoundError:
            return None
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("cache destination is not a regular file")
        if opened.st_uid != os.geteuid():
            raise OSError("cache destination is not owned by the current user")
        os.fchmod(descriptor, 0o600)
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
            raise OSError("cache permissions are not private")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 16 * 1024 * 1024:
                raise ValueError("cache is too large")
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory_fd)


def _persist(cache) -> None:
    if not cache.has_state_changed:
        return
    try:
        serialized = cache.serialize().encode("utf-8")
        if os.name == "posix":
            _persist_posix(serialized)
        else:
            _persist_portable(serialized)
    except (OSError, UnicodeError, TypeError, ValueError):
        raise AuthRequired(
            "could not store the Microsoft Graph sign-in cache securely"
        ) from None


def _persist_posix(serialized: bytes) -> None:
    """Atomically replace the cache through a private, no-follow directory."""
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise OSError("secure no-follow opens are unavailable")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | nofollow
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    directory_fd = os.open(path.parent, directory_flags)
    temporary_name: str | None = None
    temporary_fd: int | None = None
    try:
        directory_stat = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise OSError("cache parent is not a directory")
        if directory_stat.st_uid != os.geteuid():
            raise OSError("cache parent is not owned by the current user")
        os.fchmod(directory_fd, 0o700)
        if stat.S_IMODE(os.fstat(directory_fd).st_mode) != 0o700:
            raise OSError("cache parent permissions are not private")

        try:
            current = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if current is not None and not stat.S_ISREG(current.st_mode):
            raise OSError("cache destination is not a regular file")

        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow
        file_flags |= getattr(os, "O_CLOEXEC", 0)
        for _ in range(8):
            candidate = f".{path.name}.{secrets.token_hex(8)}.tmp"
            try:
                temporary_fd = os.open(
                    candidate, file_flags, 0o600, dir_fd=directory_fd
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if temporary_fd is None or temporary_name is None:
            raise OSError("could not reserve a private cache temporary file")

        view = memoryview(serialized)
        while view:
            written = os.write(temporary_fd, view)
            if written <= 0:
                raise OSError("short cache write")
            view = view[written:]
        os.fchmod(temporary_fd, 0o600)
        temporary_stat = os.fstat(temporary_fd)
        if not stat.S_ISREG(temporary_stat.st_mode):
            raise OSError("cache temporary is not a regular file")
        if temporary_stat.st_uid != os.geteuid():
            raise OSError("cache temporary is not owned by the current user")
        if stat.S_IMODE(temporary_stat.st_mode) != 0o600:
            raise OSError("cache temporary permissions are not private")
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = None

        os.replace(
            temporary_name,
            path.name,
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


def _persist_portable(serialized: bytes) -> None:
    """Keep atomic replacement semantics on Windows using native path APIs."""
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        view = memoryview(serialized)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short cache write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def get_token() -> str:
    """Silent token from cache. Raises AuthRequired with next-step guidance."""
    if not cache_path().exists():
        raise AuthRequired("Not signed in to Microsoft Graph — run the "
                           "teams_auth tool to sign in with a device code.")
    app, cache = _app()
    accounts = app.get_accounts()
    result = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
    _persist(cache)
    if not result or "access_token" not in result:
        raise AuthRequired("Graph session expired — run the teams_auth tool "
                           "to sign in again.")
    return result["access_token"]


def start_device_flow() -> dict:
    global _PENDING_FLOW
    app, _cache = _app()
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise AuthRequired(f"could not start device flow: {flow.get('error_description', flow)}")
    _PENDING_FLOW = (app, _cache, flow)
    return {"message": flow["message"], "verification_uri": flow["verification_uri"],
            "user_code": flow["user_code"]}


def complete_device_flow() -> dict:
    global _PENDING_FLOW
    if _PENDING_FLOW is None:
        raise AuthRequired("no device flow in progress — call teams_auth first")
    app, cache, flow = _PENDING_FLOW
    flow["expires_at"] = 0  # poll once; the tool is re-invoked to retry
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" in result:
        _persist(cache)
        _PENDING_FLOW = None
        return {"ok": True, "account": result.get("id_token_claims", {}).get("preferred_username")}
    err = result.get("error")
    if err in ("authorization_pending", "slow_down"):
        return {"ok": False, "pending": True,
                "detail": result.get("error_description", "authorization pending — "
                                     "finish signing in, then run teams_auth complete again")}
    _PENDING_FLOW = None
    return {"ok": False, "pending": False,
            "error": result.get("error_description") or err or "device flow failed",
            "hint": "run teams_auth again to restart sign-in"}
