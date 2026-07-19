from __future__ import annotations

import json
from pathlib import Path


def test_desktop_default_test_covers_workflow_renderer_and_platform_contracts() -> None:
    package = json.loads(
        (Path(__file__).parents[1] / "apps/desktop/package.json").read_text(
            encoding="utf-8"
        )
    )
    scripts = package["scripts"]

    assert "test:workflow-ui" in scripts["test"]
    assert "test:desktop:platforms" in scripts["test"]
    assert "activity-board.performance.test.tsx" in scripts["test:workflow-ui"]
    assert "app/workflows/index.test.tsx" in scripts["test:workflow-ui"]
    assert "lib/hermes-api.test.ts" in scripts["test:workflow-ui"]
    assert "electron/structured-api-channel.test.ts" in scripts[
        "test:desktop:platforms"
    ]
    assert "electron/structured-api-response.test.ts" in scripts[
        "test:desktop:platforms"
    ]
