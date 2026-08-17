"""Pure schema and read-only parsing for secret authority metadata.

Persistence remains centralized in ``secret_keystore`` so authority replacements
share its atomic write and strict platform-permission boundary.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


AUTHORITY_FILE = "authority.json"
AUTHORITY_VERSION = 1


class SecretAuthority(str, Enum):
    OS = "os"
    FILE = "file"
    CLEARED = "cleared"


class AuthorityRegistryError(ValueError):
    pass


def _is_reparse_point(info: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and getattr(info, "st_file_attributes", 0) & flag)


def _identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


@dataclass(frozen=True)
class AuthorityRegistry:
    version: int
    entries: Mapping[str, SecretAuthority]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AuthorityRegistryError(f"duplicate authority field: {key}")
        result[key] = value
    return result


def _validated_entries(version: Any, entries: Any) -> dict[str, SecretAuthority]:
    if type(version) is not int or version != AUTHORITY_VERSION:
        raise AuthorityRegistryError("unsupported authority registry version")
    if not isinstance(entries, dict):
        raise AuthorityRegistryError("authority entries must be an object")
    validated: dict[str, SecretAuthority] = {}
    for key, state in entries.items():
        if not isinstance(key, str) or not key:
            raise AuthorityRegistryError("authority keys must be nonempty strings")
        if not isinstance(state, str):
            raise AuthorityRegistryError("authority states must be strings")
        try:
            validated[key] = SecretAuthority(state)
        except ValueError as exc:
            raise AuthorityRegistryError(f"invalid authority state for {key}") from exc
    return validated


def encode_authority_registry(registry: AuthorityRegistry) -> bytes:
    if not isinstance(registry, AuthorityRegistry):
        raise AuthorityRegistryError("invalid authority registry")
    entries = _validated_entries(registry.version, dict(registry.entries))
    # The public constructor is intentionally lightweight, but callers must
    # still supply enum values rather than unvalidated strings.
    if any(
        not isinstance(state, SecretAuthority) for state in registry.entries.values()
    ):
        raise AuthorityRegistryError("authority entries must use SecretAuthority")
    payload = {
        "version": registry.version,
        "authorities": {key: entries[key].value for key in sorted(entries)},
    }
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def load_authority_registry(root: Path) -> AuthorityRegistry | None:
    """Read and strictly validate authority metadata without creating paths."""
    root = Path(root)
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AuthorityRegistryError("cannot inspect authority registry root") from exc
    if _is_reparse_point(root_info):
        raise AuthorityRegistryError("authority registry root is a reparse point")
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise AuthorityRegistryError("authority registry root is not a direct directory")

    path = root / AUTHORITY_FILE
    try:
        expected = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AuthorityRegistryError("cannot inspect authority registry") from exc
    if _is_reparse_point(expected):
        raise AuthorityRegistryError("authority registry is a reparse point")
    if stat.S_ISLNK(expected.st_mode) or not stat.S_ISREG(expected.st_mode):
        raise AuthorityRegistryError("authority registry is not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd: int | None = None
    try:
        fd = os.open(str(path), flags)
        opened = os.fstat(fd)
        if _is_reparse_point(opened) or not stat.S_ISREG(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (expected.st_dev, expected.st_ino):
            raise AuthorityRegistryError("authority registry changed while reading")
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = None
            payload = handle.read()
        after_root = root.lstat()
        if (
            _is_reparse_point(after_root)
            or stat.S_ISLNK(after_root.st_mode)
            or not stat.S_ISDIR(after_root.st_mode)
            or _identity(after_root) != _identity(root_info)
        ):
            raise AuthorityRegistryError("authority registry root changed while reading")
        raw = json.loads(
            payload,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except AuthorityRegistryError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuthorityRegistryError("cannot read authority registry") from exc
    finally:
        if fd is not None:
            os.close(fd)
    if not isinstance(raw, dict) or set(raw) != {"version", "authorities"}:
        raise AuthorityRegistryError("invalid authority registry fields")
    entries = _validated_entries(raw["version"], raw["authorities"])
    return AuthorityRegistry(
        version=AUTHORITY_VERSION,
        entries=MappingProxyType(entries),
    )


__all__ = [
    "AUTHORITY_FILE",
    "AUTHORITY_VERSION",
    "AuthorityRegistry",
    "AuthorityRegistryError",
    "SecretAuthority",
    "encode_authority_registry",
    "load_authority_registry",
]
