"""Immutable executor output identities for Archon workflow consumers."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Callable, Mapping

from plugins.workflow.language_schema import DURABLE_METADATA_STRING_MAX_CHARS


_HAS_DESCRIPTOR_RELATIVE_IO = (
    os.open in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and hasattr(os, "O_NOFOLLOW")
)
PRIMARY_OUTPUT_CANDIDATE_METADATA_KEY = "primary_output_candidate"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RETRYABLE_READ_ERRNOS = frozenset({
    errno.EAGAIN,
    errno.EINTR,
    errno.EIO,
    errno.EMFILE,
    errno.ENOMEM,
    errno.ENFILE,
    getattr(errno, "ESTALE", -1),
})
_PRIMARY_OUTPUT_CANDIDATE_FIELDS = frozenset({
    "attempt_relative_path",
    "media_type",
    "size_bytes",
    "sha256",
    "schema_fingerprint",
    "canonicalization_version",
    "output_type",
})
_OUTPUT_PUBLICATION_IDENTITY_FIELDS = frozenset({
    "node_id",
    "attempt_id",
    "publication_id",
    "sha256",
    "size_bytes",
    "media_type",
    "schema_fingerprint",
    "canonicalization_version",
    "output_type",
})


class ArchonOutputIntegrityError(RuntimeError):
    """An attempt-local Archon output could not be created safely."""


class ArchonOutputUnavailableError(RuntimeError):
    """An attempt-local output could not be read due to transient host I/O."""


class WorkflowOutputReferenceError(ArchonOutputIntegrityError):
    """Bounded typed failure while resolving one v3 output reference."""

    def __init__(
        self,
        code: str,
        node_id: str,
        path: tuple[str, ...] = (),
        *,
        producer_identity: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.node_id = node_id
        self.path = tuple(path)
        self.producer_identity = (
            MappingProxyType(canonical_output_publication_identity(producer_identity))
            if producer_identity is not None
            else None
        )
        super().__init__(f"{code}: output reference for {node_id}")


@dataclass(frozen=True, slots=True)
class ResolvedOutputReference:
    """The typed and deterministic text facets of one output reference."""

    typed_value: object
    rendered_text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "typed_value", _freeze_json(self.typed_value))


def canonical_output_publication_identity(
    value: Mapping[str, object],
) -> dict[str, object]:
    """Validate the bounded producer identity fenced by a resolution wait."""
    if set(value) != _OUTPUT_PUBLICATION_IDENTITY_FIELDS:
        raise ValueError("output publication identity fields are invalid")
    node_id = value.get("node_id")
    attempt_id = value.get("attempt_id")
    publication_id = value.get("publication_id")
    digest = value.get("sha256")
    size_bytes = value.get("size_bytes")
    media_type = value.get("media_type")
    schema_fingerprint = value.get("schema_fingerprint")
    canonicalization_version = value.get("canonicalization_version")
    output_type = value.get("output_type")
    if (
        not isinstance(node_id, str)
        or not node_id
        or len(node_id.encode("utf-8")) > 256
        or not isinstance(attempt_id, str)
        or not attempt_id
        or len(attempt_id.encode("utf-8")) > 256
        or (
            publication_id is not None
            and (
                not isinstance(publication_id, str)
                or re.fullmatch(r"[0-9a-f]{32}", publication_id) is None
            )
        )
        or not isinstance(digest, str)
        or _SHA256.fullmatch(digest) is None
        or isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or not 0 <= size_bytes <= 500_000
        or not isinstance(media_type, str)
        or not media_type
        or len(media_type.encode("utf-8")) > 256
        or (
            schema_fingerprint is not None
            and (
                not isinstance(schema_fingerprint, str)
                or _SHA256.fullmatch(schema_fingerprint) is None
            )
        )
        or isinstance(canonicalization_version, bool)
        or canonicalization_version != 1
        or not isinstance(output_type, str)
        or not output_type.strip()
        or len(output_type.encode("utf-8")) > DURABLE_METADATA_STRING_MAX_CHARS
    ):
        raise ValueError("output publication identity is invalid")
    identity = {
        field: value[field]
        for field in sorted(_OUTPUT_PUBLICATION_IDENTITY_FIELDS)
    }
    if len(
        json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ) > 2_048:
        raise ValueError("output publication identity is too large")
    return identity


def output_publication_identity_sha256(value: Mapping[str, object]) -> str:
    """Return the stable private identity digest used in bounded events."""
    identity = canonical_output_publication_identity(value)
    return hashlib.sha256(
        json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _canonical_relative_path(value: object) -> PurePosixPath | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or "\0" in value
        or "\\" in value
    ):
        return None
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or ".." in relative.parts
        or relative.as_posix() != value
    ):
        return None
    return relative


def _safe_component(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{kind}-{digest}"


def _directory_flags() -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return flags


def _open_directory(parent: int, name: str, *, create: bool) -> int:
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
    descriptor = os.open(name, _directory_flags(), dir_fd=parent)
    try:
        observed = os.fstat(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    if not stat.S_ISDIR(observed.st_mode):
        os.close(descriptor)
        raise ArchonOutputIntegrityError("Archon output directory is not regular")
    return descriptor


def _write_descriptor_relative(
    run_directory: Path,
    node_component: str,
    attempt_component: str,
    filename: str,
    data: bytes,
) -> Path:
    descriptors: list[int] = []
    output_descriptor: int | None = None
    remove_output = False
    try:
        root = os.open(run_directory, _directory_flags())
        descriptors.append(root)
        nodes = _open_directory(root, "nodes", create=True)
        descriptors.append(nodes)
        node = _open_directory(nodes, node_component, create=True)
        descriptors.append(node)
        try:
            os.mkdir(attempt_component, mode=0o700, dir_fd=node)
        except FileExistsError as exc:
            raise ArchonOutputIntegrityError(
                "Archon output attempt already exists"
            ) from exc
        attempt = _open_directory(node, attempt_component, create=False)
        descriptors.append(attempt)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        output_descriptor = os.open(filename, flags, 0o600, dir_fd=attempt)
        remove_output = True
        observed = os.fstat(output_descriptor)
        if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
            raise ArchonOutputIntegrityError("Archon output target is not regular")
        view = memoryview(data)
        while view:
            written = os.write(output_descriptor, view)
            if written <= 0:
                raise OSError("short Archon output write")
            view = view[written:]
        os.fsync(output_descriptor)
        os.close(output_descriptor)
        output_descriptor = None
        remove_output = False
    except ArchonOutputIntegrityError:
        raise
    except (OSError, ValueError) as exc:
        raise ArchonOutputIntegrityError("Archon output creation failed") from exc
    finally:
        if output_descriptor is not None:
            try:
                os.close(output_descriptor)
            except OSError:
                pass
        if remove_output and descriptors:
            try:
                os.unlink(filename, dir_fd=descriptors[-1])
            except OSError:
                pass
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
    return run_directory / "nodes" / node_component / attempt_component / filename


def write_archon_output_exclusive(
    run_directory: Path,
    *,
    node_id: str,
    attempt_id: str,
    filename: str,
    data: bytes,
) -> Path:
    """Create one contained output without following attacker-controlled links."""
    if filename not in {"output.json", "output.md"}:
        raise ArchonOutputIntegrityError("Archon output filename is invalid")
    # Path checks cannot make a later pathname-based create race-free. Hosts
    # without handle-relative creation must fail before touching the tree.
    if not _HAS_DESCRIPTOR_RELATIVE_IO:
        raise ArchonOutputIntegrityError(
            "Secure descriptor-relative Archon output creation is unavailable"
        )
    node_component = _safe_component("node", node_id)
    attempt_component = _safe_component("attempt", attempt_id)
    return _write_descriptor_relative(
        run_directory,
        node_component,
        attempt_component,
        filename,
        data,
    )


def _read_descriptor_relative(
    run_directory: Path,
    relative_path: str,
    *,
    size_bytes: int,
) -> bytes:
    """Read one bounded file through a no-follow descriptor chain."""
    relative = _canonical_relative_path(relative_path)
    if relative is None:
        raise ArchonOutputIntegrityError("Archon output path is invalid")
    if not _HAS_DESCRIPTOR_RELATIVE_IO:
        raise ArchonOutputIntegrityError(
            "Secure descriptor-relative Archon output reading is unavailable"
        )
    descriptors: list[int] = []
    output_descriptor: int | None = None
    try:
        root = os.open(run_directory, _directory_flags())
        descriptors.append(root)
        parent = root
        for component in relative.parts[:-1]:
            parent = _open_directory(parent, component, create=False)
            descriptors.append(parent)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
        output_descriptor = os.open(relative.parts[-1], flags, dir_fd=parent)
        observed = os.fstat(output_descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or observed.st_size != size_bytes
        ):
            raise ArchonOutputIntegrityError("Archon output file identity changed")
        remaining = size_bytes + 1
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(output_descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    except ArchonOutputIntegrityError:
        raise
    except ValueError as exc:
        raise ArchonOutputIntegrityError("Archon output path is invalid") from exc
    except OSError as exc:
        if exc.errno in _RETRYABLE_READ_ERRNOS:
            raise ArchonOutputUnavailableError(
                "Archon output is temporarily unavailable"
            ) from exc
        raise ArchonOutputIntegrityError(
            "Archon output path is not regular"
        ) from exc
    finally:
        if output_descriptor is not None:
            try:
                os.close(output_descriptor)
            except OSError:
                pass
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _freeze_json(value: object) -> object:
    if _is_frozen_json(value):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze_json(item) for key, item in value.items()
        })
    if isinstance(value, tuple | list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _normalize_candidate_json(
    value: object,
    active: set[int] | None = None,
    owned_mappings: dict[int, object] | None = None,
) -> object:
    """Copy candidate containers into compact storage owned by the candidate."""
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if math.isfinite(value):
            return value
        raise ArchonOutputIntegrityError(
            "Archon output candidate structured value is invalid"
        )
    if not isinstance(value, Mapping | tuple | list):
        raise ArchonOutputIntegrityError(
            "Archon output candidate structured value is invalid"
        )
    if active is None:
        active = set()
    if owned_mappings is None:
        owned_mappings = {}
    identity = id(value)
    if identity in active:
        raise ArchonOutputIntegrityError(
            "Archon output candidate structured value is invalid"
        )
    if isinstance(value, Mapping) and identity in owned_mappings:
        return owned_mappings[identity]
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            owned: dict[str, object] = {}
            proxy = MappingProxyType(owned)
            owned_mappings[identity] = proxy
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ArchonOutputIntegrityError(
                        "Archon output candidate structured value is invalid"
                    )
                owned[str(key)] = _normalize_candidate_json(
                    item, active, owned_mappings
                )
            return proxy
        return tuple(
            _normalize_candidate_json(item, active, owned_mappings)
            for item in value
        )
    finally:
        active.remove(identity)


def _is_frozen_json(value: object) -> bool:
    if type(value) is MappingProxyType:
        return all(
            isinstance(key, str) and _is_frozen_json(item)
            for key, item in value.items()
        )
    if isinstance(value, tuple):
        return all(_is_frozen_json(item) for item in value)
    return not isinstance(value, Mapping | list)


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
            try:
                normalized = _normalize_candidate_json(self.structured_value)
            except ArchonOutputIntegrityError:
                raise
            except (RecursionError, RuntimeError, TypeError, ValueError) as exc:
                raise ArchonOutputIntegrityError(
                    "Archon output candidate structured value is invalid"
                ) from exc
            object.__setattr__(self, "structured_value", normalized)


def primary_output_candidate_identity(
    candidate: PrimaryOutputCandidate,
) -> dict[str, object]:
    """Return the bounded, body-free identity safe to retain at completion."""
    return {
        "attempt_relative_path": candidate.attempt_relative_path,
        "media_type": candidate.media_type,
        "size_bytes": candidate.size_bytes,
        "sha256": candidate.sha256,
        "schema_fingerprint": candidate.schema_fingerprint,
        "canonicalization_version": candidate.canonicalization_version,
        "output_type": candidate.output_type,
    }


def primary_output_candidate_from_identity(
    value: object,
    *,
    structured_value: object | None = None,
) -> PrimaryOutputCandidate:
    """Validate and restore a retained winning-candidate identity."""
    if not isinstance(value, Mapping) or set(value) != _PRIMARY_OUTPUT_CANDIDATE_FIELDS:
        raise ArchonOutputIntegrityError("Archon output candidate identity is invalid")
    relative_path = value["attempt_relative_path"]
    media_type = value["media_type"]
    size_bytes = value["size_bytes"]
    digest = value["sha256"]
    schema_fingerprint = value["schema_fingerprint"]
    canonicalization_version = value["canonicalization_version"]
    output_type = value["output_type"]
    relative = _canonical_relative_path(relative_path)
    if (
        relative is None
        or not isinstance(media_type, str)
        or not media_type
        or len(media_type) > 128
        or isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or not 0 <= size_bytes <= 500_000
        or not isinstance(digest, str)
        or _SHA256.fullmatch(digest) is None
        or (
            schema_fingerprint is not None
            and (
                not isinstance(schema_fingerprint, str)
                or _SHA256.fullmatch(schema_fingerprint) is None
            )
        )
        or isinstance(canonicalization_version, bool)
        or not isinstance(canonicalization_version, int)
        or canonicalization_version != 1
        or (
            output_type is not None
            and (
                not isinstance(output_type, str)
                or not output_type.strip()
                or len(output_type) > DURABLE_METADATA_STRING_MAX_CHARS
            )
        )
    ):
        raise ArchonOutputIntegrityError("Archon output candidate identity is invalid")
    return PrimaryOutputCandidate(
        attempt_relative_path=relative_path,
        media_type=media_type,
        size_bytes=size_bytes,
        sha256=digest,
        structured_value=structured_value,
        schema_fingerprint=schema_fingerprint,
        canonicalization_version=canonicalization_version,
        output_type=output_type,
    )


@dataclass(frozen=True, slots=True)
class ResolvedNodeOutput:
    """One immutable downstream view of a winning node output."""

    canonical_bytes: bytes
    value: object
    text: str
    media_type: str
    sha256: str
    node_id: str
    attempt_id: str
    publication_id: str | None
    schema_fingerprint: str | None = None
    canonicalization_version: int = 1
    output_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _freeze_json(self.value))


def output_publication_identity(
    *,
    node_id: str,
    attempt_id: str,
    descriptor: Mapping[str, object],
    candidate: PrimaryOutputCandidate | None,
) -> dict[str, object]:
    """Build the private wait fence from the winning descriptor authority."""
    schema_fingerprint = (
        candidate.schema_fingerprint
        if candidate is not None
        else descriptor.get("schema_fingerprint")
    )
    output_type = (
        candidate.output_type
        if candidate is not None and candidate.output_type is not None
        else descriptor.get("output_type")
    )
    return canonical_output_publication_identity({
        "node_id": node_id,
        "attempt_id": attempt_id,
        "publication_id": descriptor.get("publication_id"),
        "sha256": descriptor.get("sha256"),
        "size_bytes": descriptor.get("size_bytes"),
        "media_type": descriptor.get("media_type"),
        "schema_fingerprint": schema_fingerprint,
        "canonicalization_version": (
            candidate.canonicalization_version
            if candidate is not None
            else descriptor.get("canonicalization_version", 1)
        ),
        "output_type": (
            output_type
            if isinstance(output_type, str) and output_type
            else "structured"
            if schema_fingerprint is not None
            else "text"
        ),
    })


def resolved_output_publication_identity(
    output: ResolvedNodeOutput,
) -> dict[str, object]:
    """Recover the same wait fence after a successful immutable read."""
    return canonical_output_publication_identity({
        "node_id": output.node_id,
        "attempt_id": output.attempt_id,
        "publication_id": output.publication_id,
        "sha256": output.sha256,
        "size_bytes": len(output.canonical_bytes),
        "media_type": output.media_type,
        "schema_fingerprint": output.schema_fingerprint,
        "canonicalization_version": output.canonicalization_version,
        "output_type": (
            output.output_type
            if isinstance(output.output_type, str) and output.output_type
            else "structured"
            if output.schema_fingerprint is not None
            else "text"
        ),
    })


def _render_reference_value(value: object) -> str:
    if isinstance(value, str):
        return value

    def thaw(item: object) -> object:
        if isinstance(item, Mapping):
            return {str(key): thaw(child) for key, child in item.items()}
        if isinstance(item, tuple | list):
            return [thaw(child) for child in item]
        return item

    try:
        return json.dumps(
            thaw(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ArchonOutputIntegrityError(
            "Archon structured output is not canonical JSON"
        ) from exc


def resolve_output_reference(
    output: ResolvedNodeOutput | None,
    *,
    node_id: str,
    path: tuple[str, ...] = (),
) -> ResolvedOutputReference:
    """Resolve one v3 reference without reparsing schemaless provider text."""
    if output is None:
        raise WorkflowOutputReferenceError(
            "output_reference_missing", node_id, tuple(path)
        )
    if output.node_id != node_id:
        raise WorkflowOutputReferenceError(
            "output_reference_integrity", node_id, tuple(path)
        )
    if path and output.schema_fingerprint is None:
        raise WorkflowOutputReferenceError(
            "output_reference_not_structured", node_id, tuple(path)
        )

    value = output.value
    for segment in path:
        if isinstance(value, Mapping):
            if segment not in value:
                raise WorkflowOutputReferenceError(
                    "output_reference_field_missing", node_id, tuple(path)
                )
            value = value[segment]
            continue
        if isinstance(value, tuple | list):
            if not re.fullmatch(r"0|[1-9][0-9]*", segment):
                raise WorkflowOutputReferenceError(
                    "output_reference_path_type", node_id, tuple(path)
                )
            index = int(segment)
            if index >= len(value):
                raise WorkflowOutputReferenceError(
                    "output_reference_field_missing", node_id, tuple(path)
                )
            value = value[index]
            continue
        raise WorkflowOutputReferenceError(
            "output_reference_path_type", node_id, tuple(path)
        )

    try:
        rendered = (
            output.text
            if output.schema_fingerprint is None and not path
            else _render_reference_value(value)
        )
    except ArchonOutputIntegrityError as exc:
        raise WorkflowOutputReferenceError(
            "output_reference_integrity", node_id, tuple(path)
        ) from exc
    return ResolvedOutputReference(value, rendered)


def _resolve_node_output(
    *,
    run_directory: Path,
    node_id: str,
    attempt_id: str,
    descriptor: Mapping[str, object],
    candidate: PrimaryOutputCandidate | None = None,
    publication_id: str | None = None,
    strict: bool = False,
) -> ResolvedNodeOutput:
    """Resolve one descriptor without consulting raw provider response text."""
    relative_path = descriptor.get("relative_path")
    media_type = descriptor.get("media_type")
    size_bytes = descriptor.get("size_bytes")
    digest = descriptor.get("sha256")
    relative = _canonical_relative_path(relative_path)
    if (
        descriptor.get("node_id") != node_id
        or descriptor.get("attempt_id") != attempt_id
        or relative is None
        or not isinstance(media_type, str)
        or isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes < 0
        or size_bytes > 500_000
        or not isinstance(digest, str)
        or len(digest) != 64
    ):
        raise ArchonOutputIntegrityError("Archon output descriptor is invalid")
    descriptor_publication_id = descriptor.get("publication_id")
    if (
        descriptor_publication_id is not None
        and descriptor_publication_id != publication_id
    ):
        raise ArchonOutputIntegrityError("Archon output publication identity changed")
    read_relative_path = relative_path
    if strict and publication_id is not None:
        descriptor_schema = descriptor.get("schema_fingerprint")
        descriptor_version = descriptor.get("canonicalization_version")
        descriptor_output_type = descriptor.get("output_type")
        if (
            re.fullmatch(r"[0-9a-f]{32}", publication_id) is None
            or not {
                "publication_id",
                "content_name",
                "schema_fingerprint",
                "canonicalization_version",
                "output_type",
            }.issubset(descriptor)
            or descriptor_publication_id != publication_id
            or (
                descriptor_schema is not None
                and (
                    not isinstance(descriptor_schema, str)
                    or _SHA256.fullmatch(descriptor_schema) is None
                )
            )
            or (candidate is None and descriptor_schema is not None)
            or isinstance(descriptor_version, bool)
            or descriptor_version != 1
            or not isinstance(descriptor_output_type, str)
            or not descriptor_output_type.strip()
            or len(descriptor_output_type) > DURABLE_METADATA_STRING_MAX_CHARS
        ):
            raise ArchonOutputIntegrityError(
                "Archon output publication descriptor is incomplete"
            )
        content_name = descriptor.get("content_name")
        expected_content_name = (
            "content.json"
            if media_type == "application/json"
            else "content.md"
            if media_type == "text/markdown; charset=utf-8"
            else None
        )
        if (
            expected_content_name is None
            or content_name != expected_content_name
        ):
            raise ArchonOutputIntegrityError(
                "Archon output publication descriptor is invalid"
            )
        read_relative_path = f"publications/{publication_id}/{expected_content_name}"
    if candidate is not None:
        publication_descriptor = strict and publication_id is not None
        if (
            candidate.attempt_relative_path != relative_path
            or candidate.media_type != media_type
            or candidate.size_bytes != size_bytes
            or candidate.sha256 != digest
            or (
                (publication_descriptor or "schema_fingerprint" in descriptor)
                and descriptor.get("schema_fingerprint")
                != candidate.schema_fingerprint
            )
            or (
                (publication_descriptor or "canonicalization_version" in descriptor)
                and descriptor.get("canonicalization_version")
                != candidate.canonicalization_version
            )
            or (
                (publication_descriptor or "output_type" in descriptor)
                and descriptor.get("output_type") != candidate.output_type
            )
        ):
            raise ArchonOutputIntegrityError(
                "Archon output candidate and descriptor disagree"
            )
    canonical_bytes = _read_descriptor_relative(
        run_directory,
        read_relative_path,
        size_bytes=size_bytes,
    )
    if (
        len(canonical_bytes) != size_bytes
        or hashlib.sha256(canonical_bytes).hexdigest() != digest
    ):
        raise ArchonOutputIntegrityError("Archon output digest does not match")
    try:
        text = canonical_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise ArchonOutputIntegrityError("Archon output is not UTF-8") from exc
    schema_fingerprint = candidate.schema_fingerprint if candidate is not None else None
    canonicalization_version = (
        candidate.canonicalization_version if candidate is not None else 1
    )
    if schema_fingerprint is not None and strict:
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ArchonOutputIntegrityError("Archon JSON output is invalid") from exc
        if media_type != "application/json":
            raise ArchonOutputIntegrityError(
                "Archon structured output media type is invalid"
            )
        if candidate is not None and candidate.structured_value is not None:
            if _render_reference_value(candidate.structured_value) != _render_reference_value(value):
                raise ArchonOutputIntegrityError(
                    "Archon structured output candidate content disagrees"
                )
    elif strict:
        value = text
    elif candidate is not None and candidate.structured_value is not None:
        value = candidate.structured_value
    else:
        try:
            # Phase 2 retains the JSON-looking text adapter when no publication
            # identity says whether this output was structured.
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            if media_type == "application/json":
                raise ArchonOutputIntegrityError(
                    "Archon JSON output is invalid"
                ) from exc
            value = text
    return ResolvedNodeOutput(
        canonical_bytes=canonical_bytes,
        value=value,
        text=text,
        media_type=media_type,
        sha256=digest,
        node_id=node_id,
        attempt_id=attempt_id,
        publication_id=publication_id,
        schema_fingerprint=schema_fingerprint,
        canonicalization_version=canonicalization_version,
        output_type=(
            candidate.output_type
            if candidate is not None
            else descriptor.get("output_type")
            if isinstance(descriptor.get("output_type"), str)
            else None
        ),
    )


def resolve_node_output(
    *,
    run_directory: Path,
    node_id: str,
    attempt_id: str,
    descriptor: Mapping[str, object],
    candidate: PrimaryOutputCandidate | None = None,
    publication_id: str | None = None,
    strict: bool = False,
) -> ResolvedNodeOutput:
    """Resolve one winning output, optionally mapping integrity to the v3 code."""
    try:
        return _resolve_node_output(
            run_directory=run_directory,
            node_id=node_id,
            attempt_id=attempt_id,
            descriptor=descriptor,
            candidate=candidate,
            publication_id=publication_id,
            strict=strict,
        )
    except ArchonOutputIntegrityError as exc:
        if strict:
            raise WorkflowOutputReferenceError(
                "output_reference_integrity", node_id
            ) from exc
        raise


def resolve_legacy_output_values(
    projection: Mapping[str, object],
    run_directory: Path,
    *,
    read_text: Callable[[Path], str],
) -> dict[str, object]:
    """Run the frozen Hermes artifact scan and text/JSON parsing behavior."""
    outputs: dict[str, object] = {}
    for artifact in projection.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        relative = str(artifact.get("relative_path", ""))
        if not Path(relative).name.startswith(("output.", "stdout.")):
            continue
        node_id = str(artifact.get("node_id", ""))
        try:
            text = read_text(run_directory / relative)
            try:
                outputs[node_id] = json.loads(text)
            except json.JSONDecodeError:
                outputs[node_id] = text
        except (OSError, UnicodeError, ValueError):
            continue
    return outputs


__all__ = [
    "ArchonOutputIntegrityError",
    "ArchonOutputUnavailableError",
    "PRIMARY_OUTPUT_CANDIDATE_METADATA_KEY",
    "PrimaryOutputCandidate",
    "ResolvedOutputReference",
    "ResolvedNodeOutput",
    "WorkflowOutputReferenceError",
    "canonical_output_publication_identity",
    "output_publication_identity_sha256",
    "output_publication_identity",
    "resolved_output_publication_identity",
    "primary_output_candidate_from_identity",
    "primary_output_candidate_identity",
    "resolve_legacy_output_values",
    "resolve_node_output",
    "resolve_output_reference",
    "write_archon_output_exclusive",
]
