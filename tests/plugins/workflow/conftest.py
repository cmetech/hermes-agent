from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture
def workflow_writer():
    def _write(
        root: Path,
        *,
        name: str = "example",
        description: str = "Portable workflow fixture",
        nodes: list[dict[str, Any]] | None = None,
        filename: str = "example.yaml",
        **options: Any,
    ) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        document: dict[str, Any] = {
            "name": name,
            "description": description,
            "nodes": nodes if nodes is not None else [{"id": "start", "bash": "true"}],
            **options,
        }
        path = root / filename
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        return path

    return _write
