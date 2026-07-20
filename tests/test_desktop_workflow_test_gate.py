from __future__ import annotations

import json
from pathlib import Path
import re


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
    assert scripts["test:workflow-ui"].count("app/workflows/index.test.tsx") == 1
    assert "lib/hermes-api.test.ts" in scripts["test:workflow-ui"]
    assert "electron/structured-api-channel.test.ts" in scripts[
        "test:desktop:platforms"
    ]
    assert "electron/structured-api-response.test.ts" in scripts[
        "test:desktop:platforms"
    ]


def test_task5_workflow_docs_do_not_promise_the_unmounted_desktop_trigger() -> None:
    docs = (
        Path(__file__).parents[1]
        / "website/docs/user-guide/features/workflows.md"
    ).read_text(encoding="utf-8")

    assert re.search(r"Desktop catalog[^.]*discovery only", docs, re.IGNORECASE)
    assert re.search(r"Review & Run[^.]*Task 6 trigger flow", docs, re.IGNORECASE)
    assert "Use the Desktop **Run** action" not in docs
    assert re.search(
        r"Approval and rejection[^.]*input require an expected state version",
        docs,
        re.IGNORECASE,
    )
    assert re.search(
        r"Retry and reconciliation accept one", docs, re.IGNORECASE
    )
    assert "resume, cancel, and abandon use compare-and-set" not in docs.lower()
