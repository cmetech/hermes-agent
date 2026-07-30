"""Digest-bound profile trust and redacted execution risk preflight."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Literal, Mapping

import yaml

from plugins.workflow.compat import ARCHON_TOOL_ALIASES, CompatibilityReport
from plugins.workflow.models import (
    ValidationIssue,
    WorkflowPackage,
    WorkflowValidationError,
)
from plugins.workflow.schema import is_inline_script

_LOCK_TIMEOUT_SECONDS = 5.0
_ISOLATION_CAPABILITIES = (
    "package_containment",
    "process_isolation",
    "workdir_containment",
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
WORKFLOW_RESOURCE_MAX_FILE_BYTES = 1024 * 1024
WORKFLOW_RESOURCE_MAX_TOTAL_BYTES = 8 * 1024 * 1024
WORKFLOW_RESOURCE_MAX_FILES = 512


class WorkflowTrustError(RuntimeError):
    pass


class WorkflowResourceCapacityError(WorkflowTrustError):
    """A bounded package-resource read budget was exhausted."""


class WorkflowResourceCacheMissError(WorkflowTrustError):
    """A sealed package-resource cache did not contain a requested path."""


@dataclass(slots=True)
class WorkflowResourceReadBudget:
    max_file_bytes: int
    max_total_bytes: int
    max_files: int
    bytes_read: int = 0
    files_read: int = 0
    _contents: dict[Path, bytes] = field(default_factory=dict, init=False, repr=False)
    _identities: dict[Path, tuple[int, int, int, int]] = field(
        default_factory=dict, init=False, repr=False
    )
    _aliases: dict[Path, Path] = field(default_factory=dict, init=False, repr=False)
    _sealed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        for name in ("max_file_bytes", "max_total_bytes", "max_files"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

    def read(self, path: Path, *, verify_cached_identity: bool = False) -> bytes:
        if self._sealed:
            return self.read_cached(path)
        canonical = self._logical_key(path)
        cached = self._contents.get(canonical)
        if cached is not None:
            identity = self._identities.get(canonical)
            if verify_cached_identity and identity is not None:
                current = path.stat()
                if path.is_symlink() or not path.is_file() or identity != (
                    current.st_dev,
                    current.st_ino,
                    current.st_size,
                    current.st_mtime_ns,
                ):
                    raise OSError("package resource changed after shared read")
            return cached
        if self.files_read >= self.max_files:
            raise WorkflowResourceCapacityError("package resource file limit exceeded")
        before = path.stat()
        if path.is_symlink() or not path.is_file():
            raise OSError("package resource is not a regular file")
        if before.st_size > self.max_file_bytes:
            raise WorkflowResourceCapacityError(
                "package resource per-file limit exceeded"
            )
        remaining = self.max_total_bytes - self.bytes_read
        if before.st_size > remaining:
            raise WorkflowResourceCapacityError("package resource byte limit exceeded")
        with path.open("rb") as stream:
            data = stream.read(before.st_size + 1)
        after = path.stat()
        if (
            (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            )
            or len(data) != before.st_size
        ):
            raise OSError("package resource changed during read")
        if len(data) > self.max_file_bytes or len(data) > remaining:
            raise WorkflowResourceCapacityError("package resource byte limit exceeded")
        self.files_read += 1
        self.bytes_read += len(data)
        self._contents[canonical] = data
        self._identities[canonical] = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        return data

    @staticmethod
    def _logical_key(path: Path) -> Path:
        return Path(os.path.abspath(path))

    def remember_alias(self, logical_path: Path, canonical_path: Path) -> None:
        if self._sealed:
            if self._aliases.get(self._logical_key(logical_path)) == canonical_path:
                return
            raise WorkflowResourceCacheMissError(
                "package resource cache aliases are sealed"
            )
        self._aliases[self._logical_key(logical_path)] = canonical_path

    def seal(self) -> None:
        self._sealed = True

    def read_cached(self, logical_path: Path) -> bytes:
        key = self._logical_key(logical_path)
        canonical = self._aliases.get(key, key)
        try:
            return self._contents[canonical]
        except KeyError as exc:
            raise WorkflowResourceCacheMissError(
                "sealed package resource is unavailable"
            ) from exc

    def has_cached(self, logical_path: Path) -> bool:
        key = self._logical_key(logical_path)
        canonical = self._aliases.get(key, key)
        return canonical in self._contents

    @classmethod
    def from_authenticated(
        cls,
        root: Path,
        contents: Mapping[str, bytes],
    ) -> WorkflowResourceReadBudget:
        """Build a sealed read authority from already authenticated package bytes."""
        total = sum(len(data) for data in contents.values())
        authority = cls(
            max_file_bytes=max((len(data) for data in contents.values()), default=1),
            max_total_bytes=max(total, 1),
            max_files=max(len(contents), 1),
        )
        canonical_root = root.resolve(strict=True)
        for relative, data in contents.items():
            logical_relative = PurePosixPath(relative)
            if (
                not relative
                or "\\" in relative
                or "\0" in relative
                or logical_relative.is_absolute()
                or logical_relative.as_posix() != relative
                or any(part in {"", ".", ".."} for part in logical_relative.parts)
                or not isinstance(data, bytes)
            ):
                raise ValueError("authenticated resource path or bytes are invalid")
            logical = canonical_root / relative
            canonical = authority._logical_key(logical)
            authority._contents[canonical] = bytes(data)
            authority._aliases[canonical] = canonical
        authority.bytes_read = total
        authority.files_read = len(contents)
        authority.seal()
        return authority


@dataclass(frozen=True)
class WorkflowPackageDigest:
    sha256: str
    covered_relative_paths: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionEnvironmentRequirement:
    mode: Literal["trusted_local", "isolated_backend_required"]
    required_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowRiskSummary:
    package_digest: str
    risk_digest: str
    shell_or_script_nodes: tuple[str, ...]
    requested_tools: tuple[str, ...]
    requested_skills: tuple[str, ...]
    local_mcp_servers: tuple[str, ...]
    providers: tuple[str, ...]
    outward_action_nodes: tuple[str, ...]
    required_secret_names: tuple[str, ...]
    execution_environment: Literal["trusted_local", "isolated_backend_required"]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _validation_error(path: str, code: str, message: str) -> WorkflowValidationError:
    return WorkflowValidationError(
        ValidationIssue(path=path, code=code, message=message)
    )


def _contained_resource(
    root: Path,
    path: Path,
    *,
    read_budget: WorkflowResourceReadBudget | None = None,
) -> tuple[str, bytes]:
    if read_budget is not None and read_budget.has_cached(path):
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise _validation_error(
                str(path),
                "resource_escape",
                f"workflow resource escapes package root: {path}",
            ) from exc
        return relative, read_budget.read(path)
    try:
        if path.is_symlink():
            raise _validation_error(
                str(path),
                "resource_symlink",
                f"workflow resource symlink may escape package root: {path}",
            )
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except WorkflowValidationError:
        raise
    except (OSError, ValueError) as exc:
        raise _validation_error(
            str(path),
            "resource_escape",
            f"workflow resource escape or unreadable resource: {path}: {exc}",
        ) from exc
    if not resolved.is_file():
        raise _validation_error(
            str(path),
            "resource_not_file",
            f"workflow resource is not a readable file: {path}",
        )
    try:
        if read_budget is None:
            data = resolved.read_bytes()
        else:
            data = read_budget.read(resolved)
            read_budget.remember_alias(path, resolved)
    except OSError as exc:
        raise _validation_error(
            str(path),
            "resource_unreadable",
            f"workflow resource is unreadable: {path}: {exc}",
        ) from exc
    return resolved.relative_to(root).as_posix(), data


def _named_script_path(
    package: WorkflowPackage,
    name: str,
    runtime: str,
    read_budget: WorkflowResourceReadBudget | None = None,
) -> Path:
    extension = ".py" if runtime == "uv" else ".ts"
    base = package.root / "scripts" / name
    candidates = (
        (base, base.with_suffix(extension), base.with_suffix(".js"))
        if runtime == "bun"
        else (base, base.with_suffix(extension))
    )
    for candidate in candidates:
        if (read_budget is not None and read_budget.has_cached(candidate)) or (
            candidate.exists() or candidate.is_symlink()
        ):
            return candidate
    raise _validation_error(
        name, "missing_script", f"named script resource is missing: {name}"
    )


def _command_path(
    package: WorkflowPackage,
    name: str,
    read_budget: WorkflowResourceReadBudget | None = None,
) -> Path:
    base = package.root / "commands" / name
    candidates = (base, base.with_suffix(".md"))
    for candidate in candidates:
        if (read_budget is not None and read_budget.has_cached(candidate)) or (
            candidate.exists() or candidate.is_symlink()
        ):
            return candidate
    raise _validation_error(
        name, "missing_command", f"command resource is missing: {name}"
    )


def _mcp_path(
    package: WorkflowPackage,
    reference: str,
    read_budget: WorkflowResourceReadBudget | None = None,
) -> Path:
    direct = package.root / reference
    candidates = (
        direct,
        package.root / "mcp" / reference,
        (package.root / "mcp" / reference).with_suffix(".yaml"),
    )
    for candidate in candidates:
        if (read_budget is not None and read_budget.has_cached(candidate)) or (
            candidate.exists() or candidate.is_symlink()
        ):
            return candidate
    raise _validation_error(
        reference, "missing_mcp", f"MCP definition is missing: {reference}"
    )


def _walk_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _walk_strings(item)


def compute_package_digest(
    package: WorkflowPackage,
    *,
    read_budget: WorkflowResourceReadBudget | None = None,
) -> WorkflowPackageDigest:
    """Hash the portable document and all executable package resources."""
    root = package.root.resolve(strict=True)
    resources: dict[str, bytes] = {}

    def add(path: Path) -> tuple[str, bytes]:
        relative, data = _contained_resource(root, path, read_budget=read_budget)
        resources[relative] = data
        return relative, data

    add(package.workflow_path)
    expected_sidecar = package.workflow_path.with_name(
        f"{package.workflow_path.stem}.hermes.yaml"
    )
    if package.sidecar_path is not None or expected_sidecar.exists():
        add(package.sidecar_path or expected_sidecar)
    for node in package.definition.nodes:
        if node.node_type == "command":
            add(_command_path(package, str(node.value), read_budget))
        elif node.node_type == "script" and isinstance(node.value, str):
            if not is_inline_script(node.value):
                add(
                    _named_script_path(
                        package,
                        node.value,
                        str(node.options["runtime"]),
                        read_budget,
                    )
                )
        mcp_value = node.options.get("mcp")
        references = (
            (mcp_value,)
            if isinstance(mcp_value, str)
            else tuple(mcp_value or ())
            if isinstance(mcp_value, tuple)
            else ()
        )
        for reference in references:
            if not isinstance(reference, str):
                continue
            _, mcp_bytes = add(_mcp_path(package, reference, read_budget))
            try:
                mcp_document = yaml.safe_load(mcp_bytes) or {}
            except yaml.YAMLError as exc:
                raise _validation_error(
                    reference,
                    "invalid_mcp",
                    f"invalid MCP definition: {reference}: {exc}",
                ) from exc
            for raw_candidate in _walk_strings(mcp_document):
                candidates = [raw_candidate]
                if raw_candidate.startswith("-") and "=" in raw_candidate:
                    candidates.append(raw_candidate.split("=", 1)[1])
                for candidate in candidates:
                    if not candidate or candidate.startswith(("$", "-")):
                        continue
                    resource = package.root / candidate
                    if (
                        read_budget is not None
                        and read_budget.has_cached(resource)
                    ) or resource.exists() or resource.is_symlink():
                        add(resource)

    digest = hashlib.sha256()
    for relative in sorted(resources):
        data = resources[relative]
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return WorkflowPackageDigest(digest.hexdigest(), tuple(sorted(resources)))


def _tuple_strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple | list):
        return ()
    return tuple(
        sorted({str(item) for item in value if isinstance(item, str) and item})
    )


def build_risk_summary(
    package: WorkflowPackage,
    compatibility: CompatibilityReport,
    *,
    read_budget: WorkflowResourceReadBudget | None = None,
) -> WorkflowRiskSummary:
    package_digest = compute_package_digest(package, read_budget=read_budget).sha256
    shell_nodes = tuple(
        node.id
        for node in package.definition.nodes
        if node.node_type in {"bash", "script"}
    )
    requested_tools = tuple(
        sorted({
            ARCHON_TOOL_ALIASES.get(str(tool), str(tool))
            for node in package.definition.nodes
            for field in ("allowed_tools", "denied_tools")
            for tool in node.options.get(field, ())
        })
    )
    requested_skills = tuple(
        sorted({
            str(skill)
            for node in package.definition.nodes
            for skill in node.options.get("skills", ())
        })
    )
    local_mcp = tuple(
        sorted({
            str(reference)
            for node in package.definition.nodes
            for reference in (
                (node.options.get("mcp"),)
                if isinstance(node.options.get("mcp"), str)
                else node.options.get("mcp", ())
            )
            if isinstance(reference, str)
        })
    )
    workflow_provider = package.definition.options.get("provider")
    providers = tuple(
        sorted({
            str(provider)
            for provider in [
                workflow_provider,
                *(node.options.get("provider") for node in package.definition.nodes),
            ]
            if isinstance(provider, str) and provider
        })
    )
    outward = _tuple_strings(package.sidecar.get("outward_action_nodes"))
    secrets = _tuple_strings(package.sidecar.get("required_secrets"))
    environment_value = package.sidecar.get("execution_environment", "trusted_local")
    environment: Literal["trusted_local", "isolated_backend_required"] = (
        "trusted_local"
        if environment_value == "trusted_local"
        else "isolated_backend_required"
    )
    risk_fields = {
        "package_digest": package_digest,
        "shell_or_script_nodes": shell_nodes,
        "requested_tools": requested_tools,
        "requested_skills": requested_skills,
        "local_mcp_servers": local_mcp,
        "providers": providers,
        "outward_action_nodes": outward,
        "required_secret_names": secrets,
        "execution_environment": environment,
        "compatibility": compatibility.level.value,
        "blocking_findings": tuple(
            finding.path for finding in compatibility.blocking_findings
        ),
    }
    risk_digest = hashlib.sha256(
        json.dumps(risk_fields, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return WorkflowRiskSummary(
        risk_digest=risk_digest,
        **{
            key: value
            for key, value in risk_fields.items()
            if key not in {"compatibility", "blocking_findings"}
        },
    )


def preflight_execution(
    summary: WorkflowRiskSummary,
    *,
    trusted: bool,
    backend_capabilities: Iterable[str] = (),
) -> ExecutionEnvironmentRequirement:
    if trusted and summary.execution_environment == "trusted_local":
        return ExecutionEnvironmentRequirement("trusted_local", ())
    available = frozenset(backend_capabilities)
    missing = tuple(
        capability
        for capability in _ISOLATION_CAPABILITIES
        if capability not in available
    )
    if missing:
        raise WorkflowTrustError(
            "workflow requires a configured isolated backend with: "
            + ", ".join(missing)
        )
    return ExecutionEnvironmentRequirement(
        "isolated_backend_required", _ISOLATION_CAPABILITIES
    )


@contextmanager
def _locked(path: Path, timeout: float = _LOCK_TIMEOUT_SECONDS):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, 0o600)
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        os.lseek(descriptor, 0, os.SEEK_SET)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                if os.name == "nt":  # pragma: no cover - exercised on Windows CI
                    import msvcrt

                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise WorkflowTrustError(
                        "timed out acquiring workflow trust-store lock"
                    )
                time.sleep(0.025)
        yield
    finally:
        try:
            if os.name == "nt":  # pragma: no cover
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


class WorkflowTrustStore:
    """Profile-owned digest trust with atomic cross-process updates."""

    def __init__(self, hermes_home: str | Path):
        self.path = Path(hermes_home).expanduser() / "workflow" / "trust.json"
        self.lock_path = self.path.with_suffix(".lock")

    def _read(
        self, *, mutation: bool, max_bytes: int | None = None
    ) -> dict[str, object]:
        if not self.path.exists():
            return {"version": 1, "records": {}}
        if self.path.is_symlink():
            if mutation:
                raise WorkflowTrustError(
                    "workflow trust store is corrupt: symlink not allowed"
                )
            return {"version": 1, "records": {}}
        try:
            if max_bytes is None:
                text = self.path.read_text(encoding="utf-8")
            else:
                if (
                    not isinstance(max_bytes, int)
                    or isinstance(max_bytes, bool)
                    or max_bytes <= 0
                ):
                    raise ValueError("trust-store read limit must be positive")
                with self.path.open("rb") as stream:
                    encoded = stream.read(max_bytes + 1)
                if len(encoded) > max_bytes:
                    raise ValueError("trust store exceeds the read limit")
                text = encoded.decode("utf-8")
            payload = json.loads(text)
            if (
                not isinstance(payload, dict)
                or payload.get("version") != 1
                or not isinstance(payload.get("records"), dict)
            ):
                raise ValueError("unsupported trust-store shape")
            return payload
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            if mutation:
                raise WorkflowTrustError(
                    f"workflow trust store is corrupt: {exc}"
                ) from exc
            return {"version": 1, "records": {}}

    def _write(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(self.path.parent, 0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".trust-", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            data = (
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise WorkflowTrustError(
                        "failed to write workflow trust store atomically"
                    )
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
            if hasattr(os, "O_DIRECTORY"):
                directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

    def trust(self, package_digest: str, *, actor: str, risk_digest: str) -> None:
        if not _SHA256.fullmatch(package_digest) or not _SHA256.fullmatch(risk_digest):
            raise WorkflowTrustError(
                "package and risk digests must be SHA-256 hex values"
            )
        with _locked(self.lock_path):
            payload = self._read(mutation=True)
            records = payload["records"]
            assert isinstance(records, dict)
            records[package_digest] = {
                "actor": str(actor)[:128],
                "risk_digest": risk_digest,
                "trusted_at": datetime.now(timezone.utc).isoformat(),
            }
            self._write(payload)

    def revoke(self, package_digest: str) -> bool:
        with _locked(self.lock_path):
            payload = self._read(mutation=True)
            records = payload["records"]
            assert isinstance(records, dict)
            existed = package_digest in records
            if existed:
                del records[package_digest]
                self._write(payload)
            return existed

    def check(
        self, package_digest: str, *, risk_digest: str | None = None
    ) -> Literal["trusted", "untrusted"]:
        with _locked(self.lock_path):
            return self._classify(
                self._read(mutation=False), package_digest, risk_digest=risk_digest
            )

    def snapshot_read_only(self, *, max_bytes: int) -> dict[str, object]:
        """Load one bounded trust snapshot without locks or filesystem writes."""
        return self._read(mutation=False, max_bytes=max_bytes)

    def check_snapshot(
        self,
        snapshot: Mapping[str, object],
        package_digest: str,
        *,
        risk_digest: str | None = None,
    ) -> Literal["trusted", "untrusted"]:
        """Classify one digest from a previously loaded read-only snapshot."""
        return self._classify(snapshot, package_digest, risk_digest=risk_digest)

    def check_read_only(
        self, package_digest: str, *, risk_digest: str | None = None
    ) -> Literal["trusted", "untrusted"]:
        """Classify trust without creating locks, directories, or store files."""
        return self._classify(
            self._read(mutation=False), package_digest, risk_digest=risk_digest
        )

    @staticmethod
    def _classify(
        payload: Mapping[str, object],
        package_digest: str,
        *,
        risk_digest: str | None,
    ) -> Literal["trusted", "untrusted"]:
        records = payload.get("records")
        if not isinstance(records, dict):
            return "untrusted"
        record = records.get(package_digest)
        if not isinstance(record, dict):
            return "untrusted"
        if risk_digest is not None and record.get("risk_digest") != risk_digest:
            return "untrusted"
        return "trusted"
