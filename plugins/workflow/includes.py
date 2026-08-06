"""Bounded compile-time expansion for literal workflow includes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from agent.structured_output import canonical_json_bytes
from plugins.workflow.models import (
    ExpandedWorkflowSource,
    ValidationIssue,
    WorkflowCompilationLimits,
    WorkflowIncludeAlias,
    WorkflowSourceDocument,
    WorkflowSourceNode,
    WorkflowValidationError,
)


DEFAULT_COMPILATION_LIMITS = WorkflowCompilationLimits(
    max_include_depth=3,
    max_dependencies=64,
    max_nodes=512,
    max_edges=4096,
    max_source_bytes=2 * 1024 * 1024,
    max_expanded_bytes=2 * 1024 * 1024,
)


def _issue(
    code: str,
    message: str,
    *,
    node: WorkflowSourceNode | None = None,
) -> WorkflowValidationError:
    return WorkflowValidationError(
        ValidationIssue(
            path=(f"nodes[{node.source_index}].include" if node else "nodes"),
            code=code,
            message=message,
            source_line=node.source_line if node else None,
        )
    )


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return sorted((_thaw(item) for item in value), key=repr)
    return value


def _node_mapping(node: WorkflowSourceNode) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": node.id,
        node.node_type: _thaw(node.value),
    }
    if node.depends_on:
        raw["depends_on"] = list(node.depends_on)
    raw.update(_thaw(node.options))
    return raw


def _definition_mapping(
    root: WorkflowSourceDocument, nodes: tuple[WorkflowSourceNode, ...]
) -> dict[str, Any]:
    definition = {
        "name": root.name,
        "description": root.description,
        "nodes": [_node_mapping(node) for node in nodes],
    }
    definition.update(_thaw(root.options))
    return definition


def _package_key(source: WorkflowSourceDocument) -> str:
    return f"{source.source}:{source.name}"


def _qualified(namespace: tuple[str, ...], node_id: str) -> str:
    return "__".join((*namespace, node_id))


def _dedupe(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True, slots=True)
class _ExpandedInstance:
    nodes: tuple[WorkflowSourceNode, ...]
    entries: tuple[str, ...]
    sinks: tuple[str, ...]


class _ExpansionState:
    def __init__(
        self,
        root: WorkflowSourceDocument,
        limits: WorkflowCompilationLimits,
    ) -> None:
        self.root = root
        self.limits = limits
        self.nodes_by_id: dict[str, WorkflowSourceNode] = {}
        self.node_byte_lengths: dict[str, int] = {}
        self.edge_counts: dict[str, int] = {}
        self.node_bytes = 0
        self.edge_count = 0
        self.aliases: dict[str, WorkflowIncludeAlias] = {}
        self.dependencies: list[WorkflowSourceDocument] = []
        self.dependency_keys: set[str] = set()
        self.source_bytes = len(root.definition_bytes)
        if self.source_bytes > limits.max_source_bytes:
            raise _issue(
                "include_expansion_limit",
                "selected workflow source bytes exceed the compilation limit",
            )
        self.base_canonical_bytes = self._canonical_length(
            _definition_mapping(root, ()),
            "expanded canonical definition exceeds the compilation limit",
        )

    def _canonical_length(self, value: Any, message: str) -> int:
        if self.limits.max_expanded_bytes == 0:
            raise _issue("include_expansion_limit", message)
        try:
            return len(
                canonical_json_bytes(
                    value,
                    max_bytes=self.limits.max_expanded_bytes,
                )
            )
        except ValueError as exc:
            raise _issue("include_expansion_limit", message) from exc

    def _check_projected_bytes(self, node_bytes: int, node_count: int) -> None:
        projected = self.base_canonical_bytes + node_bytes + max(0, node_count - 1)
        if projected > self.limits.max_expanded_bytes:
            raise _issue(
                "include_expansion_limit",
                "expanded canonical definition exceeds the compilation limit",
            )

    def add_dependency(
        self,
        source: WorkflowSourceDocument,
        node: WorkflowSourceNode,
        logical_chain: str,
    ) -> None:
        key = _package_key(source)
        if key in self.dependency_keys:
            return
        if len(self.dependencies) >= self.limits.max_dependencies:
            raise _issue(
                "include_dependency_limit",
                "workflow include dependency count exceeds the compilation limit: "
                f"{logical_chain}",
                node=node,
            )
        projected_source_bytes = self.source_bytes + len(source.definition_bytes)
        if projected_source_bytes > self.limits.max_source_bytes:
            raise _issue(
                "include_expansion_limit",
                "selected workflow source bytes exceed the compilation limit",
                node=node,
            )
        self.dependency_keys.add(key)
        self.dependencies.append(source)
        self.source_bytes = projected_source_bytes

    def add_node(self, node: WorkflowSourceNode) -> None:
        if node.id in self.nodes_by_id or node.id in self.aliases:
            raise _issue(
                "include_id_collision",
                f"expanded workflow node id collides: {node.id}",
                node=node,
            )
        if len(self.nodes_by_id) >= self.limits.max_nodes:
            raise _issue(
                "include_expansion_limit",
                "expanded workflow node count exceeds the compilation limit",
                node=node,
            )
        length = self._canonical_length(
            _node_mapping(node),
            "expanded canonical definition exceeds the compilation limit",
        )
        self._check_projected_bytes(
            self.node_bytes + length,
            len(self.nodes_by_id) + 1,
        )
        self.nodes_by_id[node.id] = node
        self.node_byte_lengths[node.id] = length
        self.edge_counts[node.id] = 0
        self.node_bytes += length

    def replace_node(self, node: WorkflowSourceNode) -> None:
        old = self.nodes_by_id[node.id]
        old_edges = self.edge_counts[node.id]
        new_edges = len(node.depends_on)
        projected_edges = self.edge_count - old_edges + new_edges
        if projected_edges > self.limits.max_edges:
            raise _issue(
                "include_expansion_limit",
                "expanded workflow edge count exceeds the compilation limit",
                node=node,
            )
        length = self._canonical_length(
            _node_mapping(node),
            "expanded canonical definition exceeds the compilation limit",
        )
        projected_node_bytes = (
            self.node_bytes - self.node_byte_lengths[node.id] + length
        )
        self._check_projected_bytes(projected_node_bytes, len(self.nodes_by_id))
        self.nodes_by_id[node.id] = node
        self.node_byte_lengths[node.id] = length
        self.edge_counts[node.id] = new_edges
        self.node_bytes = projected_node_bytes
        self.edge_count = projected_edges
        del old

    def add_alias(
        self,
        alias_id: str,
        instance: _ExpandedInstance,
        node: WorkflowSourceNode,
    ) -> None:
        if alias_id in self.aliases or alias_id in self.nodes_by_id:
            raise _issue(
                "include_id_collision",
                f"expanded include alias collides: {alias_id}",
                node=node,
            )
        self.aliases[alias_id] = WorkflowIncludeAlias(
            entries=instance.entries,
            sinks=instance.sinks,
            first_sink=instance.sinks[0],
        )

    def finish(self, nodes: tuple[WorkflowSourceNode, ...]) -> ExpandedWorkflowSource:
        try:
            canonical = canonical_json_bytes(
                _definition_mapping(self.root, nodes),
                max_bytes=self.limits.max_expanded_bytes,
            )
        except ValueError as exc:
            raise _issue(
                "include_expansion_limit",
                "expanded canonical definition exceeds the compilation limit",
            ) from exc
        return ExpandedWorkflowSource(
            nodes=nodes,
            include_aliases=self.aliases,
            dependencies=tuple(self.dependencies),
            source_bytes=self.source_bytes,
            canonical_definition_bytes=canonical,
        )


def _resolve_dependencies(
    dependencies: tuple[str, ...],
    local_targets: Mapping[str, tuple[str, ...]],
    namespace: tuple[str, ...],
) -> tuple[str, ...]:
    rewritten: list[str] = []
    for dependency in dependencies:
        targets = local_targets.get(dependency)
        if targets is None:
            targets = (_qualified(namespace, dependency),)
        rewritten.extend(targets)
    return _dedupe(rewritten)


def _replace_instance_node(
    nodes: list[WorkflowSourceNode], replacement: WorkflowSourceNode
) -> None:
    for index, node in enumerate(nodes):
        if node.id == replacement.id:
            nodes[index] = replacement
            return
    raise AssertionError("expanded instance lost a registered node")


def _expand_instance(
    source: WorkflowSourceDocument,
    catalog,
    state: _ExpansionState,
    *,
    namespace: tuple[str, ...],
    depth: int,
    active_keys: tuple[str, ...],
    logical_chain: tuple[str, ...],
) -> _ExpandedInstance:
    nodes: list[WorkflowSourceNode] = []
    direct_nodes: dict[str, WorkflowSourceNode] = {}
    included: list[tuple[WorkflowSourceNode, _ExpandedInstance]] = []
    local_targets: dict[str, tuple[str, ...]] = {}

    for authored in source.nodes:
        if authored.id in local_targets:
            raise _issue(
                "include_id_collision",
                f"authored workflow id collides: {authored.id}",
                node=authored,
            )
        if authored.node_type != "include":
            expanded = replace(
                authored,
                id=_qualified(namespace, authored.id),
                depends_on=(),
            )
            state.add_node(expanded)
            nodes.append(expanded)
            direct_nodes[authored.id] = expanded
            local_targets[authored.id] = (expanded.id,)
            continue

        child_depth = depth + 1
        if child_depth > state.limits.max_include_depth:
            chain = " -> ".join((*logical_chain, str(authored.value)))
            raise _issue(
                "include_depth_exceeded",
                f"workflow include depth exceeds the compilation limit: {chain}",
                node=authored,
            )
        target = str(authored.value)
        chain = " -> ".join((*logical_chain, target))
        if target in catalog.ambiguous_names:
            raise _issue(
                "include_ambiguous",
                f"workflow include target is ambiguous: {chain}",
                node=authored,
            )
        child = catalog.selected.get(target)
        if child is None:
            raise _issue(
                "include_not_found",
                f"workflow include target was not found: {chain}",
                node=authored,
            )
        child_key = _package_key(child)
        if child_key in active_keys:
            raise _issue(
                "include_cycle",
                f"workflow include cycle: {chain}",
                node=authored,
            )
        state.add_dependency(child, authored, chain)
        child_instance = _expand_instance(
            child,
            catalog,
            state,
            namespace=(*namespace, authored.id),
            depth=child_depth,
            active_keys=(*active_keys, child_key),
            logical_chain=(*logical_chain, child.name),
        )
        if not child_instance.nodes:
            raise _issue(
                "include_empty_graph",
                f"workflow include expands to no executable nodes: {target}",
                node=authored,
            )
        nodes.extend(child_instance.nodes)
        included.append((authored, child_instance))
        local_targets[authored.id] = child_instance.sinks

    for authored_id, expanded in direct_nodes.items():
        authored = source.nodes[expanded.source_index]
        dependencies = _resolve_dependencies(
            authored.depends_on, local_targets, namespace
        )
        replacement = replace(expanded, depends_on=dependencies)
        state.replace_node(replacement)
        _replace_instance_node(nodes, replacement)
        direct_nodes[authored_id] = replacement

    for authored, child_instance in included:
        parent_dependencies = _resolve_dependencies(
            authored.depends_on, local_targets, namespace
        )
        if parent_dependencies:
            for entry_id in child_instance.entries:
                entry = state.nodes_by_id[entry_id]
                options = dict(entry.options)
                options["trigger_rule"] = authored.options.get(
                    "trigger_rule", "all_success"
                )
                replacement = replace(
                    entry,
                    depends_on=_dedupe((*entry.depends_on, *parent_dependencies)),
                    options=options,
                )
                state.replace_node(replacement)
                _replace_instance_node(nodes, replacement)
        alias_id = _qualified(namespace, authored.id)
        state.add_alias(alias_id, child_instance, authored)

    node_ids = frozenset(node.id for node in nodes)
    entries = tuple(
        node.id
        for node in nodes
        if not any(dependency in node_ids for dependency in node.depends_on)
    )
    consumed = {
        dependency
        for node in nodes
        for dependency in node.depends_on
        if dependency in node_ids
    }
    sinks = tuple(node.id for node in nodes if node.id not in consumed)
    return _ExpandedInstance(nodes=tuple(nodes), entries=entries, sinks=sinks)


def expand_workflow_source(
    root: WorkflowSourceDocument,
    catalog,
    limits: WorkflowCompilationLimits = DEFAULT_COMPILATION_LIMITS,
) -> ExpandedWorkflowSource:
    """Resolve and flatten one immutable, depth-first include closure."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot

    if not isinstance(root, WorkflowSourceDocument):
        raise ValueError("root must be a workflow source document")
    if not isinstance(catalog, WorkflowCatalogSnapshot):
        raise ValueError("catalog must be an immutable workflow catalog snapshot")
    if not isinstance(limits, WorkflowCompilationLimits):
        raise ValueError("limits must be workflow compilation limits")
    state = _ExpansionState(root, limits)
    expanded = _expand_instance(
        root,
        catalog,
        state,
        namespace=(),
        depth=0,
        active_keys=(_package_key(root),),
        logical_chain=(root.name,),
    )
    return state.finish(expanded.nodes)
