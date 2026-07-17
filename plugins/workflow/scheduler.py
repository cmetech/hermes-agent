"""Deterministic durable scheduler for the initial Bash DAG slice."""

from __future__ import annotations

import os
import uuid

from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.executors.bash import BashExecutor
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


class RunScheduler:
    def __init__(self, store: RunStore, *, owner_id: str | None = None) -> None:
        self.store = store
        self.owner_id = owner_id or f"scheduler-{os.getpid()}-{uuid.uuid4().hex}"
        self.executors = {"bash": BashExecutor()}

    def advance(self, run_id: str, *, max_nodes: int | None = None):
        executed = 0
        while max_nodes is None or executed < max_nodes:
            projection = self.store.load_run(run_id)
            if projection["status"] == "queued":
                if not self.store.try_promote_run(run_id):
                    break
                projection = self.store.load_run(run_id)
            if projection["status"] in {
                "succeeded",
                "failed",
                "cancelled",
                "abandoned",
            }:
                break
            ready = sorted(
                node_id
                for node_id, node in projection["nodes"].items()
                if node["state"] == "ready"
            )
            if not ready:
                break
            node_id = ready[0]
            claim = self.store.claim_node(run_id, node_id, self.owner_id)
            if claim is None:
                continue
            package = load_workflow(
                self.store.run_directory(run_id) / "definition.yaml"
            )
            node = next(node for node in package.definition.nodes if node.id == node_id)
            executor = self.executors.get(node.node_type)
            if executor is None:
                self.store.complete_node(
                    claim,
                    status="failed",
                    error_code="unsupported_executor",
                    error_message=f"no executor for {node.node_type}",
                )
                break
            self.store.mark_node_started(claim)
            timeout = float(node.options.get("timeout", 120.0))
            result = executor.execute(
                NodeExecutionContext(
                    run_id=run_id,
                    run_directory=self.store.run_directory(run_id),
                    node=node,
                    attempt_id=claim.attempt_id,
                    timeout_seconds=timeout,
                    is_cancelled=lambda: (
                        self.store.load_run(run_id)["status"] == "cancelled"
                    ),
                )
            )
            try:
                self.store.complete_node(
                    claim,
                    status=result.status,
                    artifacts=result.artifacts,
                    error_code=result.error_code,
                    error_message=result.error_message,
                )
            except RuntimeError as exc:
                if "terminal run" not in str(exc):
                    raise
            executed += 1
        return self.store.load_run(run_id)
