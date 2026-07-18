"""Typed, bounded workflow evidence queries."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from plugins.workflow.sanitize import sanitize_projection, sanitize_text


EVIDENCE_KINDS = frozenset({
    "timeline",
    "interactions",
    "attempts",
    "logs",
    "outputs",
    "artifacts",
    "recovery",
    "coordinator",
    "cleanup",
})


class EvidenceReader:
    def __init__(self, store) -> None:
        self.store = store

    def query(
        self,
        run_id: str,
        *,
        kind: str,
        after: int = 0,
        limit: int = 100,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        if kind not in EVIDENCE_KINDS:
            raise ValueError("unsupported evidence kind")
        if after < 0 or not 1 <= limit <= 200:
            raise ValueError("invalid evidence cursor or limit")
        run = self.store.get_run_status(run_id, operator_scope=operator_scope)
        if kind == "timeline":
            page = self.store.events_after(
                run_id,
                after=after,
                limit=limit,
                operator_scope=operator_scope,
            )
            return {
                "schema_version": 1,
                "kind": kind,
                "items": sanitize_projection(page["events"]),
                "next_cursor": page["next_cursor"],
                "truncated": len(page["events"]) == limit,
            }
        items = self._items(run_id, run, kind=kind, operator_scope=operator_scope)
        page = items[after : after + limit]
        return {
            "schema_version": 1,
            "kind": kind,
            "items": sanitize_projection(page),
            "next_cursor": after + len(page),
            "truncated": after + len(page) < len(items),
        }

    def _items(self, run_id, run, *, kind, operator_scope):
        nodes = run.get("nodes", {})
        node_items = nodes.items() if isinstance(nodes, Mapping) else ()
        if kind == "interactions":
            historical = [
                event
                for event in self.store.tail_events(
                    run_id, limit=200, operator_scope=operator_scope
                )
                if str(event.get("event_type", "")).startswith("interaction_")
                or event.get("event_type") == "loop_input_provided"
            ]
            pending_items = [
                {"node_id": node_id, **pending}
                for node_id, node in node_items
                if isinstance(node, Mapping)
                and isinstance((pending := node.get("pending_interaction")), Mapping)
            ]
            return [*historical, *pending_items]
        if kind == "attempts":
            return [
                {"node_id": node_id, **attempt}
                for node_id, node in node_items
                if isinstance(node, Mapping)
                for attempt in node.get("attempts", [])
                if isinstance(attempt, Mapping)
            ]
        if kind == "outputs":
            return [
                {"node_id": node_id, "output": node["output"]}
                for node_id, node in node_items
                if isinstance(node, Mapping) and node.get("output") is not None
            ]
        if kind == "artifacts":
            return list(run.get("artifacts", []))
        if kind == "recovery":
            return [
                {"node_id": node_id, "recovery": node["recovery"]}
                for node_id, node in node_items
                if isinstance(node, Mapping)
                and isinstance(node.get("recovery"), Mapping)
            ]
        if kind == "coordinator":
            return [
                {
                    "coordinator": run.get("coordinator"),
                    "execution_mode": run.get("execution_mode"),
                    "health": run.get("health"),
                    "blocking_reason": run.get("blocking_reason"),
                    "last_semantic_progress_at": run.get("last_semantic_progress_at"),
                }
            ]
        if kind == "cleanup":
            return list(
                self.store.cleanup_history(run_id, operator_scope=operator_scope)
            )
        if kind == "logs":
            directory = self.store.run_directory(run_id, operator_scope=operator_scope)
            return self._logs(directory)
        return []

    @staticmethod
    def _logs(directory: Path) -> list[dict[str, object]]:
        items = []
        remaining = 256 * 1024
        for path in sorted((directory / "nodes").glob("*/*/std*.txt")):
            if remaining <= 0:
                break
            data = path.read_bytes()[:remaining]
            remaining -= len(data)
            text, truncated = sanitize_text(data.decode("utf-8", errors="replace"))
            items.append({
                "node_id": path.parent.parent.name,
                "attempt_id": path.parent.name,
                "stream": "stderr" if path.name == "stderr.txt" else "stdout",
                "text": text,
                "bytes_returned": len(data),
                "truncated": truncated or path.stat().st_size > len(data),
            })
        return items


__all__ = ["EVIDENCE_KINDS", "EvidenceReader"]
