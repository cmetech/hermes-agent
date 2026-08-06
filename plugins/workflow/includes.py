"""Bounded compile-time expansion for literal workflow includes."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from agent.structured_output import canonical_json_bytes
from plugins.workflow.bash_rendering import BashRenderingError, bash_output_references
from plugins.workflow.language_schema import (
    OutputReferenceToken,
    WorkflowReferenceSyntaxError,
    iter_output_references,
    iter_when_output_references,
)
from plugins.workflow.models import (
    ExpandedWorkflowSource,
    ValidationIssue,
    WorkflowCompilationLimits,
    WorkflowIncludeAlias,
    WorkflowNodeOrigin,
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
    field: str | None = None,
) -> WorkflowValidationError:
    if node is None:
        path = "nodes"
        source_line = None
    else:
        source_field = field or node.node_type
        path = f"nodes[{node.source_index}].{source_field}"
        source_line = node.field_lines.get(source_field, node.source_line)
    return WorkflowValidationError(
        ValidationIssue(
            path=path,
            code=code,
            message=message,
            source_line=source_line,
        )
    )


def _reference_issue(
    code: str,
    message: str,
    *,
    node: WorkflowSourceNode,
    field: str,
) -> WorkflowValidationError:
    if node.origin is not None and node.origin.include_instance_path:
        instance = "/".join(node.origin.include_instance_path)
        path = (
            f"include[{instance}]/{node.origin.definition_location}:"
            f"nodes[{node.origin.source_index}].{field}"
        )
    else:
        path = f"nodes[{node.source_index}].{field}"
    top_field = field.split(".", 1)[0]
    line = node.field_lines.get(
        field,
        node.field_lines.get(top_field, node.source_line),
    )
    return WorkflowValidationError(
        ValidationIssue(
            path=path,
            code=code,
            message=message,
            source_line=line,
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


def _expanded_origin(
    source: WorkflowSourceDocument,
    node: WorkflowSourceNode,
    namespace: tuple[str, ...],
    expanded_node_id: str,
) -> WorkflowNodeOrigin:
    return WorkflowNodeOrigin(
        include_instance_path=namespace,
        package_key=_package_key(source),
        workflow_name=source.name,
        catalog_source=source.source,
        precedence=source.precedence,
        definition_location=source.definition_location,
        source_index=node.source_index,
        source_line=node.source_line,
        expanded_node_id=expanded_node_id,
    )


def _dedupe(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def rewrite_reference_tokens(
    template: str,
    tokens: Iterable[OutputReferenceToken],
    rename_node: Callable[[str], str],
) -> str:
    """Rewrite parsed output references without rescanning replacements."""
    rewritten = template
    for token in reversed(tuple(tokens)):
        replacement = f"${rename_node(token.node_id)}.output"
        if token.path:
            replacement += "." + ".".join(token.path)
        rewritten = rewritten[: token.start] + replacement + rewritten[token.end :]
    return rewritten


def _rewrite_text(
    template: str,
    rename_node: Callable[[str], str],
    *,
    bash: bool = False,
    when: bool = False,
) -> str:
    if bash:
        tokens = bash_output_references(template, normalizer_version=4)
    elif when:
        tokens = tuple(
            iter_when_output_references(template, normalizer_version=4)
        )
    else:
        tokens = tuple(iter_output_references(template, normalizer_version=4))
    return rewrite_reference_tokens(template, tokens, rename_node)


def _rewrite_agents(
    value: Any,
    rewrite_field: Callable[[str, str], str],
) -> Any:
    if not isinstance(value, Mapping):
        return value
    rewritten: dict[str, Any] = {}
    for agent_id, raw_agent in value.items():
        if not isinstance(raw_agent, Mapping):
            rewritten[str(agent_id)] = _thaw(raw_agent)
            continue
        agent = _thaw(raw_agent)
        for field in ("description", "prompt"):
            template = agent.get(field)
            if isinstance(template, str):
                agent[field] = rewrite_field(
                    template,
                    f"agents.{agent_id}.{field}",
                )
        rewritten[str(agent_id)] = agent
    return rewritten


def _rewrite_hooks(
    value: Any,
    rewrite_field: Callable[[str, str], str],
) -> Any:
    if not isinstance(value, Mapping):
        return value
    rewritten = _thaw(value)
    for event, entries in rewritten.items():
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            response = entry.get("response")
            if not isinstance(response, dict):
                continue
            for field in ("systemMessage", "stopReason"):
                template = response.get(field)
                if isinstance(template, str):
                    response[field] = rewrite_field(
                        template,
                        f"hooks.{event}[{index}].response.{field}",
                    )
            specific = response.get("hookSpecificOutput")
            if not isinstance(specific, dict):
                continue
            for field in ("permissionDecisionReason", "additionalContext"):
                template = specific.get(field)
                if isinstance(template, str):
                    specific[field] = rewrite_field(
                        template,
                        f"hooks.{event}[{index}].response."
                        f"hookSpecificOutput.{field}",
                    )
    return rewritten


def rewrite_expanded_node(
    node: WorkflowSourceNode,
    namespace: tuple[str, ...],
    include_aliases: Mapping[str, WorkflowIncludeAlias],
) -> WorkflowSourceNode:
    """Rewrite one executable node using its complete include-instance namespace."""
    if not isinstance(node, WorkflowSourceNode):
        raise ValueError("node must be a workflow source node")

    def rename_node(node_id: str) -> str:
        qualified = _qualified(namespace, node_id)
        alias = include_aliases.get(qualified)
        local_node_ids = getattr(include_aliases, "local_node_ids", None)
        if namespace and local_node_ids is not None and node_id not in local_node_ids:
            raise _IncludeReferenceInvalid(
                f"output reference escapes include instance: {node_id}"
            )
        return alias.first_sink if alias is not None else qualified

    def rewrite_field(
        template: str,
        field: str,
        *,
        bash: bool = False,
        when: bool = False,
    ) -> str:
        try:
            return _rewrite_text(
                template,
                rename_node,
                bash=bash,
                when=when,
            )
        except WorkflowReferenceSyntaxError as exc:
            start = exc.start if exc.start is not None else 0
            local_alias_ids = getattr(include_aliases, "local_alias_ids", ())
            if any(
                template.startswith(f"${alias_id}.", start)
                and not template.startswith(f"${alias_id}.output", start)
                for alias_id in local_alias_ids
            ):
                raise _reference_issue(
                    "include_reference_invalid",
                    "include output references cannot address a deep child",
                    node=node,
                    field=field,
                ) from exc
            raise _reference_issue(
                exc.code,
                str(exc),
                node=node,
                field=field,
            ) from exc
        except _IncludeReferenceInvalid as exc:
            raise _reference_issue(
                "include_reference_invalid",
                str(exc),
                node=node,
                field=field,
            ) from exc
        except BashRenderingError as exc:
            raise _reference_issue(
                exc.code,
                str(exc),
                node=node,
                field=field,
            ) from exc

    value = _thaw(node.value)
    if node.node_type == "prompt" and isinstance(value, str):
        value = rewrite_field(value, "prompt")
    elif node.node_type == "bash" and isinstance(value, str):
        value = rewrite_field(value, "bash", bash=True)
    elif node.node_type == "script" and isinstance(value, str) and "$" in value:
        value = rewrite_field(value, "script")
    elif node.node_type == "loop" and isinstance(value, dict):
        for field in ("prompt", "gate_message"):
            template = value.get(field)
            if isinstance(template, str):
                value[field] = rewrite_field(template, f"loop.{field}")
        until_bash = value.get("until_bash")
        if isinstance(until_bash, str):
            value["until_bash"] = rewrite_field(
                until_bash,
                "loop.until_bash",
                bash=True,
            )
    elif node.node_type == "approval" and isinstance(value, dict):
        message = value.get("message")
        if isinstance(message, str):
            value["message"] = rewrite_field(message, "approval.message")
        on_reject = value.get("on_reject")
        if isinstance(on_reject, dict):
            prompt = on_reject.get("prompt")
            if isinstance(prompt, str):
                on_reject["prompt"] = rewrite_field(
                    prompt,
                    "approval.on_reject.prompt",
                )

    options = _thaw(node.options)
    when = options.get("when")
    if isinstance(when, str):
        options["when"] = rewrite_field(when, "when", when=True)
    system_prompt = options.get("systemPrompt")
    if isinstance(system_prompt, str):
        options["systemPrompt"] = rewrite_field(system_prompt, "systemPrompt")
    if "agents" in options:
        options["agents"] = _rewrite_agents(options["agents"], rewrite_field)
    if "hooks" in options:
        options["hooks"] = _rewrite_hooks(options["hooks"], rewrite_field)
    return replace(node, value=value, options=options)


class _IncludeReferenceInvalid(ValueError):
    pass


class _InstanceReferenceAliases(Mapping[str, WorkflowIncludeAlias]):
    """Internal alias view that also bounds legal child-local identifiers."""

    def __init__(
        self,
        aliases: Mapping[str, WorkflowIncludeAlias],
        *,
        local_node_ids: Iterable[str],
        local_alias_ids: Iterable[str],
    ) -> None:
        self._aliases = dict(aliases)
        self.local_node_ids = frozenset(local_node_ids)
        self.local_alias_ids = frozenset(local_alias_ids)

    def __getitem__(self, key: str) -> WorkflowIncludeAlias:
        return self._aliases[key]

    def __iter__(self):
        return iter(self._aliases)

    def __len__(self) -> int:
        return len(self._aliases)


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

    def _canonical_length(
        self,
        value: Any,
        message: str,
        *,
        node: WorkflowSourceNode | None = None,
        field: str | None = None,
    ) -> int:
        if self.limits.max_expanded_bytes == 0:
            raise _issue(
                "include_expansion_limit", message, node=node, field=field
            )
        try:
            return len(
                canonical_json_bytes(
                    value,
                    max_bytes=self.limits.max_expanded_bytes,
                )
            )
        except ValueError as exc:
            raise _issue(
                "include_expansion_limit", message, node=node, field=field
            ) from exc

    def _check_projected_bytes(
        self,
        node_bytes: int,
        node_count: int,
        *,
        node: WorkflowSourceNode | None = None,
    ) -> None:
        projected = self.base_canonical_bytes + node_bytes + max(0, node_count - 1)
        if projected > self.limits.max_expanded_bytes:
            raise _issue(
                "include_expansion_limit",
                "expanded canonical definition exceeds the compilation limit",
                node=node,
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
                field="include",
            )
        projected_source_bytes = self.source_bytes + len(source.definition_bytes)
        if projected_source_bytes > self.limits.max_source_bytes:
            raise _issue(
                "include_expansion_limit",
                "selected workflow source bytes exceed the compilation limit",
                node=node,
                field="include",
            )
        self.dependency_keys.add(key)
        self.dependencies.append(source)
        self.source_bytes = projected_source_bytes

    def reserve_node(self, node: WorkflowSourceNode) -> None:
        if node.id in self.nodes_by_id or node.id in self.aliases:
            raise _issue(
                "include_id_collision",
                f"expanded workflow node id collides: {node.id}",
                node=node,
                field="id",
            )
        if len(self.nodes_by_id) >= self.limits.max_nodes:
            raise _issue(
                "include_expansion_limit",
                "expanded workflow node count exceeds the compilation limit",
                node=node,
                field="id",
            )
        self.nodes_by_id[node.id] = node
        self.node_byte_lengths[node.id] = 0
        self.edge_counts[node.id] = 0

    def replace_node(self, node: WorkflowSourceNode) -> None:
        old_edges = self.edge_counts[node.id]
        new_edges = len(node.depends_on)
        projected_edges = self.edge_count - old_edges + new_edges
        if projected_edges > self.limits.max_edges:
            raise _issue(
                "include_expansion_limit",
                "expanded workflow edge count exceeds the compilation limit",
                node=node,
                field="depends_on",
            )
        length = self._canonical_length(
            _node_mapping(node),
            "expanded canonical definition exceeds the compilation limit",
            node=node,
            field=node.node_type,
        )
        projected_node_bytes = (
            self.node_bytes - self.node_byte_lengths[node.id] + length
        )
        self._check_projected_bytes(
            projected_node_bytes,
            len(self.nodes_by_id),
            node=node,
        )
        self.nodes_by_id[node.id] = node
        self.node_byte_lengths[node.id] = length
        self.edge_counts[node.id] = new_edges
        self.node_bytes = projected_node_bytes
        self.edge_count = projected_edges

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
                field="id",
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
                field="id",
            )
        if authored.node_type != "include":
            expanded_id = _qualified(namespace, authored.id)
            expanded = replace(
                authored,
                id=expanded_id,
                depends_on=(),
                origin=_expanded_origin(
                    source,
                    authored,
                    namespace,
                    expanded_id,
                ),
            )
            state.reserve_node(expanded)
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
                field="include",
            )
        target = str(authored.value)
        chain = " -> ".join((*logical_chain, target))
        if target in catalog.ambiguous_names:
            raise _issue(
                "include_ambiguous",
                f"workflow include target is ambiguous: {chain}",
                node=authored,
                field="include",
            )
        child = catalog.selected.get(target)
        if child is None:
            raise _issue(
                "include_not_found",
                f"workflow include target was not found: {chain}",
                node=authored,
                field="include",
            )
        child_key = _package_key(child)
        if child_key in active_keys:
            raise _issue(
                "include_cycle",
                f"workflow include cycle: {chain}",
                node=authored,
                field="include",
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
                field="include",
            )
        if not child_instance.entries or not child_instance.sinks:
            raise _issue(
                "include_empty_graph",
                f"workflow include has no executable entry or sink: {target}",
                node=authored,
                field="include",
            )
        nodes.extend(child_instance.nodes)
        included.append((authored, child_instance))
        local_targets[authored.id] = child_instance.sinks

    instance_alias_values = dict(state.aliases)
    local_alias_ids: list[str] = []
    for authored, child_instance in included:
        local_alias_ids.append(authored.id)
        instance_alias_values[_qualified(namespace, authored.id)] = WorkflowIncludeAlias(
            entries=child_instance.entries,
            sinks=child_instance.sinks,
            first_sink=child_instance.sinks[0],
        )
    instance_aliases = _InstanceReferenceAliases(
        instance_alias_values,
        local_node_ids=local_targets,
        local_alias_ids=local_alias_ids,
    )

    for authored_id, expanded in direct_nodes.items():
        authored = source.nodes[expanded.source_index]
        dependencies = _resolve_dependencies(
            authored.depends_on, local_targets, namespace
        )
        replacement = rewrite_expanded_node(
            replace(expanded, depends_on=dependencies),
            namespace,
            instance_aliases,
        )
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


def collect_include_edges(
    root: WorkflowSourceDocument,
    catalog,
) -> tuple[dict[str, object], ...]:
    """Project exact logical include edges without rereading mutable sources."""
    edges: list[dict[str, object]] = []

    def visit(
        source: WorkflowSourceDocument,
        namespace: tuple[str, ...],
        active_keys: tuple[str, ...],
    ) -> None:
        for node in source.nodes:
            if node.node_type != "include":
                continue
            child = catalog.selected.get(str(node.value))
            if child is None:
                raise _issue(
                    "include_not_found",
                    f"workflow include target was not found: {node.value}",
                    node=node,
                    field="include",
                )
            child_key = _package_key(child)
            if child_key in active_keys:
                raise _issue(
                    "include_cycle",
                    f"workflow include cycle: {source.name} -> {child.name}",
                    node=node,
                    field="include",
                )
            instance_path = (*namespace, node.id)
            edges.append({
                "include_instance_path": list(instance_path),
                "source_package_key": _package_key(source),
                "source_node_id": node.id,
                "target_package_key": child_key,
            })
            visit(child, instance_path, (*active_keys, child_key))

    visit(root, (), (_package_key(root),))
    return tuple(edges)
