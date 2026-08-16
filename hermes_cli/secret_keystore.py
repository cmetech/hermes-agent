"""OS-native storage for per-profile plugin secrets.

Mirrors super-cli's two-tier credential model: an OS keystore when one is
reachable, otherwise an AES-GCM encrypted file using the same on-disk
parameters (0700 directory, 0600 key and ciphertext).

Two properties are load-bearing and must not be relaxed:

* Lookups are **lazy and per-key**.  Nothing here enumerates the OS keystore
  (``keyring`` has no portable listing API) and nothing bulk-loads secrets.
* Values NEVER reach ``os.environ``.  Plugin PATs previously lived in
  ``~/.hermes/.env``, which ``load_dotenv`` exports process-wide at startup,
  handing every child process a copy.  Removing that exposure is the point.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets as _secrets
import stat
import tempfile
import threading
from pathlib import Path
from typing import Literal, Mapping, cast

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from hermes_cli import container_storage
from hermes_cli.secret_authority import (
    AUTHORITY_FILE,
    AUTHORITY_VERSION,
    AuthorityRegistry,
    AuthorityRegistryError,
    SecretAuthority,
    encode_authority_registry,
    load_authority_registry,
)
from utils import atomic_replace

try:  # pragma: no cover - import guard exercised via mock.patch
    import keyring
except Exception:  # ImportError, or a backend that explodes at import time
    keyring = None

# Imported by class rather than matched on type(exc).__name__: a string
# comparison silently stops working if keyring renames or re-homes the
# exception, and the failure mode is a swallowed revocation error.
try:  # pragma: no cover - exercised via mock.patch
    from keyring.errors import PasswordDeleteError as _PasswordDeleteError
except Exception:  # keyring absent, or an older layout
    class _PasswordDeleteError(Exception):
        """Never raised; keeps the except clause below well-typed."""

# Container durability uses the shared detector directly.  In particular it
# must not reuse config._is_container(), whose HERMES_SKIP_CHMOD behavior is a
# permission-policy override for NFS/SMB hosts rather than container evidence.

__all__ = [
    "KeystoreError",
    "FileKeystore",
    "OSKeystore",
    "SERVICE_NAME",
    "probe_os_keystore",
]

_KEY_BYTES = 32
_NONCE_BYTES = 12
_KEY_FILE = "keystore.key"
_DATA_FILE = "keystore.enc"
_LOCK_FILE = "keystore.lock"
_FILE_THREAD_LOCK = threading.RLock()
SERVICE_NAME = "hermes.plugin-secrets"
_PROBE_KEY = "__hermes_probe__"
_PROBE_VALUE = "ok"
# The probe runs once per process, on the path that decides which tier to
# use. A locked Linux keyring or an unresponsive D-Bus Secret Service blocks
# rather than raising, so the bound has to be a timeout, not an except.
PROBE_TIMEOUT_SECONDS = 3.0


def _is_windows() -> bool:
    return os.name == "nt"


class KeystoreError(RuntimeError):
    """A keystore operation failed in a way the caller must not ignore."""


class _ProfileState:
    """Cached backend and bounded-read state for one canonical profile."""

    def __init__(self, profile_identity: str) -> None:
        self.profile_identity = profile_identity
        self.backend = None
        self.resolved = False
        self.mode = None
        self.healthy = True
        self.backend_lock = threading.Lock()
        self.call_lock = threading.Lock()


_PROFILE_STATES: dict[str, _ProfileState] = {}
_PROFILE_STATES_LOCK = threading.Lock()


def _profile_state(profile_identity: str) -> _ProfileState:
    with _PROFILE_STATES_LOCK:
        state = _PROFILE_STATES.get(profile_identity)
        if state is None:
            state = _ProfileState(profile_identity)
            _PROFILE_STATES[profile_identity] = state
        return state


def _active_profile_identity() -> str:
    """Return the active profile's stable, canonical storage identity."""
    from hermes_constants import get_hermes_home

    return str(Path(get_hermes_home()).expanduser().resolve())


def _os_account_name(logical_key: str, profile_identity: str | None = None) -> str:
    """Return an opaque keyring account for one profile-local logical key."""
    profile_identity = profile_identity or _active_profile_identity()
    digest = hashlib.sha256(
        f"{profile_identity}\0{logical_key}".encode("utf-8")
    ).hexdigest()
    return f"hermes-profile-{digest}"


class OSKeystore:
    """The OS-native credential store, via the ``keyring`` package.

    Backends: macOS Keychain, Windows Credential Manager (DPAPI), Linux
    Secret Service.  Reached only after ``probe_os_keystore()`` confirms a
    working round trip, so callers can treat failures here as exceptional.
    """

    name = "os"

    def __init__(self, profile_identity: str | None = None) -> None:
        self._profile_identity = profile_identity or _active_profile_identity()
        self._state = _profile_state(self._profile_identity)

    def _account_name(self, logical_key: str) -> str:
        return _os_account_name(logical_key, self._profile_identity)

    @contextlib.contextmanager
    def _mutation_lock(self):
        """Serialize every OS mutation for this profile across processes."""
        root = Path(self._profile_identity) / "secrets"
        root.mkdir(parents=True, exist_ok=True)
        _ensure_private_permissions(root, 0o700)
        with _store_lock(root):
            yield

    def _guard(self) -> None:
        """Refuse if this backend is unavailable or already hung.

        Bounded reads call this while holding the profile call lock. That makes the
        check-and-spawn transition atomic: concurrent callers cannot all see a
        healthy latch and each abandon a worker before the first timeout.
        """
        if keyring is None:
            raise KeystoreError("keyring is not available")
        if not self._state.healthy:
            raise KeystoreError(
                "os keystore stopped responding earlier in this process"
            )

    def _bounded_read(self, operation):
        # Hold the lock through the timeout decision. Waiting callers then see
        # the false latch and fail without starting another worker.
        with self._state.call_lock:
            self._guard()
            sentinel = object()
            try:
                value = _call_bounded(operation, PROBE_TIMEOUT_SECONDS, sentinel)
            except Exception as exc:
                raise KeystoreError("keystore read failed") from exc
            if value is sentinel:
                _mark_os_unhealthy(self._state)
                raise KeystoreError("keystore read timed out")
            return value

    def get(self, key: str) -> str | None:
        """Read one secret, bounded.

        A successful probe does not make later reads safe: a keychain can be
        unlocked at startup and locked again by screen-lock or policy while
        the process runs. The Global Constraint covers reads as well as the
        probe, so the same bound applies here. A timeout raises KeystoreError,
        which get_secret turns into "not configured" rather than a hang.
        """
        return self._bounded_read(
            lambda: keyring.get_password(SERVICE_NAME, self._account_name(key))
        )

    def _set_unlocked(self, key: str, value: str) -> None:
        """Write one secret synchronously, with failures propagated.

        keyring exposes no cancellation primitive. Putting a mutation on the
        daemon timeout worker is unsafe: a timed-out set_password can commit
        later, after the caller was told it failed and after auto mode moved to
        the file tier. A later restart would then resurrect that credential.
        Probe/read are bounded because their user-secret operation is
        side-effect free; mutations remain synchronous until the backend can
        offer a cancellable or reconcilable API.
        """
        self._guard()
        try:
            keyring.set_password(SERVICE_NAME, self._account_name(key), value)
        except Exception as exc:
            raise KeystoreError("keystore write failed") from exc

    def set(self, key: str, value: str) -> None:
        with self._mutation_lock():
            self._set_unlocked(key, value)

    def _delete_unlocked(self, key: str) -> None:
        """Remove one secret. Absent is success; anything else raises.

        The distinction matters because this is the revocation path. keyring
        raises PasswordDeleteError specifically for "no such item", so an
        absent key is separable from a backend that refused -- and a refusal
        must reach the caller. Swallowing every exception here would make
        clear_secret report success while the credential kept working, which
        is the failure this whole feature exists to prevent.
        """
        self._guard()

        def _delete():
            try:
                keyring.delete_password(SERVICE_NAME, self._account_name(key))
            except _PasswordDeleteError:
                return None  # already absent -- the desired end state
            return None

        try:
            _delete()
        except Exception as exc:
            raise KeystoreError("keystore delete failed") from exc

    def delete(self, key: str) -> None:
        with self._mutation_lock():
            self._delete_unlocked(key)

    def set_many(self, values: Mapping[str, str]) -> None:
        """Synchronously write a batch, compensating prior writes on failure."""
        with self._mutation_lock():
            previous = {key: self.get(key) for key in values}
            changed: list[str] = []
            try:
                for key, value in values.items():
                    # A backend can commit before its transport reports an
                    # error, so include the current key before mutating it.
                    changed.append(key)
                    self._set_unlocked(key, value)
            except KeystoreError as write_error:
                rollback_errors: list[Exception] = []
                for key in reversed(changed):
                    try:
                        if previous[key] is None:
                            self._delete_unlocked(key)
                        else:
                            self._set_unlocked(key, previous[key])
                    except Exception as rollback_error:
                        rollback_errors.append(rollback_error)
                if rollback_errors:
                    raise KeystoreError(
                        "keystore batch write failed and rollback failed; "
                        "credential outcome is uncertain"
                    ) from write_error
                raise KeystoreError(
                    "keystore batch write failed; state restored"
                ) from write_error


def _call_bounded(operation, timeout_seconds: float, default):
    """Run `operation` on a daemon worker, returning `default` on timeout.

    Two failure modes exist and only one is an exception. A missing backend
    RAISES and is handled by the caller's except. A locked keyring or an
    unresponsive D-Bus Secret Service HANGS, and no except clause will ever
    see it -- while the Global Constraints require probing *and reads* to
    fail fast to the fallback rather than block. The backend runs headless
    under the desktop app, the workflow scheduler and cron, where a hang is
    indistinguishable from a crash.

    signal.alarm is not an option: it only works on the main thread of the
    main interpreter and this runs inside a spawned backend. This helper is
    deliberately limited to probes and reads. A read has no side effect; the
    probe touches only its namespaced, self-cleaning key. It must never wrap
    set_secret/delete_secret: Python cannot cancel the worker, so a timed-out
    credential mutation could commit after failure was reported. Tests faking
    the probe backend must keep the fake in place until the worker is done.
    """
    import threading

    outcome: list = []

    def _run() -> None:
        try:
            outcome.append(operation())
        except BaseException as exc:   # noqa: BLE001 - relayed to the caller
            outcome.append(exc)

    worker = threading.Thread(target=_run, name="hermes-keystore-io", daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive() or not outcome:
        return default
    result = outcome[0]
    if isinstance(result, BaseException):
        raise result
    return result


def _probe_round_trip(profile_identity: str | None = None) -> bool:
    """One set/get/delete cycle. Runs on a worker thread — see below."""
    # A unique probe account prevents concurrent probes for the same profile
    # from deleting one another's in-flight round-trip entry.
    probe_key = f"{_PROBE_KEY}\0{_secrets.token_hex(16)}"
    account_name = _os_account_name(probe_key, profile_identity)
    cleanup_succeeded = True
    try:
        keyring.set_password(SERVICE_NAME, account_name, _PROBE_VALUE)
        observed = keyring.get_password(SERVICE_NAME, account_name)
    except Exception:
        return False
    finally:
        try:
            keyring.delete_password(SERVICE_NAME, account_name)
        except _PasswordDeleteError:
            pass
        except Exception:
            cleanup_succeeded = False
    return cleanup_succeeded and observed == _PROBE_VALUE


def probe_os_keystore(
    timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
    *,
    profile_identity: str | None = None,
) -> bool:
    """Return True only when a full set/get/delete round trip succeeds in time.

    Mirrors super-cli's ``NewStore``, which writes a ``probe`` entry before
    committing to the OS keyring.  Two distinct failure modes have to be
    handled, and only one of them is an exception:

    * **Raises** — no backend installed, permission denied, keyring absent.
      Caught by ``_probe_round_trip``.
    * **Hangs** — a locked Linux keyring, or a D-Bus Secret Service that
      accepts the call and never answers.  No ``except`` clause will ever
      see this, and the Global Constraints require failing fast to the
      fallback rather than blocking: the backend runs headless under the
      desktop app, the workflow scheduler and cron, where a hang is
      indistinguishable from a crash.

    A daemon worker thread bounds the second case.  ``signal.alarm`` is not
    an option — it only works on the main thread of the main interpreter,
    and this runs inside a spawned backend.  The thread is abandoned rather
    than killed, which is safe: it holds no lock this process needs, and
    daemon threads do not block interpreter shutdown. It DOES still complete
    its call afterwards -- for the probe that means writing and then deleting
    a namespaced probe key, idempotent and self-cleaning, but a real write.
    Any test faking the backend must keep the fake in place until the worker
    has finished, or the abandoned thread will reach the real keyring.
    """
    if keyring is None:
        return False
    # Timeout -> False -> fall through to the file tier.
    return _call_bounded(
        lambda: _probe_round_trip(profile_identity), timeout_seconds, False
    )


class FileKeystore:
    """AES-GCM encrypted secret file, used when no OS keystore is reachable.

    Be clear-eyed about what this tier buys: the key sits beside the
    ciphertext at the same 0600 mode, so it is not a boundary against an
    attacker who can already read the user's home directory.  It defends
    against backups, cloud-sync, container images, support bundles and
    casual inspection.  super-cli's fallback has exactly this property; the
    real strength in both designs is the OS keystore tier.
    """

    name = "file"

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        try:
            root_info = self._root.lstat()
        except FileNotFoundError:
            root_info = None
        except OSError as exc:
            raise KeystoreError(
                f"cannot inspect secret-store root {self._root}"
            ) from exc
        if root_info is not None:
            if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
                raise KeystoreError(
                    f"secret-store root is not a direct directory: {self._root}"
                )
            self._secure_existing_store()

    def _secure_existing_store(self) -> None:
        _ensure_private_permissions(self._root, 0o700)
        for filename in (_KEY_FILE, _DATA_FILE, _LOCK_FILE, AUTHORITY_FILE):
            path = self._root / filename
            if _lstat_regular_artifact(path) is not None:
                _ensure_private_permissions(path, 0o600)

    def _initialize_root(self) -> None:
        """Create and secure the store root before a write transaction."""
        self._root.mkdir(parents=True, exist_ok=True)
        self._secure_existing_store()

    # -- key management -------------------------------------------------

    def _require_persistent_storage_for_new_key(self) -> None:
        """Fail before initialization unless a container mount is proven durable."""
        if (
            _lstat_regular_artifact(self._root / _KEY_FILE) is not None
            or not _in_container()
        ):
            return
        evidence = container_storage.inspect_mount_persistence(self._root)
        if evidence.state is container_storage.PersistenceState.PERSISTENT:
            return
        raise KeystoreError(
            f"refusing to generate a new encryption key for {self._root} "
            f"inside a container because storage persistence is "
            f"{evidence.state.value}: {evidence.reason}. Mount "
            f"HERMES_HOME on a persistent volume, then run "
            f"`hermes secrets doctor` and `hermes secrets repair`. "
            f"Setting secret_keystore: off in config.yaml disables the "
            f"keystore entirely -- it does NOT resume .env writes."
        )

    def _load_or_create_key(self) -> bytes:
        path = self._root / _KEY_FILE
        key_info = _lstat_regular_artifact(path)
        if key_info is not None:
            _ensure_private_permissions(path, 0o600)
            raw = _read_regular_file_nofollow(path, expected=key_info)
            if len(raw) != _KEY_BYTES:
                raise KeystoreError(
                    f"corrupt key file {path}: expected {_KEY_BYTES} bytes, "
                    f"got {len(raw)}"
                )
            return raw

        # Minting a key on a container root or memory/union filesystem is a
        # silent data-loss bug. Only fresh, positive evidence for a distinct
        # persistent mount authorizes first-run initialization.
        self._require_persistent_storage_for_new_key()

        raw = _secrets.token_bytes(_KEY_BYTES)
        _write_private(path, raw)
        return raw

    # -- payload --------------------------------------------------------

    def _read_all(self) -> dict[str, str]:
        path = self._root / _DATA_FILE
        data_info = _lstat_regular_artifact(path)
        if data_info is None:
            return {}
        _ensure_private_permissions(path, 0o600)
        if _lstat_regular_artifact(self._root / _KEY_FILE) is None:
            raise KeystoreError("cannot decrypt secrets: missing encryption key")
        blob = _read_regular_file_nofollow(path, expected=data_info)
        if len(blob) <= _NONCE_BYTES:
            raise KeystoreError("corrupt secret file")
        key = self._load_or_create_key()
        try:
            plaintext = AESGCM(key).decrypt(
                blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], None
            )
        except Exception as exc:
            raise KeystoreError(
                "cannot decrypt secrets (key may have changed)"
            ) from exc
        try:
            data = json.loads(plaintext)
        except json.JSONDecodeError as exc:
            raise KeystoreError("corrupt secret file") from exc
        if not isinstance(data, dict):
            raise KeystoreError("corrupt secret file")
        return {k: v for k, v in data.items() if isinstance(v, str)}

    def _write_all(self, data: dict[str, str]) -> None:
        key = self._load_or_create_key()
        nonce = _secrets.token_bytes(_NONCE_BYTES)
        ciphertext = AESGCM(key).encrypt(
            nonce, json.dumps(data).encode("utf-8"), None
        )
        _write_private(self._root / _DATA_FILE, nonce + ciphertext)

    # -- public API -----------------------------------------------------

    def _get_unlocked(self, key: str) -> str | None:
        """Read one value while the caller owns the profile transaction lock."""
        if _lstat_regular_artifact(self._root / _DATA_FILE) is None:
            return None
        return self._read_all().get(key)

    def _set_unlocked(self, key: str, value: str) -> None:
        """Write one value while the caller owns the profile transaction lock."""
        data = self._read_all()
        data[key] = value
        self._write_all(data)

    def _set_many_unlocked(self, values: Mapping[str, str]) -> None:
        if not values:
            return
        data = self._read_all()
        data.update(values)
        self._write_all(data)

    def _delete_unlocked(self, key: str) -> None:
        """Delete one value while the caller owns the profile transaction lock."""
        if _lstat_regular_artifact(self._root / _DATA_FILE) is None:
            return
        data = self._read_all()
        if data.pop(key, None) is not None:
            self._write_all(data)

    def get(self, key: str) -> str | None:
        if _lstat_regular_artifact(self._root / _DATA_FILE) is None:
            return None
        with _store_lock(self._root, create=False):
            return self._get_unlocked(key)

    def set(self, key: str, value: str) -> None:
        # The lock covers the complete read-modify-write transaction. Locking
        # only _write_all still loses one writer's update when two Hermes
        # processes read the same previous dictionary.
        self.set_many({key: value})

    def set_many(self, values: Mapping[str, str]) -> None:
        """Persist every value in one locked encrypted-file transaction."""
        if not values:
            return
        self._require_persistent_storage_for_new_key()
        self._initialize_root()
        with _store_lock(self._root):
            self._set_many_unlocked(values)

    def delete(self, key: str) -> None:
        if _lstat_regular_artifact(self._root / _DATA_FILE) is None:
            return
        with _store_lock(self._root, create=False):
            self._delete_unlocked(key)

    def keys(self) -> list[str]:
        if _lstat_regular_artifact(self._root / _DATA_FILE) is None:
            return []
        with _store_lock(self._root, create=False):
            return list(self._read_all())


@contextlib.contextmanager
def _store_lock(root: Path, *, create: bool = True, secure: bool = True):
    """Serialize one whole file-store transaction across threads/processes.

    The separate lock file remains stable while key/ciphertext files are
    replaced atomically. Native advisory locks are released automatically if
    a process crashes. The thread lock is also required because POSIX flock
    ownership semantics alone do not provide a portable same-process mutex.
    """
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise KeystoreError(f"secret transaction root is unavailable at {root}") from exc
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode):
        raise KeystoreError(f"secret transaction root is not a direct directory: {root}")
    lock_path = root / _LOCK_FILE
    try:
        lock_path.lstat()
        lock_existed = True
    except FileNotFoundError:
        lock_existed = False
    except OSError as exc:
        raise KeystoreError(
            f"secret transaction lock is unavailable at {lock_path}"
        ) from exc
    with _FILE_THREAD_LOCK:
        flags = os.O_RDWR | (os.O_CREAT if create else 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(str(lock_path), flags, 0o600)
        except OSError as exc:
            raise KeystoreError(
                f"secret transaction lock is unavailable at {lock_path}"
            ) from exc
        lock_info = os.fstat(fd)
        if not stat.S_ISREG(lock_info.st_mode):
            os.close(fd)
            raise KeystoreError(
                f"secret transaction lock is not a regular file: {lock_path}"
            )
        with os.fdopen(fd, "r+b") as handle:
            if secure or (_is_windows() and not lock_existed):
                _ensure_private_permissions(lock_path, 0o600)
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.seek(0)
                    handle.write(b"\0")
                    handle.flush()
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _ensure_private_permissions(path: Path, mode: int) -> None:
    """Establish and verify the required private permissions for a path."""
    if _is_windows():
        from hermes_cli.windows_permissions import (
            WindowsAclError,
            restrict_directory_to_current_user,
            restrict_file_to_current_user,
        )

        try:
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise KeystoreError(f"refusing to ACL linked path {path}")
            if stat.S_ISDIR(info.st_mode):
                restrict_directory_to_current_user(path)
            elif stat.S_ISREG(info.st_mode):
                restrict_file_to_current_user(path)
            else:
                raise KeystoreError(f"refusing to ACL non-regular path {path}")
        except KeystoreError:
            raise
        except WindowsAclError as exc:
            raise KeystoreError(f"cannot enforce Windows ACL on {path}") from exc
        except OSError as exc:
            raise KeystoreError(f"cannot inspect path for Windows ACL: {path}") from exc
        return
    fd: int | None = None
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not (
            stat.S_ISREG(before.st_mode) or stat.S_ISDIR(before.st_mode)
        ):
            raise KeystoreError(f"refusing to chmod non-regular path {path}")
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(path), flags)
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise KeystoreError(f"path changed while securing permissions: {path}")
        # Keep the established chmod failure seam while explicitly refusing
        # link traversal. The already-open no-follow descriptor anchors and
        # verifies the intended inode across this pathname operation.
        try:
            os.chmod(path, mode, follow_symlinks=False)
        except NotImplementedError:
            # Linux commonly exposes chmod but not its no-follow variant.
            # The descriptor above was opened with O_NOFOLLOW and remains the
            # authority for both mutation and verification below.
            pass
        except TypeError as exc:
            message = str(exc)
            if (
                "follow_symlinks" not in message
                and "takes no keyword arguments" not in message
            ):
                raise
        after = path.lstat()
        if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
            raise KeystoreError(f"path changed while securing permissions: {path}")
        os.fchmod(fd, mode)
        actual_mode = stat.S_IMODE(os.fstat(fd).st_mode)
    except KeystoreError:
        raise
    except (OSError, NotImplementedError) as exc:
        raise KeystoreError(
            f"cannot secure permissions on {path} to {mode:o}"
        ) from exc
    finally:
        if fd is not None:
            os.close(fd)
    if actual_mode != mode:
        raise KeystoreError(
            f"cannot secure permissions on {path} to {mode:o}; "
            f"found {actual_mode:o}"
        )


def _in_container() -> bool:
    """Compatibility wrapper around shared container-runtime detection."""
    return container_storage.is_container()


def _write_private(path: Path, payload: bytes) -> None:
    """Atomically replace an owner-only file.

    The temporary file lives beside the target, so os.replace is atomic on the
    same filesystem. A crash or disk error before replace leaves the last good
    key/ciphertext intact rather than exposing a truncated live file.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        if not _is_windows():
            try:
                os.fchmod(fd, 0o600)
            except (AttributeError, OSError, NotImplementedError) as exc:
                raise KeystoreError(
                    f"cannot secure permissions on {path} to 600"
                ) from exc
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            existing = path.lstat()
        except FileNotFoundError:
            existing = None
        except OSError as exc:
            raise KeystoreError(
                f"cannot inspect private replacement path {path}"
            ) from exc
        if existing is not None and (
            stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode)
        ):
            raise KeystoreError(
                f"private replacement target is not a regular file: {path}"
            )
        if _is_windows():
            replaced_path = Path(atomic_replace(tmp_name, path))
        else:
            os.replace(tmp_name, path)
            replaced_path = path
        _ensure_private_permissions(replaced_path, 0o600)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


__all__ += [
    "SecretAuthority",
    "get_backend",
    "get_configured_mode",
    "get_authority",
    "get_secret",
    "resolve_secret",
    "set_secret",
    "set_secrets",
    "delete_secret",
    "move_secret",
    "reset_backend_cache",
]

# AGENTS.md:124 rejects new HERMES_* env vars for non-secret config: ".env is
# for secrets only ... All behavioral settings -- timeouts, thresholds, feature
# flags, display prefs -- go in config.yaml." The keystore mode is a feature
# flag, so config.yaml is its home. AGENTS.md does permit an internal bridge
# env var, which _MODE_ENV remains -- undocumented, for tests and emergency
# override only. User-facing docs must point at config.yaml.
_MODE_KEY = "secret_keystore"
_MODE_ENV = "HERMES_SECRET_KEYSTORE"  # internal bridge; not user-facing
_VALID_MODES = frozenset({"auto", "os", "file", "off"})


def _resolve_mode() -> str:
    """config.yaml `secret_keystore`, with an internal env bridge.

    Precedence is env-then-config so a test or an operator unpicking a broken
    keychain can override without editing config.yaml. An unrecognised value
    falls back to "auto" rather than raising: this runs on the credential
    resolution path, and a typo in config.yaml must not make every plugin
    unreadable.
    """
    raw = os.environ.get(_MODE_ENV)
    if raw is None:
        try:
            from hermes_cli.config import get_config_path, load_config_readonly

            config = load_config_readonly(
                config_path=get_config_path(),
                suppress_parse_failure_side_effects=True,
            )
            raw = config.get(_MODE_KEY) if isinstance(config, dict) else None
        except Exception:
            raw = None
    mode = str(raw or "auto").strip().lower()
    return mode if mode in _VALID_MODES else "auto"


def get_configured_mode() -> Literal["auto", "os", "file", "off"]:
    """Return configured policy without resolving or probing a backend."""
    return cast(Literal["auto", "os", "file", "off"], _resolve_mode())


def reset_backend_cache() -> None:
    """Clear the cached backend. For tests and for post-migration re-probe."""
    # Detach the registry before acquiring any profile locks. Operations that
    # already hold a state keep their own object; future lookups get a fresh
    # lifecycle without ever reversing call-lock -> backend-lock ordering.
    with _PROFILE_STATES_LOCK:
        states = list(_PROFILE_STATES.values())
        _PROFILE_STATES.clear()
    for state in states:
        with state.call_lock:
            with state.backend_lock:
                state.backend = None
                state.resolved = False
                state.mode = None
                state.healthy = True


def _secrets_root(profile_identity: str) -> Path:
    return Path(profile_identity) / "secrets"


def _lstat_regular_artifact(path: Path) -> os.stat_result | None:
    """Return no-follow metadata or reject a non-regular store artifact."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise KeystoreError(f"cannot inspect secret-store artifact {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise KeystoreError(f"secret-store artifact is not a regular file: {path}")
    return info


def _artifact_present_nofollow(path: Path) -> bool:
    """Return whether a directory entry exists, including a dangling link."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise KeystoreError(f"cannot inspect secret-store artifact {path}") from exc
    return True


def _read_regular_file_nofollow(
    path: Path,
    *,
    expected: os.stat_result | None = None,
) -> bytes:
    """Read one direct regular file without traversing a symbolic link."""
    expected = expected or _lstat_regular_artifact(path)
    if expected is None:
        raise KeystoreError(f"secret-store artifact is missing: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd: int | None = None
    try:
        fd = os.open(str(path), flags)
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (expected.st_dev, expected.st_ino):
            raise KeystoreError(f"secret-store artifact changed while reading: {path}")
        with os.fdopen(fd, "rb") as handle:
            fd = None
            return handle.read()
    except KeystoreError:
        raise
    except OSError as exc:
        raise KeystoreError(f"cannot read secret-store artifact {path}") from exc
    finally:
        if fd is not None:
            os.close(fd)


def _read_file_store_readonly(root: Path) -> dict[str, str]:
    """Validate and decrypt the file tier without creating or chmodding paths.

    ``FileKeystore`` deliberately repairs permissions when it opens an existing
    store. Doctor cannot use that constructor: even a helpful chmod would
    violate its byte-for-byte read-only contract. This repair-only primitive
    therefore mirrors the strict read path using only filesystem reads; it is
    private so ordinary resolution remains lazy and per-key.
    """
    root = Path(root)
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        root_info = None
    except OSError as exc:
        raise KeystoreError(f"cannot inspect secret-store root {root}") from exc
    if root_info is not None and (
        stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode)
    ):
        raise KeystoreError(f"secret-store root is not a direct directory: {root}")
    key_path = root / _KEY_FILE
    data_path = root / _DATA_FILE
    key_info = _lstat_regular_artifact(key_path)
    data_info = _lstat_regular_artifact(data_path)
    key_exists = key_info is not None
    data_exists = data_info is not None
    if not data_exists:
        if key_exists:
            raw_key = _read_regular_file_nofollow(key_path, expected=key_info)
            if len(raw_key) != _KEY_BYTES:
                raise KeystoreError(
                    f"corrupt key file {key_path}: expected {_KEY_BYTES} bytes, "
                    f"got {len(raw_key)}"
                )
        return {}
    if not key_exists:
        raise KeystoreError("cannot decrypt secrets: missing encryption key")
    key = _read_regular_file_nofollow(key_path, expected=key_info)
    if len(key) != _KEY_BYTES:
        raise KeystoreError(
            f"corrupt key file {key_path}: expected {_KEY_BYTES} bytes, "
            f"got {len(key)}"
        )
    blob = _read_regular_file_nofollow(data_path, expected=data_info)
    if len(blob) <= _NONCE_BYTES:
        raise KeystoreError("corrupt secret file")
    try:
        plaintext = AESGCM(key).decrypt(
            blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], None
        )
    except Exception as exc:
        raise KeystoreError("cannot decrypt secrets (key may have changed)") from exc
    try:
        data = json.loads(plaintext)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KeystoreError("corrupt secret file") from exc
    if not isinstance(data, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in data.items()
    ):
        raise KeystoreError("corrupt secret file")
    return dict(data)


def get_backend():
    """Return the active backend, or None when the keystore is disabled.

    Resolved once per process: the probe can involve IPC to a keychain
    daemon and this is called on every secret resolution.
    """
    profile_identity = _active_profile_identity()
    state = _profile_state(profile_identity)
    # Keep cache rebinding in the same lock order as bounded reads and health
    # demotion. A profile transition resets the entire selection lifecycle so
    # a timed-out OS read in one profile cannot poison another profile.
    with state.call_lock:
        with state.backend_lock:
            if state.resolved:
                return state.backend
            state.backend = None
            state.resolved = False
            state.mode = None
            mode = _resolve_mode()
            if mode == "off":
                state.backend = None
            elif mode == "file":
                state.backend = FileKeystore(_secrets_root(profile_identity))
            elif mode == "os":
                state.backend = OSKeystore(profile_identity)
            else:  # auto
                state.backend = (
                    OSKeystore(profile_identity)
                    if state.healthy
                    and probe_os_keystore(profile_identity=profile_identity)
                    else FileKeystore(_secrets_root(profile_identity))
                )
            state.mode = mode
            state.resolved = True
            return state.backend


def _mark_os_unhealthy(state: _ProfileState) -> None:
    """Latch OS health without ever changing the selected or durable tier."""
    state.healthy = False


def _ensure_transaction_root(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _ensure_private_permissions(root, 0o700)


def _write_authority_registry(root: Path, registry: AuthorityRegistry) -> None:
    """Replace authority metadata atomically while the caller owns the lock."""
    _write_private(root / AUTHORITY_FILE, encode_authority_registry(registry))


def _registry_with_updates(
    registry: AuthorityRegistry | None,
    updates: Mapping[str, SecretAuthority],
) -> AuthorityRegistry:
    entries = dict(registry.entries) if registry is not None else {}
    entries.update(updates)
    return AuthorityRegistry(version=AUTHORITY_VERSION, entries=entries)


def _read_tier(
    tier: SecretAuthority,
    key: str,
    *,
    file_store: FileKeystore,
    os_store: OSKeystore,
) -> str | None:
    if tier is SecretAuthority.FILE:
        return file_store._get_unlocked(key)
    if tier is SecretAuthority.OS:
        return os_store.get(key)
    raise KeystoreError("cleared authority has no readable tier")


def _set_tier(
    tier: SecretAuthority,
    key: str,
    value: str,
    *,
    file_store: FileKeystore,
    os_store: OSKeystore,
) -> None:
    if tier is SecretAuthority.FILE:
        file_store._set_unlocked(key, value)
        return
    if tier is SecretAuthority.OS:
        os_store._set_unlocked(key, value)
        return
    raise KeystoreError("cannot write cleared authority")


def _delete_tier(
    tier: SecretAuthority,
    key: str,
    *,
    file_store: FileKeystore,
    os_store: OSKeystore,
) -> None:
    if tier is SecretAuthority.FILE:
        file_store._delete_unlocked(key)
        return
    if tier is SecretAuthority.OS:
        os_store._delete_unlocked(key)
        return
    raise KeystoreError("cannot delete cleared authority")


def _pre_registry_copies(
    key: str,
    *,
    file_store: FileKeystore,
    os_store: OSKeystore,
) -> tuple[str | None, bool, str | None]:
    """Return file value, whether OS state is known, and OS value."""
    file_value = file_store._get_unlocked(key)
    try:
        os_value = os_store.get(key)
    except Exception:
        return file_value, False, None
    return file_value, True, os_value


def _infer_pre_registry_tiers(
    file_value: str | None,
    os_known: bool,
    os_value: str | None,
    *,
    mode: str,
) -> tuple[str | None, tuple[SecretAuthority, ...]]:
    """Fail closed on competing or incompletely observable legacy state."""
    if not os_known:
        if file_value is not None:
            raise KeystoreError(
                "cannot determine OS keystore state while file data exists"
            )
        return None, ()
    if file_value is not None and os_value is not None:
        if file_value != os_value:
            raise KeystoreError("competing pre-registry secret values")
        if mode == "file":
            preferred = SecretAuthority.FILE
        else:
            preferred = SecretAuthority.OS
        other = (
            SecretAuthority.OS
            if preferred is SecretAuthority.FILE
            else SecretAuthority.FILE
        )
        return file_value, (preferred, other)
    if file_value is not None:
        return file_value, (SecretAuthority.FILE,)
    if os_value is not None:
        return os_value, (SecretAuthority.OS,)
    return None, ()


def _selected_new_authority(mode: str) -> SecretAuthority:
    if mode == "os":
        return SecretAuthority.OS
    if mode == "file":
        return SecretAuthority.FILE
    state = _profile_state(_active_profile_identity())
    if not state.healthy:
        # This chooses only the destination for an unregistered new key. It
        # deliberately does not mutate the process-cached backend, so existing
        # OS authority cannot be redirected to a stale file copy.
        return SecretAuthority.FILE
    backend = get_backend()
    if backend is None:
        raise KeystoreError(
            "secret keystore is disabled (secret_keystore: off in config.yaml)"
        )
    if getattr(backend, "name", None) == "os":
        return SecretAuthority.OS
    if getattr(backend, "name", None) == "file":
        return SecretAuthority.FILE
    raise KeystoreError("keystore selected an unknown backend")


def _assert_mutation_mode(mode: str, authority: SecretAuthority) -> None:
    if mode == "off":
        raise KeystoreError(
            "secret keystore is disabled (secret_keystore: off in config.yaml)"
        )
    if mode in {"os", "file"} and authority.value != mode:
        raise KeystoreError(
            f"registered {authority.value} authority conflicts with {mode} mode; "
            f"use move_secret(..., destination='{mode}')"
        )


def _registry_bytes(root: Path) -> bytes | None:
    path = root / AUTHORITY_FILE
    info = _lstat_regular_artifact(path)
    return (
        _read_regular_file_nofollow(path, expected=info)
        if info is not None
        else None
    )


def _restore_registry_bytes(root: Path, previous: bytes | None) -> None:
    path = root / AUTHORITY_FILE
    info = _lstat_regular_artifact(path)
    current = (
        _read_regular_file_nofollow(path, expected=info)
        if info is not None
        else None
    )
    if current == previous:
        return
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        _write_private(path, previous)


def _apply_value_transaction(
    *,
    root: Path,
    registry: AuthorityRegistry | None,
    operations: list[tuple[str, SecretAuthority, str, str | None]],
    authority_updates: Mapping[str, SecretAuthority],
    file_store: FileKeystore,
    os_store: OSKeystore,
    failure_label: str,
) -> None:
    """Apply value operations, then authority, compensating both on failure."""
    locations = list(dict.fromkeys((tier, key) for _op, tier, key, _value in operations))
    try:
        snapshots = {
            location: _read_tier(
                location[0],
                location[1],
                file_store=file_store,
                os_store=os_store,
            )
            for location in locations
        }
    except Exception as exc:
        raise KeystoreError(f"{failure_label} failed before mutation") from exc

    changed: list[tuple[SecretAuthority, str]] = []
    previous_registry = _registry_bytes(root)
    metadata_attempted = False
    try:
        for operation, tier, key, value in operations:
            location = (tier, key)
            if location not in changed:
                # Include the current location before mutation because a
                # backend may commit and then lose its reply.
                changed.append(location)
            if operation == "set":
                assert value is not None
                _set_tier(
                    tier,
                    key,
                    value,
                    file_store=file_store,
                    os_store=os_store,
                )
                observed = _read_tier(
                    tier,
                    key,
                    file_store=file_store,
                    os_store=os_store,
                )
                if observed != value:
                    raise KeystoreError("destination verification failed")
            elif operation == "delete":
                _delete_tier(
                    tier,
                    key,
                    file_store=file_store,
                    os_store=os_store,
                )
                if _read_tier(
                    tier,
                    key,
                    file_store=file_store,
                    os_store=os_store,
                ) is not None:
                    raise KeystoreError("source deletion verification failed")
            else:  # pragma: no cover - internal invariant
                raise AssertionError(f"unknown secret operation: {operation}")

        updated_registry = _registry_with_updates(registry, authority_updates)
        old_entries = dict(registry.entries) if registry is not None else {}
        if dict(updated_registry.entries) != old_entries:
            metadata_attempted = True
            _write_authority_registry(root, updated_registry)
    except Exception as operation_error:
        rollback_errors: list[Exception] = []
        for tier, key in reversed(changed):
            try:
                previous = snapshots[(tier, key)]
                if previous is None:
                    _delete_tier(
                        tier,
                        key,
                        file_store=file_store,
                        os_store=os_store,
                    )
                else:
                    _set_tier(
                        tier,
                        key,
                        previous,
                        file_store=file_store,
                        os_store=os_store,
                    )
                if _read_tier(
                    tier,
                    key,
                    file_store=file_store,
                    os_store=os_store,
                ) != previous:
                    raise KeystoreError("rollback verification failed")
            except Exception as rollback_error:
                rollback_errors.append(rollback_error)
        if metadata_attempted:
            try:
                _restore_registry_bytes(root, previous_registry)
            except Exception as rollback_error:
                rollback_errors.append(rollback_error)
        if rollback_errors:
            raise KeystoreError(
                f"{failure_label} failed and rollback failed; credential outcome "
                "is uncertain"
            ) from operation_error
        raise KeystoreError(f"{failure_label} failed; state restored") from operation_error


def get_authority(key: str) -> SecretAuthority | None:
    """Return durable authority without creating the profile store or lock."""
    root = _secrets_root(_active_profile_identity())
    path = root / AUTHORITY_FILE
    if not _artifact_present_nofollow(path):
        return None
    with _store_lock(root, create=False, secure=False):
        registry = load_authority_registry(root)
        return registry.entries.get(key) if registry is not None else None


def resolve_secret(key: str, *, legacy_value: str | None = None) -> str | None:
    """Resolve one secret through its durable authority, never creating state."""
    try:
        mode = _resolve_mode()
        if mode == "off":
            return None
        profile_identity = _active_profile_identity()
        root = _secrets_root(profile_identity)

        def _resolve_current(*, file_state_locked: bool) -> str | None:
            registry = load_authority_registry(root)
            authority = registry.entries.get(key) if registry is not None else None
            if authority is SecretAuthority.CLEARED:
                return None
            if authority is SecretAuthority.OS:
                return OSKeystore(profile_identity).get(key)
            if authority is SecretAuthority.FILE:
                return _read_file_store_readonly(root).get(key)
            if legacy_value not in {None, ""}:
                return legacy_value

            # Pre-registry inference uses the same no-create, no-permission-
            # repair reader as doctor. Reads must never mutate drifted file
            # artifacts merely because they are being considered as a tier.
            if file_state_locked:
                file_value = _read_file_store_readonly(root).get(key)
            else:
                # With no lock file, do not race a newly-created encrypted
                # store by reading it unlocked. A post-read check below retries
                # under the lock if a writer creates transaction state.
                file_value = None
            try:
                os_value = OSKeystore(profile_identity).get(key)
                os_known = True
            except Exception:
                os_value = None
                os_known = False
            value, _tiers = _infer_pre_registry_tiers(
                file_value,
                os_known,
                os_value,
                mode=mode,
            )
            return value

        lock_path = root / _LOCK_FILE
        if _artifact_present_nofollow(lock_path):
            with _store_lock(root, create=False, secure=False):
                return _resolve_current(file_state_locked=True)
        # Durable authority without its transaction lock is an inconsistent
        # live store and must fail closed. Pre-registry file debris alone does
        # not block a higher-precedence legacy value.
        if _artifact_present_nofollow(root / AUTHORITY_FILE):
            return None
        result = _resolve_current(file_state_locked=False)
        if _artifact_present_nofollow(lock_path):
            with _store_lock(root, create=False, secure=False):
                return _resolve_current(file_state_locked=True)
        if _artifact_present_nofollow(root / AUTHORITY_FILE):
            return None
        return result
    except Exception:
        # Credential reads remain non-throwing. Corruption and unavailable
        # authority surface as unconfigured until doctor/repair is requested.
        return None


def get_secret(key: str) -> str | None:
    """Compatibility wrapper for the authority-aware read facade."""
    return resolve_secret(key)


def set_secret(key: str, value: str) -> None:
    set_secrets({key: value})


def set_secrets(values: Mapping[str, str]) -> None:
    """Write a service batch and commit all authority changes together."""
    if not values:
        return
    mode = _resolve_mode()
    if mode == "off":
        raise KeystoreError(
            "secret keystore is disabled (secret_keystore: off in config.yaml)"
        )
    profile_identity = _active_profile_identity()
    root = _secrets_root(profile_identity)
    try:
        preselected: SecretAuthority | None = None
        if not root.exists():
            preselected = _selected_new_authority(mode)
            if preselected is SecretAuthority.FILE:
                FileKeystore(root)._require_persistent_storage_for_new_key()
        else:
            # Reject linked or special live artifacts before even repairing
            # root permissions or creating a transaction lock. Corruption is
            # exclusively doctor/repair territory.
            authority_info = _lstat_regular_artifact(root / AUTHORITY_FILE)
            for filename in (_KEY_FILE, _DATA_FILE):
                _lstat_regular_artifact(root / filename)
            if authority_info is not None:
                load_authority_registry(root)
        _ensure_transaction_root(root)
        with _store_lock(root):
            registry = load_authority_registry(root)
            entries = dict(registry.entries) if registry is not None else {}
            file_store = FileKeystore(root)
            os_store = OSKeystore(profile_identity)
            operations: list[tuple[str, SecretAuthority, str, str | None]] = []
            updates: dict[str, SecretAuthority] = {}
            selected = preselected

            for key, value in values.items():
                authority = entries.get(key)
                if authority in {SecretAuthority.OS, SecretAuthority.FILE}:
                    _assert_mutation_mode(mode, authority)
                    target = authority
                    source_tiers: tuple[SecretAuthority, ...] = (authority,)
                else:
                    if selected is None:
                        selected = _selected_new_authority(mode)
                    target = selected
                    source_tiers = ()
                    if authority is None:
                        file_value, os_known, os_value = _pre_registry_copies(
                            key,
                            file_store=file_store,
                            os_store=os_store,
                        )
                        _old_value, source_tiers = _infer_pre_registry_tiers(
                            file_value,
                            os_known,
                            os_value,
                            mode=mode,
                        )
                operations.append(("set", target, key, value))
                for source in source_tiers:
                    if source is not target:
                        operations.append(("delete", source, key, None))
                updates[key] = target

            _apply_value_transaction(
                root=root,
                registry=registry,
                operations=operations,
                authority_updates=updates,
                file_store=file_store,
                os_store=os_store,
                failure_label="keystore batch write",
            )
    except KeystoreError:
        raise
    except AuthorityRegistryError as exc:
        raise KeystoreError(
            "authority registry is corrupt; run `hermes secrets doctor` and "
            "`hermes secrets repair --apply`"
        ) from exc
    except Exception as exc:
        raise KeystoreError("keystore batch write failed") from exc


def _move_secret_locked(
    key: str,
    target: SecretAuthority,
    *,
    profile_identity: str,
    root: Path,
    mode: str,
    failure_label: str = "keystore move",
) -> None:
    """Run the move protocol while the caller holds the profile lock."""
    registry = load_authority_registry(root)
    authority = registry.entries.get(key) if registry is not None else None
    file_store = FileKeystore(root)
    os_store = OSKeystore(profile_identity)
    source_tiers: tuple[SecretAuthority, ...]
    if authority in {SecretAuthority.OS, SecretAuthority.FILE}:
        source_tiers = (authority,)
        value = _read_tier(
            authority,
            key,
            file_store=file_store,
            os_store=os_store,
        )
    elif authority is SecretAuthority.CLEARED:
        raise KeystoreError("cannot move a cleared secret")
    else:
        file_value, os_known, os_value = _pre_registry_copies(
            key,
            file_store=file_store,
            os_store=os_store,
        )
        value, source_tiers = _infer_pre_registry_tiers(
            file_value,
            os_known,
            os_value,
            mode=mode,
        )
    if value is None:
        raise KeystoreError("cannot move an absent secret")
    if authority is target:
        return

    operations: list[tuple[str, SecretAuthority, str, str | None]] = [
        ("set", target, key, value)
    ]
    for source in source_tiers:
        if source is not target:
            operations.append(("delete", source, key, None))
    _apply_value_transaction(
        root=root,
        registry=registry,
        operations=operations,
        authority_updates={key: target},
        file_store=file_store,
        os_store=os_store,
        failure_label=failure_label,
    )


def move_secret(
    key: str,
    destination: Literal["os", "file"],
) -> None:
    """Transactionally move one key between durable authority tiers."""
    if destination not in {"os", "file"}:
        raise KeystoreError("destination must be 'os' or 'file'")
    mode = _resolve_mode()
    if mode == "off":
        raise KeystoreError(
            "secret keystore is disabled (secret_keystore: off in config.yaml)"
        )
    profile_identity = _active_profile_identity()
    root = _secrets_root(profile_identity)
    target = SecretAuthority(destination)
    try:
        if target is SecretAuthority.FILE and not root.exists():
            FileKeystore(root)._require_persistent_storage_for_new_key()
        _ensure_transaction_root(root)
        with _store_lock(root):
            _move_secret_locked(
                key,
                target,
                profile_identity=profile_identity,
                root=root,
                mode=mode,
            )
    except KeystoreError:
        raise
    except Exception as exc:
        raise KeystoreError("keystore move failed") from exc


def delete_secret(key: str) -> None:
    """Revoke a secret and record a tombstone without restoring plaintext."""
    mode = _resolve_mode()
    if mode == "off":
        raise KeystoreError(
            "secret keystore is disabled (secret_keystore: off in config.yaml)"
        )
    profile_identity = _active_profile_identity()
    root = _secrets_root(profile_identity)
    try:
        _ensure_transaction_root(root)
        with _store_lock(root):
            registry = load_authority_registry(root)
            authority = registry.entries.get(key) if registry is not None else None
            file_store = FileKeystore(root)
            os_store = OSKeystore(profile_identity)

            tiers: tuple[SecretAuthority, ...] = ()
            pre_registry_os_unknown = False
            if authority in {SecretAuthority.OS, SecretAuthority.FILE}:
                _assert_mutation_mode(mode, authority)
                tiers = (authority,)
            elif authority is None:
                file_value, os_known, os_value = _pre_registry_copies(
                    key,
                    file_store=file_store,
                    os_store=os_store,
                )
                if os_known:
                    _value, tiers = _infer_pre_registry_tiers(
                        file_value,
                        os_known,
                        os_value,
                        mode=mode,
                    )
                else:
                    pre_registry_os_unknown = True

            # Plaintext removal is deliberately first and is never compensated.
            # A retry therefore cannot resurrect a legacy value after a later
            # backend refusal.
            from hermes_cli.config import remove_env_value

            remove_env_value(
                key,
                strict=True,
                mirror_process_env=False,
            )
            if pre_registry_os_unknown:
                raise KeystoreError(
                    "cannot determine OS keystore state for unregistered secret"
                )
            for tier in tiers:
                _delete_tier(
                    tier,
                    key,
                    file_store=file_store,
                    os_store=os_store,
                )
                if _read_tier(
                    tier,
                    key,
                    file_store=file_store,
                    os_store=os_store,
                ) is not None:
                    raise KeystoreError("keystore delete verification failed")
            updated = _registry_with_updates(
                registry,
                {key: SecretAuthority.CLEARED},
            )
            if registry is None or dict(updated.entries) != dict(registry.entries):
                _write_authority_registry(root, updated)
    except Exception as exc:
        if isinstance(exc, KeystoreError):
            raise
        raise KeystoreError("keystore delete failed") from exc
