"""Bounded safe topology projections derived only from normalized DAG data."""

from __future__ import annotations

import heapq
import unicodedata
from dataclasses import dataclass

from plugins.workflow.models import WorkflowDefinition, WorkflowNode

_TEXT_LIMIT = 12 * 1024
_LABEL_LIMIT = 80
_MERMAID_NODE_LIMIT = 100
_MERMAID_EDGE_LIMIT = 200
_MERMAID_BYTE_LIMIT = 64 * 1024
_SAFE_PUNCTUATION = frozenset(" -_.:/()")


@dataclass(frozen=True)
class TopologyProjection:
    text: str
    mermaid: str | None
    warnings: tuple[str, ...]
    node_count: int
    edge_count: int


def sanitize_topology_label(
    value: str, *, limit: int = _LABEL_LIMIT
) -> tuple[str, bool]:
    """Return a directive-safe label limited by Unicode code points."""
    safe = "".join(
        character
        if unicodedata.category(character)[0] in {"L", "N"}
        or character in _SAFE_PUNCTUATION
        else "_"
        for character in str(value)
    )
    if len(safe) <= limit:
        return safe, False
    return f"{safe[: limit - 1]}…", True


def _stable_layers(
    definition: WorkflowDefinition,
) -> tuple[tuple[WorkflowNode, ...], ...]:
    by_id = {node.id: node for node in definition.nodes}
    indegree = {node.id: len(set(node.depends_on)) for node in definition.nodes}
    outgoing: dict[str, list[str]] = {node.id: [] for node in definition.nodes}
    for node in definition.nodes:
        for dependency in set(node.depends_on):
            outgoing[dependency].append(node.id)
    for targets in outgoing.values():
        targets.sort(key=lambda node_id: (by_id[node_id].source_index, node_id))
    ready = [
        (node.source_index, node.id)
        for node in definition.nodes
        if indegree[node.id] == 0
    ]
    heapq.heapify(ready)
    layers: list[tuple[WorkflowNode, ...]] = []
    while ready:
        current = [heapq.heappop(ready) for _ in range(len(ready))]
        layer = tuple(by_id[node_id] for _, node_id in current)
        layers.append(layer)
        next_ready: list[tuple[int, str]] = []
        for node in layer:
            for target in outgoing[node.id]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    heapq.heappush(next_ready, (by_id[target].source_index, target))
        ready = next_ready
    return tuple(layers)


def _bounded_utf8(value: str, limit: int, suffix: str) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    suffix_bytes = suffix.encode("utf-8")
    prefix = encoded[: max(0, limit - len(suffix_bytes))]
    while prefix:
        try:
            return prefix.decode("utf-8") + suffix
        except UnicodeDecodeError:
            prefix = prefix[:-1]
    return suffix_bytes[:limit].decode("utf-8", errors="ignore")


def project_topology(definition: WorkflowDefinition) -> TopologyProjection:
    layers = _stable_layers(definition)
    ordered = tuple(node for layer in layers for node in layer)
    aliases = {node.id: f"n{index}" for index, node in enumerate(ordered)}
    edges = tuple(
        sorted(
            (
                (dependency, node.id)
                for node in definition.nodes
                for dependency in set(node.depends_on)
            ),
            key=lambda edge: (int(aliases[edge[0]][1:]), int(aliases[edge[1]][1:])),
        )
    )
    text_parts: list[str] = []
    for layer in layers:
        labels = [sanitize_topology_label(node.id)[0] for node in layer]
        text_parts.append(labels[0] if len(labels) == 1 else f"[{', '.join(labels)}]")
    raw_text = " -> ".join(text_parts)
    warnings: list[str] = []
    suffix = f" … [topology omitted: {len(ordered)} nodes, {len(edges)} edges]"
    text = _bounded_utf8(raw_text, _TEXT_LIMIT, suffix)
    if text != raw_text:
        warnings.append("topology_text_truncated")

    labels: dict[str, str] = {}
    any_label_truncated = False
    for node in ordered:
        label, truncated = sanitize_topology_label(f"{node.id} ({node.node_type})")
        labels[node.id] = label
        any_label_truncated = any_label_truncated or truncated
    if any_label_truncated:
        warnings.append("topology_label_truncated")

    mermaid_reasons: list[str] = []
    if len(ordered) > _MERMAID_NODE_LIMIT:
        mermaid_reasons.append("topology_mermaid_too_many_nodes")
    if len(edges) > _MERMAID_EDGE_LIMIT:
        mermaid_reasons.append("topology_mermaid_too_many_edges")
    lines = ["flowchart LR"]
    lines.extend(f'{aliases[node.id]}["{labels[node.id]}"]' for node in ordered)
    lines.extend(f"{aliases[source]} --> {aliases[target]}" for source, target in edges)
    candidate = "\n".join(lines)
    if len(candidate.encode("utf-8")) > _MERMAID_BYTE_LIMIT:
        mermaid_reasons.append("topology_mermaid_too_large")
    warnings.extend(mermaid_reasons)
    return TopologyProjection(
        text=text,
        mermaid=None if mermaid_reasons else candidate,
        warnings=tuple(warnings),
        node_count=len(ordered),
        edge_count=len(edges),
    )
