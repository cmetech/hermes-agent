"""Bounded, network-free local input handling for connector commands."""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any


MAX_INPUT_BYTES = 256 * 1024
MAX_ASSIGNMENTS = 64
MAX_ASSIGNMENT_NAME = 128
MAX_ASSIGNMENT_VALUE_BYTES = 16 * 1024
MAX_CHANGE_FILES = 100


class CliInputError(ValueError):
    """A safe local-input error suitable for a CLI usage failure."""


def _decode_utf8(payload: bytes) -> str:
    if len(payload) > MAX_INPUT_BYTES:
        raise CliInputError("input exceeds the 256 KiB limit")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        raise CliInputError("input must be valid UTF-8") from None


class BoundedInputReader:
    """Read bounded text inputs and consume stdin no more than once."""

    def __init__(self, *, stdin=None) -> None:
        self._stdin = sys.stdin if stdin is None else stdin
        self._stdin_consumed = False

    def _read_stdin(self) -> str:
        if self._stdin_consumed:
            raise CliInputError("stdin may be consumed only once per invocation")
        self._stdin_consumed = True
        stream = getattr(self._stdin, "buffer", self._stdin)
        try:
            payload = stream.read(MAX_INPUT_BYTES + 1)
        except (OSError, ValueError):
            raise CliInputError("stdin could not be read") from None
        if isinstance(payload, str):
            try:
                payload = payload.encode("utf-8")
            except UnicodeEncodeError:
                raise CliInputError("input must be valid UTF-8") from None
        if not isinstance(payload, bytes):
            raise CliInputError("stdin did not provide text input")
        return _decode_utf8(payload)

    def read_text(self, value: str, *, reject_symlink: bool = True) -> str:
        """Read a regular UTF-8 file, or ``-`` for one bounded stdin read."""
        if value == "-":
            return self._read_stdin()
        if not isinstance(value, str) or not value:
            raise CliInputError("input path must be a non-empty string")
        path = Path(value).expanduser()
        try:
            initial = path.lstat()
        except (OSError, ValueError):
            raise CliInputError("input path could not be inspected") from None
        if reject_symlink and stat.S_ISLNK(initial.st_mode):
            raise CliInputError("input path must not be a symbolic link")
        if not stat.S_ISREG(initial.st_mode):
            raise CliInputError("input must be a regular file")

        flags = os.O_RDONLY
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        if reject_symlink and hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except (OSError, TypeError, ValueError):
            raise CliInputError("input must be a readable regular file") from None
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode):
                raise CliInputError("input must be a regular file")
            if (details.st_dev, details.st_ino) != (initial.st_dev, initial.st_ino):
                raise CliInputError("input file changed while opening")
            if details.st_size > MAX_INPUT_BYTES:
                raise CliInputError("input exceeds the 256 KiB limit")
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                descriptor = -1
                payload = stream.read(MAX_INPUT_BYTES + 1)
        except CliInputError:
            raise
        except OSError:
            raise CliInputError("input file could not be read") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        return _decode_utf8(payload)

    def read_change_objects(self, paths: Iterable[str]) -> list[dict[str, Any]]:
        """Read one finite JSON object from each bounded change file."""
        paths = list(paths)
        if len(paths) > MAX_CHANGE_FILES:
            raise CliInputError("change input accepts at most 100 files")
        result = []
        for path in paths:
            text = self.read_text(path, reject_symlink=True)
            try:
                value = json.loads(
                    text,
                    parse_constant=lambda _value: (_ for _ in ()).throw(
                        ValueError()
                    ),
                )
            except (TypeError, ValueError, RecursionError):
                raise CliInputError("change file must contain valid JSON") from None
            if not isinstance(value, dict):
                raise CliInputError("change file must contain one JSON object")
            result.append(value)
        return result


def resolve_local_path(value: str) -> str:
    """Resolve an ARM upload path without opening it or taking file authority."""
    if not isinstance(value, str) or not value:
        raise CliInputError("local file path must be a non-empty string")
    try:
        return str(Path(value).expanduser().resolve(strict=False))
    except (OSError, RuntimeError):
        raise CliInputError("local file path could not be resolved") from None


def _finite_json_value(raw: str) -> Any:
    try:
        value = json.loads(
            raw,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise CliInputError("NAME=VALUE contains an invalid JSON value") from None
    if len(encoded) > MAX_ASSIGNMENT_VALUE_BYTES:
        raise CliInputError("decoded NAME=VALUE exceeds the 16 KiB limit")
    return value


def decode_name_values(
    values: Iterable[str], *, list_valued: bool = False
) -> dict[str, Any]:
    """Decode a bounded sequence of deterministic ``NAME=JSON_VALUE`` items."""
    items = list(values)
    if len(items) > MAX_ASSIGNMENTS:
        raise CliInputError("NAME=VALUE accepts at most 64 items")
    result: dict[str, Any] = {}
    for item in items:
        if not isinstance(item, str) or "=" not in item:
            raise CliInputError("structured input must use NAME=VALUE")
        name, raw = item.split("=", 1)
        if not name or len(name) > MAX_ASSIGNMENT_NAME:
            raise CliInputError("NAME must contain 1 to 128 characters")
        value = _finite_json_value(raw)
        if name in result:
            if not list_valued:
                raise CliInputError(f"duplicate structured name: {name}")
            current = result[name]
            if not isinstance(current, list):
                current = [current]
                result[name] = current
            current.append(value)
        else:
            result[name] = [value] if list_valued else value
    return result


__all__ = [
    "BoundedInputReader",
    "CliInputError",
    "MAX_INPUT_BYTES",
    "decode_name_values",
    "resolve_local_path",
]
