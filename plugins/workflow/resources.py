"""Contained workflow resources and deterministic variable substitution."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Mapping

import yaml


_COMMAND_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_VARIABLE = re.compile(
    r"\$(?:(?P<position>[1-9][0-9]*)|"
    r"(?P<node>[A-Za-z_][A-Za-z0-9_-]*)\.output(?:\.(?P<dot>[A-Za-z0-9_.-]+))?|"
    r"(?P<name>[A-Z][A-Z0-9_]*))"
)


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


class ResourceResolver:
    """Resolve package resources without permitting path traversal."""

    def __init__(
        self, package_root: str | Path, *, global_root: str | Path | None = None
    ):
        self.package_root = Path(package_root).resolve()
        self.global_root = (
            Path(global_root).resolve() if global_root is not None else None
        )

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
            if candidate.is_symlink() or not resolved.is_file():
                continue
            return self._parse_command(resolved)
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
                if candidate.is_symlink() or not resolved.is_file():
                    continue
                return ScriptResource(path=resolved, runtime=runtime)
        raise FileNotFoundError(f"script resource is missing: {name}")

    def mcp_servers(self, reference: str) -> dict[str, dict[str, object]]:
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
            if not candidate.is_symlink() and resolved.is_file():
                path = resolved
                break
        if path is None:
            raise FileNotFoundError(f"MCP definition is missing: {reference}")
        if path.stat().st_size > 256_000:
            raise ValueError("MCP definition exceeds 256000 bytes")
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
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
        return servers

    @staticmethod
    def _parse_command(path: Path) -> CommandResource:
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
                return shlex.quote(str(path))
            return shlex.quote(value)

        return _VARIABLE.sub(replace, template)


__all__ = [
    "CommandResource",
    "ResourceResolver",
    "ScriptResource",
    "VariableContext",
]
