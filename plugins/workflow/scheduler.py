"""Deterministic durable scheduler for the initial Bash DAG slice."""

from __future__ import annotations

import os
from pathlib import Path
import uuid

from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.executors.bash import BashExecutor
from plugins.workflow.resources import VariableContext
from plugins.workflow.schema import load_workflow
from plugins.workflow.sessions import NodeSessionRegistry
from plugins.workflow.store import RunStore


class RunScheduler:
    def __init__(
        self,
        store: RunStore,
        *,
        owner_id: str | None = None,
        agent_runner=None,
        session_registry: NodeSessionRegistry | None = None,
        profile_name: str = "default",
    ) -> None:
        self.store = store
        self.owner_id = owner_id or f"scheduler-{os.getpid()}-{uuid.uuid4().hex}"
        self.executors = {"bash": BashExecutor()}
        if agent_runner is not None:
            registry = session_registry or NodeSessionRegistry(store.hermes_home)
            ai_executor = AgentNodeExecutor(
                agent_runner,
                session_registry=registry,
                profile_name=profile_name,
            )
            self.executors.update({"command": ai_executor, "prompt": ai_executor})

    @staticmethod
    def _read_text(path: Path, *, limit: int = 500_000) -> str:
        data = path.read_bytes()
        if len(data) > limit:
            raise ValueError(f"workflow value exceeds {limit} bytes: {path}")
        return data.decode("utf-8")

    def _variables(self, projection: dict[str, object], run_directory: Path):
        arguments = ""
        manifest_path = run_directory / "inputs.json"
        if manifest_path.is_file():
            import json

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = manifest.get("arguments")
            if isinstance(entry, dict):
                arguments = self._read_text(run_directory / entry["relative_path"])
        outputs: dict[str, str] = {}
        for artifact in projection.get("artifacts", []):
            if not isinstance(artifact, dict):
                continue
            relative = str(artifact.get("relative_path", ""))
            if not Path(relative).name.startswith("output."):
                continue
            node_id = str(artifact.get("node_id", ""))
            try:
                outputs[node_id] = self._read_text(run_directory / relative)
            except (OSError, UnicodeError, ValueError):
                continue
        return VariableContext(
            arguments=arguments,
            user_message=arguments,
            artifacts_dir=run_directory / "artifacts",
            workflow_id=str(projection["run_id"]),
            base_branch="base",
            docs_dir=run_directory / "docs",
            node_outputs=outputs,
        )

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
                    workflow_name=package.definition.name,
                    workflow_options=package.definition.options,
                    variable_context=self._variables(
                        projection, self.store.run_directory(run_id)
                    ),
                    predecessor_results={
                        dependency: {
                            field: projection["nodes"][dependency][field]
                            for field in ("session_id", "cache_fingerprint")
                            if field in projection["nodes"][dependency]
                        }
                        for dependency in node.depends_on
                    },
                    operator_scope=str(
                        projection.get("operator_scope_digest") or "local"
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
                    metadata=result.metadata,
                )
            except RuntimeError as exc:
                if "terminal run" not in str(exc):
                    raise
            executed += 1
        return self.store.load_run(run_id)
