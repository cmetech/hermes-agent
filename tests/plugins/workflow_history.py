"""Helpers for tests that exercise already-admitted workflow semantics."""

from __future__ import annotations

from pathlib import Path

from plugins.workflow.schema import load_workflow, load_workflow_snapshot


def load_recorded_v4_workflow(path: str | Path):
    """Load Archon fixtures with their pre-Phase-5 normalizer contract."""
    workflow_path = Path(path)
    sidecar_path = workflow_path.with_name(f"{workflow_path.stem}.hermes.yaml")
    sidecar_bytes = sidecar_path.read_bytes() if sidecar_path.exists() else None
    if sidecar_bytes is not None and b"archon-2026-07" in sidecar_bytes:
        return load_workflow_snapshot(
            workflow_path,
            workflow_bytes=workflow_path.read_bytes(),
            sidecar_bytes=sidecar_bytes,
            normalizer_version=4,
        )
    return load_workflow(workflow_path)
