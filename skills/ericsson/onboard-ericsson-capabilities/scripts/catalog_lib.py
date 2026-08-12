"""Deterministic catalog generation and repository reconciliation."""

from __future__ import annotations

import ast
import json
import math
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any

import yaml

import bounded_source


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
ENTRY_DIR = "skills/ericsson/onboard-ericsson-capabilities/references/capabilities"

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
_BUILTIN_BACKEND_PLUGINS = frozenset({"plugins/workflow"})
_BUILTIN_WORKFLOW_REPLACED_SKILLS = frozenset(
    {
        "skills/ericsson/workflow-builder",
        "skills/ericsson/workflow-orchestrator",
    }
)
_BUILTIN_WORKFLOW_SKILLS = frozenset(
    {
        "skills/productivity/workflow",
        "skills/software-development/workflow-builder",
    }
)
_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
_WORKFLOW_PROMPT_TOOL = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\s+tool\b")
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
_WORKFLOW_SIDECAR_FIELDS = frozenset(
    {
        "language_compatibility",
        "delivery_defaults",
        "required_services",
        "retention",
        "tags",
        "outward_action_nodes",
        "outward_action_policy",
        "execution_environment",
        "overlap_policy",
        "pause_lane_policy",
        "concurrency_key",
        "limits",
        "resource_limits",
        "required_secrets",
        "scheduling",
    }
)
_WORKFLOW_SIDECAR_MAPPING_FIELDS = frozenset(
    {
        "delivery_defaults",
        "retention",
        "limits",
        "resource_limits",
        "scheduling",
    }
)
_WORKFLOW_SIDECAR_LIST_FIELDS = frozenset(
    {
        "required_services",
        "tags",
        "outward_action_nodes",
        "required_secrets",
    }
)
_WORKFLOW_LIMIT_INTEGER_BOUNDS = {
    "max_parallel_nodes": (1, 64),
    "max_total_workers": (1, 256),
    "max_executing_runs": (1, 256),
    "max_queued_runs": (1, 100_000),
    "max_paused_runs": (1, 100_000),
    "max_nonterminal_runs": (1, 200_000),
    "max_start_requests_per_minute": (1, 100_000),
    "combined_retries": (1, 5),
    "process_tree_rss_bytes": (16 * 1024 * 1024, 1024**4),
    "max_descendants": (0, 4096),
}
_WORKFLOW_LIMIT_NUMBERS = frozenset(
    {
        "ai_idle_timeout_seconds",
        "ai_wall_timeout_seconds",
        "provider_request_timeout_seconds",
        "subprocess_timeout_seconds",
        "heartbeat_seconds",
        "lease_seconds",
        "coordinator_web_election_grace_seconds",
        "runnable_stall_seconds",
        "semantic_stall_seconds",
        "cooperative_shutdown_seconds",
        "term_grace_seconds",
        "kill_reap_grace_seconds",
        "process_tree_cpu_seconds",
    }
)
_WORKFLOW_RESOURCE_LIMITS = frozenset(
    {
        "process_tree_rss_bytes",
        "process_tree_cpu_seconds",
        "max_descendants",
    }
)
# Match the repository's bounded onboarding JSON-tree capacity convention.
_WORKFLOW_SIDECAR_MAX_BYTES = 64 * 1024
_WORKFLOW_SIDECAR_MAX_DEPTH = 24
_WORKFLOW_SIDECAR_MAX_ENTRIES = 2048
_WORKFLOW_SIDECAR_CYCLE_ERROR = "workflow sidecar structure must not contain cycles"
_WORKFLOW_SIDECAR_BYTE_ERROR = "workflow sidecar exceeds safe byte limit"
_WORKFLOW_SIDECAR_LIMIT_ERROR = "workflow sidecar exceeds safe structure limits"


CatalogError = bounded_source.SourceError


def _require_string_mapping_keys(value: object) -> None:
    """Validate one bounded YAML graph without recursive Python calls.

    Repeated acyclic aliases are safe and traversed once. An identity still on
    the active path is a cycle. Every reference still consumes the entry
    budget, so aliases cannot amplify an otherwise oversized document.
    """

    bounded_source._validate_graph(value, bounded_source.WORKFLOW_SIDECAR_CONTRACT)


def validate_workflow_sidecar(
    metadata: object, *, node_ids: set[str] | frozenset[str]
) -> list[str]:
    """Statically enforce the published Archon companion compatibility shape."""
    if not isinstance(metadata, dict):
        return ["root"]
    _require_string_mapping_keys(metadata)
    problems: list[str] = []
    for field in sorted(set(metadata) - _WORKFLOW_SIDECAR_FIELDS):
        problems.append(field)
    profile = metadata.get("language_compatibility")
    if not isinstance(profile, str) or profile not in {
        "hermes-legacy",
        "archon-2026-07",
    }:
        problems.append("language_compatibility")
    for field in sorted(_WORKFLOW_SIDECAR_MAPPING_FIELDS & set(metadata)):
        if not isinstance(metadata[field], dict):
            problems.append(field)
    for field in sorted(_WORKFLOW_SIDECAR_LIST_FIELDS & set(metadata)):
        value = metadata[field]
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item for item in value
        ):
            problems.append(field)
    for field in ("outward_action_policy", "concurrency_key"):
        if field in metadata and (
            not isinstance(metadata[field], str) or not metadata[field]
        ):
            problems.append(field)
    enums = {
        "execution_environment": {"trusted_local", "isolated_backend_required"},
        "overlap_policy": {"queue", "allow", "forbid"},
        "pause_lane_policy": {"hold", "release"},
    }
    for field, allowed in enums.items():
        if field in metadata and (
            not isinstance(metadata[field], str) or metadata[field] not in allowed
        ):
            problems.append(field)
    if (
        "pause_lane_policy" in metadata
        and metadata.get("overlap_policy", "queue") != "queue"
    ):
        problems.append("pause_lane_policy")
    outward = metadata.get("outward_action_nodes")
    if isinstance(outward, list):
        for item in outward:
            if isinstance(item, str) and item and item not in node_ids:
                problems.append("outward_action_nodes")
                break

    limits = metadata.get("limits")
    if isinstance(limits, dict):
        known = set(_WORKFLOW_LIMIT_INTEGER_BOUNDS) | set(_WORKFLOW_LIMIT_NUMBERS)
        for field in sorted(set(limits) - known):
            problems.append(f"limits.{field}")
        for field, value in limits.items():
            if field in _WORKFLOW_LIMIT_INTEGER_BOUNDS:
                minimum, maximum = _WORKFLOW_LIMIT_INTEGER_BOUNDS[field]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not minimum <= value <= maximum
                ):
                    problems.append(f"limits.{field}")
            elif field in _WORKFLOW_LIMIT_NUMBERS and (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or value <= 0
            ):
                problems.append(f"limits.{field}")
        defaults = {
            "heartbeat_seconds": 5.0,
            "lease_seconds": 30.0,
            "ai_idle_timeout_seconds": 300.0,
            "ai_wall_timeout_seconds": 1800.0,
            "provider_request_timeout_seconds": 300.0,
        }
        tightened = {}
        for field, default in defaults.items():
            value = limits.get(field, default)
            tightened[field] = (
                min(default, float(value))
                if not isinstance(value, bool)
                and isinstance(value, int | float)
                and math.isfinite(value)
                and value > 0
                else default
            )
        if tightened["lease_seconds"] < 3 * tightened["heartbeat_seconds"]:
            problems.append("limits.lease_seconds")
        if tightened["ai_idle_timeout_seconds"] > tightened["ai_wall_timeout_seconds"]:
            problems.append("limits.ai_idle_timeout_seconds")
        if (
            tightened["provider_request_timeout_seconds"]
            > tightened["ai_wall_timeout_seconds"]
        ):
            problems.append("limits.provider_request_timeout_seconds")
    resources = metadata.get("resource_limits")
    if isinstance(resources, dict):
        for field in sorted(set(resources) - _WORKFLOW_RESOURCE_LIMITS):
            problems.append(f"resource_limits.{field}")
        for field, value in resources.items():
            if field in _WORKFLOW_LIMIT_INTEGER_BOUNDS:
                minimum, maximum = _WORKFLOW_LIMIT_INTEGER_BOUNDS[field]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not minimum <= value <= maximum
                ):
                    problems.append(f"resource_limits.{field}")
            elif field == "process_tree_cpu_seconds" and (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or value <= 0
            ):
                problems.append(f"resource_limits.{field}")
    return sorted(set(problems))


def _relative_label(path: Path) -> str:
    return path.as_posix()


def _load_yaml_mapping(path: Path, *, label: str) -> dict[str, Any]:
    if label == "workflow sidecar":
        value = bounded_source.load_yaml_mapping(
            path, bounded_source.WORKFLOW_SIDECAR_CONTRACT
        )
        if value is None:
            raise CatalogError("workflow sidecar is missing")
        return value
    if label == "workflow metadata":
        value = bounded_source.load_yaml_mapping(
            path, bounded_source.WORKFLOW_METADATA_CONTRACT
        )
        assert value is not None
        return value
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise CatalogError(f"{path}: invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogError(f"{path}: {label} must be a mapping")
    return value


def _workflow_language_profile(
    workflow_file: Path,
    relative: str,
    problems: list[str],
    *,
    workflow_metadata: dict[str, Any],
) -> str | None:
    """Read the language profile from the workflow's real sibling sidecar."""
    sidecar = workflow_file.with_name(f"{workflow_file.stem}.hermes.yaml")
    metadata = bounded_source.load_yaml_mapping(
        sidecar, bounded_source.WORKFLOW_SIDECAR_CONTRACT
    )
    if metadata is None:
        return None
    nodes = workflow_metadata.get("nodes", [])
    node_ids = (
        {
            node["id"]
            for node in nodes
            if isinstance(node, dict) and isinstance(node.get("id"), str)
        }
        if isinstance(nodes, list)
        else set()
    )
    for field in validate_workflow_sidecar(metadata, node_ids=node_ids):
        problems.append(f"invalid workflow sidecar: {relative}: {field}")
    profile = metadata.get("language_compatibility")
    if not isinstance(profile, str):
        problems.append(f"invalid workflow sidecar: {relative}")
        return "invalid"
    return profile


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


def build_catalog(repo: Path, *, entries: list[dict] | None = None) -> dict:
    if entries is None:
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


def validate_repository(
    repo: Path,
    entries: list[dict],
    *,
    inventory: dict[str, Any] | None = None,
) -> list[str]:
    if inventory is None:
        inventory = collect_repository_inventory(repo)
    represented = collect_entry_inventory(entries)
    problems = compare_inventories(inventory, represented)
    problems.extend(validate_flow_maturity(repo, entries))
    problems.extend(validate_configuration_names(repo, entries, inventory=inventory))
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
        if not isinstance(entry_id, str) or not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", entry_id
        ):
            raise CatalogError(f"{path}: id must be a lowercase slug")
        if entry_id in seen_ids:
            raise CatalogError(f"duplicate entry id: {entry_id}")
        seen_ids.add(entry_id)
        if isinstance(path, Path) and path.stem != entry_id:
            raise CatalogError(
                f"{path}: entry id must match filename: expected {path.stem}, got {entry_id}"
            )

        if (
            not isinstance(entry["display_name"], str)
            or not entry["display_name"].strip()
        ):
            raise CatalogError(
                f"entry {entry_id}: display_name must be a non-empty string"
            )
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
                raise CatalogError(
                    f"entry {entry_id}: duplicate configuration name: {name}"
                )
            config_names.add(name)
            if item["kind"] not in CONFIG_KINDS:
                raise CatalogError(
                    f"{label}: unknown configuration kind: {item['kind']}"
                )
            if not isinstance(item["required"], bool):
                raise CatalogError(f"{label}: required must be a boolean")
            guidance = item["guidance"]
            if not isinstance(guidance, str) or not guidance.strip():
                raise CatalogError(f"{label}: guidance must be a non-empty string")
            if item["kind"] == "static-secret" and _SECRET_VALUE_PATTERN.search(
                guidance
            ):
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


def _manifest_plugin_paths(manifest: dict[str, Any], path: Path) -> set[str]:
    value = manifest.get("plugins", [])
    if not isinstance(value, list):
        raise CatalogError(f"{path}: plugins must be a list")

    repo = path.parents[1]
    plugin_paths: set[str] = set()
    for index, item in enumerate(value):
        if isinstance(item, str):
            if (
                item in _BUILTIN_BACKEND_PLUGINS
                and not (repo / item / "plugin.yaml").is_file()
            ):
                continue
            plugin_paths.add(item)
            continue
        if not isinstance(item, dict):
            raise CatalogError(f"{path}: plugins[{index}] must be a string or mapping")
        plugin_path = item.get("path")
        enabled = item.get("enabled")
        if not isinstance(plugin_path, str):
            raise CatalogError(f"{path}: plugins[{index}].path must be a string")
        if not isinstance(enabled, bool):
            raise CatalogError(f"{path}: plugins[{index}].enabled must be a boolean")
        if not enabled and not (repo / plugin_path / "plugin.yaml").is_file():
            continue
        plugin_paths.add(plugin_path)
    return plugin_paths


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
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        compile(tree, str(path), "exec", dont_inherit=True)
        return tree
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


def _schema_contract(
    tree: ast.Module,
    **_unused: object,
) -> tuple[set[str], dict[str, str]]:
    """Read literal SCHEMAS keys and their declared public names."""

    active = _active_binding(tree.body, len(tree.body), "SCHEMAS")
    if active is None:
        return set(), {}
    schemas = _direct_named_dict(active[1], "SCHEMAS")
    if schemas is None:
        return set(), {}
    tools: set[str] = set()
    schema_names: dict[str, str] = {}
    for key, value in zip(schemas.keys, schemas.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            continue
        tools.add(key.value)
        if not isinstance(value, ast.Dict):
            continue
        for field, field_value in zip(value.keys, value.values):
            if (
                isinstance(field, ast.Constant)
                and field.value == "name"
                and isinstance(field_value, ast.Constant)
                and isinstance(field_value.value, str)
            ):
                schema_names[key.value] = field_value.value
    return tools, schema_names


def _direct_named_dict(statement: ast.stmt | None, name: str) -> ast.Dict | None:
    if (
        isinstance(statement, ast.Assign)
        and bool(statement.targets)
        and all(isinstance(target, ast.Name) for target in statement.targets)
        and any(target.id == name for target in statement.targets)
        and isinstance(statement.value, ast.Dict)
    ):
        return statement.value
    if (
        isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
        and statement.target.id == name
        and isinstance(statement.value, ast.Dict)
    ):
        return statement.value
    return None


def _directly_binds(statement: ast.stmt, name: str) -> bool:
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return statement.name == name
    if isinstance(statement, ast.Assign):
        return any(
            isinstance(target, ast.Name) and target.id == name
            for target in statement.targets
        )
    if isinstance(statement, ast.AnnAssign):
        return isinstance(statement.target, ast.Name) and statement.target.id == name
    if isinstance(statement, ast.Delete):
        return any(
            isinstance(target, ast.Name) and target.id == name
            for target in statement.targets
        )
    if isinstance(statement, ast.Import):
        return any(
            (item.asname or item.name.split(".", 1)[0]) == name
            for item in statement.names
        )
    if isinstance(statement, ast.ImportFrom):
        return any((item.asname or item.name) == name for item in statement.names)
    return False


def _bound_names(statement: ast.stmt) -> set[str]:
    """Collect bindings in this lexical scope, including compound statements."""

    class BindingVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.names: set[str] = set()

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                self.names.add(node.id)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.names.add(node.name)

            for expression in node.decorator_list:
                self.visit(expression)
            for expression in [*node.args.defaults, *node.args.kw_defaults]:
                if expression is not None:
                    self.visit(expression)
            arguments = [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
            if node.args.vararg is not None:
                arguments.append(node.args.vararg)
            if node.args.kwarg is not None:
                arguments.append(node.args.kwarg)
            for argument in arguments:
                if argument.annotation is not None:
                    self.visit(argument.annotation)
            if node.returns is not None:
                self.visit(node.returns)
            for parameter in getattr(node, "type_params", ()):
                self.visit(parameter)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.names.add(node.name)
            for expression in [*node.decorator_list, *node.bases]:
                self.visit(expression)
            for keyword in node.keywords:
                self.visit(keyword.value)
            for parameter in getattr(node, "type_params", ()):
                self.visit(parameter)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            for expression in [*node.args.defaults, *node.args.kw_defaults]:
                if expression is not None:
                    self.visit(expression)

        def _visit_comprehension(self, node: ast.AST, results: list[ast.expr]) -> None:
            for generator in node.generators:
                self.visit(generator.iter)
                for condition in generator.ifs:
                    self.visit(condition)
            for expression in results:
                self.visit(expression)

        def visit_ListComp(self, node: ast.ListComp) -> None:
            self._visit_comprehension(node, [node.elt])

        visit_SetComp = visit_ListComp
        visit_GeneratorExp = visit_ListComp

        def visit_DictComp(self, node: ast.DictComp) -> None:
            self._visit_comprehension(node, [node.key, node.value])

        def visit_Import(self, node: ast.Import) -> None:
            self.names.update(
                item.asname or item.name.split(".", 1)[0] for item in node.names
            )

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            self.names.update(item.asname or item.name for item in node.names)

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            if node.type is not None:
                self.visit(node.type)
            if isinstance(node.name, str):
                self.names.add(node.name)
            for item in node.body:
                self.visit(item)

        def visit_MatchAs(self, node: ast.MatchAs) -> None:
            if isinstance(node.name, str):
                self.names.add(node.name)
            if node.pattern is not None:
                self.visit(node.pattern)

        def visit_MatchStar(self, node: ast.MatchStar) -> None:
            if isinstance(node.name, str):
                self.names.add(node.name)

        def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
            if isinstance(node.rest, str):
                self.names.add(node.rest)
            for pattern in node.patterns:
                self.visit(pattern)

    visitor = BindingVisitor()
    visitor.visit(statement)
    return visitor.names


def _scope_directives(statements: list[ast.stmt]) -> set[str]:
    """Return global/nonlocal names declared in this exact lexical scope."""

    class DirectiveVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.names: set[str] = set()

        def visit_Global(self, node: ast.Global) -> None:
            self.names.update(node.names)

        def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
            self.names.update(node.names)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return None

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return None

    visitor = DirectiveVisitor()
    for statement in statements:
        visitor.visit(statement)
    return visitor.names


def _active_binding(
    statements: list[ast.stmt], end: int, name: str
) -> tuple[int, ast.stmt | None] | None:
    active: tuple[int, ast.stmt | None] | None = None
    for index, statement in enumerate(statements[:end]):
        if not _may_bind_name(statement, name):
            continue
        active = (
            (index, statement) if _directly_binds(statement, name) else (index, None)
        )
    return active


def _may_bind_name(statement: ast.stmt, name: str) -> bool:
    class WildcardVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.found = False

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            self.found = self.found or any(item.name == "*" for item in node.names)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return None

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return None

    visitor = WildcardVisitor()
    visitor.visit(statement)
    return name in _bound_names(statement) or visitor.found


def _registered_tools(tree: ast.Module, schema_tools: set[str]) -> set[str]:
    """Retain the legacy literal and direct-SCHEMAS registration inventory."""

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


def _schema_import_sources(
    tree: ast.Module, schema_tools_by_module: dict[str, set[str]]
) -> dict[str, str]:
    """Map direct local schema-module imports to their lexical bindings."""

    sources: dict[str, str] = {}
    for index, statement in enumerate(tree.body):
        if isinstance(statement, ast.Import):
            for item_index, item in enumerate(statement.names):
                source = item.name.rsplit(".", 1)[-1]
                binding = item.asname or item.name.split(".", 1)[0]
                final_in_statement = not any(
                    (later.asname or later.name.split(".", 1)[0]) == binding
                    for later in statement.names[item_index + 1 :]
                )
                if (
                    final_in_statement
                    and item.name == source
                    and source in schema_tools_by_module
                    and _active_binding(tree.body, len(tree.body), binding)
                    == (index, statement)
                ):
                    sources[binding] = source
        elif isinstance(statement, ast.ImportFrom):
            for item_index, item in enumerate(statement.names):
                binding = item.asname or item.name
                final_in_statement = not any(
                    (later.asname or later.name) == binding
                    for later in statement.names[item_index + 1 :]
                )
                if (
                    final_in_statement
                    and statement.level > 0
                    and statement.module is None
                    and item.name in schema_tools_by_module
                    and _active_binding(tree.body, len(tree.body), binding)
                    == (index, statement)
                ):
                    sources[binding] = item.name
    return sources


def _direct_register(tree: ast.Module) -> ast.FunctionDef | None:
    active = _active_binding(tree.body, len(tree.body), "register")
    if active is None or not isinstance(active[1], ast.FunctionDef):
        return None
    register = active[1]
    if (
        register.decorator_list
        or register.args.posonlyargs
        or len(register.args.args) != 1
        or register.args.defaults
        or register.args.kwonlyargs
        or register.args.vararg is not None
        or register.args.kwarg is not None
    ):
        return None
    return register


def _factory_returns_own_callable(
    factory: ast.FunctionDef,
    *,
    required_parameter: str | None = None,
) -> bool:
    """Prove only that a local factory directly returns its own callable."""

    parameters = [*factory.args.posonlyargs, *factory.args.args]
    if (
        factory.decorator_list
        or len(parameters) != 1
        or factory.args.vararg is not None
        or factory.args.kwarg is not None
        or factory.args.kwonlyargs
        or factory.args.defaults
    ):
        return False
    if required_parameter is not None and parameters[0].arg != required_parameter:
        return False
    if len(factory.body) == 1 and isinstance(factory.body[0], ast.Return):
        return isinstance(factory.body[0].value, ast.Lambda)
    if (
        len(factory.body) == 2
        and isinstance(factory.body[0], (ast.FunctionDef, ast.AsyncFunctionDef))
        and not factory.body[0].decorator_list
        and isinstance(factory.body[1], ast.Return)
        and isinstance(factory.body[1].value, ast.Name)
    ):
        return factory.body[1].value.id == factory.body[0].name
    return False


def _schema_loop(
    statement: ast.stmt, context_name: str
) -> tuple[str, str, str, ast.Call, int] | None:
    if not (
        isinstance(statement, ast.For)
        and not statement.orelse
        and isinstance(statement.target, ast.Tuple)
        and len(statement.target.elts) == 2
        and all(isinstance(item, ast.Name) for item in statement.target.elts)
        and statement.target.elts[0].id != statement.target.elts[1].id
        and isinstance(statement.iter, ast.Call)
        and not statement.iter.args
        and not statement.iter.keywords
        and isinstance(statement.iter.func, ast.Attribute)
        and statement.iter.func.attr == "items"
        and isinstance(statement.iter.func.value, ast.Attribute)
        and statement.iter.func.value.attr == "SCHEMAS"
    ):
        return None
    calls = [
        (index, item.value)
        for index, item in enumerate(statement.body)
        if isinstance(item, ast.Expr)
        and isinstance(item.value, ast.Call)
        and isinstance(item.value.func, ast.Attribute)
        and item.value.func.attr == "register_tool"
        and isinstance(item.value.func.value, ast.Name)
        and item.value.func.value.id == context_name
    ]
    if len(calls) != 1:
        return None
    name = statement.target.elts[0].id
    schema = statement.target.elts[1].id
    binding = (
        statement.iter.func.value.value.id
        if isinstance(statement.iter.func.value.value, ast.Name)
        else ""
    )
    call_index, call = calls[0]
    return name, schema, binding, call, call_index


def _keyword_values(call: ast.Call) -> dict[str, ast.expr] | None:
    values: dict[str, ast.expr] = {}
    for keyword in call.keywords:
        if keyword.arg is None or keyword.arg in values:
            return None
        values[keyword.arg] = keyword.value
    return values


def _callable_map_keys(
    tree: ast.Module,
    register: ast.FunctionDef,
    loop_index: int,
    map_name: str,
) -> set[str] | None:
    parameter_names = {
        argument.arg for argument in [*register.args.posonlyargs, *register.args.args]
    }
    directives = _scope_directives(register.body)
    if map_name in parameter_names:
        return None
    local = _active_binding(register.body, loop_index, map_name)
    if local is not None:
        if map_name in directives:
            return None
        statements = register.body
        map_index, statement = local
    else:
        map_scope = (
            register.body[:loop_index] if map_name in directives else register.body
        )
        if any(_may_bind_name(item, map_name) for item in map_scope):
            return None
        statements = tree.body
        module = _active_binding(tree.body, len(tree.body), map_name)
        if module is None:
            return None
        map_index, statement = module
    mapping = _direct_named_dict(statement, map_name)
    if mapping is None:
        return None

    def callable_binding(name: str) -> tuple[int, ast.stmt | None] | None:
        if name in parameter_names:
            return None
        active = _active_binding(statements, map_index, name)
        local_map = statements is register.body
        if active is not None and local_map and name in directives:
            return None
        if active is not None or statements is tree.body:
            return active
        callable_scope = (
            register.body[:map_index] if name in directives else register.body
        )
        if any(_may_bind_name(item, name) for item in callable_scope):
            return None
        return _active_binding(tree.body, len(tree.body), name)

    keys: set[str] = set()
    for key, value in zip(mapping.keys, mapping.values):
        if (
            not isinstance(key, ast.Constant)
            or not isinstance(key.value, str)
            or key.value in keys
        ):
            return None
        callable_value = isinstance(value, ast.Lambda)
        if isinstance(value, ast.Name):
            binding = callable_binding(value.id)
            callable_value = binding is not None and isinstance(
                binding[1], (ast.FunctionDef, ast.AsyncFunctionDef)
            )
        elif (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and len(value.args) == 1
            and not value.keywords
        ):
            binding = callable_binding(value.func.id)
            callable_value = (
                binding is not None
                and isinstance(binding[1], ast.FunctionDef)
                and _factory_returns_own_callable(binding[1])
            )
        if not callable_value:
            return None
        keys.add(key.value)
    return keys


def _schema_loop_handler_contract(
    tree: ast.Module,
    schema_bindings: dict[str, set[str]],
) -> tuple[set[str], set[str]]:
    """Recognize only the authorized direct structural registration forms."""

    register = _direct_register(tree)
    if register is None:
        return set(), set()
    context_name = register.args.args[0].arg
    directives = _scope_directives(register.body)
    handled: set[str] = set()
    registered: set[str] = set()
    for loop_index, statement in enumerate(register.body):
        shell = _schema_loop(statement, context_name)
        if shell is None:
            continue
        name, schema, binding, call, call_index = shell
        loop_tools = schema_bindings.get(binding)
        if loop_tools is None:
            continue
        schema_binding_scope = (
            register.body[:loop_index] if binding in directives else register.body
        )
        if (
            context_name in {name, schema, binding}
            or any(
                _may_bind_name(item, context_name)
                for item in register.body[:loop_index]
            )
            or any(_may_bind_name(item, binding) for item in schema_binding_scope)
        ):
            continue
        loop_prefix_bindings = set().union(
            *(_bound_names(item) for item in statement.body[:call_index]), set()
        )
        if {context_name, name, schema} & loop_prefix_bindings:
            continue
        if any(
            isinstance(item, ast.ImportFrom)
            and any(alias.name == "*" for alias in item.names)
            for item in statement.body[:call_index]
        ):
            continue
        keywords = _keyword_values(call)
        if keywords is None:
            continue
        if not (
            isinstance(keywords.get("name"), ast.Name)
            and keywords["name"].id == name
            and isinstance(keywords.get("schema"), ast.Name)
            and keywords["schema"].id == schema
        ):
            continue
        handler = keywords.get("handler")
        if (
            isinstance(handler, ast.Call)
            and isinstance(handler.func, ast.Name)
            and len(handler.args) == 1
            and not handler.keywords
            and isinstance(handler.args[0], ast.Name)
            and handler.args[0].id == name
        ):
            if handler.func.id in (
                {name, schema, context_name} | loop_prefix_bindings | directives
            ):
                continue
            factory_info = _active_binding(register.body, loop_index, handler.func.id)
            if (
                factory_info is not None
                and isinstance(factory_info[1], ast.FunctionDef)
                and _factory_returns_own_callable(
                    factory_info[1], required_parameter=name
                )
            ):
                handled.update(loop_tools)
                registered.update(loop_tools)
            continue
        if (
            isinstance(handler, ast.Subscript)
            and isinstance(handler.value, ast.Name)
            and isinstance(handler.slice, ast.Name)
            and handler.slice.id == name
        ):
            if handler.value.id in (
                {name, schema, context_name} | loop_prefix_bindings
            ):
                continue
            mapped = _callable_map_keys(tree, register, loop_index, handler.value.id)
            if mapped is not None:
                handled.update(mapped)
                registered.update(loop_tools & mapped)
    return handled, registered


def _schema_loop_handler_tools(tree: ast.Module, schema_tools: set[str]) -> set[str]:
    """Compatibility wrapper for the legacy single-binding helper."""

    bindings = {
        node.iter.func.value.value.id: set(schema_tools)
        for node in ast.walk(tree)
        if isinstance(node, ast.For)
        and isinstance(node.iter, ast.Call)
        and isinstance(node.iter.func, ast.Attribute)
        and isinstance(node.iter.func.value, ast.Attribute)
        and isinstance(node.iter.func.value.value, ast.Name)
        and node.iter.func.value.attr == "SCHEMAS"
    }
    return _schema_loop_handler_contract(tree, bindings)[0]


def _portable_config_basename(value: object) -> str | None:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        return None
    if value.rstrip(" .") != value or any(
        ord(character) < 32
        or 0xD800 <= ord(character) <= 0xDFFF
        or character in '<>"/\\|?*:'
        for character in value
    ):
        return None
    device = value.split(".", 1)[0].upper()
    if device in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} or re.fullmatch(
        r"(?:COM|LPT)[1-9¹²³]", device
    ):
        return None
    if PurePosixPath(value).name != value or PureWindowsPath(value).name != value:
        return None
    if len(value.encode("utf-16-le")) > bounded_source.MAX_WIN32_COMPONENT_UTF16_BYTES:
        return None
    return value


def _plugin_config_schema_contract(
    plugin_dir: Path,
    metadata: dict[str, Any],
    relative: str,
    problems: list[str],
) -> tuple[set[str], set[str]]:
    descriptor = metadata.get("config_schema")
    if descriptor is None:
        return set(), set()
    safe_descriptor = _portable_config_basename(descriptor)
    if safe_descriptor is None:
        problems.append(f"unsafe plugin config schema: {relative}")
        return set(), set()
    try:
        is_junction = getattr(plugin_dir, "is_junction", lambda: False)
        if plugin_dir.is_symlink() or is_junction():
            problems.append(f"unsafe plugin config schema: {relative}")
            return set(), set()
    except OSError:
        problems.append(f"invalid plugin config schema: {relative}: {safe_descriptor}")
        return set(), set()
    try:
        value = bounded_source.load_json_mapping_relative(
            plugin_dir, safe_descriptor, bounded_source.CONFIG_SCHEMA_CONTRACT
        )
        assert value is not None
    except CatalogError as exc:
        if exc.code is bounded_source.SourceErrorCode.MISSING_SOURCE:
            problems.append(
                f"missing plugin config schema: {relative}: {safe_descriptor}"
            )
        else:
            problems.append(
                f"invalid plugin config schema: {relative}: {safe_descriptor}"
            )
        return set(), set()
    version = value.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        problems.append(f"unsupported plugin config schema version: {relative}")
        return set(), set()
    fields = value.get("fields")
    if not isinstance(fields, list) or len(fields) > 128:
        problems.append(f"invalid plugin config schema: {relative}: fields")
        return set(), set()
    required: set[str] = set()
    optional: set[str] = set()
    seen: set[str] = set()
    for index, field in enumerate(fields):
        if not isinstance(field, dict):
            problems.append(
                f"invalid plugin configuration field: {relative}: fields[{index}]"
            )
            continue
        field_id = field.get("id")
        storage = field.get("storage")
        field_type = field.get("type")
        is_required = field.get("required", False)
        if (
            not isinstance(field_id, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", field_id)
            or not isinstance(storage, str)
            or storage not in {"setting", "secret"}
            or not isinstance(field_type, str)
            or not field_type.strip()
            or not isinstance(is_required, bool)
        ):
            problems.append(
                f"invalid plugin configuration field: {relative}: fields[{index}]"
            )
            continue
        if field_id in seen:
            problems.append(
                f"duplicate plugin configuration field: {relative}: {field_id}"
            )
            continue
        seen.add(field_id)
        (required if is_required else optional).add(field_id)
    return required, optional


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
            if (
                (is_environ_get or is_getenv)
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
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


def _mcp_runtime_contract(root: Path, problems: list[str]) -> tuple[set[str], set[str]]:
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
    manifest_plugins = _manifest_plugin_paths(manifest, manifest_path)
    manifest_mcp_local = _manifest_list(manifest, "mcpLocal", manifest_path)
    manifest_workflows = _manifest_list(manifest, "workflows", manifest_path)
    workflow_core_tools = _manifest_list(manifest, "workflowCoreTools", manifest_path)
    replaced_builtin_skills = (
        _BUILTIN_WORKFLOW_REPLACED_SKILLS
        if any(
            item in _BUILTIN_BACKEND_PLUGINS
            for item in manifest.get("plugins", [])
            if isinstance(item, str)
        )
        else frozenset()
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
    descriptor_required_configuration: set[str] = set()
    descriptor_optional_configuration: set[str] = set()
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
        descriptor_required, descriptor_optional = _plugin_config_schema_contract(
            plugin_file.parent, metadata, relative, problems
        )
        configuration.update(descriptor_required)
        configuration.update(descriptor_optional)
        implementation_configuration.update(descriptor_required)
        implementation_configuration.update(descriptor_optional)
        descriptor_required_configuration.update(descriptor_required)
        descriptor_optional_configuration.update(descriptor_optional)

        python_trees: list[tuple[Path, ast.Module]] = []
        for python_file in sorted(plugin_file.parent.glob("*.py")):
            tree = _parse_python(python_file, problems)
            if tree is not None:
                python_trees.append((python_file, tree))
        schema_tools: set[str] = set()
        schema_tools_by_module: dict[str, set[str]] = {}
        schema_names: dict[str, str] = {}
        implementation_environment: set[str] = set()
        for python_file, tree in python_trees:
            file_tools, file_schema_names = _schema_contract(tree)
            schema_tools.update(file_tools)
            if file_tools:
                schema_tools_by_module[python_file.stem] = file_tools
            schema_names.update(file_schema_names)
            implementation_environment.update(_environment_accesses(tree))
        init_tree = next(
            (tree for path, tree in python_trees if path.name == "__init__.py"),
            None,
        )
        handler_tools: set[str] = set()
        registered_tools: set[str] = set()
        if init_tree is not None:
            schema_sources = _schema_import_sources(init_tree, schema_tools_by_module)
            schema_bindings = {
                binding: set(schema_tools_by_module[source])
                for binding, source in schema_sources.items()
            }
            handler_tools, registered_tools = _schema_loop_handler_contract(
                init_tree, schema_bindings
            )

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
            problems.append(
                f"local MCP dispatcher missing schema: {local_path}: {tool}"
            )
        for server_name in mcp_local_servers.get(local_path, set()):
            mcp_server_tools[server_name].update(listed)

    actual_workflows: set[str] = set()
    workflow_names: set[str] = set()
    workflow_inputs: dict[str, dict[str, bool]] = {}
    workflow_toolsets: dict[str, set[str]] = {}
    workflow_mcp_servers: dict[str, set[str]] = {}
    workflow_tool_nodes: dict[str, list[tuple[str, set[str], set[str], str]]] = {}
    workflow_files = sorted((repo / "workflows").glob("*.yml")) + [
        path
        for path in sorted((repo / "workflows").glob("*.yaml"))
        if not path.name.endswith(".hermes.yaml")
    ]
    for workflow_file in workflow_files:
        relative = workflow_file.relative_to(repo).as_posix()
        metadata = _load_yaml_mapping(workflow_file, label="workflow metadata")
        profile = _workflow_language_profile(
            workflow_file,
            relative,
            problems,
            workflow_metadata=metadata,
        )
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
        if isinstance(requires, dict):
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
        elif isinstance(requires, list):
            if profile is None:
                problems.append(f"missing Archon workflow sidecar: {relative}")
            elif profile != "archon-2026-07":
                problems.append(f"incompatible workflow sidecar: {relative}")
            if all(isinstance(item, str) and item for item in requires):
                workflow_toolsets[relative] = set(requires)
                workflow_mcp_servers[relative] = set()
            else:
                problems.append(f"invalid workflow requires: {relative}")
        else:
            problems.append(f"invalid workflow requires: {relative}")
        inputs = metadata.get("inputs", [])
        parsed_inputs: dict[str, bool] = {}
        if not isinstance(inputs, list):
            problems.append(f"invalid workflow inputs: {relative}")
        else:
            for index, item in enumerate(inputs):
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    problems.append(
                        f"invalid workflow input: {relative}: inputs[{index}]"
                    )
                    continue
                input_name = item["name"]
                if input_name in parsed_inputs:
                    problems.append(
                        f"duplicate workflow input: {relative}: {input_name}"
                    )
                    continue
                parsed_inputs[input_name] = "default" not in item
        workflow_inputs[relative] = parsed_inputs
        parsed_tool_nodes: list[tuple[str, set[str], set[str], str]] = []
        nodes = metadata.get("nodes", [])
        if isinstance(nodes, list):
            for index, node in enumerate(nodes):
                if not isinstance(node, dict):
                    continue
                if node.get("kind") == "tool":
                    tool_field = "tools"
                elif "allowed_tools" in node:
                    tool_field = "allowed_tools"
                else:
                    continue
                node_id = node.get("id")
                if not isinstance(node_id, str):
                    node_id = f"nodes[{index}]"
                node_tools = _string_list_metadata(
                    node, tool_field, workflow_file, problems
                )
                if tool_field == "tools" and not node_tools:
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

    for name in sorted(
        (required_configuration | optional_configuration) - manifest_environment
    ):
        problems.append(f"configuration missing from manifest env: {name}")
    for name in sorted(manifest_environment - implementation_configuration):
        problems.append(f"unused manifest environment: {name}")
    required_configuration.update(descriptor_required_configuration)
    optional_configuration.update(descriptor_optional_configuration)
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
                problems.append(f"unknown workflow tool: {workflow}: {node_id}: {tool}")
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
    return _deep_freeze(
        {
            "manifest_skills": manifest_skills,
            "manifest_plugins": manifest_plugins,
            "manifest_mcp_local": manifest_mcp_local,
            "manifest_workflows": manifest_workflows,
            "replaced_builtin_skills": replaced_builtin_skills,
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
    )


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, list | tuple):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_deep_freeze(item) for item in value)
    return value


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
            if represented_key == "skills" and item in _BUILTIN_WORKFLOW_SKILLS:
                continue
            if represented_key == "plugins" and item in _BUILTIN_BACKEND_PLUGINS:
                continue
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
            if (
                actual_key == "actual_skills"
                and item in inventory["replaced_builtin_skills"]
            ):
                continue
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
        expected = (
            FLOW_STATUS_TO_MATURITY.get(status) if isinstance(status, str) else None
        )
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


def validate_configuration_names(
    repo: Path,
    entries: list[dict],
    *,
    inventory: dict[str, Any] | None = None,
) -> list[str]:
    if inventory is None:
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
            problems.append(
                f"unrepresented workflow input: entry {entry['id']}: {name}"
            )
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
            elif (
                reference not in _BUILTIN_WORKFLOW_SKILLS
                and reference not in _BUILTIN_BACKEND_PLUGINS
                and not (repo / reference).exists()
            ):
                problems.append(f"missing entry path: {entry_id}: {reference}")
        for artifact in entry["artifacts"]:
            if _is_unsafe_reference(artifact):
                problems.append(f"unsafe entry path: {entry_id}: {artifact}")
            elif "/" in artifact or Path(artifact).suffix.lower() in _PATH_SUFFIXES:
                if not (repo / artifact).exists():
                    problems.append(f"missing entry path: {entry_id}: {artifact}")
    return problems
