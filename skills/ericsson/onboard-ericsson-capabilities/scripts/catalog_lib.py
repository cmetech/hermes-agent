"""Deterministic catalog generation and repository reconciliation."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml


SCHEMA_VERSION = 1
MATURITIES = {
    "available",
    "partially-ported",
    "planned-not-implemented",
    "not-supported-no-port-planned",
}
CONFIG_KINDS = {
    "static-secret",
    "static-setting",
    "interactive-sign-in",
    "permission",
    "local-software",
    "workflow-input",
}
FLOW_STATUS_TO_MATURITY = {
    "intent-ported": "available",
    "partially-ported": "partially-ported",
    "not-ported": "planned-not-implemented",
    "not-supported-no-port-planned": "not-supported-no-port-planned",
}
ENTRY_REQUIRED = {
    "id",
    "display_name",
    "aliases",
    "goals",
    "maturity",
    "recommendation_eligible",
    "source_flows",
    "implementation",
    "platforms",
    "configuration",
    "reads",
    "writes",
    "artifacts",
    "demonstrations",
    "troubleshooting",
}
ENTRY_DIR = (
    "skills/ericsson/onboard-ericsson-capabilities/references/capabilities"
)

_ENTRY_ALLOWED = ENTRY_REQUIRED
_IMPLEMENTATION_KEYS = {
    "skills",
    "plugins",
    "mcp_servers",
    "workflows",
    "tools",
}
_CONFIG_REQUIRED = {"name", "kind", "required", "guidance"}
_PLATFORMS = {"linux", "macos", "windows"}
_LIST_FIELDS = {
    "aliases",
    "goals",
    "source_flows",
    "platforms",
    "reads",
    "writes",
    "artifacts",
    "demonstrations",
    "troubleshooting",
}
_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)\b(?:value|token|password|secret|api[_ -]?key)\s*[:=]\s*\S+"
)
_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
_WORKFLOW_PROMPT_TOOL = re.compile(
    r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\s+tool\b"
)
_WORKFLOW_INVOKED_TOOL = re.compile(
    r"(?i)\b(?:call|use|run|invoke)\s+(?:the\s+)?`?"
    r"([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b`?"
)
_GENERIC_ENVIRONMENT = {
    "APPDATA",
    "HERMES_HOME",
    "HOME",
    "LOCALAPPDATA",
    "PATH",
    "PYTHONPATH",
    "TEMP",
    "TMP",
}
_PATH_SUFFIXES = {".md", ".yml", ".yaml", ".json", ".py", ".txt"}


class CatalogError(ValueError):
    """Raised when catalog source data violates the authoring contract."""


def _relative_label(path: Path) -> str:
    return path.as_posix()


def _load_yaml_mapping(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise CatalogError(f"{path}: invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogError(f"{path}: {label} must be a mapping")
    return value


def read_frontmatter(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CatalogError(f"{path}: cannot read frontmatter: {exc}") from exc
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise CatalogError(f"{path}: missing YAML frontmatter")
    closing_index = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.rstrip("\r\n") == "---"
        ),
        None,
    )
    if closing_index is None:
        raise CatalogError(f"{path}: missing YAML frontmatter closing delimiter")
    try:
        value = yaml.safe_load("".join(lines[1:closing_index]))
    except yaml.YAMLError as exc:
        raise CatalogError(f"{path}: invalid YAML frontmatter: {exc}") from exc
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise CatalogError(f"{path}: frontmatter must be a mapping")
    return value


def load_entries(repo: Path) -> list[dict]:
    paths = sorted((repo / ENTRY_DIR).glob("*.md"))
    entries = [read_frontmatter(path) | {"_path": path} for path in paths]
    validate_entry_shapes(entries)
    return sorted(entries, key=lambda entry: entry["id"])


def build_catalog(repo: Path) -> dict:
    entries = load_entries(repo)
    manifest_path = repo / "sets/ericsson.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"{manifest_path}: invalid manifest: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("version"), str):
        raise CatalogError(f"{manifest_path}: manifest version must be a string")
    items = [compact_entry(entry, repo) for entry in entries]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "catalogVersion": manifest["version"],
        "capabilities": sorted(items, key=lambda item: item["id"]),
    }


def validate_repository(repo: Path, entries: list[dict]) -> list[str]:
    inventory = collect_repository_inventory(repo)
    represented = collect_entry_inventory(entries)
    problems = compare_inventories(inventory, represented)
    problems.extend(validate_flow_maturity(repo, entries))
    problems.extend(validate_configuration_names(repo, entries))
    problems.extend(validate_entry_paths(repo, entries))
    problems.extend(inventory["problems"])
    return sorted(set(problems))


def serialize_catalog(catalog: dict) -> str:
    return json.dumps(catalog, indent=2, sort_keys=True) + "\n"


def _expect_string_list(entry_id: str, field: str, value: Any) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CatalogError(f"entry {entry_id}: {field} must be a list of strings")


def _is_unsafe_reference(value: str) -> bool:
    posix = PurePosixPath(value.replace("\\", "/"))
    windows = PureWindowsPath(value)
    return posix.is_absolute() or windows.is_absolute() or ".." in posix.parts


def validate_entry_shapes(entries: list[dict]) -> None:
    seen_ids: set[str] = set()
    for entry in entries:
        path = entry.get("_path", "<entry>")
        public_keys = set(entry) - {"_path"}
        unknown = sorted(public_keys - _ENTRY_ALLOWED)
        if unknown:
            raise CatalogError(f"{path}: unknown fields: {', '.join(unknown)}")
        missing = sorted(ENTRY_REQUIRED - public_keys)
        if missing:
            raise CatalogError(f"{path}: missing required fields: {', '.join(missing)}")

        entry_id = entry["id"]
        if not isinstance(entry_id, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", entry_id):
            raise CatalogError(f"{path}: id must be a lowercase slug")
        if entry_id in seen_ids:
            raise CatalogError(f"duplicate entry id: {entry_id}")
        seen_ids.add(entry_id)
        if isinstance(path, Path) and path.stem != entry_id:
            raise CatalogError(
                f"{path}: entry id must match filename: expected {path.stem}, got {entry_id}"
            )

        if not isinstance(entry["display_name"], str) or not entry["display_name"].strip():
            raise CatalogError(f"entry {entry_id}: display_name must be a non-empty string")
        for field in sorted(_LIST_FIELDS):
            _expect_string_list(entry_id, field, entry[field])
        if not entry["goals"]:
            raise CatalogError(f"entry {entry_id}: goals must not be empty")

        maturity = entry["maturity"]
        if maturity not in MATURITIES:
            raise CatalogError(f"entry {entry_id}: unknown maturity: {maturity}")
        eligible = entry["recommendation_eligible"]
        if not isinstance(eligible, bool):
            raise CatalogError(
                f"entry {entry_id}: recommendation_eligible must be a boolean"
            )
        if eligible and maturity != "available":
            raise CatalogError(
                f"entry {entry_id}: {maturity} cannot be recommendation eligible"
            )

        unknown_platforms = sorted(set(entry["platforms"]) - _PLATFORMS)
        if unknown_platforms:
            raise CatalogError(
                f"entry {entry_id}: unknown platform: {', '.join(unknown_platforms)}"
            )

        implementation = entry["implementation"]
        if not isinstance(implementation, dict):
            raise CatalogError(f"entry {entry_id}: implementation must be a mapping")
        unknown_implementation = sorted(set(implementation) - _IMPLEMENTATION_KEYS)
        if unknown_implementation:
            raise CatalogError(
                f"entry {entry_id}: unknown implementation fields: "
                + ", ".join(unknown_implementation)
            )
        for field, value in implementation.items():
            _expect_string_list(entry_id, f"implementation.{field}", value)
        if maturity == "available" and not any(implementation.values()):
            raise CatalogError(
                f"entry {entry_id}: available entry must reference an implementation"
            )

        configuration = entry["configuration"]
        if not isinstance(configuration, list):
            raise CatalogError(f"entry {entry_id}: configuration must be a list")
        config_names: set[str] = set()
        for index, item in enumerate(configuration):
            label = f"entry {entry_id}: configuration[{index}]"
            if not isinstance(item, dict):
                raise CatalogError(f"{label} must be a mapping")
            missing_config = sorted(_CONFIG_REQUIRED - set(item))
            if missing_config:
                raise CatalogError(
                    f"{label}: configuration item missing required fields: "
                    + ", ".join(missing_config)
                )
            unknown_config = sorted(set(item) - _CONFIG_REQUIRED)
            if unknown_config:
                raise CatalogError(
                    f"{label}: unknown configuration fields: "
                    + ", ".join(unknown_config)
                )
            name = item["name"]
            if not isinstance(name, str) or not name.strip():
                raise CatalogError(f"{label}: name must be a non-empty string")
            if name in config_names:
                raise CatalogError(f"entry {entry_id}: duplicate configuration name: {name}")
            config_names.add(name)
            if item["kind"] not in CONFIG_KINDS:
                raise CatalogError(f"{label}: unknown configuration kind: {item['kind']}")
            if not isinstance(item["required"], bool):
                raise CatalogError(f"{label}: required must be a boolean")
            guidance = item["guidance"]
            if not isinstance(guidance, str) or not guidance.strip():
                raise CatalogError(f"{label}: guidance must be a non-empty string")
            if item["kind"] == "static-secret" and _SECRET_VALUE_PATTERN.search(guidance):
                raise CatalogError(f"{label}: secret guidance must not contain a value")

        references = list(entry["source_flows"])
        for values in implementation.values():
            references.extend(values)
        for reference in references:
            if _is_unsafe_reference(reference):
                raise CatalogError(f"entry {entry_id}: unsafe reference: {reference}")


def compact_entry(entry: dict, repo: Path) -> dict:
    del repo  # The entry pointer is deliberately relative to the bundled skill.
    path = Path(entry["_path"])
    return {
        "id": entry["id"],
        "displayName": entry["display_name"],
        "aliases": entry["aliases"],
        "goals": entry["goals"],
        "maturity": entry["maturity"],
        "recommendationEligible": entry["recommendation_eligible"],
        "entry": f"references/capabilities/{path.name}",
    }


def _manifest_list(manifest: dict[str, Any], key: str, path: Path) -> set[str]:
    value = manifest.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CatalogError(f"{path}: {key} must be a list of strings")
    return set(value)


def _string_list_metadata(
    metadata: dict[str, Any], key: str, path: Path, problems: list[str]
) -> set[str]:
    value = metadata.get(key, [])
    if value is None:
        return set()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        problems.append(f"invalid metadata list: {_relative_label(path)}: {key}")
        return set()
    return set(value)


def _parse_python(path: Path, problems: list[str]) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        problems.append(f"invalid plugin Python: {_relative_label(path)}: {exc}")
        return None


def _assigned_dicts(tree: ast.Module, variable: str) -> list[ast.Dict]:
    values: list[ast.Dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if isinstance(value, ast.Dict) and any(
            isinstance(target, ast.Name) and target.id == variable for target in targets
        ):
            values.append(value)
    return values


def _literal_dict_keys(value: ast.Dict) -> set[str]:
    return {
        key.value
        for key in value.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _schema_contract(tree: ast.Module) -> tuple[set[str], dict[str, str]]:
    tools: set[str] = set()
    schema_names: dict[str, str] = {}
    for schemas in _assigned_dicts(tree, "SCHEMAS"):
        for key, value in zip(schemas.keys, schemas.values):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                continue
            tools.add(key.value)
            if isinstance(value, ast.Dict):
                for field, field_value in zip(value.keys, value.values):
                    if (
                        isinstance(field, ast.Constant)
                        and field.value == "name"
                        and isinstance(field_value, ast.Constant)
                        and isinstance(field_value.value, str)
                    ):
                        schema_names[key.value] = field_value.value
    return tools, schema_names


def _registered_tools(tree: ast.Module, schema_tools: set[str]) -> set[str]:
    registered: set[str] = set()
    loops_over_schemas = any(
        isinstance(node, ast.For)
        and isinstance(node.iter, ast.Call)
        and isinstance(node.iter.func, ast.Attribute)
        and node.iter.func.attr == "items"
        and isinstance(node.iter.func.value, ast.Attribute)
        and node.iter.func.value.attr == "SCHEMAS"
        for node in ast.walk(tree)
    )
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register_tool"
        ):
            continue
        for keyword in node.keywords:
            if keyword.arg != "name":
                continue
            if isinstance(keyword.value, ast.Constant) and isinstance(
                keyword.value.value, str
            ):
                registered.add(keyword.value.value)
            elif isinstance(keyword.value, ast.Name) and loops_over_schemas:
                registered.update(schema_tools)
    return registered


def _environment_accesses(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args:
            function = node.func
            is_environ_get = (
                isinstance(function, ast.Attribute)
                and function.attr == "get"
                and isinstance(function.value, ast.Attribute)
                and function.value.attr == "environ"
                and isinstance(function.value.value, ast.Name)
                and function.value.value.id == "os"
            )
            is_getenv = (
                isinstance(function, ast.Attribute)
                and function.attr == "getenv"
                and isinstance(function.value, ast.Name)
                and function.value.id == "os"
            )
            if (is_environ_get or is_getenv) and isinstance(
                node.args[0], ast.Constant
            ) and isinstance(node.args[0].value, str):
                names.add(node.args[0].value)
        if isinstance(node, ast.Subscript):
            target = node.value
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "environ"
                and isinstance(target.value, ast.Name)
                and target.value.id == "os"
            ):
                key = node.slice
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    names.add(key.value)
    return names - _GENERIC_ENVIRONMENT


def _mcp_runtime_contract(
    root: Path, problems: list[str]
) -> tuple[set[str], set[str]]:
    listed: set[str] = set()
    dispatched: set[str] = set()
    for python_file in sorted(root.rglob("*.py")):
        tree = _parse_python(python_file, problems)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Tool"
            ):
                for keyword in node.keywords:
                    if (
                        keyword.arg == "name"
                        and isinstance(keyword.value, ast.Constant)
                        and isinstance(keyword.value.value, str)
                    ):
                        listed.add(keyword.value.value)
        for function in (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "call_tool"
        ):
            for node in ast.walk(function):
                if not (
                    isinstance(node, ast.Compare)
                    and isinstance(node.left, ast.Name)
                    and node.left.id == "name"
                ):
                    continue
                for comparator in node.comparators:
                    if isinstance(comparator, ast.Constant) and isinstance(
                        comparator.value, str
                    ):
                        dispatched.add(comparator.value)
    return listed, dispatched


def _workflow_prompt_tools(prompt: str, input_names: set[str]) -> set[str]:
    """Extract only explicit tool grammar and invocation-context identifiers."""
    names = set(_WORKFLOW_PROMPT_TOOL.findall(prompt))
    names.update(_WORKFLOW_INVOKED_TOOL.findall(prompt))
    return {name.lower() for name in names} - input_names


def collect_repository_inventory(repo: Path) -> dict[str, set[str] | list[str]]:
    manifest_path = repo / "sets/ericsson.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"{manifest_path}: invalid manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise CatalogError(f"{manifest_path}: manifest must be a mapping")

    manifest_skills = _manifest_list(manifest, "skills", manifest_path)
    manifest_plugins = _manifest_list(manifest, "plugins", manifest_path)
    manifest_mcp_local = _manifest_list(manifest, "mcpLocal", manifest_path)
    manifest_workflows = _manifest_list(manifest, "workflows", manifest_path)
    workflow_core_tools = _manifest_list(
        manifest, "workflowCoreTools", manifest_path
    )
    problems: list[str] = []

    actual_skills: set[str] = set()
    skill_names: set[str] = set()
    for skill_file in sorted((repo / "skills/ericsson").glob("*/SKILL.md")):
        relative = skill_file.parent.relative_to(repo).as_posix()
        metadata = read_frontmatter(skill_file)
        name = metadata.get("name")
        actual_skills.add(relative)
        if isinstance(name, str):
            skill_names.add(name)
            if name != skill_file.parent.name:
                problems.append(
                    f"skill name mismatch: {relative}: expected {skill_file.parent.name}, got {name}"
                )
        else:
            problems.append(f"missing skill name: {relative}")

    actual_plugins: set[str] = set()
    plugin_names: set[str] = set()
    tools: set[str] = set()
    plugin_tools: dict[str, set[str]] = {}
    configuration: set[str] = set()
    implementation_configuration: set[str] = set()
    required_configuration: set[str] = set()
    optional_configuration: set[str] = set()
    for plugin_file in sorted((repo / "plugins").glob("*/plugin.yaml")):
        relative = plugin_file.parent.relative_to(repo).as_posix()
        metadata = _load_yaml_mapping(plugin_file, label="plugin metadata")
        actual_plugins.add(relative)
        name = metadata.get("name")
        if isinstance(name, str):
            plugin_names.add(name)
            if name != plugin_file.parent.name:
                problems.append(
                    f"plugin name mismatch: {relative}: expected {plugin_file.parent.name}, got {name}"
                )
        else:
            problems.append(f"missing plugin name: {relative}")
        declared_tools = _string_list_metadata(
            metadata, "provides_tools", plugin_file, problems
        )
        plugin_required = _string_list_metadata(
            metadata, "requires_env", plugin_file, problems
        )
        plugin_optional = _string_list_metadata(
            metadata, "optional_env", plugin_file, problems
        )
        configuration.update(plugin_required)
        configuration.update(plugin_optional)
        required_configuration.update(plugin_required)
        optional_configuration.update(plugin_optional)

        python_trees: list[tuple[Path, ast.Module]] = []
        for python_file in sorted(plugin_file.parent.glob("*.py")):
            tree = _parse_python(python_file, problems)
            if tree is not None:
                python_trees.append((python_file, tree))
        schema_tools: set[str] = set()
        schema_names: dict[str, str] = {}
        implementation_environment: set[str] = set()
        for _python_file, tree in python_trees:
            file_tools, file_schema_names = _schema_contract(tree)
            schema_tools.update(file_tools)
            schema_names.update(file_schema_names)
            implementation_environment.update(_environment_accesses(tree))
        init_tree = next(
            (tree for path, tree in python_trees if path.name == "__init__.py"),
            None,
        )
        handler_tools: set[str] = set()
        registered_tools: set[str] = set()
        if init_tree is not None:
            for handlers in _assigned_dicts(init_tree, "handlers"):
                handler_tools.update(_literal_dict_keys(handlers))
            registered_tools = _registered_tools(init_tree, schema_tools)

        for tool in sorted(declared_tools - schema_tools):
            problems.append(
                f"plugin tool declaration not registered: {relative}: {tool}"
            )
        for tool in sorted(schema_tools - declared_tools):
            problems.append(f"undeclared runtime plugin tool: {relative}: {tool}")
        for tool in sorted(schema_tools - handler_tools):
            problems.append(f"plugin tool missing handler: {relative}: {tool}")
        for tool in sorted(handler_tools - schema_tools):
            problems.append(f"plugin handler missing schema: {relative}: {tool}")
        for tool in sorted(schema_tools - registered_tools):
            problems.append(f"plugin tool not runtime-registered: {relative}: {tool}")
        for tool, schema_name in sorted(schema_names.items()):
            if schema_name != tool:
                problems.append(
                    f"plugin schema name mismatch: {relative}: {tool} != {schema_name}"
                )
        declared_environment = plugin_required | plugin_optional
        for env_name in sorted(implementation_environment - declared_environment):
            problems.append(
                f"undeclared implementation configuration: {relative}: {env_name}"
            )
        for env_name in sorted(declared_environment - implementation_environment):
            problems.append(
                f"unused plugin configuration declaration: {relative}: {env_name}"
            )
        if isinstance(name, str):
            plugin_tools[name] = schema_tools
        implementation_configuration.update(implementation_environment)
        tools.update(schema_tools)

    mcp_config = manifest.get("mcpServers")
    if not isinstance(mcp_config, str) or _is_unsafe_reference(mcp_config):
        raise CatalogError(f"{manifest_path}: mcpServers must be a safe relative path")
    mcp_path = repo / mcp_config
    mcp_metadata = _load_yaml_mapping(mcp_path, label="MCP metadata")
    mcp_servers_value = mcp_metadata.get("mcp_servers")
    if not isinstance(mcp_servers_value, dict):
        raise CatalogError(f"{mcp_path}: mcp_servers must be a mapping")
    mcp_servers = set(mcp_servers_value)
    if any(not isinstance(name, str) for name in mcp_servers):
        raise CatalogError(f"{mcp_path}: MCP server names must be strings")
    mcp_local_servers: dict[str, set[str]] = {
        path: set() for path in manifest_mcp_local
    }
    for server_name, server in mcp_servers_value.items():
        if not isinstance(server, dict):
            problems.append(f"invalid MCP server registration: {server_name}")
            continue
        serialized = yaml.safe_dump(server)
        server_environment = {
            name
            for name in _ENV_PLACEHOLDER.findall(serialized)
            if name != "CAPABILITY_DIR"
        }
        configuration.update(server_environment)
        required_configuration.update(server_environment)
        implementation_configuration.update(server_environment)
        if isinstance(server, dict):
            for local_path in manifest_mcp_local:
                local_name = Path(local_path).name
                local_pattern = re.compile(
                    rf"\$\{{CAPABILITY_DIR\}}[/\\]+"
                    rf"{re.escape(local_name)}(?:[/\\]|$)"
                )
                executable_values: list[str] = []
                for field in ("command", "args", "cwd", "working_directory"):
                    value = server.get(field)
                    if isinstance(value, str):
                        executable_values.append(value)
                    elif isinstance(value, list):
                        executable_values.extend(
                            item for item in value if isinstance(item, str)
                        )
                if any(local_pattern.search(value) for value in executable_values):
                    mcp_local_servers[local_path].add(server_name)

    mcp_local_tools: dict[str, set[str]] = {}
    mcp_server_tools: dict[str, set[str]] = {name: set() for name in mcp_servers}
    for local_path in sorted(manifest_mcp_local):
        listed, dispatched = _mcp_runtime_contract(repo / local_path, problems)
        mcp_local_tools[local_path] = listed
        for tool in sorted(listed - dispatched):
            problems.append(f"local MCP tool missing dispatcher: {local_path}: {tool}")
        for tool in sorted(dispatched - listed):
            problems.append(f"local MCP dispatcher missing schema: {local_path}: {tool}")
        for server_name in mcp_local_servers.get(local_path, set()):
            mcp_server_tools[server_name].update(listed)

    actual_workflows: set[str] = set()
    workflow_names: set[str] = set()
    workflow_inputs: dict[str, dict[str, bool]] = {}
    workflow_toolsets: dict[str, set[str]] = {}
    workflow_mcp_servers: dict[str, set[str]] = {}
    workflow_tool_nodes: dict[
        str, list[tuple[str, set[str], set[str], str]]
    ] = {}
    for workflow_file in sorted((repo / "workflows").glob("*.yml")) + sorted(
        (repo / "workflows").glob("*.yaml")
    ):
        relative = workflow_file.relative_to(repo).as_posix()
        metadata = _load_yaml_mapping(workflow_file, label="workflow metadata")
        actual_workflows.add(relative)
        name = metadata.get("name")
        if isinstance(name, str):
            workflow_names.add(name)
            if name != workflow_file.stem:
                problems.append(
                    f"workflow name mismatch: {relative}: expected {workflow_file.stem}, got {name}"
                )
        else:
            problems.append(f"missing workflow name: {relative}")
        requires = metadata.get("requires", {})
        if not isinstance(requires, dict):
            problems.append(f"invalid workflow requires: {relative}")
        else:
            workflow_required = _string_list_metadata(
                requires, "env", workflow_file, problems
            )
            configuration.update(workflow_required)
            required_configuration.update(workflow_required)
            implementation_configuration.update(workflow_required)
            workflow_toolsets[relative] = _string_list_metadata(
                requires, "toolsets", workflow_file, problems
            )
            workflow_mcp_servers[relative] = _string_list_metadata(
                requires, "mcp_servers", workflow_file, problems
            )
        inputs = metadata.get("inputs", [])
        parsed_inputs: dict[str, bool] = {}
        if not isinstance(inputs, list):
            problems.append(f"invalid workflow inputs: {relative}")
        else:
            for index, item in enumerate(inputs):
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    problems.append(f"invalid workflow input: {relative}: inputs[{index}]")
                    continue
                input_name = item["name"]
                if input_name in parsed_inputs:
                    problems.append(f"duplicate workflow input: {relative}: {input_name}")
                    continue
                parsed_inputs[input_name] = "default" not in item
        workflow_inputs[relative] = parsed_inputs
        parsed_tool_nodes: list[tuple[str, set[str], set[str], str]] = []
        nodes = metadata.get("nodes", [])
        if isinstance(nodes, list):
            for index, node in enumerate(nodes):
                if not isinstance(node, dict) or node.get("kind") != "tool":
                    continue
                node_id = node.get("id")
                if not isinstance(node_id, str):
                    node_id = f"nodes[{index}]"
                node_tools = _string_list_metadata(
                    node, "tools", workflow_file, problems
                )
                if not node_tools:
                    problems.append(
                        f"workflow tool node missing tools: {relative}: {node_id}"
                    )
                prompt_value = node.get("prompt", "")
                prompt = prompt_value if isinstance(prompt_value, str) else ""
                prompt_tools = _workflow_prompt_tools(prompt, set(parsed_inputs))
                parsed_tool_nodes.append((node_id, node_tools, prompt_tools, prompt))
        workflow_tool_nodes[relative] = parsed_tool_nodes

    env = manifest.get("env", [])
    if not isinstance(env, list):
        raise CatalogError(f"{manifest_path}: env must be a list")
    manifest_environment: set[str] = set()
    for index, item in enumerate(env):
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            problems.append(f"invalid manifest env item: env[{index}]")
        else:
            manifest_environment.add(item["key"])
            configuration.add(item["key"])

    for name in sorted((required_configuration | optional_configuration) - manifest_environment):
        problems.append(f"configuration missing from manifest env: {name}")
    for name in sorted(manifest_environment - implementation_configuration):
        problems.append(f"unused manifest environment: {name}")
    for workflow, toolsets in sorted(workflow_toolsets.items()):
        for toolset in sorted(toolsets - plugin_names):
            problems.append(f"unknown workflow toolset: {workflow}: {toolset}")
    for workflow, servers in sorted(workflow_mcp_servers.items()):
        for server in sorted(servers - mcp_servers):
            problems.append(f"unknown workflow MCP server: {workflow}: {server}")
    for workflow, nodes in sorted(workflow_tool_nodes.items()):
        available_tools = set(workflow_core_tools)
        for toolset in workflow_toolsets.get(workflow, set()):
            available_tools.update(plugin_tools.get(toolset, set()))
        for server in workflow_mcp_servers.get(workflow, set()):
            available_tools.update(mcp_server_tools.get(server, set()))
        for node_id, node_tools, prompt_tools, prompt in nodes:
            for tool in sorted(node_tools - available_tools):
                problems.append(
                    f"unknown workflow tool: {workflow}: {node_id}: {tool}"
                )
            for tool in sorted(prompt_tools - node_tools):
                problems.append(
                    f"undeclared workflow prompt tool: {workflow}: {node_id}: {tool}"
                )
            for tool in sorted(node_tools):
                if not re.search(rf"\b{re.escape(tool)}\b", prompt):
                    problems.append(
                        f"workflow tool absent from prompt: {workflow}: {node_id}: {tool}"
                    )

    flows = {
        path.relative_to(repo).as_posix()
        for path in sorted((repo / "docs/flows").glob("*.md"))
        if not path.name.startswith("_")
    }
    return {
        "manifest_skills": manifest_skills,
        "manifest_plugins": manifest_plugins,
        "manifest_mcp_local": manifest_mcp_local,
        "manifest_workflows": manifest_workflows,
        "actual_skills": actual_skills,
        "actual_plugins": actual_plugins,
        "actual_mcp_local": {
            path.parent.relative_to(repo).as_posix()
            for path in sorted((repo / "mcp").glob("*/run_server.py"))
        },
        "actual_workflows": actual_workflows,
        "skill_names": skill_names,
        "plugin_names": plugin_names,
        "mcp_servers": mcp_servers,
        "mcp_local_servers": mcp_local_servers,
        "mcp_local_tools": mcp_local_tools,
        "mcp_server_tools": mcp_server_tools,
        "workflow_names": workflow_names,
        "workflow_inputs": workflow_inputs,
        "workflow_toolsets": workflow_toolsets,
        "workflow_mcp_servers": workflow_mcp_servers,
        "workflow_tool_nodes": workflow_tool_nodes,
        "workflow_core_tools": workflow_core_tools,
        "tools": tools,
        "configuration": configuration,
        "required_configuration": required_configuration,
        "optional_configuration": optional_configuration,
        "manifest_environment": manifest_environment,
        "implementation_configuration": implementation_configuration,
        "flows": flows,
        "problems": problems,
    }


def collect_entry_inventory(entries: list[dict]) -> dict[str, set[str]]:
    represented: dict[str, set[str]] = {
        "skills": set(),
        "plugins": set(),
        "mcp_servers": set(),
        "workflows": set(),
        "tools": set(),
        "flows": set(),
        "configuration": set(),
    }
    for entry in entries:
        implementation = entry["implementation"]
        for key in _IMPLEMENTATION_KEYS:
            represented[key].update(implementation.get(key, []))
        represented["flows"].update(entry["source_flows"])
        represented["configuration"].update(
            item["name"] for item in entry["configuration"]
        )
    return represented


def compare_inventories(
    inventory: dict[str, set[str] | list[str]], represented: dict[str, set[str]]
) -> list[str]:
    problems: list[str] = []
    comparisons = (
        ("manifest_skills", "skills", "unrepresented manifest skill"),
        ("manifest_plugins", "plugins", "unrepresented manifest plugin"),
        ("manifest_workflows", "workflows", "unrepresented manifest workflow"),
        ("mcp_servers", "mcp_servers", "unrepresented MCP server"),
        ("tools", "tools", "unrepresented plugin tool"),
        ("flows", "flows", "unrepresented flow"),
    )
    for source_key, represented_key, message in comparisons:
        for item in sorted(set(inventory[source_key]) - represented[represented_key]):
            problems.append(f"{message}: {item}")
    for local_path in sorted(set(inventory["manifest_mcp_local"])):
        bound_servers = inventory["mcp_local_servers"].get(local_path, set())
        if not bound_servers.intersection(represented["mcp_servers"]):
            problems.append(f"unrepresented manifest local MCP: {local_path}")

    known = (
        ("actual_skills", "skills", "unknown entry skill"),
        ("actual_plugins", "plugins", "unknown entry plugin"),
        ("actual_workflows", "workflows", "unknown entry workflow"),
        ("mcp_servers", "mcp_servers", "unknown entry MCP server"),
        ("tools", "tools", "unknown entry tool"),
        ("flows", "flows", "unknown entry flow"),
    )
    for source_key, represented_key, message in known:
        for item in sorted(represented[represented_key] - set(inventory[source_key])):
            problems.append(f"{message}: {item}")

    manifest_actual = (
        ("manifest_skills", "actual_skills", "missing manifest skill path"),
        ("manifest_plugins", "actual_plugins", "missing manifest plugin path"),
        ("manifest_mcp_local", "actual_mcp_local", "missing manifest local MCP path"),
        ("manifest_workflows", "actual_workflows", "missing manifest workflow path"),
    )
    for manifest_key, actual_key, message in manifest_actual:
        for item in sorted(set(inventory[manifest_key]) - set(inventory[actual_key])):
            problems.append(f"{message}: {item}")
    unpackaged = (
        ("actual_skills", "manifest_skills", "unpackaged repository skill"),
        ("actual_plugins", "manifest_plugins", "unpackaged repository plugin"),
        ("actual_mcp_local", "manifest_mcp_local", "unpackaged repository local MCP"),
        ("actual_workflows", "manifest_workflows", "unpackaged repository workflow"),
    )
    for actual_key, manifest_key, message in unpackaged:
        for item in sorted(set(inventory[actual_key]) - set(inventory[manifest_key])):
            problems.append(f"{message}: {item}")
    return problems


def _implementation_names(entry: dict) -> set[str]:
    implementation = entry["implementation"]
    names: set[str] = set(implementation.get("mcp_servers", []))
    names.update(
        f"{name}-mcp"
        for name in implementation.get("mcp_servers", [])
        if not name.endswith("-mcp")
    )
    names.update(implementation.get("tools", []))
    suffixes = {
        "skills": "skill",
        "plugins": "plugin",
        "workflows": "workflow",
    }
    for key, suffix in suffixes.items():
        for value in implementation.get(key, []):
            stem = Path(value).stem
            names.add(stem)
            names.add(f"{stem}-{suffix}")
    return names


def validate_flow_maturity(repo: Path, entries: list[dict]) -> list[str]:
    problems: list[str] = []
    by_flow: dict[str, list[dict]] = {}
    for entry in entries:
        for flow in entry["source_flows"]:
            by_flow.setdefault(flow, []).append(entry)

    flow_paths = sorted(
        path
        for path in (repo / "docs/flows").glob("*.md")
        if not path.name.startswith("_")
    )
    for path in flow_paths:
        relative = path.relative_to(repo).as_posix()
        flow_entries = by_flow.get(relative, [])
        metadata = read_frontmatter(path)
        status = metadata.get("status")
        expected = FLOW_STATUS_TO_MATURITY.get(status) if isinstance(status, str) else None
        if expected is None:
            problems.append(f"unknown flow status: {relative}: {status}")
        target_artifacts = metadata.get("target_artifacts")
        platforms = metadata.get("platforms")
        if not isinstance(target_artifacts, list) or any(
            not isinstance(item, str) for item in target_artifacts
        ):
            problems.append(f"invalid flow target_artifacts: {relative}")
            target_artifacts = []
        if not isinstance(platforms, list) or any(
            not isinstance(item, str) for item in platforms
        ):
            problems.append(f"invalid flow platforms: {relative}")
            platforms = []

        for entry in flow_entries:
            if expected is not None and entry["maturity"] != expected:
                problems.append(
                    f"flow maturity mismatch: {relative}: {status} requires {expected}, "
                    f"entry {entry['id']} has {entry['maturity']}"
                )
            missing_platforms = sorted(set(platforms) - set(entry["platforms"]))
            if missing_platforms:
                problems.append(
                    f"flow platform mismatch: {relative}: entry {entry['id']} "
                    f"does not cover {', '.join(missing_platforms)}"
                )
        if expected == "available":
            implementation_names: set[str] = set()
            for entry in flow_entries:
                implementation_names.update(_implementation_names(entry))
            for target in sorted(set(target_artifacts) - implementation_names):
                problems.append(
                    f"unrepresented flow target artifact: {relative}: {target}"
                )
    return problems


def validate_configuration_names(repo: Path, entries: list[dict]) -> list[str]:
    inventory = collect_repository_inventory(repo)
    represented = collect_entry_inventory(entries)
    problems = [
        f"unrepresented configuration: {name}"
        for name in sorted(
            set(inventory["configuration"]) - represented["configuration"]
        )
    ]
    required = set(inventory["required_configuration"])
    optional = set(inventory["optional_configuration"])
    authoritative_static = set(inventory["configuration"])
    for name in sorted(required & optional):
        problems.append(f"conflicting configuration requiredness: {name}")
    for entry in entries:
        entry_workflows = entry["implementation"].get("workflows", [])
        authoritative_inputs: dict[str, bool] = {}
        for workflow in entry_workflows:
            authoritative_inputs.update(inventory["workflow_inputs"].get(workflow, {}))
        represented_inputs = {
            item["name"]: item
            for item in entry["configuration"]
            if item["kind"] == "workflow-input"
        }
        for name in sorted(set(authoritative_inputs) - set(represented_inputs)):
            problems.append(f"unrepresented workflow input: entry {entry['id']}: {name}")
        for name in sorted(set(represented_inputs) - set(authoritative_inputs)):
            problems.append(f"unknown workflow input: entry {entry['id']}: {name}")
        for item in entry["configuration"]:
            name = item["name"]
            if item["kind"] in {"static-secret", "static-setting"} and (
                name not in authoritative_static
            ):
                problems.append(
                    f"unknown onboarding configuration: entry {entry['id']}: {name}"
                )
            if name in required and not item["required"]:
                problems.append(
                    f"configuration requiredness mismatch: entry {entry['id']}: "
                    f"{name} must set required: true"
                )
            if name in optional and item["required"]:
                problems.append(
                    f"configuration requiredness mismatch: entry {entry['id']}: "
                    f"{name} must set required: false"
                )
            if item["kind"] == "workflow-input" and name in authoritative_inputs:
                input_required = authoritative_inputs[name]
                if input_required != item["required"]:
                    problems.append(
                        f"workflow input requiredness mismatch: entry {entry['id']}: "
                        f"{name} must set required: {str(input_required).lower()}"
                    )
    return problems


def validate_entry_paths(repo: Path, entries: list[dict]) -> list[str]:
    problems: list[str] = []
    for entry in entries:
        entry_id = entry["id"]
        implementation = entry["implementation"]
        references = list(entry["source_flows"])
        for field in ("skills", "plugins", "workflows"):
            references.extend(implementation.get(field, []))
        for reference in references:
            if _is_unsafe_reference(reference):
                problems.append(f"unsafe entry path: {entry_id}: {reference}")
            elif not (repo / reference).exists():
                problems.append(f"missing entry path: {entry_id}: {reference}")
        for artifact in entry["artifacts"]:
            if _is_unsafe_reference(artifact):
                problems.append(f"unsafe entry path: {entry_id}: {artifact}")
            elif "/" in artifact or Path(artifact).suffix.lower() in _PATH_SUFFIXES:
                if not (repo / artifact).exists():
                    problems.append(f"missing entry path: {entry_id}: {artifact}")
    return problems
