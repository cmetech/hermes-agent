"""Child entry point for :mod:`agent.plugin_agent`; not a public API."""

from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import threading
import unicodedata
from typing import Any
from urllib.parse import unquote, urlsplit


_PROTOCOL_VERSION = 1
_MAX_REQUEST_BYTES = 1_000_000
_MAX_FRAME_BYTES = 4_000_000
_protocol_stdout = sys.stdout
_emit_lock = threading.Lock()
_cancel_event = threading.Event()
_active_agent: Any = None


class PackageMCPUnavailable(RuntimeError):
    """A request-carried MCP cannot be supplied to Hermes' tool loop."""

    failure_kind = "package_mcp_unavailable"


_AUTHORITY_DESCRIPTOR_KEY = "__hermes_authenticated_local_mcp"
_AUTHORITY_CWD_KEY = "__hermes_private_mcp_cwd"
_AUTHORITY_ROOT_PREFIX = "hermes-workflow-authority-"
_AUTHORITY_CONTROL_DIRECTORY = "control"
_AUTHORITY_PAYLOAD_DIRECTORY = "payload"
_AUTHORITY_MANIFEST_NAME = ".hermes-authority-manifest-v1.json"
_AUTHORITY_MAX_FILES = 512
_AUTHORITY_MAX_FILE_BYTES = 1 * 1024 * 1024
_AUTHORITY_MAX_TOTAL_BYTES = 8 * 1024 * 1024
_AUTHORITY_MAX_MANIFEST_BYTES = 4_000_000
_AUTHORITY_LOADER = (
    "import os,runpy,sys;"
    "r,e=sys.argv[1:3];p=os.path.join(r,*e.split('/'));"
    "os.chdir(r);sys.path[:]=[os.path.dirname(p),r,*sys.path];"
    "sys.argv=[p,*sys.argv[3:]];runpy.run_path(p,run_name='__main__')"
)


def _canonical_authority_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise PackageMCPUnavailable(
            "package_mcp_unavailable: authenticated MCP authority is invalid"
        )
    logical = Path(value)
    if (
        logical.is_absolute()
        or value != logical.as_posix()
        or any(part in {"", ".", ".."} for part in logical.parts)
    ):
        raise PackageMCPUnavailable(
            "package_mcp_unavailable: authenticated MCP authority is invalid"
        )
    return value


def _validate_authority_descriptor(
    descriptor: object,
) -> tuple[Path, Path, dict[str, dict[str, object]]]:
    if not isinstance(descriptor, dict) or descriptor.get("version") != 2:
        raise PackageMCPUnavailable(
            "package_mcp_unavailable: authenticated MCP authority is invalid"
        )
    root_value = descriptor.get("root")
    source_value = descriptor.get("source_root")
    payload_value = descriptor.get("payload")
    manifest_value = descriptor.get("manifest")
    manifest_digest = descriptor.get("manifest_sha256")
    nonce = descriptor.get("nonce")
    file_count = descriptor.get("file_count")
    total_bytes = descriptor.get("total_bytes")
    root_identity = descriptor.get("root_identity")
    if (
        not isinstance(root_value, str)
        or not os.path.isabs(root_value)
        or not isinstance(source_value, str)
        or not os.path.isabs(source_value)
        or payload_value != _AUTHORITY_PAYLOAD_DIRECTORY
        or manifest_value
        != f"{_AUTHORITY_CONTROL_DIRECTORY}/{_AUTHORITY_MANIFEST_NAME}"
        or not isinstance(manifest_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", manifest_digest) is None
        or not isinstance(nonce, str)
        or re.fullmatch(r"[0-9a-f]{64}", nonce) is None
        or isinstance(file_count, bool)
        or not isinstance(file_count, int)
        or not 0 < file_count <= _AUTHORITY_MAX_FILES
        or isinstance(total_bytes, bool)
        or not isinstance(total_bytes, int)
        or not 0 <= total_bytes <= _AUTHORITY_MAX_TOTAL_BYTES
        or not isinstance(root_identity, dict)
        or isinstance(root_identity.get("device"), bool)
        or not isinstance(root_identity.get("device"), int)
        or isinstance(root_identity.get("inode"), bool)
        or not isinstance(root_identity.get("inode"), int)
    ):
        raise PackageMCPUnavailable(
            "package_mcp_unavailable: authenticated MCP authority is invalid"
        )
    root = Path(root_value)
    payload_root = root / _AUTHORITY_PAYLOAD_DIRECTORY
    control_root = root / _AUTHORITY_CONTROL_DIRECTORY
    source_root = Path(os.path.abspath(source_value))
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise PackageMCPUnavailable(
            "package_mcp_unavailable: authenticated MCP authority is missing"
        ) from exc
    if (
        root.is_symlink()
        or not root.is_dir()
        or not root.name.startswith(_AUTHORITY_ROOT_PREFIX)
    ):
        raise PackageMCPUnavailable(
            "package_mcp_unavailable: authenticated MCP authority is invalid"
        )
    if (root_stat.st_dev, root_stat.st_ino) != (
        root_identity["device"],
        root_identity["inode"],
    ):
        raise PackageMCPUnavailable(
            "package_mcp_unavailable: authenticated MCP authority changed"
        )
    try:
        payload_stat = payload_root.lstat()
        control_stat = control_root.lstat()
        if (
            payload_root.is_symlink()
            or not payload_root.is_dir()
            or control_root.is_symlink()
            or not control_root.is_dir()
            or {path.name for path in root.iterdir()}
            != {_AUTHORITY_CONTROL_DIRECTORY, _AUTHORITY_PAYLOAD_DIRECTORY}
        ):
            raise OSError("authority layout is invalid")
    except OSError as exc:
        raise PackageMCPUnavailable(
            "package_mcp_unavailable: authenticated MCP authority changed"
        ) from exc
    manifest_path = root / manifest_value
    try:
        manifest_before = manifest_path.lstat()
        if (
            manifest_path.is_symlink()
            or not manifest_path.is_file()
            or manifest_before.st_size > _AUTHORITY_MAX_MANIFEST_BYTES
        ):
            raise OSError("manifest is not a bounded regular file")
        manifest_bytes = manifest_path.read_bytes()
        manifest_after = manifest_path.lstat()
    except OSError as exc:
        raise PackageMCPUnavailable(
            "package_mcp_unavailable: authenticated MCP authority changed"
        ) from exc
    if (
        len(manifest_bytes) != manifest_before.st_size
        or (
            manifest_before.st_dev,
            manifest_before.st_ino,
            manifest_before.st_size,
            manifest_before.st_mtime_ns,
        )
        != (
            manifest_after.st_dev,
            manifest_after.st_ino,
            manifest_after.st_size,
            manifest_after.st_mtime_ns,
        )
        or hashlib.sha256(manifest_bytes).hexdigest() != manifest_digest
    ):
        raise PackageMCPUnavailable(
            "package_mcp_unavailable: authenticated MCP authority changed"
        )
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PackageMCPUnavailable(
            "package_mcp_unavailable: authenticated MCP authority is invalid"
        ) from exc
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"version", "nonce", "files"}
        or manifest.get("version") != 1
        or manifest.get("nonce") != nonce
        or not isinstance(manifest.get("files"), dict)
    ):
        raise PackageMCPUnavailable(
            "package_mcp_unavailable: authenticated MCP authority is invalid"
        )
    files = manifest["files"]
    if len(files) != file_count:
        raise PackageMCPUnavailable(
            "package_mcp_unavailable: authenticated MCP authority changed"
        )
    for relative, metadata in files.items():
        _canonical_authority_relative(relative)
        if not isinstance(metadata, dict) or set(metadata) != {"sha256", "size"}:
            raise PackageMCPUnavailable(
                "package_mcp_unavailable: authenticated MCP authority is invalid"
            )
    observed: set[str] = set()
    observed_directories: set[str] = set()
    total = 0
    for current, directories, names in os.walk(payload_root, followlinks=False):
        current_path = Path(current)
        for directory in directories:
            path = current_path / directory
            if path.is_symlink() or not path.is_dir():
                raise PackageMCPUnavailable(
                    "package_mcp_unavailable: authenticated MCP authority is invalid"
                )
            observed_directories.add(path.relative_to(payload_root).as_posix())
        for filename in names:
            path = current_path / filename
            try:
                relative = path.relative_to(payload_root).as_posix()
                metadata = files[relative]
                before = path.lstat()
            except (KeyError, OSError, ValueError) as exc:
                raise PackageMCPUnavailable(
                    "package_mcp_unavailable: authenticated MCP authority changed"
                ) from exc
            if (
                path.is_symlink()
                or not path.is_file()
                or not isinstance(metadata, dict)
            ):
                raise PackageMCPUnavailable(
                    "package_mcp_unavailable: authenticated MCP authority changed"
                )
            expected_size = metadata.get("size")
            expected_digest = metadata.get("sha256")
            if (
                isinstance(expected_size, bool)
                or not isinstance(expected_size, int)
                or not 0 <= expected_size <= _AUTHORITY_MAX_FILE_BYTES
                or not isinstance(expected_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)
                or before.st_size != expected_size
            ):
                raise PackageMCPUnavailable(
                    "package_mcp_unavailable: authenticated MCP authority changed"
                )
            try:
                data = path.read_bytes()
                after = path.lstat()
            except OSError as exc:
                raise PackageMCPUnavailable(
                    "package_mcp_unavailable: authenticated MCP authority changed"
                ) from exc
            if (
                before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or hashlib.sha256(data).hexdigest() != expected_digest
            ):
                raise PackageMCPUnavailable(
                    "package_mcp_unavailable: authenticated MCP authority changed"
                )
            total += len(data)
            if total > _AUTHORITY_MAX_TOTAL_BYTES:
                raise PackageMCPUnavailable(
                    "package_mcp_unavailable: authenticated MCP authority is too large"
                )
            observed.add(relative)
    try:
        final_root_stat = root.lstat()
        final_payload_stat = payload_root.lstat()
        final_control_stat = control_root.lstat()
        final_manifest_stat = manifest_path.lstat()
        final_manifest_bytes = manifest_path.read_bytes()
        control_entries = {path.name for path in control_root.iterdir()}
        root_entries = {path.name for path in root.iterdir()}
    except OSError as exc:
        raise PackageMCPUnavailable(
            "package_mcp_unavailable: authenticated MCP authority changed"
        ) from exc
    expected_directories = {
        parent.as_posix()
        for relative in files
        for parent in Path(relative).parents
        if parent.as_posix() != "."
    }
    if (
        observed != set(files)
        or observed_directories != expected_directories
        or control_entries != {_AUTHORITY_MANIFEST_NAME}
        or root_entries
        != {_AUTHORITY_CONTROL_DIRECTORY, _AUTHORITY_PAYLOAD_DIRECTORY}
        or total != total_bytes
        or hashlib.sha256(final_manifest_bytes).hexdigest() != manifest_digest
        or (
            manifest_after.st_dev,
            manifest_after.st_ino,
            manifest_after.st_size,
            manifest_after.st_mtime_ns,
        )
        != (
            final_manifest_stat.st_dev,
            final_manifest_stat.st_ino,
            final_manifest_stat.st_size,
            final_manifest_stat.st_mtime_ns,
        )
        or (
            payload_stat.st_dev,
            payload_stat.st_ino,
            payload_stat.st_size,
            payload_stat.st_mtime_ns,
        )
        != (
            final_payload_stat.st_dev,
            final_payload_stat.st_ino,
            final_payload_stat.st_size,
            final_payload_stat.st_mtime_ns,
        )
        or (
            control_stat.st_dev,
            control_stat.st_ino,
            control_stat.st_size,
            control_stat.st_mtime_ns,
        )
        != (
            final_control_stat.st_dev,
            final_control_stat.st_ino,
            final_control_stat.st_size,
            final_control_stat.st_mtime_ns,
        )
        or (
            root_stat.st_dev,
            root_stat.st_ino,
            root_stat.st_size,
            root_stat.st_mtime_ns,
        )
        != (
            final_root_stat.st_dev,
            final_root_stat.st_ino,
            final_root_stat.st_size,
            final_root_stat.st_mtime_ns,
        )
    ):
        raise PackageMCPUnavailable(
            "package_mcp_unavailable: authenticated MCP authority changed"
        )
    return payload_root, source_root, files


_PATHLIKE_EXTENSIONS = frozenset({
    ".bash",
    ".cjs",
    ".crt",
    ".exe",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".key",
    ".mjs",
    ".pem",
    ".py",
    ".pyw",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
})
_PATH_OPTION = re.compile(
    r"^--?(?:config|cwd|dir|directory|entry|file|manifest|path|root|script)(?:[-_].*)?$",
    re.IGNORECASE,
)
_COMPOUND_OPTION_ASSIGNMENT = re.compile(r"^(?P<option>--?[^=\s]+)=(?P<value>.*)$")
_PATH_ENV = re.compile(
    r"(?:^|_)(?:CONFIG|CWD|DIR|DIRECTORY|ENTRY|FILE|MANIFEST|PATH|ROOT|SCRIPT)$",
    re.IGNORECASE,
)
_URI_SCHEME = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*):(?P<body>.*)$")
_NETWORK_URI_SCHEMES = frozenset({"http", "https", "ws", "wss"})
_NETWORK_ENCODED_AMBIGUITY = re.compile(
    r"%(?:25)*(?:0[0-9a-f]|1[0-9a-f]|20|5c|7f)", re.IGNORECASE
)
_NETWORK_FORBIDDEN_UNICODE_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
_CLASSIFICATION_MAX_BYTES = 256_000
_CLASSIFICATION_MAX_DECODE_PASSES = 64
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_SCOPED_PACKAGE = re.compile(r"^@[^/\\\s]+/[^/\\\s]+$")
_PACKAGE_SPEC = re.compile(
    r"^(?:@[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*|"
    r"[A-Za-z0-9][A-Za-z0-9._-]*)(?:@[A-Za-z0-9][A-Za-z0-9._+*~^<>=|-]*)?$"
)
_PACKAGE_ASSIGNMENT_OPTIONS = {
    "npx": frozenset({"--package", "-p"}),
    "uvx": frozenset({"--from", "--with", "-w", "--with-editable"}),
}
_PYTHON_EXECUTABLE = re.compile(r"pythonw?(?:3(?:\.\d+)?)?")
_PYTHON_MODULE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_CLOSURE_ERROR = (
    "package_mcp_unavailable: authenticated local MCP runtime closure cannot be "
    "proven; place the Python server and every local dependency in the workflow "
    "package, reference them with canonical relative paths, then re-trust and "
    "start a new run"
)


def _authority_relative(
    value: str, source_root: Path, *, compound: bool = False
) -> tuple[str, str] | None:
    option, candidate = _path_candidate(value, compound=compound)
    prefix = f"{option}=" if option else ""
    if candidate.startswith("./"):
        candidate = candidate[2:]
    path = Path(candidate)
    if path.is_absolute():
        try:
            candidate = path.relative_to(source_root).as_posix()
        except ValueError:
            return None
    try:
        return prefix, _canonical_authority_relative(candidate)
    except PackageMCPUnavailable:
        return None


def _has_valid_percent_syntax(value: str) -> bool:
    return all(
        character != "%"
        or (
            index + 2 < len(value)
            and value[index + 1] in _HEX_DIGITS
            and value[index + 2] in _HEX_DIGITS
        )
        for index, character in enumerate(value)
    )


def _contains_forbidden_network_characters(value: str) -> bool:
    return "\\" in value or any(
        character.isspace()
        or unicodedata.category(character) in _NETWORK_FORBIDDEN_UNICODE_CATEGORIES
        for character in value
    )


def _has_ambiguous_network_encoding(value: str) -> bool:
    decoded = value
    max_passes = min(max(len(value), 1), _CLASSIFICATION_MAX_DECODE_PASSES)
    for _ in range(max_passes):
        if _contains_forbidden_network_characters(decoded):
            return True
        if "%" not in decoded or not _has_valid_percent_syntax(decoded):
            return False
        try:
            expanded = unquote(decoded, errors="strict")
        except UnicodeDecodeError:
            return True
        if len(expanded) >= len(decoded):
            return False
        decoded = expanded
    return True


def _supported_network_url(candidate: str) -> bool:
    if (
        not candidate
        or _has_ambiguous_network_encoding(candidate)
        or not _has_valid_percent_syntax(candidate)
        or _NETWORK_ENCODED_AMBIGUITY.search(candidate) is not None
    ):
        return False
    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        parsed.port
    except (UnicodeError, ValueError):
        return False
    if (
        parsed.scheme.lower() not in _NETWORK_URI_SCHEMES
        or not parsed.netloc
        or not hostname
        or "%" in parsed.netloc
    ):
        return False
    userinfo, separator, authority = parsed.netloc.rpartition("@")
    if not separator:
        authority = parsed.netloc
    elif not userinfo or "@" in userinfo:
        return False
    if authority.endswith(":"):
        return False
    if authority.startswith("["):
        closing = authority.find("]")
        suffix = authority[closing + 1 :]
        if closing < 0 or (suffix and re.fullmatch(r":[0-9]+", suffix) is None):
            return False
        try:
            ipaddress.IPv6Address(hostname)
        except ValueError:
            return False
    elif authority.count(":") > 1:
        return False
    if re.fullmatch(r"[0-9.]+", hostname):
        try:
            ipaddress.IPv4Address(hostname)
        except ValueError:
            return False
    try:
        hostname.encode("idna")
    except UnicodeError:
        return False
    return True


def _supported_network_view(value: str, *, compound: bool) -> bool:
    candidate = value
    if compound:
        assignment = _COMPOUND_OPTION_ASSIGNMENT.fullmatch(candidate)
        if assignment is not None:
            candidate = assignment.group("value")
    return _supported_network_url(candidate)


def _normalized_classification_value(value: str, *, compound: bool) -> str:
    """Return a bounded, unambiguous view used only for safety classification."""
    try:
        encoded_size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise PackageMCPUnavailable(_CLOSURE_ERROR) from exc
    if encoded_size > _CLASSIFICATION_MAX_BYTES:
        raise PackageMCPUnavailable(_CLOSURE_ERROR)
    normalized = value
    max_passes = min(max(len(value), 1), _CLASSIFICATION_MAX_DECODE_PASSES)
    for _ in range(max_passes):
        if _supported_network_view(normalized, compound=compound):
            return normalized
        if "%" not in normalized:
            return normalized
        if not _has_valid_percent_syntax(normalized):
            raise PackageMCPUnavailable(_CLOSURE_ERROR)
        try:
            expanded = unquote(normalized, errors="strict")
        except UnicodeDecodeError as exc:
            raise PackageMCPUnavailable(_CLOSURE_ERROR) from exc
        if len(expanded) >= len(normalized):
            raise PackageMCPUnavailable(_CLOSURE_ERROR)
        normalized = expanded
    raise PackageMCPUnavailable(_CLOSURE_ERROR)


def _path_candidate(value: str, *, compound: bool = False) -> tuple[str, str]:
    normalized = _normalized_classification_value(value, compound=compound)
    if compound:
        match = _COMPOUND_OPTION_ASSIGNMENT.fullmatch(normalized)
        if match is not None:
            return match.group("option"), match.group("value")
    return "", normalized


def _uri_disposition(value: str, *, compound: bool = False) -> str | None:
    """Classify explicit URIs without consulting mutable filesystem state."""
    candidate = _path_candidate(value, compound=compound)[1]
    if _supported_network_url(candidate):
        return "network"
    match = _URI_SCHEME.fullmatch(candidate)
    if match is None:
        return None
    return "unsafe"


def _looks_path_like(value: str, *, compound: bool = False) -> bool:
    option, candidate = _path_candidate(value, compound=compound)
    uri_disposition = _uri_disposition(value, compound=compound)
    if uri_disposition == "network":
        return False
    if uri_disposition == "unsafe":
        return True
    if not candidate:
        return False
    if (
        candidate.startswith(("./", "../", ".\\", "..\\", "/", "~"))
        or "/" in candidate
        or "\\" in candidate
        or re.match(r"^[A-Za-z]:", candidate)
        or Path(candidate).suffix.lower() in _PATHLIKE_EXTENSIONS
    ):
        return True
    return bool(option and _PATH_OPTION.fullmatch(option))


def _is_package_spec_argument(value: str, executable: str) -> bool:
    options = _PACKAGE_ASSIGNMENT_OPTIONS.get(executable)
    if options is None:
        return False
    option, candidate = _path_candidate(value, compound=True)
    if option:
        return option in options and _PACKAGE_SPEC.fullmatch(candidate) is not None
    return _SCOPED_PACKAGE.fullmatch(candidate) is not None


def _finalize_authenticated_mcp_config(
    servers: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    finalized: dict[str, dict[str, Any]] = {}
    validated: dict[str, tuple[Path, Path, dict[str, dict[str, object]]]] = {}
    for name, raw in servers.items():
        config = dict(raw)
        descriptor = config.pop(_AUTHORITY_DESCRIPTOR_KEY, None)
        if descriptor is None:
            finalized[name] = config
            continue
        if not isinstance(descriptor, dict):
            raise PackageMCPUnavailable(
                "package_mcp_unavailable: authenticated MCP authority is invalid"
            )
        try:
            cache_key = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise PackageMCPUnavailable(
                "package_mcp_unavailable: authenticated MCP authority is invalid"
            ) from exc
        authority = validated.get(cache_key)
        if authority is None:
            authority = _validate_authority_descriptor(descriptor)
            validated[cache_key] = authority
        root, source_root, files = authority
        command = config.get("command")
        args = config.get("args", [])
        local_references: set[str] = set()

        def reject_unproven() -> None:
            raise PackageMCPUnavailable(_CLOSURE_ERROR)

        def rewrite(
            value: object,
            *,
            compound: bool = False,
            required: bool = False,
            path_hint: bool = False,
            allow_path_literal: bool = False,
        ) -> tuple[object, str | None]:
            if not isinstance(value, str):
                if required:
                    reject_unproven()
                return value, None
            reference = _authority_relative(value, source_root, compound=compound)
            if reference is None:
                if required or path_hint or (
                    not allow_path_literal
                    and _looks_path_like(value, compound=compound)
                ):
                    reject_unproven()
                return value, None
            prefix, relative = reference
            if relative not in files:
                candidate = _path_candidate(value, compound=compound)[1]
                path = Path(candidate)
                if path.is_absolute():
                    try:
                        path.relative_to(source_root)
                    except ValueError:
                        pass
                    else:
                        raise PackageMCPUnavailable(
                            "package_mcp_unavailable: authenticated MCP authority changed"
                        )
                if required or path_hint or (
                    not allow_path_literal
                    and _looks_path_like(value, compound=compound)
                ):
                    reject_unproven()
                return value, None
            local_references.add(relative)
            return f"{prefix}{root.joinpath(*Path(relative).parts)}", relative

        if command is None and isinstance(config.get("url"), str):
            if _uri_disposition(config["url"]) != "network":
                reject_unproven()
            finalized[name] = config
            continue
        if not isinstance(command, str) or not command:
            raise PackageMCPUnavailable(
                "package_mcp_unavailable: authenticated MCP launch config is invalid"
            )
        if not isinstance(args, list):
            raise PackageMCPUnavailable(
                "package_mcp_unavailable: authenticated MCP launch config is invalid"
            )
        executable = re.split(r"[/\\]", command)[-1].lower().removesuffix(".exe")
        is_python = _PYTHON_EXECUTABLE.fullmatch(executable) is not None
        command_path_hint = _looks_path_like(command) and not is_python
        rewritten_command, _command_relative = rewrite(
            command,
            path_hint=command_path_hint,
            allow_path_literal=is_python,
        )
        config["command"] = rewritten_command

        rewritten: list[str] = []
        argument_references: list[str | None] = []
        for index, value in enumerate(args):
            if not isinstance(value, str):
                raise PackageMCPUnavailable(
                    "package_mcp_unavailable: authenticated MCP launch config is invalid"
                )
            package_spec = _is_package_spec_argument(value, executable)
            python_code = is_python and index > 0 and args[index - 1] == "-c"
            rewritten_value, relative = rewrite(
                value,
                compound=True,
                path_hint=(
                    not package_spec
                    and not python_code
                    and _looks_path_like(value, compound=True)
                ),
                allow_path_literal=package_spec or python_code,
            )
            rewritten.append(str(rewritten_value))
            argument_references.append(relative)
        rewritten_env = config.get("env")
        if rewritten_env is not None and not isinstance(rewritten_env, dict):
            raise PackageMCPUnavailable(
                "package_mcp_unavailable: authenticated MCP launch config is invalid"
            )
        if isinstance(rewritten_env, dict):
            rewritten_environment: dict[object, object] = {}
            for key, value in rewritten_env.items():
                path_hint = (
                    isinstance(value, str)
                    and (
                        _looks_path_like(value)
                        or (isinstance(key, str) and _PATH_ENV.search(key) is not None)
                    )
                )
                rewritten_environment[key] = rewrite(value, path_hint=path_hint)[0]
            config["env"] = rewritten_environment
        runtime_files = config.get("runtime_files")
        if runtime_files is not None:
            if not isinstance(runtime_files, list):
                raise PackageMCPUnavailable(
                    "package_mcp_unavailable: authenticated MCP launch config is invalid"
                )
            config["runtime_files"] = [
                rewrite(value, required=True)[0] for value in runtime_files
            ]

        if is_python:
            isolated_args = rewritten[1:] if rewritten[:1] == ["-I"] else rewritten
            isolated_refs = (
                argument_references[1:]
                if rewritten[:1] == ["-I"]
                else argument_references
            )
            if isolated_args[:1] == ["-c"]:
                if len(isolated_args) < 2:
                    raise PackageMCPUnavailable(
                        "package_mcp_unavailable: authenticated MCP launch config is invalid"
                    )
                config["args"] = ["-I", *isolated_args]
            elif isolated_args[:1] == ["-m"]:
                if (
                    len(isolated_args) < 2
                    or _PYTHON_MODULE.fullmatch(isolated_args[1]) is None
                ):
                    reject_unproven()
                config["args"] = ["-I", *isolated_args]
            else:
                entry = isolated_refs[0] if isolated_refs else None
                if (
                    entry is None
                    or not entry.lower().endswith((".py", ".pyw"))
                    or not isolated_args
                ):
                    reject_unproven()
                config["args"] = [
                    "-I",
                    "-c",
                    _AUTHORITY_LOADER,
                    str(root),
                    entry,
                    *isolated_args[1:],
                ]
            config[_AUTHORITY_CWD_KEY] = str(root)
        elif local_references:
            reject_unproven()
        else:
            config["args"] = rewritten
        finalized[name] = config
    return finalized


_ARCHON_HOOK_EVENT_MAP = {
    "PreToolUse": "pre_tool_call",
    "PostToolUse": "post_tool_call",
    "PostToolUseFailure": "post_tool_call",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "SessionStart": "on_session_start",
    "SessionEnd": "on_session_end",
    "UserPromptSubmit": "pre_llm_call",
    "PermissionRequest": "pre_approval_request",
    "Setup": "on_session_start",
    "Elicitation": "pre_approval_request",
    "ElicitationResult": "post_approval_response",
    "InstructionsLoaded": "pre_llm_call",
    "TaskCompleted": "subagent_stop",
}
_UNSUPPORTED_ARCHON_HOOK_EVENTS = {
    "Notification",
    "Stop",
    "PreCompact",
    "TeammateIdle",
    "ConfigChange",
    "WorktreeCreate",
    "WorktreeRemove",
}


def _translate_hook_response(event: str, response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict):
        raise ValueError(f"{event} hook response must be a mapping")
    specific = response.get("hookSpecificOutput")
    if specific is not None and not isinstance(specific, dict):
        raise ValueError(f"{event} hookSpecificOutput must be a mapping")
    specific = specific or {}
    declared = specific.get("hookEventName")
    if declared is not None and declared != event:
        raise ValueError(f"{event} hookEventName does not match {declared}")
    translated: dict[str, Any] = {}
    permission = specific.get("permissionDecision")
    reason = specific.get("permissionDecisionReason") or response.get("stopReason")
    if permission == "deny" or response.get("decision") == "block":
        translated.update({
            "action": "block",
            "message": str(reason or "blocked by node hook"),
        })
    elif permission == "ask":
        translated.update({
            "action": "approve",
            "message": str(reason or "approval requested by node hook"),
        })
    elif permission not in {None, "allow"}:
        raise ValueError(f"{event} permissionDecision is invalid")
    if response.get("continue") is False:
        translated.update({
            "action": "block",
            "message": str(reason or "node hook stopped execution"),
        })
    updated = specific.get("updatedInput")
    if updated is not None:
        if not isinstance(updated, dict):
            raise ValueError(f"{event} updatedInput must be a mapping")
        translated["args"] = dict(updated)
    context = specific.get("additionalContext") or response.get("systemMessage")
    if context:
        translated["context"] = str(context)
    if "updatedMCPToolOutput" in specific:
        translated["output"] = specific["updatedMCPToolOutput"]
    if "content" in specific:
        translated["content"] = specific["content"]
    if "action" in specific:
        translated["elicitation_action"] = specific["action"]
    return translated or None


def _compile_node_hook_resources(raw_hooks: Any) -> tuple[dict[str, Any], ...]:
    if raw_hooks is None:
        return ()
    if not isinstance(raw_hooks, (tuple, list)):
        raise ValueError("node hooks must be a list")
    compiled = []
    for index, raw in enumerate(raw_hooks):
        if not isinstance(raw, dict):
            raise ValueError(f"node hook {index} must be a mapping")
        unknown = set(raw) - {"event", "matcher", "response", "timeout"}
        if unknown:
            raise ValueError(
                f"node hook {index} has unknown field: {sorted(unknown)[0]}"
            )
        event = raw.get("event")
        if event in _UNSUPPORTED_ARCHON_HOOK_EVENTS:
            raise ValueError(f"unsupported node hook event: {event}")
        if event not in _ARCHON_HOOK_EVENT_MAP:
            raise ValueError(f"unknown node hook event: {event}")
        matcher = raw.get("matcher")
        try:
            compiled_matcher = re.compile(matcher) if matcher is not None else None
        except (TypeError, re.error) as exc:
            raise ValueError(f"node hook {index} matcher is invalid") from exc
        timeout = raw.get("timeout", 30)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not 0 < timeout <= 300
        ):
            raise ValueError(f"node hook {index} timeout must be between 0 and 300")
        response = raw.get("response")
        translated = _translate_hook_response(str(event), response)
        compiled.append({
            "event": event,
            "hermes_event": _ARCHON_HOOK_EVENT_MAP[event],
            "matcher": compiled_matcher,
            "timeout": float(timeout),
            "response": translated,
        })
    return tuple(compiled)


def _install_node_hooks(raw_hooks: Any) -> list[dict[str, str]]:
    resources = _compile_node_hook_resources(raw_hooks)
    if not resources:
        return []
    from hermes_cli.plugins import get_plugin_manager

    manager = get_plugin_manager()
    observed: list[dict[str, str]] = []

    def matches(resource: dict[str, Any], kwargs: dict[str, Any]) -> bool:
        matcher = resource["matcher"]
        return matcher is None or bool(
            matcher.search(str(kwargs.get("tool_name") or ""))
        )

    for resource in resources:
        event = str(resource["event"])
        hermes_event = str(resource["hermes_event"])
        response = resource["response"]

        def callback(
            _resource=resource,
            _event=event,
            _response=response,
            **kwargs,
        ):
            if not matches(_resource, kwargs):
                return None
            if _event == "PostToolUseFailure" and kwargs.get("status") not in {
                "failed",
                "error",
                "blocked",
            }:
                return None
            if _event == "PostToolUse" and kwargs.get("status") in {
                "failed",
                "error",
                "blocked",
            }:
                return None
            observed.append({
                "event": _event,
                "tool_name": str(kwargs.get("tool_name") or ""),
            })
            return dict(_response) if isinstance(_response, dict) else None

        manager._hooks.setdefault(hermes_event, []).append(callback)
        if event == "PreToolUse" and isinstance(response, dict) and "args" in response:

            def middleware(
                _resource=resource,
                _response=response,
                **kwargs,
            ):
                if not matches(_resource, kwargs):
                    return None
                observed.append({
                    "event": "PreToolUse",
                    "tool_name": str(kwargs.get("tool_name") or ""),
                })
                return {
                    "args": dict(_response["args"]),
                    "source": "workflow-node-hook",
                }

            manager._middleware.setdefault("tool_request", []).append(middleware)
    return observed


def _build_inline_agent_handler(
    *,
    plugin_id: str,
    definitions: dict[str, Any],
    workdir: Path,
    parent_request: Any,
    runner_factory,
    emit_progress,
    pause,
):
    """Build the synchronous worker-local ``workflow_agent`` dispatcher."""
    admission_lock = threading.Lock()
    total_started = 0
    maximum_children = max(0, min(64, getattr(parent_request, "max_descendants", 32)))

    def handler(args: dict[str, Any]) -> dict[str, Any]:
        nonlocal total_started
        task = args.get("task")
        if not isinstance(task, str) or not task.strip() or len(task) > 100_000:
            return {"error": "task must contain 1 to 100000 characters"}
        agent_id = args.get("agent_id")
        definition = definitions.get(agent_id)
        if not isinstance(definition, dict):
            return {"error": "unknown inline agent"}
        with admission_lock:
            if total_started >= maximum_children:
                return {"error": "inline agent descendant limit exhausted"}
            total_started += 1
        instructions = str(definition.get("instructions") or "").strip()
        prompt_parts = [str(definition["prompt"]).strip(), task.strip()]
        if instructions:
            prompt_parts.insert(0, instructions)
        prompt = "\n\n".join(prompt_parts)
        emit_progress(phase="inline_agent_started", agent_id=str(agent_id))
        from agent.plugin_agent import PluginAgentRunRequest

        parent = parent_request
        request = PluginAgentRunRequest(
            prompt=prompt,
            provider=getattr(parent, "provider", None),
            model=definition.get("model") or getattr(parent, "model", None),
            allowed_tools=(
                tuple(definition["allowed_tools"])
                if definition.get("allowed_tools") is not None
                else None
            ),
            denied_tools=tuple(definition.get("denied_tools", ())),
            workdir=workdir,
            max_iterations=int(definition.get("max_iterations", 90)),
            max_api_attempts=getattr(parent, "max_api_attempts", 1),
            idle_timeout_seconds=getattr(parent, "idle_timeout_seconds", 300.0),
            wall_timeout_seconds=getattr(parent, "wall_timeout_seconds", 1800.0),
            provider_request_timeout_seconds=getattr(
                parent, "provider_request_timeout_seconds", 300.0
            ),
            approved_action_digest=getattr(parent, "approved_action_digest", None),
            reasoning_config=getattr(parent, "reasoning_config", None),
            fallback_model=getattr(parent, "fallback_model", None),
            request_overrides=getattr(parent, "request_overrides", {}),
            max_budget_usd=getattr(parent, "max_budget_usd", None),
            sandbox_policy=getattr(parent, "sandbox_policy", None),
            max_process_tree_rss_bytes=getattr(
                parent, "max_process_tree_rss_bytes", 2048 * 1024 * 1024
            ),
            max_process_tree_cpu_seconds=getattr(
                parent, "max_process_tree_cpu_seconds", 900.0
            ),
            max_descendants=max(0, getattr(parent, "max_descendants", 32) - 1),
            cooperative_shutdown_seconds=getattr(
                parent, "cooperative_shutdown_seconds", 5.0
            ),
            term_grace_seconds=getattr(parent, "term_grace_seconds", 5.0),
            kill_reap_grace_seconds=getattr(parent, "kill_reap_grace_seconds", 2.0),
        )
        result = runner_factory(plugin_id).run(request)
        if result.status == "paused":
            descriptor = dict(result.pending_interaction or {})
            pause(descriptor)
            emit_progress(phase="inline_agent_paused", agent_id=str(agent_id))
            return {"status": "paused", "pending_interaction": descriptor}
        emit_progress(phase=f"inline_agent_{result.status}", agent_id=str(agent_id))
        if result.status != "completed":
            return {
                "status": result.status,
                "error": "isolated inline agent did not complete",
            }
        return {
            "status": "completed",
            "result": _sanitize(result.final_response, 64_000),
            "usage": dict(result.usage),
        }

    return handler


def _emit(frame_type: str, **payload: Any) -> None:
    frame = {"protocol_version": _PROTOCOL_VERSION, "type": frame_type, **payload}
    encoded = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _MAX_FRAME_BYTES:
        raise RuntimeError("plugin-agent output frame exceeds protocol limit")
    with _emit_lock:
        _protocol_stdout.write(encoded + "\n")
        _protocol_stdout.flush()


def _sanitize(value: Any, limit: int = 2000) -> str:
    text = str(value or "")[:limit]
    try:
        from agent.redact import redact_sensitive_text

        return redact_sensitive_text(text)
    except Exception:
        return text


def _interaction_descriptor(kind: str, payload: dict[str, Any]) -> dict[str, str]:
    safe = {key: _sanitize(value, 1000) for key, value in payload.items()}
    digest = hashlib.sha256(
        json.dumps([kind, safe], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {"kind": kind, "action_digest": digest, **safe}


def _interaction(kind: str, payload: dict[str, Any]) -> dict[str, str]:
    descriptor = _interaction_descriptor(kind, payload)
    _emit("interaction", interaction=descriptor)
    return descriptor


def _configured_model(requested: str | None) -> str:
    if requested:
        return requested
    from hermes_cli.config import load_config

    configured = (load_config() or {}).get("model")
    if isinstance(configured, dict):
        return str(configured.get("default") or configured.get("model") or "").strip()
    if isinstance(configured, str):
        return configured.strip()
    return ""


def _tool_name(schema: dict[str, Any]) -> str:
    function = schema.get("function")
    return str(function.get("name", "")) if isinstance(function, dict) else ""


def _run(payload: dict[str, Any]) -> dict[str, Any]:
    global _active_agent
    from agent.plugin_agent import PluginAgentRunRequest, _validate_request

    request_data = payload.get("request")
    if not isinstance(request_data, dict):
        raise ValueError("request payload is missing")
    request_data = dict(request_data)
    if request_data.get("workdir"):
        request_data["workdir"] = Path(request_data["workdir"])
    for name in (
        "enabled_toolsets",
        "allowed_tools",
        "denied_tools",
        "skills",
        "hooks",
    ):
        if request_data.get(name) is not None:
            request_data[name] = tuple(request_data[name])
    request = PluginAgentRunRequest(**request_data)
    _validate_request(request)
    plugin_id = str(payload.get("plugin_id") or "")
    if not plugin_id:
        raise ValueError("plugin_id is missing")

    worker_mcp = None
    original_mcp_loader = None
    timeout_mod = None
    configured_timeout = None
    registry = None
    session_db = None
    inline_registered = False
    callbacks_installed = False
    original_approval_callback = None
    original_sudo_callback = None
    original_secret_callback = None
    node_hook_manager = None
    original_node_hooks = None
    original_node_middleware = None
    original_registry_generation = None

    try:
        # A node worker sees only the MCP definitions carried by its immutable
        # request. Environment placeholders resolve here, after IPC, and
        # resolved values are never returned to the plugin or parent process.
        from tools import mcp_tool as worker_mcp
        from tools.registry import registry

        original_registry_generation = registry._generation

        try:
            from hermes_cli.env_loader import load_hermes_dotenv

            load_hermes_dotenv()
        except Exception:
            pass
        raw_mcp = dict(request.mcp_servers or {})
        resolved_mcp = {
            str(name): worker_mcp._interpolate_env_vars(dict(config))
            for name, config in raw_mcp.items()
        }
        resolved_mcp = _finalize_authenticated_mcp_config(resolved_mcp)
        enabled_mcp_names = {
            name
            for name, config in resolved_mcp.items()
            if worker_mcp._parse_boolish(config.get("enabled", True), default=True)
        }
        original_mcp_loader = worker_mcp._load_mcp_config
        worker_mcp._load_mcp_config = lambda: resolved_mcp

        # Bound provider calls inside this isolated process without introducing
        # a parent-visible config/env mutation or a new AIAgent constructor
        # surface.
        import hermes_cli.timeouts as timeout_mod

        configured_timeout = timeout_mod.get_provider_request_timeout

        def bounded_provider_timeout(provider: str, model_name: str) -> float:
            profile_timeout = configured_timeout(provider, model_name)
            request_timeout = float(request.provider_request_timeout_seconds)
            if profile_timeout is None:
                return request_timeout
            return min(request_timeout, float(profile_timeout))

        timeout_mod.get_provider_request_timeout = bounded_provider_timeout

        from hermes_cli.runtime_provider import (
            classify_resolved_execution_runtime,
            resolve_runtime_provider,
        )

        model = _configured_model(request.model)
        runtime = None
        if enabled_mcp_names:
            runtime = resolve_runtime_provider(
                requested=request.provider, target_model=model or None
            )
            runtime_capabilities = classify_resolved_execution_runtime(runtime)
            if not runtime_capabilities.hermes_managed_tool_loop:
                raise PackageMCPUnavailable(
                    "package_mcp_unavailable: resolved runtime does not use "
                    "Hermes' tool loop"
                )

        if enabled_mcp_names:
            try:
                worker_mcp.discover_mcp_tools()
            except Exception as exc:
                raise PackageMCPUnavailable(
                    "package_mcp_unavailable: request MCP discovery failed"
                ) from exc
            statuses = {
                str(status.get("name")): status
                for status in worker_mcp.get_mcp_status()
            }
            unavailable = sorted(
                name
                for name in enabled_mcp_names
                if statuses.get(name, {}).get("status") != "connected"
            )
            if unavailable:
                raise PackageMCPUnavailable(
                    "package_mcp_unavailable: required request MCP server(s) "
                    f"did not connect: {', '.join(unavailable)}"
                )

        # Import the agent only after the request loader is installed and
        # required MCP servers have registered their tools. Construction stays
        # below runtime classification and tool-policy validation.
        from run_agent import AIAgent

        allowed = None if request.allowed_tools is None else set(request.allowed_tools)
        denied = set(request.denied_tools) | {"delegate_task"}
        if not request.inline_agents:
            denied.add("workflow_agent")
        pending: list[dict[str, str]] = []
        approved_action_consumed = False

        def pause(descriptor: dict[str, str]) -> None:
            pending.append(descriptor)
            active = _active_agent
            if active is not None:
                active._interrupt_requested = True

        if request.inline_agents:
            from agent.plugin_agent import PluginAgentRunner

            inline_handler = _build_inline_agent_handler(
                plugin_id=plugin_id,
                definitions={
                    str(name): dict(definition)
                    for name, definition in request.inline_agents.items()
                },
                workdir=Path(request.workdir or Path.cwd()),
                parent_request=request,
                runner_factory=PluginAgentRunner,
                emit_progress=lambda **progress: _emit("progress", **progress),
                pause=pause,
            )
            registry.register(
                name="workflow_agent",
                toolset="workflow-node",
                schema={
                    "name": "workflow_agent",
                    "description": "Run one declared workflow-local inline agent synchronously.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent_id": {
                                "type": "string",
                                "enum": sorted(request.inline_agents),
                            },
                            "task": {"type": "string", "maxLength": 100000},
                        },
                        "required": ["agent_id", "task"],
                        "additionalProperties": False,
                    },
                },
                handler=lambda args, **_kwargs: json.dumps(
                    inline_handler(args), ensure_ascii=False
                ),
            )
            inline_registered = True

        def approval(command, description, **_kwargs):
            nonlocal approved_action_consumed
            descriptor = _interaction_descriptor(
                "approval",
                {"command": command, "description": description},
            )
            if (
                not approved_action_consumed
                and request.approved_action_digest == descriptor["action_digest"]
            ):
                approved_action_consumed = True
                return "once"
            _emit("interaction", interaction=descriptor)
            pause(descriptor)
            return "deny"

        def clarify(question, choices=None):
            pause(
                _interaction(
                    "clarification",
                    {"question": question, "choices": choices or []},
                )
            )
            return "Interaction paused for a host-provided answer."

        def sudo():
            pause(_interaction("sudo", {"message": "sudo credential required"}))
            return ""

        def secret(name, prompt_text, metadata):
            pause(
                _interaction(
                    "secret",
                    {
                        "name": name,
                        "prompt": prompt_text,
                        "skill": metadata.get("skill_name", ""),
                    },
                )
            )
            return {"success": False, "stored_as": name, "validated": False}

        with registry.scoped_names(allowed_names=allowed, denied_names=denied):
            from hermes_state import SessionDB

            known = set(registry._tools)
            unknown = sorted(
                (set(request.allowed_tools or ()) | set(request.denied_tools)) - known
            )
            if unknown:
                raise ValueError(f"unknown tool name(s): {', '.join(unknown)}")

            if runtime is None:
                runtime = resolve_runtime_provider(
                    requested=request.provider, target_model=model or None
                )

            session_db = SessionDB()
            history = None
            if request.context_mode == "shared":
                if session_db.get_session(request.session_id) is None:
                    raise ValueError("session_id does not identify an existing session")
                history = session_db.get_messages_as_conversation(request.session_id)

            prompt = request.prompt
            if request.skills:
                from agent.skill_commands import build_preloaded_skills_prompt

                skill_text, _loaded, missing = build_preloaded_skills_prompt(
                    list(request.skills), task_id=request.session_id
                )
                if missing:
                    raise ValueError(f"unknown skill(s): {', '.join(missing)}")
                if skill_text:
                    # Skill content is part of the new user turn, never a
                    # system prompt mutation, preserving cache and role
                    # alternation.
                    prompt = f"{skill_text}\n\n{request.prompt}"

            from tools import skills_tool, terminal_tool

            original_approval_callback = terminal_tool._get_approval_callback()
            original_sudo_callback = terminal_tool._get_sudo_password_callback()
            original_secret_callback = skills_tool._secret_capture_callback
            callbacks_installed = True
            terminal_tool.set_approval_callback(approval)
            terminal_tool.set_sudo_password_callback(sudo)
            skills_tool.set_secret_capture_callback(secret)
            agent = AIAgent(
                model=model,
                max_iterations=request.max_iterations,
                provider=runtime.get("provider"),
                base_url=runtime.get("base_url"),
                api_key=runtime.get("api_key"),
                api_mode=runtime.get("api_mode"),
                acp_command=runtime.get("command"),
                acp_args=runtime.get("args"),
                credential_pool=runtime.get("credential_pool"),
                ephemeral_system_prompt=request.ephemeral_system_prompt,
                reasoning_config=dict(request.reasoning_config or {}),
                fallback_model=(
                    {
                        "provider": runtime.get("provider"),
                        "model": request.fallback_model,
                    }
                    if request.fallback_model
                    else None
                ),
                request_overrides=dict(request.request_overrides),
                enabled_toolsets=(
                    list(request.enabled_toolsets)
                    if request.enabled_toolsets is not None
                    else None
                ),
                quiet_mode=True,
                platform="plugin-agent",
                session_id=request.session_id,
                session_db=session_db,
                clarify_callback=clarify,
            )
            agent._api_max_retries = request.max_api_attempts
            _active_agent = agent
            from hermes_cli.plugins import get_plugin_manager

            node_hook_manager = get_plugin_manager()
            original_node_hooks = {
                name: list(callbacks)
                for name, callbacks in node_hook_manager._hooks.items()
            }
            original_node_middleware = {
                name: list(callbacks)
                for name, callbacks in node_hook_manager._middleware.items()
            }
            hook_events = _install_node_hooks(request.hooks)
            if _cancel_event.is_set():
                agent._interrupt_requested = True
            visible = {
                name
                for name in registry._tools
                if (allowed is None or name in allowed) and name not in denied
            }
            agent.tools = [
                tool for tool in (agent.tools or []) if _tool_name(tool) in visible
            ]
            agent.valid_tool_names = {_tool_name(tool) for tool in agent.tools}
            if not agent.valid_tool_names <= visible:
                raise RuntimeError("agent tool scope verification failed")

            _emit("progress", phase="running", session_id=agent.session_id)
            response = agent.run_conversation(prompt, conversation_history=history)
            usage = {
                "input_tokens": int(getattr(agent, "session_input_tokens", 0) or 0),
                "output_tokens": int(getattr(agent, "session_output_tokens", 0) or 0),
                "cache_read_tokens": int(
                    getattr(agent, "session_cache_read_tokens", 0) or 0
                ),
                "cache_write_tokens": int(
                    getattr(agent, "session_cache_write_tokens", 0) or 0
                ),
            }
            failed = bool(response.get("failed"))
            return {
                "final_response": _sanitize(
                    response.get("final_response", ""), 500_000
                ),
                "session_id": str(agent.session_id or ""),
                "provider": str(agent.provider or ""),
                "model": str(agent.model or ""),
                "status": "paused"
                if pending
                else ("failed" if failed else "completed"),
                "pending_interaction": pending[0] if pending else None,
                "usage": usage,
                "audit": {
                    "plugin_id": plugin_id,
                    "tool_names": sorted(agent.valid_tool_names),
                    "api_calls": int(response.get("api_calls", 0) or 0),
                    "hook_events": hook_events,
                    "max_budget_usd": request.max_budget_usd,
                    "sandbox_policy_declared": request.sandbox_policy is not None,
                },
            }
    finally:
        _active_agent = None
        try:
            if node_hook_manager is not None and original_node_hooks is not None:
                node_hook_manager._hooks.clear()
                node_hook_manager._hooks.update(original_node_hooks)
            if node_hook_manager is not None and original_node_middleware is not None:
                node_hook_manager._middleware.clear()
                node_hook_manager._middleware.update(original_node_middleware)
        finally:
            try:
                if callbacks_installed:
                    terminal_tool.set_approval_callback(original_approval_callback)
                    terminal_tool.set_sudo_password_callback(original_sudo_callback)
                    skills_tool.set_secret_capture_callback(original_secret_callback)
            finally:
                try:
                    close_db = getattr(session_db, "close", None)
                    if callable(close_db):
                        close_db()
                finally:
                    try:
                        if inline_registered and registry is not None:
                            registry.deregister("workflow_agent")
                    finally:
                        try:
                            if worker_mcp is not None:
                                worker_mcp.shutdown_mcp_servers()
                        finally:
                            if (
                                worker_mcp is not None
                                and original_mcp_loader is not None
                            ):
                                worker_mcp._load_mcp_config = original_mcp_loader
                            if (
                                timeout_mod is not None
                                and configured_timeout is not None
                            ):
                                timeout_mod.get_provider_request_timeout = (
                                    configured_timeout
                                )
                            if (
                                registry is not None
                                and original_registry_generation is not None
                            ):
                                registry._generation = original_registry_generation


def main() -> int:
    raw = sys.stdin.buffer.readline(_MAX_REQUEST_BYTES + 1)
    if not raw or len(raw) > _MAX_REQUEST_BYTES:
        return 2
    try:
        payload = json.loads(raw)
        if (
            payload.get("protocol_version") != _PROTOCOL_VERSION
            or payload.get("type") != "run"
        ):
            raise ValueError("unsupported plugin-agent protocol frame")

        def watch_coordinator() -> None:
            # The parent keeps stdin open as a lifeline after the request. EOF
            # means it died or cancelled; interrupt the synchronous agent loop.
            sys.stdin.buffer.read(1)
            _cancel_event.set()
            agent = _active_agent
            if agent is not None:
                agent._interrupt_requested = True

        threading.Thread(
            target=watch_coordinator, name="plugin-agent-lifeline", daemon=True
        ).start()
        with redirect_stdout(sys.stderr):
            result = _run(payload)
    except BaseException as exc:
        plugin_id = ""
        try:
            plugin_id = str(payload.get("plugin_id") or "")
        except Exception:
            pass
        failure_kind = getattr(exc, "failure_kind", type(exc).__name__)
        if not isinstance(failure_kind, str) or not failure_kind:
            failure_kind = type(exc).__name__
        result = {
            "final_response": "",
            "session_id": "",
            "provider": "",
            "model": "",
            "status": "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
            "pending_interaction": None,
            "usage": {},
            "audit": {
                "plugin_id": plugin_id,
                "failure_kind": failure_kind,
                "error": _sanitize(exc),
            },
        }
    _emit("result", result=result)
    # Keep the direct worker alive until the coordinator acknowledges receipt
    # by closing its stdin lifeline. This closes the tiny result/exit race in
    # which descendants could otherwise outlive an already-reaped parent.
    _cancel_event.wait(timeout=2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
