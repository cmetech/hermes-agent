"""Contained workflow resources and deterministic variable substitution."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Callable, Iterable, Mapping

import yaml

from plugins.workflow.language_schema import (
    WorkflowReferenceSyntaxError,
    iter_output_references,
)
from plugins.workflow.bash_rendering import (
    RenderedBashCommand,
    bash_output_references,
    render_v3_bash,
)
from plugins.workflow.output_resolution import (
    ResolvedNodeOutput,
    ResolvedOutputReference,
    WorkflowOutputReferenceError,
    resolve_output_reference,
)


_COMMAND_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_AUTHORITY_DESCRIPTOR_KEY = "__hermes_authenticated_local_mcp"
_AUTHORITY_CWD_KEY = "__hermes_private_mcp_cwd"
_AUTHORITY_MAX_FILES = 512
_AUTHORITY_MAX_FILE_BYTES = 1 * 1024 * 1024
_AUTHORITY_MAX_TOTAL_BYTES = 8 * 1024 * 1024
_AUTHORITY_MANIFEST_NAME = ".hermes-authority-manifest-v1.json"
_AUTHORITY_CONTROL_DIRECTORY = "control"
_AUTHORITY_PAYLOAD_DIRECTORY = "payload"
_AUTHORITY_MAX_MANIFEST_BYTES = 4_000_000
logger = logging.getLogger(__name__)
_VARIABLE = re.compile(
    r"\$(?:(?P<position>[1-9][0-9]*)|"
    r"(?P<node>[A-Za-z_][A-Za-z0-9_-]*)\.output(?:\.(?P<dot>[A-Za-z0-9_.-]+))?|"
    r"(?P<name>[A-Z][A-Z0-9_]*))"
)
_SCALAR_VARIABLE = re.compile(
    r"\$(?:(?P<position>[1-9][0-9]*)|(?P<name>[A-Z][A-Z0-9_]*))"
)
_REFERENCE_NODE_CANDIDATE = re.compile(
    r"\$(?P<node>[A-Za-z_][A-Za-z0-9_-]*)"
)


def iter_output_field_references(
    template: str,
    *,
    normalizer_version: int = 2,
) -> Iterable[tuple[str, tuple[str, ...]]]:
    """Yield field references recognized by runtime variable substitution."""
    if normalizer_version == 3:
        for reference in iter_output_references(template, normalizer_version=3):
            if reference.path:
                yield reference.node_id, reference.path
        return
    for match in _VARIABLE.finditer(template):
        node = match.group("node")
        dot = match.group("dot")
        if node is not None and dot is not None:
            yield node, tuple(dot.split("."))


def _shell_quote_context(template: str, end: int) -> str | None:
    """Return the POSIX quote containing ``end``, ignoring escaped quotes."""
    quote: str | None = None
    escaped = False
    for character in template[:end]:
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote != "'":
            escaped = True
            continue
        if character == "'" and quote != '"':
            quote = None if quote == "'" else "'"
        elif character == '"' and quote != "'":
            quote = None if quote == '"' else '"'
    return quote


def _iter_shell_quote_contexts(
    template: str,
    ends: Iterable[int],
) -> Iterable[str | None]:
    """Yield quote contexts for ordered offsets with one template scan."""
    quote: str | None = None
    escaped = False
    position = 0
    for end in ends:
        if end < position:
            raise ValueError("shell quote offsets must be ordered")
        while position < end:
            character = template[position]
            position += 1
            if escaped:
                escaped = False
                continue
            if character == "\\" and quote != "'":
                escaped = True
                continue
            if character == "'" and quote != '"':
                quote = None if quote == "'" else "'"
            elif character == '"' and quote != "'":
                quote = None if quote == '"' else '"'
        yield quote


def _quote_shell_value(value: str, quote: str | None) -> str:
    if quote == "'":
        return value.replace("'", "'\\''")
    if quote == '"':
        return (
            value
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("$", "\\$")
            .replace("`", "\\`")
        )
    return shlex.quote(value)


def _render_json_value(value: object) -> str:
    def thaw(item: object) -> object:
        if isinstance(item, Mapping):
            return {str(key): thaw(child) for key, child in item.items()}
        if isinstance(item, tuple | list):
            return [thaw(child) for child in item]
        return item

    return json.dumps(thaw(value), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class CommandResource:
    path: Path
    body: str
    description: str | None = None
    argument_hint: str | None = None


@dataclass(frozen=True)
class ScriptResource:
    path: Path
    runtime: str
    authenticated_bytes: bytes | None = None


def parse_command_resource(path: Path, text: str) -> CommandResource:
    """Parse one already-authenticated UTF-8 command resource."""
    metadata: dict[str, object] = {}
    body = text
    if text.startswith("---\n"):
        header, separator, remainder = text[4:].partition("\n---\n")
        if not separator:
            raise ValueError(f"command frontmatter is not terminated: {path}")
        parsed = yaml.safe_load(header) or {}
        if not isinstance(parsed, dict):
            raise ValueError(f"command frontmatter must be a mapping: {path}")
        metadata = parsed
        body = remainder
    description = metadata.get("description")
    argument_hint = metadata.get("argument-hint")
    return CommandResource(
        path=path,
        body=body,
        description=str(description) if description is not None else None,
        argument_hint=str(argument_hint) if argument_hint is not None else None,
    )


class AuthenticatedExecutionMaterializer:
    """Private, disposable files whose consumers verify bytes before execution."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="hermes-workflow-authority-"))
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        self.control_root = self.root / _AUTHORITY_CONTROL_DIRECTORY
        self.payload_root = self.root / _AUTHORITY_PAYLOAD_DIRECTORY
        self.control_root.mkdir(mode=0o700)
        self.payload_root.mkdir(mode=0o700)
        self._closed = False
        self._entries: dict[str, tuple[str, Path]] = {}
        self._descriptor: dict[str, object] | None = None

    @staticmethod
    def _canonical_relative(relative: str) -> PurePosixPath:
        logical = PurePosixPath(relative)
        if (
            not relative
            or "\\" in relative
            or "\0" in relative
            or logical.is_absolute()
            or logical.as_posix() != relative
            or any(part in {"", ".", ".."} for part in logical.parts)
        ):
            raise ValueError("authenticated execution path must be canonical")
        return logical

    def materialize(self, relative: str, data: bytes) -> Path:
        if self._closed:
            raise RuntimeError("authenticated execution materializer is closed")
        if self._descriptor is not None:
            raise RuntimeError("authenticated execution materializer is finalized")
        logical = self._canonical_relative(relative)
        if len(data) > _AUTHORITY_MAX_FILE_BYTES:
            raise ValueError("authenticated execution file exceeds 1048576 bytes")
        digest = hashlib.sha256(data).hexdigest()
        previous = self._entries.get(relative)
        if previous is not None:
            if previous[0] != digest:
                raise ValueError("authenticated execution path has conflicting bytes")
            return previous[1]
        if len(self._entries) >= _AUTHORITY_MAX_FILES:
            raise ValueError("authenticated execution closure exceeds 512 files")
        if (
            sum(path.stat().st_size for _, path in self._entries.values()) + len(data)
            > _AUTHORITY_MAX_TOTAL_BYTES
        ):
            raise ValueError("authenticated execution closure exceeds 8388608 bytes")
        path = self.payload_root.joinpath(*logical.parts)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o400)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        self._entries[relative] = (digest, path)
        return path

    def materialize_all(self, contents: Mapping[str, bytes]) -> dict[str, object]:
        if self._descriptor is not None:
            expected = {
                relative: hashlib.sha256(bytes(data)).hexdigest()
                for relative, data in contents.items()
            }
            observed = {
                relative: digest for relative, (digest, _path) in self._entries.items()
            }
            if expected != observed:
                raise ValueError(
                    "authenticated execution closure has conflicting bytes"
                )
            return dict(self._descriptor)
        files: dict[str, dict[str, object]] = {}
        for relative in sorted(contents):
            data = bytes(contents[relative])
            self.materialize(relative, data)
            files[relative] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        nonce = secrets.token_hex(32)
        manifest = {
            "version": 1,
            "nonce": nonce,
            "files": files,
        }
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(encoded) > _AUTHORITY_MAX_MANIFEST_BYTES:
            raise ValueError("authenticated execution manifest is too large")
        manifest_path = self.control_root / _AUTHORITY_MANIFEST_NAME
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(manifest_path, flags, 0o400)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            manifest_path.unlink(missing_ok=True)
            raise
        root_stat = self.root.lstat()
        self._descriptor = {
            "version": 2,
            "root": str(self.root),
            "payload": _AUTHORITY_PAYLOAD_DIRECTORY,
            "manifest": (
                f"{_AUTHORITY_CONTROL_DIRECTORY}/{_AUTHORITY_MANIFEST_NAME}"
            ),
            "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
            "file_count": len(files),
            "total_bytes": sum(int(metadata["size"]) for metadata in files.values()),
            "nonce": nonce,
            "root_identity": {
                "device": root_stat.st_dev,
                "inode": root_stat.st_ino,
            },
        }
        return dict(self._descriptor)

    def cleanup(self) -> None:
        if self._closed:
            return
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        failure: OSError | None = None
        for attempt in range(3):
            try:
                shutil.rmtree(self.root)
                self._closed = True
                return
            except FileNotFoundError:
                self._closed = True
                return
            except OSError as exc:
                failure = exc
                logger.warning(
                    "authenticated execution cleanup attempt %d failed: %s",
                    attempt + 1,
                    exc,
                )
                if attempt < 2:
                    time.sleep(0.01 * (attempt + 1))
        raise OSError("authenticated execution authority cleanup failed") from failure


class ResourceResolver:
    """Resolve package resources without permitting path traversal."""

    def __init__(
        self,
        package_root: str | Path,
        *,
        global_root: str | Path | None = None,
        sealed_paths: Iterable[str] | None = None,
        sealed_bytes: Mapping[str, bytes] | None = None,
    ):
        raw_root = Path(package_root).expanduser()
        self.package_root = Path(os.path.abspath(raw_root))
        self.global_root = (
            Path(os.path.abspath(Path(global_root).expanduser()))
            if global_root is not None
            else None
        )
        self.sealed_paths = (
            frozenset(sealed_paths) if sealed_paths is not None else None
        )
        self.sealed_bytes = (
            {str(path): bytes(data) for path, data in sealed_bytes.items()}
            if sealed_bytes is not None
            else None
        )
        if (
            self.sealed_bytes is not None
            and self.sealed_paths is not None
            and frozenset(self.sealed_bytes) != self.sealed_paths
        ):
            raise ValueError("authenticated resource bytes must match sealed paths")
        if self.sealed_bytes is not None:
            for relative, data in self.sealed_bytes.items():
                logical = PurePosixPath(relative)
                if (
                    not relative
                    or "\\" in relative
                    or "\0" in relative
                    or logical.is_absolute()
                    or logical.as_posix() != relative
                    or any(part in {"", ".", ".."} for part in logical.parts)
                    or not isinstance(data, bytes)
                ):
                    raise ValueError("authenticated resource path or bytes are invalid")

    def _is_sealed(self, path: Path) -> bool:
        if self.sealed_paths is None:
            return True
        try:
            relative = path.relative_to(self.package_root).as_posix()
        except ValueError:
            return False
        return relative in self.sealed_paths

    def _relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.package_root).as_posix()
        except ValueError as exc:
            raise ValueError("resource escapes authenticated package") from exc

    def _authenticated_bytes(self, path: Path) -> bytes:
        """Return scheduler-authenticated bytes or read a live admitted source."""
        relative = self._relative(path)
        if self.sealed_bytes is None:
            return path.read_bytes()
        expected = self.sealed_bytes.get(relative)
        if expected is None:
            raise ValueError(f"resource is not authenticated: {relative}")
        return expected

    def read_bytes(self, relative: str) -> bytes:
        """Load one contained resource through the scheduler's byte authority."""
        normalized = relative.replace("\\", "/")
        logical = PurePosixPath(normalized)
        if (
            not relative
            or logical.is_absolute()
            or logical.as_posix() != normalized
            or any(part in {"", ".", ".."} for part in logical.parts)
        ):
            raise ValueError("resource must be a contained relative path")
        candidate = self.package_root / normalized
        if self.sealed_bytes is not None:
            try:
                return self.sealed_bytes[normalized]
            except KeyError as exc:
                raise FileNotFoundError(f"resource is missing: {relative}") from exc
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self.package_root)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise FileNotFoundError(f"resource is missing: {relative}") from exc
        if (
            candidate.is_symlink()
            or not resolved.is_file()
            or not self._is_sealed(resolved)
        ):
            raise FileNotFoundError(f"resource is missing: {relative}")
        return self._authenticated_bytes(resolved)

    def text(self, relative: str) -> str:
        """Load one contained UTF-8 resource through the byte authority."""
        return self.read_bytes(relative).decode("utf-8")

    def command(self, name: str) -> CommandResource:
        if not isinstance(name, str) or not _COMMAND_NAME.fullmatch(name):
            raise ValueError("command must be a contained command name")
        filename = name if name.endswith(".md") else f"{name}.md"
        if self.sealed_bytes is not None:
            relative = f"commands/{filename}"
            try:
                encoded = self.sealed_bytes[relative]
            except KeyError as exc:
                raise FileNotFoundError(f"command resource is missing: {name}") from exc
            return self._parse_command(
                self.package_root / relative,
                text=encoded.decode("utf-8"),
            )
        roots = [self.package_root]
        if self.global_root is not None:
            roots.append(self.global_root)
        for root in roots:
            candidate = root / "commands" / filename
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(root)
            except (FileNotFoundError, OSError, ValueError):
                continue
            if (
                candidate.is_symlink()
                or not resolved.is_file()
                or not self._is_sealed(resolved)
            ):
                continue
            return self._parse_command(
                resolved,
                text=self._authenticated_bytes(resolved).decode("utf-8"),
            )
        raise FileNotFoundError(f"command resource is missing: {name}")

    def script(self, name: str, *, runtime: str) -> ScriptResource:
        if runtime not in {"bun", "uv"}:
            raise ValueError("runtime must be bun or uv")
        normalized = name.replace("\\", "/")
        relative = PurePosixPath(normalized)
        if (
            not name
            or relative.is_absolute()
            or ".." in relative.parts
            or normalized.startswith("~")
        ):
            raise ValueError("script must be a contained script name")
        suffix = relative.suffix.lower()
        if runtime == "uv" and suffix and suffix != ".py":
            raise ValueError("uv requires a Python script")
        if runtime == "bun" and suffix and suffix not in {".js", ".ts"}:
            raise ValueError("bun requires a JavaScript or TypeScript script")
        if suffix:
            names = (normalized,)
        elif runtime == "uv":
            names = (normalized, f"{normalized}.py")
        else:
            names = (normalized, f"{normalized}.ts", f"{normalized}.js")
        if self.sealed_bytes is not None:
            for candidate_name in names:
                relative_name = f"scripts/{candidate_name}"
                authenticated = self.sealed_bytes.get(relative_name)
                if authenticated is not None:
                    return ScriptResource(
                        path=self.package_root / relative_name,
                        runtime=runtime,
                        authenticated_bytes=authenticated,
                    )
            raise FileNotFoundError(f"script resource is missing: {name}")
        roots = [self.package_root]
        if self.global_root is not None:
            roots.append(self.global_root)
        for root in roots:
            for candidate_name in names:
                candidate = root / "scripts" / candidate_name
                try:
                    resolved = candidate.resolve(strict=True)
                    resolved.relative_to(root / "scripts")
                except (FileNotFoundError, OSError, ValueError):
                    continue
                if (
                    candidate.is_symlink()
                    or not resolved.is_file()
                    or not self._is_sealed(resolved)
                ):
                    continue
                authenticated = self._authenticated_bytes(resolved)
                return ScriptResource(
                    path=resolved,
                    runtime=runtime,
                    authenticated_bytes=(
                        authenticated if self.sealed_bytes is not None else None
                    ),
                )
        raise FileNotFoundError(f"script resource is missing: {name}")

    def mcp_servers(
        self,
        reference: str,
        *,
        materializer: AuthenticatedExecutionMaterializer | None = None,
    ) -> dict[str, dict[str, object]]:
        """Load one contained, snapshotted MCP definition without interpolation."""
        normalized = reference.replace("\\", "/")
        relative = PurePosixPath(normalized)
        if (
            not reference
            or relative.is_absolute()
            or ".." in relative.parts
            or normalized.startswith("~")
        ):
            raise ValueError("MCP definition must be a contained relative resource")
        candidates = (
            self.package_root / normalized,
            self.package_root / "mcp" / normalized,
            (self.package_root / "mcp" / normalized).with_suffix(".yaml"),
        )
        path = None
        if self.sealed_bytes is not None:
            for candidate in candidates:
                if self._relative(candidate) in self.sealed_bytes:
                    path = candidate
                    break
        else:
            for candidate in candidates:
                try:
                    resolved = candidate.resolve(strict=True)
                    resolved.relative_to(self.package_root)
                except (FileNotFoundError, OSError, ValueError):
                    continue
                if (
                    not candidate.is_symlink()
                    and resolved.is_file()
                    and self._is_sealed(resolved)
                ):
                    path = resolved
                    break
        if path is None:
            raise FileNotFoundError(f"MCP definition is missing: {reference}")
        encoded = self._authenticated_bytes(path)
        if len(encoded) > 256_000:
            raise ValueError("MCP definition exceeds 256000 bytes")
        document = yaml.safe_load(encoded.decode("utf-8")) or {}
        if not isinstance(document, dict):
            raise ValueError("MCP definition must be a mapping")
        raw_servers = document.get("mcp_servers", document)
        if "command" in document or "url" in document:
            raw_servers = {path.stem: document}
        if not isinstance(raw_servers, dict) or not raw_servers:
            raise ValueError("MCP definition must contain at least one server")
        servers: dict[str, dict[str, object]] = {}
        for name, raw in raw_servers.items():
            if not isinstance(name, str) or not _COMMAND_NAME.fullmatch(name):
                raise ValueError(f"invalid MCP server name: {name}")
            if not isinstance(raw, dict):
                raise ValueError(f"MCP server {name} must be a mapping")
            servers[name] = dict(raw)
        if self.sealed_bytes is not None:
            if materializer is None:
                raise ValueError(
                    "authenticated MCP resources require private execution materialization"
                )
            closure = materializer.materialize_all(self.sealed_bytes)
            for server in servers.values():
                server[_AUTHORITY_DESCRIPTOR_KEY] = {
                    **closure,
                    "source_root": str(self.package_root),
                }
        return servers

    @staticmethod
    def _parse_command(path: Path, *, text: str | None = None) -> CommandResource:
        if text is None:
            text = path.read_text(encoding="utf-8")
        return parse_command_resource(path, text)


@dataclass(frozen=True)
class VariableContext:
    """Approved prompt variables; environment values are intentionally absent."""

    arguments: str = ""
    user_message: str = ""
    artifacts_dir: Path | None = None
    workflow_id: str = ""
    base_branch: str = ""
    docs_dir: Path | None = None
    context: str = ""
    loop_user_input: str = ""
    loop_prev_output: str = ""
    rejection_reason: str = ""
    node_outputs: Mapping[
        str, str | ResolvedNodeOutput | WorkflowOutputReferenceError
    ] = field(default_factory=dict)
    normalizer_version: int = 2

    def output_reference(
        self, node_id: str, path: tuple[str, ...] = ()
    ) -> ResolvedOutputReference:
        """Resolve one Archon v3 output through the canonical strict resolver."""
        raw = self.node_outputs.get(node_id)
        if isinstance(raw, WorkflowOutputReferenceError):
            raise WorkflowOutputReferenceError(raw.code, node_id, tuple(path))
        return resolve_output_reference(
            raw if isinstance(raw, ResolvedNodeOutput) else None,
            node_id=node_id,
            path=path,
        )

    def _value(self, match: re.Match[str]) -> str | None:
        position = match.group("position")
        if position is not None:
            try:
                values = shlex.split(self.arguments)
            except ValueError:
                values = self.arguments.split()
            index = int(position) - 1
            return values[index] if index < len(values) else ""
        node = match.group("node")
        if node is not None:
            dot = match.group("dot")
            if self.normalizer_version == 3:
                return self.output_reference(
                    node,
                    tuple(dot.split(".")) if dot else (),
                ).rendered_text
            raw = self.node_outputs.get(node)
            if raw is None:
                return ""
            if not dot:
                return raw.text if isinstance(raw, ResolvedNodeOutput) else raw
            try:
                # Phase 2 keeps the legacy string adapter below. Archon values
                # arrive already parsed so prompt and shell consumers never
                # independently reinterpret provider text.
                value: object = (
                    raw.value
                    if isinstance(raw, ResolvedNodeOutput)
                    else json.loads(raw)
                )
                for part in dot.split("."):
                    if isinstance(value, Mapping):
                        value = value[part]
                    elif isinstance(value, tuple | list) and part.isdigit():
                        value = value[int(part)]
                    else:
                        return ""
            except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                return ""
            if isinstance(value, str):
                return value
            if value is None:
                return "null"
            if isinstance(value, bool):
                return "true" if value else "false"
            return _render_json_value(value)
        values = {
            "ARGUMENTS": self.arguments,
            "USER_MESSAGE": self.user_message,
            "ARTIFACTS_DIR": str(self.artifacts_dir) if self.artifacts_dir else "",
            "WORKFLOW_ID": self.workflow_id,
            "BASE_BRANCH": self.base_branch,
            "DOCS_DIR": str(self.docs_dir) if self.docs_dir else "",
            "CONTEXT": self.context,
            "LOOP_USER_INPUT": self.loop_user_input,
            "LOOP_PREV_OUTPUT": self.loop_prev_output,
            "REJECTION_REASON": self.rejection_reason,
        }
        return values.get(match.group("name"))

    def environment(self) -> dict[str, str]:
        """Return the non-secret variable environment for named scripts."""
        return {
            "ARGUMENTS": self.arguments,
            "USER_MESSAGE": self.user_message,
            "ARTIFACTS_DIR": str(self.artifacts_dir) if self.artifacts_dir else "",
            "WORKFLOW_ID": self.workflow_id,
            "BASE_BRANCH": self.base_branch,
            "DOCS_DIR": str(self.docs_dir) if self.docs_dir else "",
            "CONTEXT": self.context,
            "LOOP_USER_INPUT": self.loop_user_input,
            "LOOP_PREV_OUTPUT": self.loop_prev_output,
            "REJECTION_REASON": self.rejection_reason,
        }

    def render_prompt(self, template: str) -> str:
        def replace(match: re.Match[str]) -> str:
            value = self._value(match)
            return match.group(0) if value is None else value

        return _VARIABLE.sub(replace, template)

    def render_bash(
        self,
        template: str,
        *,
        spill_directory: str | Path,
        max_inline_chars: int = 8192,
    ) -> str:
        if max_inline_chars <= 0:
            raise ValueError("max_inline_chars must be positive")
        root = Path(spill_directory).resolve()
        root.mkdir(parents=True, exist_ok=True)

        def replace(match: re.Match[str]) -> str:
            value = self._value(match)
            if value is None:
                return match.group(0)
            if len(value) > max_inline_chars:
                digest = hashlib.sha256(value.encode()).hexdigest()[:16]
                path = root / f"variable-{digest}.txt"
                path.write_text(value, encoding="utf-8")
                value = str(path)
            return _quote_shell_value(
                value, _shell_quote_context(template, match.start())
            )

        return _VARIABLE.sub(replace, template)


@dataclass(frozen=True)
class StrictSubstitutionRenderer:
    """Dependency-scoped Archon v3 rendering over immutable output facets."""

    variables: VariableContext
    direct_dependencies: frozenset[str]
    output_resolver: Callable[
        [str, tuple[str, ...]], ResolvedOutputReference
    ] | None = None

    def _output(self, node_id: str, path: tuple[str, ...]) -> str:
        if node_id not in self.direct_dependencies:
            raise WorkflowOutputReferenceError(
                "output_reference_not_declared_dependency",
                node_id,
                path,
            )
        resolver = self.output_resolver or self.variables.output_reference
        return resolver(node_id, path).rendered_text

    @staticmethod
    def _references(template: str, *, bash_contexts: bool = False):
        try:
            if bash_contexts:
                return bash_output_references(template)
            return tuple(iter_output_references(template, normalizer_version=3))
        except WorkflowReferenceSyntaxError as exc:
            candidate = (
                _REFERENCE_NODE_CANDIDATE.match(template, exc.start)
                if exc.start is not None
                else None
            )
            raise WorkflowOutputReferenceError(
                exc.code,
                candidate.group("node") if candidate is not None else "invalid",
            ) from exc

    def resolve_outputs(
        self, *templates: str
    ) -> Mapping[tuple[str, tuple[str, ...]], ResolvedOutputReference]:
        """Resolve each canonical output facet once without rendering text."""
        resolved: dict[
            tuple[str, tuple[str, ...]], ResolvedOutputReference
        ] = {}
        resolver = self.output_resolver or self.variables.output_reference
        for template in templates:
            for reference in self._references(template):
                key = (reference.node_id, reference.path)
                if key in resolved:
                    continue
                if reference.node_id not in self.direct_dependencies:
                    raise WorkflowOutputReferenceError(
                        "output_reference_not_declared_dependency",
                        reference.node_id,
                        reference.path,
                    )
                resolved[key] = resolver(reference.node_id, reference.path)
        return MappingProxyType(resolved)

    def _scalar(
        self,
        match: re.Match[str],
        *,
        positional_values: list[str] | None,
    ) -> str | None:
        position = match.group("position")
        if position is not None:
            index = int(position) - 1
            if positional_values is None:
                raise ValueError("positional values must be parsed before rendering")
            return (
                positional_values[index]
                if index < len(positional_values)
                else ""
            )
        values = {
            "ARGUMENTS": self.variables.arguments,
            "USER_MESSAGE": self.variables.user_message,
            "ARTIFACTS_DIR": (
                str(self.variables.artifacts_dir)
                if self.variables.artifacts_dir
                else ""
            ),
            "WORKFLOW_ID": self.variables.workflow_id,
            "BASE_BRANCH": self.variables.base_branch,
            "DOCS_DIR": str(self.variables.docs_dir) if self.variables.docs_dir else "",
            "CONTEXT": self.variables.context,
            "LOOP_USER_INPUT": self.variables.loop_user_input,
            "LOOP_PREV_OUTPUT": self.variables.loop_prev_output,
            "REJECTION_REASON": self.variables.rejection_reason,
        }
        return values.get(match.group("name"))

    def _substitutions(
        self,
        template: str,
        *,
        include_scalar_variables: bool,
        classify_bash_contexts: bool = False,
    ) -> tuple[tuple[int, int, str], ...]:
        references = self._references(
            template,
            bash_contexts=classify_bash_contexts,
        )
        substitutions = [
            (
                reference.start,
                reference.end,
                self._output(reference.node_id, reference.path),
            )
            for reference in references
        ]
        if include_scalar_variables:
            reference_cursor = 0
            positional_values: list[str] | None = None
            for match in _SCALAR_VARIABLE.finditer(template):
                while (
                    reference_cursor < len(references)
                    and references[reference_cursor].end <= match.start()
                ):
                    reference_cursor += 1
                if reference_cursor < len(references) and (
                    match.start() < references[reference_cursor].end
                    and match.end() > references[reference_cursor].start
                ):
                    continue
                if match.group("position") is not None and positional_values is None:
                    try:
                        positional_values = shlex.split(self.variables.arguments)
                    except ValueError:
                        positional_values = self.variables.arguments.split()
                value = self._scalar(
                    match,
                    positional_values=positional_values,
                )
                if value is not None:
                    substitutions.append((match.start(), match.end(), value))
        substitutions.sort(key=lambda item: item[0])
        return tuple(substitutions)

    @staticmethod
    def _replace(
        template: str,
        substitutions: Iterable[tuple[int, int, str]],
    ) -> str:
        rendered: list[str] = []
        position = 0
        for start, end, value in substitutions:
            rendered.extend((template[position:start], value))
            position = end
        rendered.append(template[position:])
        return "".join(rendered)

    def render_outputs(self, template: str) -> str:
        """Resolve only canonical output tokens, leaving dynamic variables intact."""
        return self._replace(
            template,
            self._substitutions(template, include_scalar_variables=False),
        )

    def render_prompt(self, template: str) -> str:
        """Render only the requested initial body; replacements are not rescanned."""
        return self._replace(
            template,
            self._substitutions(template, include_scalar_variables=True),
        )

    def render_bash(
        self,
        template: str,
        *,
        spill_directory: str | Path,
        max_inline_chars: int = 8192,
        secure_v3: bool = False,
    ) -> str | RenderedBashCommand:
        """Keep the existing loop Bash materialization with strict references."""
        if max_inline_chars <= 0:
            raise ValueError("max_inline_chars must be positive")
        substitutions = self._substitutions(
            template,
            include_scalar_variables=True,
            classify_bash_contexts=secure_v3,
        )
        if secure_v3:
            return render_v3_bash(
                template,
                substitutions,
                spill_directory=spill_directory,
            )
        root = Path(spill_directory).resolve()
        root.mkdir(parents=True, exist_ok=True)
        rendered: list[str] = []
        position = 0
        quote_contexts = _iter_shell_quote_contexts(
            template,
            (start for start, _, _ in substitutions),
        )
        for (start, end, raw_value), quote_context in zip(
            substitutions,
            quote_contexts,
            strict=True,
        ):
            rendered.append(template[position:start])
            value = raw_value
            if len(value) > max_inline_chars:
                digest = hashlib.sha256(value.encode()).hexdigest()[:16]
                path = root / f"variable-{digest}.txt"
                path.write_text(value, encoding="utf-8")
                value = str(path)
            rendered.append(
                _quote_shell_value(value, quote_context)
            )
            position = end
        rendered.append(template[position:])
        return "".join(rendered)


def substitution_renderer(
    variables: VariableContext,
    *,
    direct_dependencies: Iterable[str],
    output_resolver: Callable[
        [str, tuple[str, ...]], ResolvedOutputReference
    ] | None = None,
) -> VariableContext | StrictSubstitutionRenderer:
    """Select strict v3 rendering without changing legacy substitution."""
    if variables.normalizer_version != 3:
        return variables
    return StrictSubstitutionRenderer(
        variables=variables,
        direct_dependencies=frozenset(direct_dependencies),
        output_resolver=output_resolver,
    )


__all__ = [
    "CommandResource",
    "ResourceResolver",
    "ScriptResource",
    "StrictSubstitutionRenderer",
    "VariableContext",
    "iter_output_field_references",
    "substitution_renderer",
]
