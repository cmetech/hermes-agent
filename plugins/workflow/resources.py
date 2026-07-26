"""Contained workflow resources and deterministic variable substitution."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shlex
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping

import yaml


_COMMAND_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_VARIABLE = re.compile(
    r"\$(?:(?P<position>[1-9][0-9]*)|"
    r"(?P<node>[A-Za-z_][A-Za-z0-9_-]*)\.output(?:\.(?P<dot>[A-Za-z0-9_.-]+))?|"
    r"(?P<name>[A-Z][A-Z0-9_]*))"
)


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


def _quote_shell_value(value: str, quote: str | None) -> str:
    if quote == "'":
        return value.replace("'", "'\\''")
    if quote == '"':
        return (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("$", "\\$")
            .replace("`", "\\`")
        )
    return shlex.quote(value)


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


class AuthenticatedExecutionMaterializer:
    """Private, disposable files whose consumers verify bytes before execution."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="hermes-workflow-authority-"))
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        self._closed = False

    def materialize(self, relative: str, data: bytes) -> Path:
        suffix = PurePosixPath(relative).suffix
        name = hashlib.sha256(relative.encode("utf-8") + b"\0" + data).hexdigest()
        path = self.root / f"{name}{suffix}"
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
        return path

    def cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        shutil.rmtree(self.root, ignore_errors=True)


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
        self.package_root = Path(package_root).resolve()
        self.global_root = (
            Path(global_root).resolve() if global_root is not None else None
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
        """Read a resource once and rebind it to the scheduler's byte authority."""
        relative = self._relative(path)
        if self.sealed_bytes is None:
            return path.read_bytes()
        expected = self.sealed_bytes.get(relative)
        if expected is None:
            raise ValueError(f"resource is not authenticated: {relative}")
        try:
            before = path.stat()
            if path.is_symlink() or not path.is_file():
                raise OSError("resource is not a regular file")
            actual = path.read_bytes()
            after = path.stat()
        except OSError as exc:
            raise ValueError(f"authenticated resource is unreadable: {relative}") from exc
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
            or len(actual) != before.st_size
            or not hmac.compare_digest(actual, expected)
        ):
            raise ValueError(f"resource changed after authentication: {relative}")
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
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self.package_root)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise FileNotFoundError(f"resource is missing: {relative}") from exc
        if candidate.is_symlink() or not resolved.is_file() or not self._is_sealed(resolved):
            raise FileNotFoundError(f"resource is missing: {relative}")
        return self._authenticated_bytes(resolved)

    def text(self, relative: str) -> str:
        """Load one contained UTF-8 resource through the byte authority."""
        return self.read_bytes(relative).decode("utf-8")

    def command(self, name: str) -> CommandResource:
        if not isinstance(name, str) or not _COMMAND_NAME.fullmatch(name):
            raise ValueError("command must be a contained command name")
        filename = name if name.endswith(".md") else f"{name}.md"
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
            local_references: dict[str, tuple[Path, bytes]] = {}
            for value in self._walk_strings(document):
                candidate = self.package_root / value
                try:
                    relative = candidate.relative_to(self.package_root).as_posix()
                except ValueError:
                    continue
                if relative in self.sealed_bytes:
                    local_references[value] = (
                        candidate,
                        self._authenticated_bytes(candidate),
                    )
            if local_references:
                if materializer is None:
                    raise ValueError(
                        "authenticated local MCP resources require private execution materialization"
                    )
                consumed: set[str] = set()
                for name, server in servers.items():
                    command = server.get("command")
                    args = server.get("args", [])
                    if not isinstance(command, str) or not isinstance(args, list):
                        continue
                    referenced = [
                        (index, value, local_references[value])
                        for index, value in enumerate(args)
                        if isinstance(value, str) and value in local_references
                    ]
                    if not referenced:
                        continue
                    executable = Path(command).name.lower()
                    python_command = executable in {
                        "python", "python3", "python.exe", "python3.exe"
                    }
                    if len(referenced) != 1 or not python_command:
                        raise ValueError(
                            f"MCP server {name} uses an unsupported authenticated local "
                            "executable; use a Python stdio server or an installed command"
                        )
                    index, _value, (source, source_bytes) = referenced[0]
                    consumed.add(_value)
                    if source.suffix.lower() != ".py":
                        raise ValueError(
                            f"MCP server {name} authenticated local executable must be Python"
                        )
                    relative = source.relative_to(self.package_root).as_posix()
                    materialized = materializer.materialize(relative, source_bytes)
                    digest = hashlib.sha256(source_bytes).hexdigest()
                    loader = (
                        "import hashlib,sys;"
                        "p,d,n=sys.argv[1:4];b=open(p,'rb').read();"
                        "hashlib.sha256(b).hexdigest()==d or sys.exit(86);"
                        "sys.argv=[n,*sys.argv[4:]];"
                        "g={'__name__':'__main__','__file__':n};"
                        "exec(compile(b,n,'exec'),g,g)"
                    )
                    server["args"] = [
                        *args[:index],
                        "-c",
                        loader,
                        str(materialized),
                        digest,
                        relative,
                        *args[index + 1 :],
                    ]
                # Every local reference must either have been replaced above or
                # fail closed: generic MCP consumers reopen path arguments later.
                if set(local_references) != consumed:
                    raise ValueError(
                        "authenticated local MCP resource cannot be passed through by path"
                    )
        return servers

    @staticmethod
    def _walk_strings(value: object) -> Iterable[str]:
        if isinstance(value, str):
            yield value
        elif isinstance(value, Mapping):
            for item in value.values():
                yield from ResourceResolver._walk_strings(item)
        elif isinstance(value, list | tuple):
            for item in value:
                yield from ResourceResolver._walk_strings(item)

    @staticmethod
    def _parse_command(path: Path, *, text: str | None = None) -> CommandResource:
        if text is None:
            text = path.read_text(encoding="utf-8")
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
    node_outputs: Mapping[str, str] = field(default_factory=dict)

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
            raw = self.node_outputs.get(node)
            if raw is None:
                return ""
            dot = match.group("dot")
            if not dot:
                return raw
            try:
                value: object = json.loads(raw)
                for part in dot.split("."):
                    if isinstance(value, dict):
                        value = value[part]
                    elif isinstance(value, list) and part.isdigit():
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
            return json.dumps(value, sort_keys=True, separators=(",", ":"))
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


__all__ = [
    "CommandResource",
    "ResourceResolver",
    "ScriptResource",
    "VariableContext",
]
