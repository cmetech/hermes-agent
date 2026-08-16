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
import json
import os
import secrets as _secrets
import tempfile
import threading
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# The one Hermes import this module allows itself beyond get_hermes_home.
# _is_container already exists at hermes_cli/config.py:836 and honours the
# HERMES_CONTAINER / HERMES_SKIP_CHMOD opt-outs plus the /.dockerenv marker,
# so container detection stays one implementation rather than two.
#
# D4's container check is _in_container() below rather than
# config._is_container(): that helper also returns True for HERMES_SKIP_CHMOD,
# a permission opt-out used on NFS and SMB mounts that are not containers.

__all__ = ["KeystoreError", "FileKeystore"]

_KEY_BYTES = 32
_NONCE_BYTES = 12
_KEY_FILE = "keystore.key"
_DATA_FILE = "keystore.enc"
_LOCK_FILE = "keystore.lock"
_FILE_THREAD_LOCK = threading.RLock()


class KeystoreError(RuntimeError):
    """A keystore operation failed in a way the caller must not ignore."""


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
        self._root.mkdir(parents=True, exist_ok=True)
        _chmod(self._root, 0o700)

    # -- key management -------------------------------------------------

    def _load_or_create_key(self) -> bytes:
        path = self._root / _KEY_FILE
        if path.exists():
            raw = path.read_bytes()
            if len(raw) != _KEY_BYTES:
                raise KeystoreError(
                    f"corrupt key file {path}: expected {_KEY_BYTES} bytes, "
                    f"got {len(raw)}"
                )
            return raw

        # D4: in a container, minting a fresh key is usually a silent
        # data-loss bug rather than first-run setup. If HERMES_HOME is not a
        # persisted volume, the key dies with the container and every secret
        # encrypted under it becomes permanently unreadable -- and the symptom
        # ("all my credentials vanished after a restart") points nowhere near
        # the cause. Refuse loudly instead, and name the fix.
        if _in_container():
            raise KeystoreError(
                f"refusing to generate a new encryption key at {path} inside a "
                f"container. The key would not survive a restart and every "
                f"secret written under it would become unreadable. Mount "
                f"HERMES_HOME on a persistent volume. Setting "
                f"secret_keystore: off in config.yaml disables the keystore "
                f"entirely -- it does NOT resume .env writes, which the write "
                f"path refuses by design."
            )

        raw = _secrets.token_bytes(_KEY_BYTES)
        _write_private(path, raw)
        return raw

    # -- payload --------------------------------------------------------

    def _read_all(self) -> dict[str, str]:
        path = self._root / _DATA_FILE
        if not path.exists():
            return {}
        blob = path.read_bytes()
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

    def get(self, key: str) -> str | None:
        with _store_lock(self._root):
            return self._read_all().get(key)

    def set(self, key: str, value: str) -> None:
        # The lock covers the complete read-modify-write transaction. Locking
        # only _write_all still loses one writer's update when two Hermes
        # processes read the same previous dictionary.
        with _store_lock(self._root):
            data = self._read_all()
            data[key] = value
            self._write_all(data)

    def delete(self, key: str) -> None:
        with _store_lock(self._root):
            data = self._read_all()
            if data.pop(key, None) is not None:
                self._write_all(data)

    def keys(self) -> list[str]:
        with _store_lock(self._root):
            return list(self._read_all())


@contextlib.contextmanager
def _store_lock(root: Path):
    """Serialize one whole file-store transaction across threads/processes.

    The separate lock file remains stable while key/ciphertext files are
    replaced atomically. Native advisory locks are released automatically if
    a process crashes. The thread lock is also required because POSIX flock
    ownership semantics alone do not provide a portable same-process mutex.
    """
    lock_path = root / _LOCK_FILE
    with _FILE_THREAD_LOCK:
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+b") as handle:
            _chmod(lock_path, 0o600)
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
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


def _chmod(path: Path, mode: int) -> None:
    """chmod, tolerating platforms and filesystems that do not support it."""
    try:
        os.chmod(path, mode)
    except (OSError, NotImplementedError):
        pass


def _in_container() -> bool:
    """Container detection for D4, narrower than config._is_container.

    config._is_container() also returns True for HERMES_SKIP_CHMOD, which is
    a *permission* opt-out people set on NFS and SMB mounts where 0600 breaks
    multi-UID access -- ordinary hosts, not containers. Reusing it here would
    refuse key creation on an NFS box and give the operator a message about
    persistent volumes that makes no sense on their machine.

    HERMES_CONTAINER stays honoured: that one does mean "container".
    """
    if os.environ.get("HERMES_CONTAINER"):
        return True
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8") as handle:
            cgroup = handle.read()
    except OSError:
        return False
    return any(marker in cgroup for marker in ("docker", "lxc", "kubepods"))


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
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        _chmod(path, 0o600)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
