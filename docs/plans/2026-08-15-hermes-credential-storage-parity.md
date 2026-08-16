# Hermes Credential Storage Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store per-profile plugin secrets (Jira/GitLab/Confluence/ARM PATs) in the OS-native credential store with an encrypted-file fallback, so no plugin PAT is ever written to `~/.hermes/.env` in plaintext or exported into `os.environ`.

**Architecture:** A new `hermes_cli/secret_keystore.py` provides a two-tier store mirroring super-cli's design: probe an OS keystore (`keyring` → macOS Keychain / Windows Credential Manager / Linux Secret Service); if unreachable, fall back to an AES-GCM encrypted file with `0700` directory and `0600` key and ciphertext. Lookups are lazy and per-key — the module never enumerates and never touches `os.environ`. It is wired into `plugin_configuration.py` at exactly two points: consulted in `_resolved()` only when the existing legacy authorities miss, and used in place of `save_env_value()` on the write path. A `hermes secrets migrate` command moves existing `HERMES_PLUGIN_*` entries out of `.env`.

**Tech Stack:** Python 3.11+, `keyring` (new dependency), `cryptography` 48.0.1 (already pinned), pytest via `scripts/run_tests.sh`.

**Spec:** `/Users/coreyellis/tmp_supercli/SUPER-CLI-ARCHITECTURE.md` §4.2 (super-cli's credential model, the design being matched) and `/Users/coreyellis/tmp_supercli/PLUGIN-GAP-ANALYSIS.md` (the gap this closes).

**Repo:** `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

## Global Constraints

- **Test runner:** `scripts/run_tests.sh` ONLY. `AGENTS.md:1339` — *"ALWAYS use `scripts/run_tests.sh` — do not call `pytest` directly."*
- **Purely additive to the precedence ladder.** 556 test files touch `.env` / `load_env` / `save_env_value`. Do NOT change `save_env_value` or `load_env` semantics for any key other than `HERMES_PLUGIN_*`.
- **Never place a plugin secret in `os.environ`.** No `os.environ[...] = ...`, no `mirror_process_env=True`, no registration as a startup `SecretSource` (its `apply_all` writes to `os.environ` by design).
- **Never block on an interactive prompt.** The backend runs headless under the desktop app, the workflow scheduler and cron. Probing and reads must fail fast to the fallback rather than hang.
- **Never silently write plaintext.** If both keystore tiers fail, raise — do not fall back to `.env`.
- **File modes match super-cli exactly:** directory `0o700`, key file `0o600`, ciphertext `0o600`.
- **Existing precedence is preserved:** managed env > secret scope > external secret sources > `.env` — the keystore is consulted only when all of those miss.
- **Python compatibility:** match the floor already declared in `pyproject.toml`; do not raise it.

## Decisions Taken

These were judgement calls made while writing the plan. Flag any you disagree with before execution starts.

| # | Decision | Rationale |
|---|---|---|
| D1 | Keystore is consulted **last**, only when legacy authorities miss | Preserves managed/scope/external override precedence and keeps un-migrated `.env` working. Purely additive. |
| D2 | Migration is **explicit** (`hermes secrets migrate`), not automatic on read | An automatic migration mutating `.env` as a side effect of a read is surprising and hard to test. Explicit + `--dry-run` is reviewable. |
| D3 | Keystore is **default-on** (`auto`), overridable via `HERMES_SECRET_KEYSTORE` | Opt-in leaves the insecure default in place, which is the actual problem. `auto` degrades safely via probe. |
| D4 | In containers, the file tier requires a **persisted** key; an ephemeral key is refused loudly | Silently generating a key that vanishes on restart would destroy credentials and be blamed on this feature. |
| D5 | Service name `hermes.plugin-secrets` | Mirrors super-cli's `contextcore.super-cli`; namespaced so it never collides with other Hermes keychain items. |

## File Structure

| File | Responsibility |
|---|---|
| **Create** `hermes_cli/secret_keystore.py` | Two-tier keystore: probe, OS backend, encrypted-file backend, lazy `get_secret`/`set_secret`/`delete_secret`. Self-contained; no Hermes config imports beyond `get_hermes_home`. |
| **Create** `tests/hermes_cli/test_secret_keystore.py` | Unit tests for both backends, probe behaviour, file modes, crypto round-trip. |
| **Create** `hermes_cli/secrets_migrate.py` | `HERMES_PLUGIN_*` migration: enumerate from `.env`, write to keystore, verify read-back, remove from `.env`. |
| **Create** `tests/hermes_cli/test_secrets_migrate.py` | Migration tests including dry-run and verify-before-delete. |
| **Modify** `hermes_cli/plugin_configuration.py:1024-1042` | Read path: consult keystore when legacy authorities miss. |
| **Modify** `hermes_cli/plugin_configuration.py:1182-1193` | Write path: keystore instead of `save_env_value`. |
| **Modify** `pyproject.toml` | Add `keyring` dependency. |
| **Modify** `hermes_cli/main.py:11521` | Add `migrate` to the existing `secrets` subparser. |
| **Modify** `hermes_cli/config.py:861-876` | `_secure_file`: apply a real ACL on Windows instead of no-op. |
| **Create** `tests/hermes_cli/test_secure_file_windows.py` | Windows ACL behaviour, managed/container skips, POSIX unchanged. |

---

### Task 1: Add the `keyring` dependency

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nothing
- Produces: `keyring` importable at runtime

`pyproject.toml` pins dependencies with CVE annotations and upper-bound caps (see the `cryptography==48.0.1` comment). Follow that convention: pin exactly and record why.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, in the same dependency list that contains `cryptography==48.0.1`, add:

```toml
  # OS-native credential storage for per-profile plugin secrets (Jira/GitLab/
  # Confluence/ARM PATs). Backends: macOS Keychain, Windows Credential Manager,
  # Linux Secret Service. Probed at runtime with an encrypted-file fallback, so
  # a missing backend degrades rather than fails — see hermes_cli/secret_keystore.py.
  "keyring==25.6.0",
```

- [ ] **Step 2: Install and verify the import**

Run: `.venv/bin/python -m pip install 'keyring==25.6.0'`
Then: `.venv/bin/python -c "import keyring; print(keyring.__version__)"`
Expected: `25.6.0`

- [ ] **Step 3: Verify no dependency resolution conflict**

Run: `.venv/bin/python -m pip check`
Expected: `No broken requirements found.`

If `pip check` reports a conflict, stop and report it — do not force-install. `pyproject.toml` has documented caps (`msal` and `alibabacloud-tea-openapi` cap `cryptography<49`) and a conflict here needs a decision, not a workaround.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "build: add keyring dependency for OS-native plugin secret storage"
```

---

### Task 2: Encrypted-file backend

**Files:**
- Create: `hermes_cli/secret_keystore.py`
- Test: `tests/hermes_cli/test_secret_keystore.py`

**Interfaces:**
- Consumes: `hermes_constants.get_hermes_home`
- Produces:
  - `class KeystoreError(RuntimeError)`
  - `class FileKeystore` with `__init__(self, root: Path)`, `get(key: str) -> str | None`, `set(key: str, value: str) -> None`, `delete(key: str) -> None`, `keys() -> list[str]`, attribute `name = "file"`

Build the fallback tier first — it has no platform dependency, so it is fully testable in CI.

- [ ] **Step 1: Write the failing tests**

Create `tests/hermes_cli/test_secret_keystore.py`:

```python
"""Tests for hermes_cli.secret_keystore — two-tier plugin secret storage."""

import os
import stat
import sys
from unittest import mock

import pytest

from hermes_cli.secret_keystore import FileKeystore, KeystoreError


class TestFileKeystore:
    def test_round_trip(self, tmp_path):
        store = FileKeystore(tmp_path)
        store.set("HERMES_PLUGIN_ABC_PAT", "s3cret-value")
        assert store.get("HERMES_PLUGIN_ABC_PAT") == "s3cret-value"

    def test_missing_key_returns_none(self, tmp_path):
        store = FileKeystore(tmp_path)
        assert store.get("HERMES_PLUGIN_NOPE_PAT") is None

    def test_delete_removes_value(self, tmp_path):
        store = FileKeystore(tmp_path)
        store.set("K", "v")
        store.delete("K")
        assert store.get("K") is None

    def test_delete_missing_key_is_silent(self, tmp_path):
        FileKeystore(tmp_path).delete("ABSENT")

    def test_ciphertext_does_not_contain_plaintext(self, tmp_path):
        store = FileKeystore(tmp_path)
        store.set("K", "distinctive-plaintext-marker")
        blob = (tmp_path / "keystore.enc").read_bytes()
        assert b"distinctive-plaintext-marker" not in blob

    def test_survives_new_instance(self, tmp_path):
        FileKeystore(tmp_path).set("K", "v")
        assert FileKeystore(tmp_path).get("K") == "v"

    def test_keys_lists_stored_names(self, tmp_path):
        store = FileKeystore(tmp_path)
        store.set("A", "1")
        store.set("B", "2")
        assert sorted(store.keys()) == ["A", "B"]

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
    def test_file_modes_match_super_cli(self, tmp_path):
        store = FileKeystore(tmp_path)
        store.set("K", "v")
        assert stat.S_IMODE(os.stat(tmp_path).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(tmp_path / "keystore.key").st_mode) == 0o600
        assert stat.S_IMODE(os.stat(tmp_path / "keystore.enc").st_mode) == 0o600

    def test_corrupt_key_file_raises(self, tmp_path):
        FileKeystore(tmp_path).set("K", "v")
        (tmp_path / "keystore.key").write_bytes(b"too-short")
        with pytest.raises(KeystoreError, match="expected 32 bytes"):
            FileKeystore(tmp_path).get("K")

    def test_wrong_key_raises_rather_than_returning_garbage(self, tmp_path):
        FileKeystore(tmp_path).set("K", "v")
        (tmp_path / "keystore.key").write_bytes(b"\x00" * 32)
        with pytest.raises(KeystoreError, match="cannot decrypt"):
            FileKeystore(tmp_path).get("K")


class TestContainerKeyPersistence:
    """Decision D4: an ephemeral key in a container is refused loudly.

    Generating one silently is the worst available outcome -- every secret
    written under it becomes unreadable at the next restart, and the symptom
    ("my credentials vanished") gives no hint of the cause.
    """

    def test_new_key_in_a_container_is_refused(self, tmp_path):
        from hermes_cli import config

        with mock.patch.object(config, "_is_container", return_value=True):
            with pytest.raises(KeystoreError, match="persistent volume"):
                FileKeystore(tmp_path).set("K", "v")

    def test_refusal_leaves_no_key_file_behind(self, tmp_path):
        from hermes_cli import config

        with mock.patch.object(config, "_is_container", return_value=True):
            with pytest.raises(KeystoreError):
                FileKeystore(tmp_path).set("K", "v")
        assert not (tmp_path / "keystore.key").exists()

    def test_an_existing_key_in_a_container_is_fine(self, tmp_path):
        """A mounted volume with a key already on it is the supported setup —
        the refusal is about *creating* a key, not about containers."""
        from hermes_cli import config

        FileKeystore(tmp_path).set("K", "v")           # key created outside the container
        with mock.patch.object(config, "_is_container", return_value=True):
            assert FileKeystore(tmp_path).get("K") == "v"

    def test_outside_a_container_a_new_key_is_created(self, tmp_path):
        from hermes_cli import config

        with mock.patch.object(config, "_is_container", return_value=False):
            FileKeystore(tmp_path).set("K", "v")
        assert (tmp_path / "keystore.key").exists()
```

`mock.patch.object(config, "_is_container", ...)` patches the function on its defining
module, which is what `secret_keystore` resolves through at call time — patching
`hermes_cli.secret_keystore._is_container` would work too, but patching the definition
keeps one seam for both this task and Task 8's Windows ACL tests.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `scripts/run_tests.sh tests/hermes_cli/test_secret_keystore.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hermes_cli.secret_keystore'`

- [ ] **Step 3: Implement the file backend**

Create `hermes_cli/secret_keystore.py`:

```python
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

import json
import os
import secrets as _secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# The one Hermes import this module allows itself beyond get_hermes_home.
# _is_container already exists at hermes_cli/config.py:836 and honours the
# HERMES_CONTAINER / HERMES_SKIP_CHMOD opt-outs plus the /.dockerenv marker,
# so container detection stays one implementation rather than two.
#
# Imported as a MODULE, not `from ... import _is_container`: a from-import
# binds the function object at import time, and a test patching
# config._is_container would then not be seen here at all.
from hermes_cli import config as _hermes_config

__all__ = ["KeystoreError", "FileKeystore"]

_KEY_BYTES = 32
_NONCE_BYTES = 12
_KEY_FILE = "keystore.key"
_DATA_FILE = "keystore.enc"


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
        if _hermes_config._is_container():
            raise KeystoreError(
                f"refusing to generate a new encryption key at {path} inside a "
                f"container. The key would not survive a restart and every "
                f"secret written under it would become unreadable. Mount "
                f"HERMES_HOME on a persistent volume, or set "
                f"HERMES_SECRET_KEYSTORE=off to keep using .env."
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
        return self._read_all().get(key)

    def set(self, key: str, value: str) -> None:
        data = self._read_all()
        data[key] = value
        self._write_all(data)

    def delete(self, key: str) -> None:
        data = self._read_all()
        if data.pop(key, None) is not None:
            self._write_all(data)

    def keys(self) -> list[str]:
        return list(self._read_all())


def _chmod(path: Path, mode: int) -> None:
    """chmod, tolerating platforms and filesystems that do not support it."""
    try:
        os.chmod(path, mode)
    except (OSError, NotImplementedError):
        pass


def _write_private(path: Path, payload: bytes) -> None:
    """Write owner-only, creating with restrictive mode from the start.

    os.open with 0o600 avoids the window where a chmod-after-write would
    leave the file world-readable.
    """
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    _chmod(path, 0o600)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `scripts/run_tests.sh tests/hermes_cli/test_secret_keystore.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/secret_keystore.py tests/hermes_cli/test_secret_keystore.py
git commit -m "feat: add AES-GCM encrypted file backend for plugin secrets"
```

---

### Task 3: OS keystore backend and probe

**Files:**
- Modify: `hermes_cli/secret_keystore.py`
- Test: `tests/hermes_cli/test_secret_keystore.py`

**Interfaces:**
- Consumes: `FileKeystore`, `KeystoreError` from Task 2
- Produces:
  - `class OSKeystore` with `get`/`set`/`delete`, attribute `name = "os"`
  - `probe_os_keystore() -> bool`
  - `SERVICE_NAME = "hermes.plugin-secrets"`

The probe is the safety mechanism that makes default-on viable — it is exactly super-cli's `NewStore` writing a `probe` entry before committing to the OS keyring.

- [ ] **Step 1: Write the failing tests**

Append to `tests/hermes_cli/test_secret_keystore.py`:

```python
from unittest import mock

from hermes_cli.secret_keystore import (
    SERVICE_NAME,
    OSKeystore,
    probe_os_keystore,
)


class _FakeKeyring:
    """In-memory stand-in for the keyring module."""

    def __init__(self, fail=False):
        self.store = {}
        self.fail = fail

    def get_password(self, service, name):
        if self.fail:
            raise RuntimeError("no backend")
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        if self.fail:
            raise RuntimeError("no backend")
        self.store[(service, name)] = value

    def delete_password(self, service, name):
        if self.fail:
            raise RuntimeError("no backend")
        self.store.pop((service, name), None)


class TestOSKeystore:
    def test_round_trip(self):
        fake = _FakeKeyring()
        with mock.patch("hermes_cli.secret_keystore.keyring", fake):
            store = OSKeystore()
            store.set("K", "v")
            assert store.get("K") == "v"

    def test_uses_namespaced_service_name(self):
        fake = _FakeKeyring()
        with mock.patch("hermes_cli.secret_keystore.keyring", fake):
            OSKeystore().set("K", "v")
        assert (SERVICE_NAME, "K") in fake.store

    def test_backend_failure_raises_keystore_error(self):
        with mock.patch("hermes_cli.secret_keystore.keyring", _FakeKeyring(fail=True)):
            with pytest.raises(KeystoreError):
                OSKeystore().set("K", "v")


class TestProbe:
    def test_probe_true_when_round_trip_works(self):
        with mock.patch("hermes_cli.secret_keystore.keyring", _FakeKeyring()):
            assert probe_os_keystore() is True

    def test_probe_false_when_backend_unavailable(self):
        with mock.patch("hermes_cli.secret_keystore.keyring", _FakeKeyring(fail=True)):
            assert probe_os_keystore() is False

    def test_probe_false_when_keyring_not_installed(self):
        with mock.patch("hermes_cli.secret_keystore.keyring", None):
            assert probe_os_keystore() is False

    def test_probe_leaves_no_residue(self):
        fake = _FakeKeyring()
        with mock.patch("hermes_cli.secret_keystore.keyring", fake):
            probe_os_keystore()
        assert fake.store == {}

    def test_probe_false_when_round_trip_value_mismatches(self):
        fake = _FakeKeyring()
        fake.get_password = lambda service, name: "wrong-value"
        with mock.patch("hermes_cli.secret_keystore.keyring", fake):
            assert probe_os_keystore() is False

    def test_probe_gives_up_on_a_hanging_backend(self):
        """The failure mode that a try/except cannot catch.

        A locked Linux keyring or a D-Bus Secret Service that never answers
        blocks inside keyring rather than raising. The Global Constraints
        require probing to "fail fast to the fallback rather than hang", and
        an exception handler does not deliver that.
        """
        import threading

        released = threading.Event()

        class _HangingKeyring:
            def set_password(self, service, name, value):
                released.wait(timeout=10)   # unblocked by the test, not the probe

            def get_password(self, service, name):
                released.wait(timeout=10)

            def delete_password(self, service, name):
                pass

        with mock.patch("hermes_cli.secret_keystore.keyring", _HangingKeyring()):
            start = time.monotonic()
            result = probe_os_keystore(timeout_seconds=0.25)
            elapsed = time.monotonic() - start

        released.set()
        assert result is False
        assert elapsed < 5.0, f"probe blocked for {elapsed:.1f}s instead of failing fast"

    def test_probe_timeout_is_configurable_and_defaulted(self):
        import inspect

        signature = inspect.signature(probe_os_keystore)
        assert signature.parameters["timeout_seconds"].default == PROBE_TIMEOUT_SECONDS
```

Add `import time` and `PROBE_TIMEOUT_SECONDS` to this test module's imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `scripts/run_tests.sh tests/hermes_cli/test_secret_keystore.py -v -k "OSKeystore or Probe"`
Expected: FAIL — `ImportError: cannot import name 'OSKeystore'`

- [ ] **Step 3: Implement the OS backend and probe**

In `hermes_cli/secret_keystore.py`, extend `__all__` and add the import and classes:

```python
__all__ = [
    "KeystoreError",
    "FileKeystore",
    "OSKeystore",
    "SERVICE_NAME",
    "probe_os_keystore",
]

try:  # pragma: no cover - import guard exercised via mock.patch
    import keyring
except Exception:  # ImportError, or a backend that explodes at import time
    keyring = None

SERVICE_NAME = "hermes.plugin-secrets"
_PROBE_KEY = "__hermes_probe__"
_PROBE_VALUE = "ok"
# The probe runs once per process, on the path that decides which tier to
# use. A locked Linux keyring or an unresponsive D-Bus Secret Service blocks
# rather than raising, so the bound has to be a timeout, not an except.
PROBE_TIMEOUT_SECONDS = 3.0


class OSKeystore:
    """The OS-native credential store, via the ``keyring`` package.

    Backends: macOS Keychain, Windows Credential Manager (DPAPI), Linux
    Secret Service.  Reached only after ``probe_os_keystore()`` confirms a
    working round trip, so callers can treat failures here as exceptional.
    """

    name = "os"

    def get(self, key: str) -> str | None:
        if keyring is None:
            raise KeystoreError("keyring is not available")
        try:
            return keyring.get_password(SERVICE_NAME, key)
        except Exception as exc:
            raise KeystoreError(f"keystore read failed for {key}") from exc

    def set(self, key: str, value: str) -> None:
        if keyring is None:
            raise KeystoreError("keyring is not available")
        try:
            keyring.set_password(SERVICE_NAME, key, value)
        except Exception as exc:
            raise KeystoreError(f"keystore write failed for {key}") from exc

    def delete(self, key: str) -> None:
        if keyring is None:
            raise KeystoreError("keyring is not available")
        try:
            keyring.delete_password(SERVICE_NAME, key)
        except Exception:
            # Deleting an absent item is not an error, and no backend
            # distinguishes "missing" from "failed" portably.
            pass


def _probe_round_trip() -> bool:
    """One set/get/delete cycle. Runs on a worker thread — see below."""
    try:
        keyring.set_password(SERVICE_NAME, _PROBE_KEY, _PROBE_VALUE)
        observed = keyring.get_password(SERVICE_NAME, _PROBE_KEY)
    except Exception:
        return False
    finally:
        try:
            keyring.delete_password(SERVICE_NAME, _PROBE_KEY)
        except Exception:
            pass
    return observed == _PROBE_VALUE


def probe_os_keystore(timeout_seconds: float = PROBE_TIMEOUT_SECONDS) -> bool:
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
    than killed, which is correct: it holds no lock this process needs, it
    cannot write anything (the probe key is namespaced and deleted by the
    same call), and daemon threads do not block interpreter shutdown.
    """
    if keyring is None:
        return False

    import threading

    outcome: list[bool] = []

    def _run() -> None:
        outcome.append(_probe_round_trip())

    worker = threading.Thread(target=_run, name="hermes-keystore-probe", daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        # Timed out. Treat as unavailable and fall through to the file tier.
        return False
    return bool(outcome and outcome[0])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `scripts/run_tests.sh tests/hermes_cli/test_secret_keystore.py -v`
Expected: PASS (21 tests)

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/secret_keystore.py tests/hermes_cli/test_secret_keystore.py
git commit -m "feat: add OS keystore backend with availability probe"
```

---

### Task 4: Tier-selection facade

**Files:**
- Modify: `hermes_cli/secret_keystore.py`
- Test: `tests/hermes_cli/test_secret_keystore.py`

**Interfaces:**
- Consumes: `OSKeystore`, `FileKeystore`, `probe_os_keystore`
- Produces:
  - `get_backend() -> OSKeystore | FileKeystore` (process-cached)
  - `get_secret(key: str) -> str | None`
  - `set_secret(key: str, value: str) -> None`
  - `delete_secret(key: str) -> None`
  - `reset_backend_cache() -> None` (tests only)
  - env override `HERMES_SECRET_KEYSTORE` ∈ `{auto, os, file, off}`

`get_secret` must never raise on a read — a read failure has to look like "no value" so plugin resolution falls through to the normal not-configured path rather than crashing an agent turn. Writes must raise, because silently losing a credential the user just typed is worse than an error.

- [ ] **Step 1: Write the failing tests**

Append to `tests/hermes_cli/test_secret_keystore.py`:

```python
import hermes_cli.secret_keystore as sk


@pytest.fixture(autouse=True)
def _reset_backend_cache():
    sk.reset_backend_cache()
    yield
    sk.reset_backend_cache()


class TestBackendSelection:
    def test_prefers_os_when_probe_succeeds(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        with mock.patch.object(sk, "keyring", _FakeKeyring()):
            assert sk.get_backend().name == "os"

    def test_falls_back_to_file_when_probe_fails(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        with mock.patch.object(sk, "keyring", _FakeKeyring(fail=True)):
            assert sk.get_backend().name == "file"

    def test_env_override_forces_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "file")
        with mock.patch.object(sk, "keyring", _FakeKeyring()):
            assert sk.get_backend().name == "file"

    def test_env_override_off_disables_keystore(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "off")
        assert sk.get_backend() is None
        assert sk.get_secret("K") is None

    def test_probe_runs_once_per_process(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        calls = []
        with mock.patch.object(sk, "keyring", _FakeKeyring()):
            with mock.patch.object(
                sk, "probe_os_keystore", side_effect=lambda: calls.append(1) or True
            ):
                sk.get_backend()
                sk.get_backend()
        assert len(calls) == 1


class TestModuleLevelAPI:
    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "file")
        sk.set_secret("HERMES_PLUGIN_X_PAT", "tok")
        assert sk.get_secret("HERMES_PLUGIN_X_PAT") == "tok"

    def test_get_secret_never_raises_on_backend_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        broken = mock.Mock()
        broken.name = "os"
        broken.get.side_effect = KeystoreError("boom")
        with mock.patch.object(sk, "get_backend", return_value=broken):
            assert sk.get_secret("K") is None

    def test_set_secret_raises_on_backend_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        broken = mock.Mock()
        broken.name = "os"
        broken.set.side_effect = KeystoreError("boom")
        with mock.patch.object(sk, "get_backend", return_value=broken):
            with pytest.raises(KeystoreError):
                sk.set_secret("K", "v")

    def test_set_secret_raises_when_keystore_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "off")
        with pytest.raises(KeystoreError):
            sk.set_secret("K", "v")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `scripts/run_tests.sh tests/hermes_cli/test_secret_keystore.py -v -k "BackendSelection or ModuleLevelAPI"`
Expected: FAIL — `AttributeError: module 'hermes_cli.secret_keystore' has no attribute 'reset_backend_cache'`

- [ ] **Step 3: Implement the facade**

In `hermes_cli/secret_keystore.py`, extend `__all__` and append:

```python
__all__ += [
    "get_backend",
    "get_secret",
    "set_secret",
    "delete_secret",
    "reset_backend_cache",
]

import threading

_BACKEND = None
_BACKEND_RESOLVED = False
_BACKEND_LOCK = threading.Lock()
_MODE_ENV = "HERMES_SECRET_KEYSTORE"


def reset_backend_cache() -> None:
    """Clear the cached backend. For tests and for post-migration re-probe."""
    global _BACKEND, _BACKEND_RESOLVED
    with _BACKEND_LOCK:
        _BACKEND = None
        _BACKEND_RESOLVED = False


def _secrets_root():
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home()) / "secrets"


def get_backend():
    """Return the active backend, or None when the keystore is disabled.

    Resolved once per process: the probe can involve IPC to a keychain
    daemon and this is called on every secret resolution.
    """
    global _BACKEND, _BACKEND_RESOLVED
    if _BACKEND_RESOLVED:
        return _BACKEND
    with _BACKEND_LOCK:
        if _BACKEND_RESOLVED:
            return _BACKEND
        mode = (os.environ.get(_MODE_ENV) or "auto").strip().lower()
        if mode == "off":
            _BACKEND = None
        elif mode == "file":
            _BACKEND = FileKeystore(_secrets_root())
        elif mode == "os":
            _BACKEND = OSKeystore()
        else:  # auto
            _BACKEND = OSKeystore() if probe_os_keystore() else FileKeystore(
                _secrets_root()
            )
        _BACKEND_RESOLVED = True
        return _BACKEND


def get_secret(key: str) -> str | None:
    """Return one secret, or None.

    Never raises.  A read failure must look like "not configured" so the
    caller reports a missing credential instead of crashing an agent turn.
    """
    backend = get_backend()
    if backend is None:
        return None
    try:
        return backend.get(key)
    except KeystoreError:
        return None


def set_secret(key: str, value: str) -> None:
    """Store one secret. Raises KeystoreError rather than losing the value.

    Deliberately never falls back to plaintext .env — that would silently
    undo the entire point of this module.
    """
    backend = get_backend()
    if backend is None:
        raise KeystoreError(
            f"secret keystore is disabled ({_MODE_ENV}=off); cannot store {key}"
        )
    backend.set(key, value)


def delete_secret(key: str) -> None:
    backend = get_backend()
    if backend is None:
        return
    try:
        backend.delete(key)
    except KeystoreError:
        pass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `scripts/run_tests.sh tests/hermes_cli/test_secret_keystore.py -v`
Expected: PASS (28 tests)

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/secret_keystore.py tests/hermes_cli/test_secret_keystore.py
git commit -m "feat: add keystore tier selection with probe caching and env override"
```

---

### Task 5: Read path — consult the keystore when legacy authorities miss

**Files:**
- Modify: `hermes_cli/plugin_configuration.py:1024-1042`
- Test: `tests/hermes_cli/test_plugin_configuration_storage.py`

**Interfaces:**
- Consumes: `secret_keystore.get_secret` from Task 4
- Produces: no new public API; `_resolved()` behaviour extended

This is the additive change that keeps all 556 env-touching tests green. The keystore is consulted **last** so managed env, secret scope and external sources keep winning, and a not-yet-migrated `.env` entry still resolves.

- [ ] **Step 1: Write the failing tests**

Append to `tests/hermes_cli/test_plugin_configuration_storage.py`:

**Every test here drives `PluginConfigurationService`, never `secret_keystore` directly.**
That is the whole point of this task: the behaviour under test is *"the read path
consults the keystore"*, and a test that calls `sk.set_secret()` then `sk.get_secret()`
asserts only that Task 4 works — it would pass with `_resolved()` completely untouched,
letting this task be marked done while changing nothing. Use the file's existing
`_service()` helper and read resolved state through `service.detail()`, exactly as
`test_profile_secret_reads_ignore_process_global_value` already does.

```python
from unittest import mock

import hermes_cli.secret_keystore as sk


def _token_field(service):
    """Resolved state of the sample connector's secret field, via detail()."""
    return next(
        field
        for field in service.detail("sample-connector")["fields"]
        if field["id"] == "token"
    )


class TestKeystoreReadPath:
    def test_keystore_value_is_resolved_when_env_has_no_entry(
        self, tmp_path, monkeypatch
    ):
        """RED before Task 5: .env has no entry, so detail() reports the
        field unset no matter what the keystore holds."""
        from hermes_cli.plugin_configuration import _secret_storage_key

        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "file")
        sk.reset_backend_cache()
        service, _ = _service(tmp_path)
        sk.set_secret(_secret_storage_key("sample-connector", "token"), "from-keystore")

        assert _token_field(service)["is_set"] is True

    def test_legacy_env_value_wins_over_keystore(self, tmp_path, monkeypatch):
        """Un-migrated .env entries must keep working, and managed/scoped
        overrides ride the same path — so legacy authorities take precedence.

        Asserted through the real resolution order rather than a reimplementation
        of it: the point is that `_resolved` consults the keystore *after* the
        profile store, and only a test that runs `_resolved` can show that.
        """
        from hermes_cli.plugin_configuration import (
            PluginConfigurationService,
            _secret_storage_key,
        )

        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "file")
        sk.reset_backend_cache()
        service, manager = _service(tmp_path)
        key = _secret_storage_key("sample-connector", "token")
        sk.set_secret(key, "from-keystore")

        with mock.patch.object(
            PluginConfigurationService,
            "_profile_secret_values",
            staticmethod(lambda: {key: "from-env"}),
        ):
            resolved, _invalid = service._resolved(
                "sample-connector", manager._plugins["sample-connector"]
            )

        assert resolved["token"] == "from-env"

    def test_keystore_is_consulted_only_after_the_profile_store_misses(
        self, tmp_path, monkeypatch
    ):
        """Precedence is an ordering property, so assert on the call itself.

        Without this, a read path that consulted the keystore *first* and then
        let .env overwrite the result would still satisfy the test above.
        """
        from hermes_cli.plugin_configuration import (
            PluginConfigurationService,
            _secret_storage_key,
        )

        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
        sk.reset_backend_cache()
        service, manager = _service(tmp_path)
        key = _secret_storage_key("sample-connector", "token")

        with mock.patch.object(
            PluginConfigurationService,
            "_profile_secret_values",
            staticmethod(lambda: {key: "from-env"}),
        ):
            with mock.patch.object(sk, "get_secret") as get_secret:
                service._resolved(
                    "sample-connector", manager._plugins["sample-connector"]
                )

        get_secret.assert_not_called()

    def test_keystore_read_failure_leaves_the_field_unconfigured(
        self, tmp_path, monkeypatch
    ):
        """A broken keystore must not raise out of a read path the dashboard
        calls on every page load — it degrades to 'not configured'."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
        sk.reset_backend_cache()
        service, _ = _service(tmp_path)
        broken = mock.Mock()
        broken.name = "os"
        broken.get.side_effect = sk.KeystoreError("boom")

        with mock.patch.object(sk, "get_backend", return_value=broken):
            assert _token_field(service)["is_set"] is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `scripts/run_tests.sh tests/hermes_cli/test_plugin_configuration_storage.py -v -k KeystoreReadPath`

Expected: FAIL, and **check the reason**. The first test must fail on
`assert _token_field(service)["is_set"] is True` — i.e. the read path does not yet
consult the keystore. If instead it errors on an import or a missing
`sk.reset_backend_cache`, Task 4 is incomplete: fix that first, then re-run, because an
import error proves nothing about the behaviour this task adds.

`test_keystore_is_consulted_only_after_the_profile_store_misses` is expected to **pass**
before implementation — nothing calls the keystore yet. It is a regression guard for the
ordering, not a RED test, and it must still pass after Step 3.

- [ ] **Step 3: Modify the read path**

In `hermes_cli/plugin_configuration.py`, replace the SECRET branch inside `_resolved()` (currently lines 1027-1030):

```python
            if field.storage is FieldStorage.SECRET:
                value = secret_values.get(_secret_storage_key(plugin_id, field.id))
                if value not in {None, ""}:
                    present = True
```

with:

```python
            if field.storage is FieldStorage.SECRET:
                storage_key = _secret_storage_key(plugin_id, field.id)
                value = secret_values.get(storage_key)
                if value in {None, ""}:
                    # Legacy authorities missed -> consult the OS keystore.
                    # Deliberately last: managed env, the secret scope and
                    # external sources must keep overriding, and a profile
                    # that has not run `hermes secrets migrate` yet still
                    # resolves from its plaintext .env entry.
                    value = secret_keystore.get_secret(storage_key)
                if value not in {None, ""}:
                    present = True
```

Add the import near the other `hermes_cli` imports at the top of the file:

```python
from hermes_cli import secret_keystore
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `scripts/run_tests.sh tests/hermes_cli/test_plugin_configuration_storage.py -v`
Expected: PASS

- [ ] **Step 5: Verify no regression across the plugin configuration suite**

Run:
```bash
scripts/run_tests.sh tests/hermes_cli/test_plugin_configuration.py \
  tests/hermes_cli/test_plugin_configuration_storage.py \
  tests/hermes_cli/test_plugin_setup_actions.py \
  tests/hermes_cli/test_ericsson_connector_distribution.py \
  tests/plugins/test_plugin_dashboard_auth_contract.py -v
```
Expected: PASS, no failures. These are the five files that reference plugin secret storage.

- [ ] **Step 6: Commit**

```bash
git add hermes_cli/plugin_configuration.py tests/hermes_cli/test_plugin_configuration_storage.py
git commit -m "feat: resolve plugin secrets from OS keystore when .env has no entry"
```

---

### Task 6: Write path — store to the keystore instead of `.env`

**Files:**
- Modify: `hermes_cli/plugin_configuration.py:1182-1193` (save path, in `update()`)
- Modify: `hermes_cli/plugin_configuration.py:1196-1220` (`clear_secret()`)
- Test: `tests/hermes_cli/test_plugin_configuration_storage.py`

**Interfaces:**
- Consumes: `secret_keystore.set_secret`, `secret_keystore.delete_secret`, `secret_keystore.KeystoreError`
- Produces: no new public API; saved secrets no longer reach `.env`, and cleared secrets are removed from **both** tiers

**`clear_secret` is part of this task, not a follow-up.** Moving writes to the keystore
while leaving `clear_secret` calling `remove_env_value` alone produces a revocation bug:
the operator clears a credential in the dashboard, the UI reports it gone because `.env`
no longer has it, and the keystore copy keeps authenticating. A credential that cannot be
revoked through the revoke button is worse than one stored in plaintext.

- [ ] **Step 1: Write the failing tests**

As in Task 5, every test drives `PluginConfigurationService` — `update()` and
`clear_secret()` — never `secret_keystore` directly. A test that calls `sk.set_secret()`
and then inspects `.env` is asserting that Task 2 works, and would pass with this task's
production change never made.

Append to `tests/hermes_cli/test_plugin_configuration_storage.py`:

```python
class TestKeystoreWritePath:
    def test_saved_secret_does_not_reach_the_env_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "file")
        sk.reset_backend_cache()
        service, _ = _service(tmp_path)

        service.update("sample-connector", secrets={"token": "should-not-be-in-env"})

        env_path = tmp_path / "profile" / ".env"
        contents = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        assert "should-not-be-in-env" not in contents

    def test_saved_secret_is_readable_back_through_detail(self, tmp_path, monkeypatch):
        """Round trip through the production paths, not the keystore API."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "file")
        sk.reset_backend_cache()
        service, _ = _service(tmp_path)

        service.update("sample-connector", secrets={"token": "v"})

        assert _token_field(service)["is_set"] is True

    def test_unrelated_env_entries_are_preserved(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "file")
        sk.reset_backend_cache()
        (tmp_path / "profile").mkdir(parents=True, exist_ok=True)
        env_path = tmp_path / "profile" / ".env"
        env_path.write_text("EXISTING=keepme\n", encoding="utf-8")
        service, _ = _service(tmp_path)

        service.update("sample-connector", secrets={"token": "v"})

        assert "EXISTING=keepme" in env_path.read_text(encoding="utf-8")

    def test_keystore_write_failure_raises_rather_than_writing_plaintext(
        self, tmp_path, monkeypatch
    ):
        """Global Constraint: never silently write plaintext. If both tiers
        are unavailable the save must fail loudly."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "off")
        sk.reset_backend_cache()
        service, _ = _service(tmp_path)

        with pytest.raises(PluginConfigurationError):
            service.update("sample-connector", secrets={"token": "v"})

        env_path = tmp_path / "profile" / ".env"
        contents = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        assert "token" not in contents.lower() or "v" not in contents


class TestKeystoreClearPath:
    def test_clearing_removes_the_keystore_copy(self, tmp_path, monkeypatch):
        """The revocation bug this task exists to prevent: writes go to the
        keystore, so a clear that only touches .env leaves the credential
        live while the UI reports it gone."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "file")
        sk.reset_backend_cache()
        service, _ = _service(tmp_path)
        service.update("sample-connector", secrets={"token": "live-credential"})
        assert _token_field(service)["is_set"] is True

        service.clear_secret("sample-connector", "token")

        assert _token_field(service)["is_set"] is False

    def test_clearing_an_absent_secret_is_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "file")
        sk.reset_backend_cache()
        service, _ = _service(tmp_path)

        service.clear_secret("sample-connector", "token")

        assert _token_field(service)["is_set"] is False

    def test_clearing_still_removes_an_unmigrated_env_entry(
        self, tmp_path, monkeypatch
    ):
        """Profiles that have not run `hermes secrets migrate` keep a .env
        entry. Clearing must remove both tiers, not swap which one it forgets."""
        from hermes_cli.plugin_configuration import _secret_storage_key

        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "file")
        sk.reset_backend_cache()
        (tmp_path / "profile").mkdir(parents=True, exist_ok=True)
        key = _secret_storage_key("sample-connector", "token")
        (tmp_path / "profile" / ".env").write_text(
            f"{key}=legacy-plaintext\n", encoding="utf-8"
        )
        service, _ = _service(tmp_path)

        service.clear_secret("sample-connector", "token")

        contents = (tmp_path / "profile" / ".env").read_text(encoding="utf-8")
        assert "legacy-plaintext" not in contents
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `scripts/run_tests.sh tests/hermes_cli/test_plugin_configuration_storage.py -v -k "KeystoreWritePath or KeystoreClearPath"`

Expected: FAIL, and **check the reasons**:
- `test_saved_secret_does_not_reach_the_env_file` fails because the secret *is* in `.env`
- `test_clearing_removes_the_keystore_copy` fails on the final assertion — the field is
  still set after the clear, which is exactly the revocation bug

If any of these error on an import instead, an earlier task is incomplete. Fix that first.

- [ ] **Step 3: Modify the write path**

In `hermes_cli/plugin_configuration.py`, replace the persistence block (currently lines 1182-1193):

```python
            try:
                for field_id, value in secrets.items():
                    save_env_value(
                        _secret_storage_key(canonical_id, field_id),
                        value,
                        mirror_process_env=False,
                        strict=True,
                    )
            except ConfigurationPersistenceError as exc:
                raise PluginConfigurationError(
                    "plugin configuration could not be persisted"
                ) from exc
```

with:

```python
            try:
                for field_id, value in secrets.items():
                    # Store in the OS keystore (or its encrypted-file
                    # fallback), never in .env: load_dotenv exports the whole
                    # .env into os.environ at startup, which would hand a copy
                    # of every PAT to every child process Hermes spawns.
                    secret_keystore.set_secret(
                        _secret_storage_key(canonical_id, field_id), value
                    )
            except secret_keystore.KeystoreError as exc:
                raise PluginConfigurationError(
                    "plugin configuration could not be persisted"
                ) from exc
```

Leave the `save_env_value` / `ConfigurationPersistenceError` import in place if it is still used for non-secret settings elsewhere in the file; remove it only if it becomes unused (the linter will tell you).

- [ ] **Step 4: Modify the clear path**

In the same file, `clear_secret()` currently removes only the `.env` entry. Now that writes
land in the keystore, that leaves the credential live. Replace its persistence block
(currently lines 1209-1219):

```python
        from hermes_cli.config import ConfigurationPersistenceError, remove_env_value

        try:
            remove_env_value(
                _secret_storage_key(canonical_id, field_id),
                mirror_process_env=False,
                strict=True,
            )
        except ConfigurationPersistenceError as exc:
            raise PluginConfigurationError(
                "plugin configuration could not be persisted"
            ) from exc
```

with:

```python
        from hermes_cli.config import ConfigurationPersistenceError, remove_env_value

        storage_key = _secret_storage_key(canonical_id, field_id)
        # Clear BOTH tiers. The keystore holds anything written since this
        # feature landed; .env still holds anything from a profile that has
        # not run `hermes secrets migrate`. Removing only one leaves a
        # credential that the dashboard reports as cleared and that still
        # authenticates -- worse than never having moved it.
        try:
            secret_keystore.delete_secret(storage_key)
        except secret_keystore.KeystoreError as exc:
            raise PluginConfigurationError(
                "plugin configuration could not be persisted"
            ) from exc
        try:
            remove_env_value(
                storage_key,
                mirror_process_env=False,
                strict=True,
            )
        except ConfigurationPersistenceError as exc:
            raise PluginConfigurationError(
                "plugin configuration could not be persisted"
            ) from exc
```

Order matters: the keystore is cleared first, because it is the tier that would still
authenticate. If the `.env` removal then fails, the operator gets an error and a
credential that is already revoked — the safe direction to fail in.

`delete_secret` must treat an absent key as success (Task 2 and Task 3 both specify this),
so clearing a field that was never set is not an error.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `scripts/run_tests.sh tests/hermes_cli/test_plugin_configuration_storage.py -v`
Expected: PASS

- [ ] **Step 6: Verify the five plugin-secret test files still pass**

Run:
```bash
scripts/run_tests.sh tests/hermes_cli/test_plugin_configuration.py \
  tests/hermes_cli/test_plugin_configuration_storage.py \
  tests/hermes_cli/test_plugin_setup_actions.py \
  tests/hermes_cli/test_ericsson_connector_distribution.py \
  tests/plugins/test_plugin_dashboard_auth_contract.py -v
```
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add hermes_cli/plugin_configuration.py tests/hermes_cli/test_plugin_configuration_storage.py
git commit -m "feat: persist and clear plugin secrets via the keystore, not .env"
```

---

### Task 7: `hermes secrets migrate`

**Files:**
- Create: `hermes_cli/secrets_migrate.py`
- Create: `tests/hermes_cli/test_secrets_migrate.py`
- Modify: `hermes_cli/main.py:11521` (the existing `secrets` subparser)

**Interfaces:**
- Consumes: `secret_keystore.set_secret`/`get_secret`, `hermes_cli.config.load_env`, `hermes_cli.config.remove_env_value`
- Produces:
  - `find_legacy_secrets() -> dict[str, str]`
  - `migrate_secrets(dry_run: bool = False) -> MigrationReport`
  - `@dataclass MigrationReport(migrated: list[str], failed: list[str], dry_run: bool)`

Verify-before-delete is the critical ordering: read the value back out of the keystore and compare before removing the `.env` line. Deleting first and failing to write loses the user's credential permanently.

- [ ] **Step 1: Write the failing tests**

Create `tests/hermes_cli/test_secrets_migrate.py`:

```python
"""Tests for hermes_cli.secrets_migrate."""

from unittest import mock

import pytest

import hermes_cli.secret_keystore as sk
from hermes_cli.secrets_migrate import (
    MigrationReport,
    find_legacy_secrets,
    migrate_secrets,
)


@pytest.fixture(autouse=True)
def _keystore(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "file")
    sk.reset_backend_cache()
    yield
    sk.reset_backend_cache()


class TestFindLegacySecrets:
    def test_selects_only_plugin_secret_keys(self):
        env = {
            "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT": "tok1",
            "HERMES_PLUGIN_DEF456_API_TOKEN": "tok2",
            "HERMES_PLUGIN_PAYLOAD_MAX_CHARS": "50000",
            "ANTHROPIC_API_KEY": "sk-x",
            "HERMES_HOME": "/somewhere",
        }
        with mock.patch("hermes_cli.secrets_migrate.load_env", return_value=env):
            found = find_legacy_secrets()
        assert set(found) == {
            "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT",
            "HERMES_PLUGIN_DEF456_API_TOKEN",
        }

    def test_ignores_empty_values(self):
        with mock.patch(
            "hermes_cli.secrets_migrate.load_env",
            return_value={"HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT": ""},
        ):
            assert find_legacy_secrets() == {}


class TestMigrate:
    def test_dry_run_writes_nothing(self):
        env = {"HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT": "tok"}
        removed = []
        with mock.patch("hermes_cli.secrets_migrate.load_env", return_value=env):
            with mock.patch(
                "hermes_cli.secrets_migrate.remove_env_value",
                side_effect=lambda k: removed.append(k),
            ):
                report = migrate_secrets(dry_run=True)
        assert report.migrated == ["HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT"]
        assert report.dry_run is True
        assert removed == []
        assert sk.get_secret("HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT") is None

    def test_migrates_then_removes_from_env(self):
        env = {"HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT": "tok"}
        removed = []
        with mock.patch("hermes_cli.secrets_migrate.load_env", return_value=env):
            with mock.patch(
                "hermes_cli.secrets_migrate.remove_env_value",
                side_effect=lambda k: removed.append(k),
            ):
                report = migrate_secrets()
        assert report.migrated == ["HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT"]
        assert sk.get_secret("HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT") == "tok"
        assert removed == ["HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT"]

    def test_env_entry_kept_when_keystore_write_fails(self):
        env = {"HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT": "tok"}
        removed = []
        with mock.patch("hermes_cli.secrets_migrate.load_env", return_value=env):
            with mock.patch(
                "hermes_cli.secrets_migrate.remove_env_value",
                side_effect=lambda k: removed.append(k),
            ):
                with mock.patch.object(
                    sk, "set_secret", side_effect=sk.KeystoreError("boom")
                ):
                    report = migrate_secrets()
        assert report.failed == ["HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT"]
        assert report.migrated == []
        assert removed == []

    def test_env_entry_kept_when_readback_mismatches(self):
        env = {"HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT": "tok"}
        removed = []
        with mock.patch("hermes_cli.secrets_migrate.load_env", return_value=env):
            with mock.patch(
                "hermes_cli.secrets_migrate.remove_env_value",
                side_effect=lambda k: removed.append(k),
            ):
                with mock.patch.object(sk, "get_secret", return_value="different"):
                    report = migrate_secrets()
        assert report.failed == ["HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT"]
        assert removed == []

    def test_no_legacy_secrets_is_a_clean_noop(self):
        with mock.patch("hermes_cli.secrets_migrate.load_env", return_value={}):
            report = migrate_secrets()
        assert report == MigrationReport(migrated=[], failed=[], dry_run=False)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `scripts/run_tests.sh tests/hermes_cli/test_secrets_migrate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hermes_cli.secrets_migrate'`

- [ ] **Step 3: Implement the migration**

Create `hermes_cli/secrets_migrate.py`:

```python
"""One-way migration of plugin secrets out of ~/.hermes/.env.

Plugin PATs were historically stored as HERMES_PLUGIN_<digest>_<SLUG>
entries in ~/.hermes/.env.  That file is exported into os.environ at
startup by load_dotenv, so every child process Hermes spawns inherited a
copy of every PAT.  This moves them into the keystore and removes the
plaintext line.

Ordering is deliberate: write, read back, compare, and only then remove.
Removing first and failing to write would destroy the credential.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from hermes_cli import secret_keystore
from hermes_cli.config import load_env, remove_env_value

__all__ = ["MigrationReport", "find_legacy_secrets", "migrate_secrets"]

# Matches keys minted by plugin_configuration._secret_storage_key:
#   HERMES_PLUGIN_<32 uppercase hex>_<SLUG>
# The digest anchor is what keeps unrelated names such as
# HERMES_PLUGIN_PAYLOAD_MAX_CHARS out of the migration set.
_PLUGIN_SECRET_KEY = re.compile(r"^HERMES_PLUGIN_[0-9A-F]{32}_[A-Z0-9_]+$")


@dataclass
class MigrationReport:
    migrated: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    dry_run: bool = False


def find_legacy_secrets() -> dict[str, str]:
    """Return plugin secret entries still present in .env."""
    return {
        key: value
        for key, value in load_env().items()
        if _PLUGIN_SECRET_KEY.match(key) and value
    }


def migrate_secrets(dry_run: bool = False) -> MigrationReport:
    """Move plugin secrets from .env into the keystore.

    Idempotent: keys already migrated are simply absent from .env and are
    therefore skipped on subsequent runs.
    """
    report = MigrationReport(dry_run=dry_run)
    for key, value in find_legacy_secrets().items():
        if dry_run:
            report.migrated.append(key)
            continue
        try:
            secret_keystore.set_secret(key, value)
        except secret_keystore.KeystoreError:
            report.failed.append(key)
            continue
        if secret_keystore.get_secret(key) != value:
            # Read-back disagreed; keep the .env entry so the credential
            # is not lost, and let the operator investigate.
            report.failed.append(key)
            continue
        remove_env_value(key)
        report.migrated.append(key)
    return report
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `scripts/run_tests.sh tests/hermes_cli/test_secrets_migrate.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Wire up the CLI subcommand**

**The `secrets` command lives in `hermes_cli/main.py`, not `plugins_cmd.py`.** `main.py:11521`
already creates it — `subparsers.add_parser("secrets", help="Manage external secret sources
(Bitwarden, 1Password)")` — with its own sub-subparsers beneath. `plugins_cmd.py` contains no
`add_parser` calls at all; it is a handler module reached via `plugins_command`, so there is
no argparse pattern in that file to follow.

Add `migrate` to the **existing** `secrets` command's sub-subparser in `main.py`, beside the
external-secret-manager subcommands already there. Do not create a second `secrets` parser —
argparse would raise on the duplicate name, and the failure surfaces at import time as a
confusing conflict error rather than anything about this feature.

Put the handler in `hermes_cli/secrets_migrate.py` next to `migrate_secrets` and reference it
from `main.py`, keeping `main.py`'s addition to a parser block and a `set_defaults(func=...)`:

The handler:

```python
def _handle_secrets_migrate(args) -> int:
    from hermes_cli.secrets_migrate import migrate_secrets
    from hermes_cli import secret_keystore

    report = migrate_secrets(dry_run=args.dry_run)
    backend = secret_keystore.get_backend()
    backend_name = backend.name if backend is not None else "disabled"

    if not report.migrated and not report.failed:
        print("No plugin secrets found in .env — nothing to migrate.")
        return 0

    verb = "Would migrate" if report.dry_run else "Migrated"
    print(f"{verb} {len(report.migrated)} secret(s) to the {backend_name} keystore.")
    for key in report.migrated:
        print(f"  {verb.lower()}: {key}")
    if report.failed:
        print(f"\n{len(report.failed)} secret(s) could NOT be migrated and remain in .env:")
        for key in report.failed:
            print(f"  failed: {key}")
        print("\nRe-run after resolving the keystore problem. Nothing was lost.")
        return 1
    return 0
```

Add the parser wiring alongside the file's existing subparsers:

```python
    migrate_parser = secrets_subparsers.add_parser(
        "migrate",
        help="Move plugin secrets from .env into the OS keystore",
    )
    migrate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would move without changing anything",
    )
    migrate_parser.set_defaults(func=_handle_secrets_migrate)
```

- [ ] **Step 6: Verify the command end to end**

Run: `.venv/bin/python -m hermes_cli secrets migrate --dry-run`
Expected: either `No plugin secrets found in .env — nothing to migrate.` or a list of `would migrate:` lines. Exit code 0. Confirm `~/.hermes/.env` is byte-identical afterwards:

```bash
shasum ~/.hermes/.env && .venv/bin/python -m hermes_cli secrets migrate --dry-run && shasum ~/.hermes/.env
```
Expected: identical hashes.

- [ ] **Step 7: Commit**

```bash
git add hermes_cli/secrets_migrate.py tests/hermes_cli/test_secrets_migrate.py hermes_cli/main.py
git commit -m "feat: add 'hermes secrets migrate' to move plugin PATs out of .env"
```

---

### Task 8: Apply a real ACL on Windows

**Files:**
- Modify: `hermes_cli/config.py:861-876` (`_secure_file`)
- Test: `tests/hermes_cli/test_secure_file_windows.py` (create)

**Interfaces:**
- Consumes: nothing
- Produces: `_secure_file(path)` restricts access on Windows as well as POSIX; `_windows_restrict_acl(path) -> bool` returns whether the ACL was applied

`_secure_file` currently documents itself as *"Set file to owner-only read/write (0600). **No-op on Windows.**"* That leaves `~/.hermes/.env` with default ACLs — inherited from the user profile directory, which on many corporate images means Administrators and SYSTEM at minimum, and on shared or roaming-profile machines potentially more.

This is broader than plugin PATs. `.env` also holds session tokens, model provider API keys, and every other credential Hermes stores. Tasks 1–7 remove plugin secrets from that file; this task protects everything still in it.

- [ ] **Step 1: Write the failing tests**

Create `tests/hermes_cli/test_secure_file_windows.py`:

```python
"""_secure_file must restrict access on Windows, not silently no-op."""

import sys
from unittest import mock

import pytest

from hermes_cli import config


class TestWindowsAcl:
    def test_windows_path_invokes_icacls(self, tmp_path):
        target = tmp_path / ".env"
        target.write_text("SECRET=x", encoding="utf-8")
        with mock.patch.object(config.sys, "platform", "win32"):
            with mock.patch.object(config.subprocess, "run") as run:
                run.return_value = mock.Mock(returncode=0)
                config._secure_file(target)
        assert run.called
        argv = run.call_args[0][0]
        assert argv[0] == "icacls"
        assert str(target) in argv
        assert "/inheritance:r" in argv

    def test_grant_is_restricted_to_the_current_user(self, tmp_path):
        target = tmp_path / ".env"
        target.write_text("SECRET=x", encoding="utf-8")
        with mock.patch.object(config.sys, "platform", "win32"):
            with mock.patch.object(config, "_current_windows_principal",
                                   return_value="CORP\\alice"):
                with mock.patch.object(config.subprocess, "run") as run:
                    run.return_value = mock.Mock(returncode=0)
                    config._secure_file(target)
        argv = run.call_args[0][0]
        assert any("CORP\\alice" in part for part in argv)

    def test_icacls_failure_does_not_raise(self, tmp_path):
        """A failed ACL must not break setup — but see the next test."""
        target = tmp_path / ".env"
        target.write_text("SECRET=x", encoding="utf-8")
        with mock.patch.object(config.sys, "platform", "win32"):
            with mock.patch.object(config.subprocess, "run",
                                   side_effect=OSError("no icacls")):
                config._secure_file(target)

    def test_icacls_failure_warns_once(self, tmp_path, capsys):
        """Silently passing is exactly what produced this gap. A failure the
        operator never sees is indistinguishable from no protection."""
        config._WARNED_ACL_PATHS.clear()
        target = tmp_path / ".env"
        target.write_text("SECRET=x", encoding="utf-8")
        with mock.patch.object(config.sys, "platform", "win32"):
            with mock.patch.object(config.subprocess, "run",
                                   side_effect=OSError("no icacls")):
                config._secure_file(target)
                config._secure_file(target)
        warnings = capsys.readouterr().err
        assert warnings.count("could not restrict") == 1

    def test_managed_mode_still_skips(self, tmp_path):
        target = tmp_path / ".env"
        target.write_text("SECRET=x", encoding="utf-8")
        with mock.patch.object(config, "is_managed", return_value=True):
            with mock.patch.object(config.sys, "platform", "win32"):
                with mock.patch.object(config.subprocess, "run") as run:
                    config._secure_file(target)
        assert not run.called

    def test_container_still_skips(self, tmp_path):
        target = tmp_path / ".env"
        target.write_text("SECRET=x", encoding="utf-8")
        with mock.patch.object(config, "_is_container", return_value=True):
            with mock.patch.object(config.sys, "platform", "win32"):
                with mock.patch.object(config.subprocess, "run") as run:
                    config._secure_file(target)
        assert not run.called

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX behaviour")
    def test_posix_behaviour_is_unchanged(self, tmp_path):
        import os
        import stat

        target = tmp_path / ".env"
        target.write_text("SECRET=x", encoding="utf-8")
        config._secure_file(target)
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `scripts/run_tests.sh tests/hermes_cli/test_secure_file_windows.py -v`
Expected: FAIL — `AttributeError: module 'hermes_cli.config' has no attribute '_current_windows_principal'`

- [ ] **Step 3: Implement**

In `hermes_cli/config.py`, add near the other module-level warning sets:

```python
# Paths we have already warned about failing to ACL, so repeated
# save_env_value() calls do not spam one message per write.
_WARNED_ACL_PATHS: set[str] = set()
```

Then replace `_secure_file` and add its Windows helpers:

```python
def _current_windows_principal() -> str:
    """Return DOMAIN\\user for the current account, for an icacls grant.

    getpass.getuser() reads USERNAME, which is what icacls expects; the
    domain qualifier matters on corporate machines where a bare username can
    be ambiguous between the local SAM and the domain.
    """
    import getpass

    user = getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    return f"{domain}\\{user}" if domain else user


def _windows_restrict_acl(path) -> bool:
    """Grant only the current user, dropping inherited ACEs. True if applied.

    /inheritance:r removes inherited entries -- without it the grant is
    additive and whatever the profile directory already allowed still
    applies, which is the entire problem being fixed.
    """
    principal = _current_windows_principal()
    try:
        completed = subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{principal}:(R,W)",
            ],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _secure_file(path):
    """Restrict a file to its owner.

    POSIX: chmod 0600. Windows: icacls with inheritance removed -- the mode
    argument to os.chmod is close to meaningless there, which is why this
    used to be documented as a no-op and left credentials with whatever the
    user profile directory happened to allow.

    Skipped in managed mode -- the NixOS activation script sets
    group-readable permissions (0640) on config files.

    Skipped in containers -- Docker/Podman volume mounts often need broader
    permissions. Set HERMES_SKIP_CHMOD=1 to force-skip on other systems.
    """
    if is_managed() or _is_container():
        return
    try:
        if not os.path.exists(str(path)):
            return
    except OSError:
        return

    if sys.platform == "win32":
        if _windows_restrict_acl(path):
            return
        key = str(path)
        if key not in _WARNED_ACL_PATHS:
            _WARNED_ACL_PATHS.add(key)
            print(
                f"  Warning: could not restrict access to {path}. It may be "
                f"readable by other accounts on this machine. Check that "
                f"icacls.exe is available on PATH.",
                file=sys.stderr,
            )
        return

    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass
```

Ensure `subprocess` and `sys` are imported at the top of `config.py` if they are not already.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `scripts/run_tests.sh tests/hermes_cli/test_secure_file_windows.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Verify no regression in the broader config suite**

Run: `scripts/run_tests.sh tests/hermes_cli/ -v -k "config or env"`
Expected: PASS. `_secure_file` is called from `save_env_value` and its neighbours, so a mistake here shows up across the whole `.env` surface.

- [ ] **Step 6: Commit**

```bash
git add hermes_cli/config.py tests/hermes_cli/test_secure_file_windows.py
git commit -m "fix: restrict .env with an ACL on Windows instead of silently no-op"
```

> **Cross-repo follow-up, not covered here.** `ericsson-capabilities/plugins/ericsson-teams/graph_auth.py` has the same gap on the same platform. Its POSIX path (`_persist_posix`) is careful — `0o700` directory, `O_NOFOLLOW`, `O_DIRECTORY`, `O_CLOEXEC`, atomic replace through a directory fd — but `_persist_portable` passes `0o600` to `os.open` on Windows, where the mode is largely ignored, and its `mkdir` sets no mode at all. That file holds **Entra refresh tokens**, which are longer-lived than a PAT and can mint new access tokens. The fix is the same `icacls` treatment applied to `$HERMES_HOME/ericsson/`. It belongs in the connector repo, so it cannot be a task in this plan — raise it as its own change.

---

### Task 9: Platform verification gate

**Files:**
- Create: `docs/verification/2026-08-15-credential-storage-parity.md`

**Interfaces:**
- Consumes: everything from Tasks 1-8
- Produces: a signed-off verification record

This task is manual. The macOS dual-binary case in particular cannot be caught by unit tests: Keychain ACLs are bound to the accessing binary, and the desktop spawns its own interpreter (`buildDesktopBackendEnv` takes a `venvRoot`). If the desktop's interpreter path differs from the CLI's, macOS sees two applications and issues two separate authorisation prompts — or fails silently when the backend runs detached.

**How much of this blocks the merge.** An earlier draft made all four platforms blocking, which no single machine can satisfy — so the plan could not complete anywhere. The ruling:

| Platform | Status | Rationale |
|---|---|---|
| **macOS** | **Blocking** | The dual-binary Keychain case is the failure this task exists to catch, and it is the development platform. |
| Windows | Non-blocking — record as outstanding | Task 8's ACL work is unit-tested. The manual leg confirms real DPAPI behaviour and belongs on the Windows box, which `CLAUDE.md:106` already names as the live end-to-end target. |
| Linux | Non-blocking — record as outstanding | The headless / no-D-Bus path degrades to the file tier, covered by Task 2 and by Task 3's probe tests including the hanging-backend case. |
| Container | Non-blocking — record as outstanding | D4's refusal is unit-tested in Task 2. The manual leg only confirms the message an operator actually sees. |

Complete the macOS leg, then record each remaining platform in the verification document explicitly as **outstanding**, with a date and an owner. Do not mark them passed and do not delete the rows: an unrun check recorded as outstanding is a known gap, while one silently dropped is a false clean bill of health.

- [ ] **Step 1: Record the resolved backend on each platform**

On each of macOS, Windows and Linux, run:

```bash
.venv/bin/python -c "from hermes_cli import secret_keystore as s; b=s.get_backend(); print(b.name if b else 'disabled')"
```
Expected: `os` on all three desktop platforms.

- [ ] **Step 2: macOS — verify CLI and desktop share one Keychain grant**

1. Configure a plugin credential via the **CLI**.
2. Launch the **desktop app** and open the same plugin's configuration.
3. Record: did macOS prompt for Keychain access a second time?
4. Capture both interpreter paths:

```bash
# CLI
.venv/bin/python -c "import sys; print(sys.executable)"
# Desktop backend — from the running app
ps -Ao pid,command | grep -i hermes | grep -i python
```

**Pass:** same interpreter path, at most one authorisation prompt.
**Fail:** different paths or a second prompt → stop and escalate. The likely remedy is pinning the desktop backend to the same interpreter, but that is a design change and needs its own decision.

- [ ] **Step 3: macOS — verify detached backend access**

Trigger a plugin tool from a **cron-scheduled** run (no GUI session attached) and confirm the credential resolves without hanging.

**Pass:** resolves, or falls back cleanly to the file tier.
**Fail:** hangs → the probe is blocking; make the probe's failure path more aggressive before shipping.

- [ ] **Step 4: Windows — verify Credential Manager storage and the .env ACL**

Configure a plugin credential, then confirm the PAT is in Credential Manager and **not** in `.env`:

```powershell
cmdkey /list | Select-String "hermes.plugin-secrets"
Select-String -Path "$env:USERPROFILE\.hermes\.env" -Pattern "HERMES_PLUGIN_[0-9A-F]{32}_"
```
Expected: the first returns entries; the second returns nothing.

Then confirm Task 8's ACL actually applied:

```powershell
icacls "$env:USERPROFILE\.hermes\.env"
```
Expected: only the current user listed, and **no** `(I)` inherited-entry markers. Inherited ACEs present means `/inheritance:r` did not take effect and the file is still broadly readable.

- [ ] **Step 5: Container — verify the file tier and key persistence**

In the Docker image, confirm the backend resolves to `file`, then verify a credential survives a container restart.

**Pass:** credential still resolves after restart.
**Fail:** the key is ephemeral (D4) → the image must mount `~/.hermes/secrets` on a volume, or the deployment must use an external secret source. Document whichever is chosen.

- [ ] **Step 6: Verify no plugin PAT reaches `os.environ`**

With a plugin credential configured, from a running backend:

```bash
.venv/bin/python -c "import os,re; print([k for k in os.environ if re.match(r'HERMES_PLUGIN_[0-9A-F]{32}_', k)])"
```
Expected: `[]`

- [ ] **Step 7: Full regression run**

Run: `scripts/run_tests.sh`
Expected: no new failures versus the pre-change baseline. Record the baseline before starting Task 1 — with 556 env-touching test files, "was it already failing?" must be answerable.

- [ ] **Step 8: Write and commit the verification record**

Create `docs/verification/2026-08-15-credential-storage-parity.md` capturing, for each platform: resolved backend, prompt behaviour, interpreter paths, `.env` scan result, `os.environ` scan result, and the full-suite pass/fail delta.

```bash
git add docs/verification/2026-08-15-credential-storage-parity.md
git commit -m "docs: record credential storage parity platform verification"
```

---

## Self-Review

**Spec coverage.** Every element of super-cli's model in `SUPER-CLI-ARCHITECTURE.md` §4.2 maps to a task: OS keyring → Task 3; probe-then-fallback → Tasks 3 and 4; AES-GCM file with `0700`/`0600` → Task 2; lazy per-key resolution → Tasks 4 and 5; no plaintext at rest → Tasks 6 and 7. The gap-analysis item "plaintext PATs with no ACL on Windows" is closed by Tasks 3, 6 and verified in Task 8 Step 4.

**Deliberately out of scope**, and tracked separately rather than silently dropped:
- **The MSAL token cache on Windows.** `ericsson-teams/graph_auth.py:_persist_portable` has the identical gap on the identical platform, protecting **Entra refresh tokens** — a longer-lived credential than any PAT, since it can mint new access tokens. Its POSIX sibling is meticulous (`0o700`, `O_NOFOLLOW`, `O_DIRECTORY`, `O_CLOEXEC`, atomic replace via directory fd), which makes the Windows path's silence more surprising, not less. The fix is Task 8's `icacls` treatment applied to `$HERMES_HOME/ericsson/`. It lives in `ericsson-capabilities`, so it cannot be a task in this plan — it needs its own change in that repo.
- Migrating `ericsson-sharepoint` and `ericsson-teams` credential *fields* — those plugins have their own `auth.py`, and they inherit Tasks 1–7 only insofar as they use `storage: secret` fields.
- **Entra platform identity generally.** Not needed for Jira/Confluence/GitLab/ARM, which are all PAT-authenticated. It becomes relevant only if EVMS is added or if Ericsson moves those four to SSO-only.

**Type consistency.** `get_secret`/`set_secret`/`delete_secret`/`get_backend`/`reset_backend_cache` are used with identical signatures in Tasks 4-7. `KeystoreError` is defined in Task 2 and raised or caught in Tasks 3, 4, 6, 7. `MigrationReport` fields (`migrated`, `failed`, `dry_run`) match between Task 7's implementation, its tests, and the CLI handler. Backends expose the same `name`/`get`/`set`/`delete` surface, so `get_backend()` returning either is safe.

**Known risk carried into execution:** Task 8 Step 2 can fail in a way that requires a design change rather than a fix. That is why it is a gate, not a checklist item.
