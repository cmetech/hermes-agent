#!/usr/bin/env python3
"""Persist a small, sanitized Ericsson onboarding checkpoint per profile."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import re
import stat
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised through fail-closed tests
    fcntl = None  # type: ignore[assignment]


ALLOWED_FIELDS = {
    "schemaVersion",
    "catalogVersion",
    "selectedCapabilities",
    "maturity",
    "readinessFacts",
    "completedSteps",
    "pendingActions",
    "artifactPointers",
    "nextPrompt",
    "createdAt",
    "updatedAt",
}

READINESS_FACT_FIELDS = (
    "discoverable",
    "enabled",
    "platformSupported",
    "requiredSettingsConfigured",
    "permissionAdequate",
    "dependencyAvailable",
    "authenticationValidated",
    "safeProbeSucceeded",
)

MATURITY_VALUES = {
    "available",
    "partially-ported",
    "planned-not-implemented",
    "not-supported-no-port-planned",
}

READINESS_VALUES = {
    "ready",
    "missing",
    "needs-user-action",
    "unavailable-on-platform",
    "planned-not-implemented",
    "unknown-needs-check",
}

_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,2}$")
_CAPABILITY_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SENSITIVE_KEY_PARTS = (
    "token",
    "password",
    "cookie",
    "certificate",
    "privatekey",
    "secret",
)
_PROTECTED_VALUE_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])Bearer\s+[A-Za-z0-9._~+/=-]{16,}(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"),
    re.compile(r"(?<![A-Za-z0-9])(?:gh[opusr]_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{20,})(?![A-Za-z0-9_-])"),
    re.compile(r"(?<![A-Za-z0-9])(?:sk-(?:proj-)?|xox[baprs]-)[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])", re.I),
    re.compile(r"(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])"),
    re.compile(r"-----BEGIN (?:[A-Z ]*PRIVATE KEY|CERTIFICATE)-----"),
    re.compile(r"(?<![A-Fa-f0-9])[A-Fa-f0-9]{40,}(?![A-Fa-f0-9])"),
)
_WINDOWS_DEVICE_NAMES = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.I
)

_MAX_PAYLOAD_BYTES = 64 * 1024
_MAX_COLLECTION_ITEMS = 64
_MAX_CAPABILITIES = 32
_MAX_TEXT_LENGTH = 2048
_MAX_PROMPT_LENGTH = 4096
_MAX_POINTER_LENGTH = 1024
_MAX_JSON_DEPTH = 24
_MAX_JSON_NODES = 2048
_MAX_JSON_CONTAINER = 64
_LOCK_TIMEOUT_SECONDS = 0.5
_LOCK_RETRY_SECONDS = 0.02

_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_SECURE_PLATFORM = bool(
    os.name == "posix"
    and fcntl is not None
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "fchmod")
    and os.open in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and os.rename in os.supports_dir_fd
    and os.link in os.supports_dir_fd
)


class StateError(Exception):
    """Base class for safe, user-displayable state errors."""


class StateValidationError(StateError):
    """The proposed checkpoint is not safe or does not match the schema."""


class StateIOError(StateError):
    """The checkpoint could not be read or written safely."""


_WINDOWS_BACKEND: Any | None = None


def _backend_kind(platform_name: str | None = None) -> str:
    """Select a secure persistence backend without silently degrading."""

    name = os.name if platform_name is None else platform_name
    if name == "posix":
        return "posix"
    if name == "nt":
        return "windows"
    raise StateIOError(
        "Secure onboarding-state persistence is unavailable on this platform."
    )


def _windows_backend() -> Any:
    global _WINDOWS_BACKEND
    if _WINDOWS_BACKEND is not None:
        return _WINDOWS_BACKEND
    path = Path(__file__).with_name("onboarding_state_windows.py")
    spec = importlib.util.spec_from_file_location("_ericsson_onboarding_state_windows", path)
    if spec is None or spec.loader is None:
        raise StateIOError(
            "Secure onboarding-state persistence is unavailable on this platform."
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise StateIOError(
            "Secure onboarding-state persistence is unavailable on this platform."
        ) from error
    _WINDOWS_BACKEND = module
    return module


def _windows_call(name: str, *args: object, **kwargs: object) -> Any:
    backend = _windows_backend()
    try:
        return getattr(backend, name)(*args, **kwargs)
    except backend.WindowsStateError as error:
        raise StateIOError(str(error)) from error


def _secure_platform_available() -> bool:
    """Whether descriptor-relative, no-follow persistence is available."""

    return _SECURE_PLATFORM


def _require_secure_platform() -> None:
    if not _secure_platform_available():
        raise StateIOError(
            "Secure onboarding-state persistence is unavailable on this platform."
        )


def _resolved_path(value: os.PathLike[str] | str) -> Path:
    try:
        text = os.fspath(value)
        if not isinstance(text, str) or not text.strip() or "\x00" in text:
            raise ValueError
        path = Path(text).expanduser()
        # Preserve the lexical Windows path so the native backend can inspect every
        # component for reparse points instead of resolving through one first.
        if os.name == "nt":
            return path.absolute()
        return path.resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise StateError("The active Co-Worker profile home is not available.") from error


def resolve_home(explicit: os.PathLike[str] | str | None = None) -> Path:
    """Resolve the active profile without inventing a legacy default path."""

    if explicit is not None:
        return _resolved_path(explicit)
    try:
        module = importlib.import_module("hermes_constants")
        getter = getattr(module, "get_hermes_home", None)
        runtime_home = getter() if callable(getter) else None
    except Exception:
        runtime_home = None
    if runtime_home:
        return _resolved_path(runtime_home)
    environment_home = os.environ.get("HERMES_HOME")
    if environment_home:
        return _resolved_path(environment_home)
    raise StateError(
        "The active Co-Worker profile home is not available; pass --home or start "
        "this check from Co-Worker."
    )


def _sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _protected_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _PROTECTED_VALUE_PATTERNS)


def _validate_json_tree(value: object) -> None:
    """Bound and inspect JSON-shaped data without recursive traversal."""

    stack: list[tuple[object, int]] = [(value, 0)]
    seen: set[int] = set()
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
            raise StateValidationError("Onboarding state exceeds safe structure limits.")
        if item is None or isinstance(item, (str, bool, int, float)):
            if isinstance(item, float) and (item != item or item in (float("inf"), float("-inf"))):
                raise StateValidationError("Onboarding state contains a non-standard number.")
            if isinstance(item, str) and _protected_value(item):
                raise StateValidationError(
                    "A credential-like value is not allowed in onboarding state."
                )
            continue
        if not isinstance(item, (dict, list)):
            raise StateValidationError("Onboarding state must be ordinary JSON data.")
        identity = id(item)
        if identity in seen:
            raise StateValidationError("Onboarding state must not reuse or cycle containers.")
        seen.add(identity)
        if len(item) > _MAX_JSON_CONTAINER:
            raise StateValidationError("Onboarding state exceeds safe structure limits.")
        if isinstance(item, dict):
            for key, nested in item.items():
                if not isinstance(key, str):
                    raise StateValidationError("Onboarding state object keys must be strings.")
                if _sensitive_key(key):
                    raise StateValidationError(
                        "Sensitive field names are not allowed in onboarding state."
                    )
                stack.append((nested, depth + 1))
        else:
            stack.extend((nested, depth + 1) for nested in item)


def _reject_constant(value: str) -> object:
    del value
    raise StateValidationError("Onboarding state contains a non-standard number.")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise StateValidationError("Onboarding state contains duplicate object fields.")
        result[key] = value
    return result


def _strict_json_loads(text: str) -> object:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except StateError:
        raise
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError) as error:
        raise StateValidationError("Onboarding state is not valid strict JSON.") from error
    _validate_json_tree(value)
    return value


def _text(value: object, field: str, *, maximum: int = _MAX_TEXT_LENGTH) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise StateValidationError(f"{field} must be a non-empty bounded string.")
    if any(ord(character) < 32 and character != "\t" for character in value):
        raise StateValidationError(f"{field} contains unsupported control characters.")
    return value


def _version(value: object, field: str) -> str:
    text = _text(value, field, maximum=32)
    if not _VERSION_RE.fullmatch(text):
        raise StateValidationError(f"{field} must be a numeric dotted version.")
    return text


def _capability_id(value: object, field: str) -> str:
    text = _text(value, field, maximum=80)
    if not _CAPABILITY_ID_RE.fullmatch(text):
        raise StateValidationError(f"{field} must be a lowercase capability slug.")
    return text


def _timestamp(value: object, field: str) -> str:
    text = _text(value, field, maximum=40)
    if "T" not in text or not (text.endswith("Z") or re.search(r"[+-]\d\d:\d\d$", text)):
        raise StateValidationError(f"{field} must be an ISO 8601 timestamp with a timezone.")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as error:
        raise StateValidationError(
            f"{field} must be an ISO 8601 timestamp with a timezone."
        ) from error
    if parsed.tzinfo is None:
        raise StateValidationError(f"{field} must be an ISO 8601 timestamp with a timezone.")
    return text


def _string_list(
    value: object,
    field: str,
    *,
    maximum_items: int = _MAX_COLLECTION_ITEMS,
    maximum_length: int = _MAX_TEXT_LENGTH,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise StateValidationError(f"{field} must be a bounded list of strings.")
    return [_text(item, f"{field} item", maximum=maximum_length) for item in value]


def _artifact_pointer(value: object) -> str:
    pointer = _text(value, "artifactPointers item", maximum=_MAX_POINTER_LENGTH)
    if any(ord(character) < 32 for character in pointer):
        raise StateValidationError("Artifact pointers cannot contain control characters.")
    normalized = pointer.replace("\\", "/")
    lowered = normalized.lower()
    if normalized.startswith("//") or lowered.startswith(
        ("//?/", "//./", "/??/", "/device/")
    ):
        raise StateValidationError("Artifact pointers cannot use network or device paths.")
    drive = bool(re.match(r"^[A-Za-z]:/", normalized))
    remainder = normalized[2:] if drive else normalized
    if ":" in remainder:
        raise StateValidationError("Artifact pointers cannot use URLs or alternate streams.")
    if not drive and re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", normalized):
        raise StateValidationError("Artifact pointers must be local paths, not URLs.")
    if normalized.startswith("~") or ".." in PurePosixPath(normalized).parts:
        raise StateValidationError("Artifact pointers cannot traverse outside their path.")
    components = [part.rstrip(" .") for part in normalized.split("/") if part]
    if any(_WINDOWS_DEVICE_NAMES.fullmatch(part) for part in components):
        raise StateValidationError("Artifact pointers cannot use device names.")
    return pointer


def validate_state(payload: object) -> dict[str, Any]:
    """Return a JSON-normalized checkpoint after strict safety validation."""

    _validate_json_tree(payload)
    try:
        serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise StateValidationError("Onboarding state must be ordinary JSON data.") from error
    if len(serialized.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise StateValidationError("Onboarding state exceeds the safe size limit.")
    normalized = _strict_json_loads(serialized)
    if not isinstance(normalized, dict):
        raise StateValidationError("Onboarding state must be a JSON object.")
    if set(normalized) != ALLOWED_FIELDS:
        raise StateValidationError("Onboarding state fields do not match the supported schema.")

    _version(normalized["schemaVersion"], "schemaVersion")
    _version(normalized["catalogVersion"], "catalogVersion")
    selected = _string_list(
        normalized["selectedCapabilities"],
        "selectedCapabilities",
        maximum_items=_MAX_CAPABILITIES,
        maximum_length=80,
    )
    selected = [_capability_id(item, "selectedCapabilities item") for item in selected]
    if len(selected) != len(set(selected)):
        raise StateValidationError("selectedCapabilities cannot contain duplicates.")
    selected_set = set(selected)

    maturity = normalized["maturity"]
    if not isinstance(maturity, dict):
        raise StateValidationError("maturity must map capability IDs to maturity values.")
    maturity_ids = {_capability_id(key, "maturity capability ID") for key in maturity}
    if maturity_ids != selected_set:
        raise StateValidationError("maturity must contain every selected capability once.")
    if any(not isinstance(value, str) or value not in MATURITY_VALUES for value in maturity.values()):
        raise StateValidationError("maturity contains an unsupported value.")

    readiness = normalized["readinessFacts"]
    if not isinstance(readiness, dict) or len(readiness) > _MAX_CAPABILITIES:
        raise StateValidationError("readinessFacts must be a bounded capability mapping.")
    expected_fact_fields = {"state", *READINESS_FACT_FIELDS}
    for capability, facts in readiness.items():
        capability_id = _capability_id(capability, "readinessFacts capability ID")
        if capability_id not in selected_set:
            raise StateValidationError("readinessFacts can only describe selected capabilities.")
        if not isinstance(facts, dict) or set(facts) != expected_fact_fields:
            raise StateValidationError(
                "Each readinessFacts entry must contain the supported fact fields."
            )
        readiness_state = facts["state"]
        if not isinstance(readiness_state, str) or readiness_state not in READINESS_VALUES:
            raise StateValidationError("readinessFacts contains an unsupported state.")
        for fact in READINESS_FACT_FIELDS:
            if facts[fact] is not None and not isinstance(facts[fact], bool):
                raise StateValidationError("Readiness fact values must be Boolean or null.")

    _string_list(normalized["completedSteps"], "completedSteps")
    _string_list(normalized["pendingActions"], "pendingActions")
    pointers = _string_list(
        normalized["artifactPointers"],
        "artifactPointers",
        maximum_length=_MAX_POINTER_LENGTH,
    )
    for pointer in pointers:
        _artifact_pointer(pointer)
    _text(normalized["nextPrompt"], "nextPrompt", maximum=_MAX_PROMPT_LENGTH)
    _timestamp(normalized["createdAt"], "createdAt")
    _timestamp(normalized["updatedAt"], "updatedAt")
    return normalized


def _identity(descriptor: int) -> tuple[int, int]:
    details = os.fstat(descriptor)
    return details.st_dev, details.st_ino


def _sync_descriptor(descriptor: int, message: str) -> None:
    try:
        os.fsync(descriptor)
    except OSError as error:
        raise StateIOError(message) from error


def _repair_mode(descriptor: int, mode: int, *, directory: bool) -> None:
    details = os.fstat(descriptor)
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(details.st_mode):
        raise StateIOError("The onboarding state path has an unsafe object type.")
    current_mode = stat.S_IMODE(details.st_mode)
    try:
        if current_mode != mode:
            os.fchmod(descriptor, mode)
        repaired = os.fstat(descriptor)
    except OSError as error:
        raise StateIOError("Unable to enforce onboarding state permissions.") from error
    if stat.S_IMODE(repaired.st_mode) != mode:
        raise StateIOError("Unable to enforce onboarding state permissions.")
    if current_mode != mode:
        _sync_descriptor(
            descriptor,
            "Unable to durably enforce onboarding state permissions.",
        )


def _open_dir_at(parent: int, name: str, *, create: bool, repair: bool) -> int:
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
    except FileNotFoundError:
        if not create:
            raise StateIOError("The onboarding state directory changed unexpectedly.")
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
        except OSError as error:
            raise StateIOError("Unable to prepare the onboarding state directory.") from error
        # The entry was observed absent. Even if another creator won the mkdir
        # race, sync the trusted parent before accepting and using that entry.
        _sync_descriptor(
            parent,
            "Unable to durably create the onboarding state directory.",
        )
        try:
            descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
        except OSError as error:
            raise StateIOError("Unable to prepare the onboarding state directory.") from error
    except OSError as error:
        raise StateIOError(
            "Refusing a symbolic link or unsafe onboarding state directory."
        ) from error
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise StateIOError("The onboarding state path is not a directory.")
        if repair:
            _repair_mode(descriptor, 0o700, directory=True)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_home(path: Path) -> int:
    """Walk the absolute home from filesystem root without following symlinks."""

    descriptor = os.open(os.sep, _DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            try:
                child = _open_dir_at(descriptor, part, create=True, repair=False)
            except StateError:
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


@dataclass
class _StateDirectory:
    home_path: Path
    home_fd: int
    onboarding_fd: int
    root_fd: int
    home_identity: tuple[int, int]
    onboarding_identity: tuple[int, int]
    root_identity: tuple[int, int]

    @classmethod
    def open(cls, home: os.PathLike[str] | str) -> "_StateDirectory":
        _require_secure_platform()
        home_path = _resolved_path(home)
        home_fd = _open_home(home_path)
        onboarding_fd = root_fd = -1
        try:
            onboarding_fd = _open_dir_at(home_fd, "onboarding", create=True, repair=True)
            root_fd = _open_dir_at(onboarding_fd, "ericsson", create=True, repair=True)
            return cls(
                home_path,
                home_fd,
                onboarding_fd,
                root_fd,
                _identity(home_fd),
                _identity(onboarding_fd),
                _identity(root_fd),
            )
        except Exception:
            if root_fd >= 0:
                os.close(root_fd)
            if onboarding_fd >= 0:
                os.close(onboarding_fd)
            os.close(home_fd)
            raise

    @property
    def path(self) -> Path:
        return self.home_path / "onboarding/ericsson"

    def verify(self) -> None:
        reopened_home = _open_home(self.home_path)
        reopened_onboarding = reopened_root = -1
        try:
            if _identity(reopened_home) != self.home_identity:
                raise StateIOError("The active profile home changed during the operation.")
            reopened_onboarding = _open_dir_at(
                reopened_home, "onboarding", create=False, repair=False
            )
            if _identity(reopened_onboarding) != self.onboarding_identity:
                raise StateIOError("The onboarding state directory changed during the operation.")
            reopened_root = _open_dir_at(
                reopened_onboarding, "ericsson", create=False, repair=False
            )
            if _identity(reopened_root) != self.root_identity:
                raise StateIOError("The onboarding state directory changed during the operation.")
        finally:
            if reopened_root >= 0:
                os.close(reopened_root)
            if reopened_onboarding >= 0:
                os.close(reopened_onboarding)
            os.close(reopened_home)

    def close(self) -> None:
        os.close(self.root_fd)
        os.close(self.onboarding_fd)
        os.close(self.home_fd)


def _open_regular_at(directory: int, name: str, flags: int = os.O_RDONLY) -> int | None:
    try:
        descriptor = os.open(name, flags | _NOFOLLOW, dir_fd=directory)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise StateIOError("Refusing an unsafe onboarding state file.") from error
    try:
        _repair_mode(descriptor, 0o600, directory=False)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


@contextmanager
def _locked_directory(home: os.PathLike[str] | str) -> Iterator[_StateDirectory]:
    root = _StateDirectory.open(home)
    lock_fd: int | None = None
    locked = False
    primary_failed = False
    try:
        try:
            for attempt in range(3):
                try:
                    lock_fd = os.open(
                        ".state.lock",
                        os.O_RDWR | _NOFOLLOW,
                        dir_fd=root.root_fd,
                    )
                    break
                except FileNotFoundError as missing:
                    try:
                        lock_fd = os.open(
                            ".state.lock",
                            os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
                            0o600,
                            dir_fd=root.root_fd,
                        )
                        _sync_descriptor(
                            root.root_fd,
                            "Unable to durably create the onboarding state lock.",
                        )
                        break
                    except FileExistsError:
                        if attempt == 2:
                            raise missing
            if lock_fd is None:
                raise StateIOError("Unable to lock onboarding state safely.")
            _repair_mode(lock_fd, 0o600, directory=False)
            assert fcntl is not None
            deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError as error:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise StateIOError(
                            "Onboarding state is busy; retry shortly."
                        ) from error
                    time.sleep(min(_LOCK_RETRY_SECONDS, remaining))
        except StateError:
            raise
        except OSError as error:
            raise StateIOError("Unable to lock onboarding state safely.") from error
        root.verify()
        yield root
        root.verify()
    except BaseException:
        primary_failed = True
        raise
    finally:
        release_error: OSError | None = None
        if lock_fd is not None:
            try:
                if fcntl is not None and locked:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError as error:
                release_error = error
            try:
                os.close(lock_fd)
            except OSError as error:
                release_error = release_error or error
        try:
            root.close()
        except OSError as error:
            release_error = release_error or error
        if release_error is not None and not primary_failed:
            raise StateIOError(
                "Unable to release the onboarding state lock safely."
            ) from release_error


def _read_fd(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, 8192)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_PAYLOAD_BYTES:
            raise StateValidationError("Saved onboarding state exceeds the safe size limit.")
        chunks.append(chunk)
    return b"".join(chunks)


@dataclass(frozen=True)
class _Snapshot:
    payload: dict[str, Any]
    identity: tuple[int, int]
    digest: bytes


def _load_snapshot(root: _StateDirectory) -> _Snapshot | None:
    descriptor = _open_regular_at(root.root_fd, "current.json")
    if descriptor is None:
        return None
    try:
        identity = _identity(descriptor)
        content = _read_fd(descriptor)
        if _identity(descriptor) != identity:
            raise StateIOError("Saved onboarding state changed while it was read.")
    finally:
        os.close(descriptor)
    try:
        text = content.decode("utf-8")
    except UnicodeError as error:
        raise StateIOError("Unable to read saved onboarding state safely.") from error
    payload = validate_state(_strict_json_loads(text))
    return _Snapshot(payload, identity, hashlib.sha256(content).digest())


def _encode(payload: dict[str, Any]) -> bytes:
    content = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    if len(content) > _MAX_PAYLOAD_BYTES:
        raise StateValidationError("Onboarding state exceeds the safe size limit.")
    return content


def _decode_payload(content: bytes) -> dict[str, Any]:
    if len(content) > _MAX_PAYLOAD_BYTES:
        raise StateValidationError("Saved onboarding state exceeds the safe size limit.")
    try:
        text = content.decode("utf-8")
    except UnicodeError as error:
        raise StateIOError("Unable to read saved onboarding state safely.") from error
    return validate_state(_strict_json_loads(text))


def _write_temp(directory: int, prefix: str, content: bytes) -> tuple[str, tuple[int, int]]:
    name = f".{prefix}.{uuid.uuid4().hex}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        _repair_mode(descriptor, 0o600, directory=False)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
        return name, _identity(descriptor)
    except StateError as error:
        if not _unlink_if_present(directory, name):
            raise StateIOError(
                f"{error} A leftover temporary file named {name} remains; "
                "inspect and remove it before retrying."
            ) from error
        raise
    except OSError as error:
        cleanup = ""
        if not _unlink_if_present(directory, name):
            cleanup = (
                f" A leftover temporary file named {name} remains; inspect and "
                "remove it before retrying."
            )
        raise StateIOError(
            f"Unable to write onboarding state safely.{cleanup}"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _unlink_if_present(directory: int, name: str) -> bool:
    try:
        os.unlink(name, dir_fd=directory)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _verify_named_identity(directory: int, name: str, identity: tuple[int, int]) -> None:
    descriptor = _open_regular_at(directory, name)
    if descriptor is None:
        raise StateIOError("Onboarding state changed during the operation.")
    try:
        if _identity(descriptor) != identity:
            raise StateIOError("Onboarding state changed during the operation.")
    finally:
        os.close(descriptor)


def _replace_current(root: _StateDirectory, payload: dict[str, Any]) -> None:
    existing = _open_regular_at(root.root_fd, "current.json")
    if existing is not None:
        os.close(existing)
    content = _encode(payload)
    temporary, identity = _write_temp(root.root_fd, "current.json", content)
    replaced = False
    try:
        root.verify()
        os.replace(
            temporary,
            "current.json",
            src_dir_fd=root.root_fd,
            dst_dir_fd=root.root_fd,
        )
        replaced = True
        _verify_named_identity(root.root_fd, "current.json", identity)
        root.verify()
        os.fsync(root.root_fd)
    except StateError as error:
        if replaced:
            raise StateIOError(
                "The current.json replacement may have committed, but durability or "
                "verification is uncertain; inspect current.json before retrying."
            ) from error
        if not _unlink_if_present(root.root_fd, temporary):
            raise StateIOError(
                f"{error} A leftover temporary file named {temporary} remains; "
                "inspect and remove it before retrying."
            ) from error
        raise
    except OSError as error:
        if replaced:
            raise StateIOError(
                "The current.json replacement may have committed, but durability or "
                "verification is uncertain; inspect current.json before retrying."
            ) from error
        cleanup = ""
        if not _unlink_if_present(root.root_fd, temporary):
            cleanup = (
                f" A leftover temporary file named {temporary} remains; inspect and "
                "remove it before retrying."
            )
        raise StateIOError(
            f"Unable to write onboarding state safely.{cleanup}"
        ) from error
    finally:
        _unlink_if_present(root.root_fd, temporary)


@dataclass
class _HistoryDirectory:
    root: _StateDirectory
    fd: int
    identity: tuple[int, int]

    @classmethod
    def open(cls, root: _StateDirectory) -> "_HistoryDirectory":
        descriptor = _open_dir_at(root.root_fd, "history", create=True, repair=True)
        return cls(root, descriptor, _identity(descriptor))

    def verify(self) -> None:
        descriptor = _open_dir_at(self.root.root_fd, "history", create=False, repair=False)
        try:
            if _identity(descriptor) != self.identity:
                raise StateIOError("The onboarding history directory changed during the operation.")
        finally:
            os.close(descriptor)

    def close(self) -> None:
        os.close(self.fd)


def _archive_no_replace(
    root: _StateDirectory, name: str, payload: dict[str, Any]
) -> Path:
    history = _HistoryDirectory.open(root)
    temporary = ""
    linked = False
    relative_history = f"history/{name}"
    try:
        temporary, identity = _write_temp(history.fd, name, _encode(payload))
        root.verify()
        history.verify()
        try:
            os.link(
                temporary,
                name,
                src_dir_fd=history.fd,
                dst_dir_fd=history.fd,
                follow_symlinks=False,
            )
            linked = True
        except FileExistsError as error:
            raise StateIOError("An onboarding history entry already exists.") from error
        _verify_named_identity(history.fd, name, identity)
        os.unlink(temporary, dir_fd=history.fd)
        temporary = ""
        os.fsync(history.fd)
        history.verify()
        root.verify()
        return root.path / "history" / name
    except StateError as error:
        cleanup = ""
        if temporary and not _unlink_if_present(history.fd, temporary):
            cleanup = (
                f" A leftover temporary remains at history/{temporary}; inspect and "
                "remove it before retrying."
            )
        temporary = ""
        if linked:
            raise StateIOError(
                f"A partial archive may exist at {relative_history}, and current.json "
                f"remains active; inspect both artifacts before retrying.{cleanup}"
            ) from error
        if cleanup:
            raise StateIOError(f"{error}{cleanup}") from error
        raise
    except OSError as error:
        cleanup = ""
        if temporary and not _unlink_if_present(history.fd, temporary):
            cleanup = (
                f" A leftover temporary remains at history/{temporary}; inspect and "
                "remove it before retrying."
            )
        temporary = ""
        if linked:
            raise StateIOError(
                f"A partial archive may exist at {relative_history}, and current.json "
                f"remains active; inspect both artifacts before retrying.{cleanup}"
            ) from error
        raise StateIOError(
            f"Unable to archive onboarding state safely.{cleanup}"
        ) from error
    finally:
        if temporary:
            _unlink_if_present(history.fd, temporary)
        history.close()


def _best_effort_sync(descriptor: int) -> bool:
    try:
        os.fsync(descriptor)
        return True
    except OSError:
        return False


def _restore_quarantine(root: _StateDirectory, quarantine: str) -> str:
    """Restore or expose a quarantined generation without overwriting current."""

    recovery_path = root.path / quarantine
    try:
        os.link(
            quarantine,
            "current.json",
            src_dir_fd=root.root_fd,
            dst_dir_fd=root.root_fd,
            follow_symlinks=False,
        )
    except FileExistsError:
        _best_effort_sync(root.root_fd)
        return (
            "a newer journey remains at current.json and the prior journey remains "
            f"at {recovery_path}; inspect both before retrying"
        )
    except OSError:
        _best_effort_sync(root.root_fd)
        return (
            f"the prior journey remains at {recovery_path}; inspect that recovery "
            "file and restore it to current.json before retrying"
        )

    if not _best_effort_sync(root.root_fd):
        return (
            "the active journey is accessible at current.json and a recovery link "
            f"remains at {recovery_path}; inspect both before retrying"
        )
    try:
        os.unlink(quarantine, dir_fd=root.root_fd)
    except OSError:
        _best_effort_sync(root.root_fd)
        return (
            "the active journey is restored at current.json and a recovery link "
            f"remains at {recovery_path}; inspect both before retrying"
        )
    if not _best_effort_sync(root.root_fd):
        return (
            "the active journey is restored at current.json, but restoration "
            "durability is unconfirmed; inspect current.json before retrying"
        )
    return "the active journey was restored at current.json; inspect it and retry"


def _remove_snapshot(root: _StateDirectory, snapshot: _Snapshot) -> None:
    quarantine = f".current.{uuid.uuid4().hex}.remove"
    try:
        os.rename(
            "current.json",
            quarantine,
            src_dir_fd=root.root_fd,
            dst_dir_fd=root.root_fd,
        )
    except FileNotFoundError as error:
        raise StateIOError("Onboarding state changed during the operation.") from error
    except OSError as error:
        raise StateIOError("Unable to remove onboarding state safely.") from error

    try:
        descriptor = _open_regular_at(root.root_fd, quarantine)
        if descriptor is None:
            raise StateIOError("Onboarding state changed during the operation.")
        try:
            content = _read_fd(descriptor)
            same = (
                _identity(descriptor) == snapshot.identity
                and hashlib.sha256(content).digest() == snapshot.digest
            )
        finally:
            os.close(descriptor)
    except Exception as error:
        recovery = _restore_quarantine(root, quarantine)
        raise StateIOError(
            f"Current removal failed; {recovery}."
        ) from error
    if not same:
        recovery = _restore_quarantine(root, quarantine)
        raise StateIOError(f"Onboarding state changed; {recovery}.")
    try:
        os.unlink(quarantine, dir_fd=root.root_fd)
    except OSError as error:
        recovery = _restore_quarantine(root, quarantine)
        raise StateIOError(f"Current removal failed; {recovery}.") from error
    try:
        os.fsync(root.root_fd)
    except OSError as error:
        raise StateIOError(
            "current.json was removed, but removal durability is unconfirmed; "
            "inspect current onboarding state before retrying"
        ) from error


def save_current(home: os.PathLike[str] | str, payload: object) -> Path:
    validated = validate_state(payload)
    if _backend_kind() == "windows":
        return _windows_call("save_current_bytes", home, _encode(validated))
    with _locked_directory(home) as root:
        _replace_current(root, validated)
        return root.path / "current.json"


def load_current(home: os.PathLike[str] | str) -> dict[str, Any] | None:
    if _backend_kind() == "windows":
        content = _windows_call("load_current_bytes", home)
        return None if content is None else _decode_payload(content)
    with _locked_directory(home) as root:
        snapshot = _load_snapshot(root)
        return None if snapshot is None else snapshot.payload


def complete_current(
    home: os.PathLike[str] | str, now: datetime | None = None
) -> Path:
    moment = now or datetime.now(timezone.utc)
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise StateValidationError("Completion time must include a timezone.")
    stamp = moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if _backend_kind() == "windows":
        return _windows_call(
            "complete_current_bytes", home, f"{stamp}.json", _decode_payload
        )
    with _locked_directory(home) as root:
        snapshot = _load_snapshot(root)
        if snapshot is None:
            raise StateIOError("There is no active onboarding state to complete.")
        history = _archive_no_replace(root, f"{stamp}.json", snapshot.payload)
        try:
            _remove_snapshot(root, snapshot)
        except StateIOError as error:
            raise StateIOError(
                f"Completion archived history at {history}, but did not finish: "
                f"{error} Inspect the history and recovery locations before retrying."
            ) from error
        return history


def clear_current(home: os.PathLike[str] | str) -> bool:
    if _backend_kind() == "windows":
        return _windows_call("clear_current_bytes", home, _decode_payload)
    with _locked_directory(home) as root:
        snapshot = _load_snapshot(root)
        if snapshot is None:
            return False
        try:
            _remove_snapshot(root, snapshot)
        except StateIOError as error:
            raise StateIOError(
                f"Clear did not finish: {error} Inspect current.json or the named "
                "recovery location before retrying."
            ) from error
        return True


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise StateValidationError("The onboarding state command is invalid.")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description=__doc__)
    parser.add_argument("--home", help="Explicit active Co-Worker profile home")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show", help="Show the sanitized active checkpoint")
    save = commands.add_parser("save", help="Save a sanitized JSON checkpoint")
    save.add_argument("--input", required=True, help="Path to sanitized JSON input")
    commands.add_parser("complete", help="Move the checkpoint to local history")
    commands.add_parser("clear", help="Forget the active checkpoint")
    return parser


def _read_input(path: str) -> object:
    try:
        source = Path(path)
        if source.stat().st_size > _MAX_PAYLOAD_BYTES:
            raise StateValidationError("Onboarding state exceeds the safe size limit.")
        content = source.read_bytes()
        if len(content) > _MAX_PAYLOAD_BYTES:
            raise StateValidationError("Onboarding state exceeds the safe size limit.")
        return _strict_json_loads(content.decode("utf-8"))
    except StateError:
        raise
    except (OSError, RuntimeError, UnicodeError, ValueError) as error:
        raise StateIOError("Unable to read the onboarding state input as JSON.") from error


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        home = resolve_home(args.home)
        if args.command == "show":
            result: dict[str, object] = {"ok": True, "state": load_current(home)}
        elif args.command == "save":
            result = {"ok": True, "path": str(save_current(home, _read_input(args.input)))}
        elif args.command == "complete":
            result = {"ok": True, "path": str(complete_current(home))}
        else:
            result = {"ok": True, "cleared": clear_current(home)}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except StateError as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 1
    except Exception:
        print(
            json.dumps(
                {"ok": False, "error": "Unable to process onboarding state safely."},
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
