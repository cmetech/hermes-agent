"""Immutable executor output identities for Archon workflow consumers."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, tuple | list):
        return tuple(_freeze_json(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class PrimaryOutputCandidate:
    """One attempt-local authoritative value, not yet a publication."""

    attempt_relative_path: str
    media_type: str
    size_bytes: int
    sha256: str
    structured_value: object | None
    schema_fingerprint: str | None
    canonicalization_version: int
    output_type: str | None

    def __post_init__(self) -> None:
        if self.structured_value is not None:
            object.__setattr__(
                self, "structured_value", _freeze_json(self.structured_value)
            )


__all__ = ["PrimaryOutputCandidate"]
