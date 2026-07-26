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

    # This gate asserts COVERAGE, not script composition.
    #
    # It used to require that `test` textually chained `test:workflow-ui` and
    # `test:desktop:platforms`. The desktop suites were then restructured: the
    # default `test` became a bare, unscoped `vitest run` (which executes every
    # declared project, so it covers strictly MORE than the old chain), and the
    # four electron files that use the node:test runner moved to a separate
    # `test:electron:node` script because vitest cannot execute them at all.
    #
    # The old string matching therefore failed while the coverage it existed to
    # protect was intact -- a false alarm that is worse than no gate. These
    # assertions follow the coverage instead.
    config = (Path(__file__).parents[1] / "apps/desktop/vitest.config.ts").read_text(
        encoding="utf-8"
    )

    # (1) The default run must not be narrowed to a single project, or one of
    #     the two halves silently stops running.
    assert re.search(r"\bvitest run\b", scripts["test"])
    assert "--project" not in scripts["test"], (
        "default `test` is scoped to one project; the other project's tests "
        f"would stop running: {scripts['test']!r}"
    )
    # (2) Both halves must actually be declared as projects for (1) to mean
    #     anything: the renderer UI and the electron platform contracts.
    assert "name: 'ui'" in config
    assert "name: 'electron'" in config

    # (3) The workflow renderer suites still exist and are still picked up by
    #     the ui project's include glob, which is what makes (1) cover them.
    #     Asserting existence + glob membership rather than a literal mention
    #     in test:workflow-ui: that script switched from enumerating files to
    #     naming directories, and src/lib/hermes-api.test.ts is no longer named
    #     by it at all -- yet all three still run under the default `vitest
    #     run`, which is the property this gate is defending.
    ui_include = re.search(r"include:\s*\['([^']+)'\]", config)
    assert ui_include and ui_include.group(1) == "src/**/*.test.{ts,tsx}", (
        "ui project include glob changed; re-verify the files below still run"
    )

    desktop = Path(__file__).parents[1] / "apps/desktop"
    for relative in (
        "src/components/activity-board/activity-board.performance.test.tsx",
        "src/app/workflows/index.test.tsx",
        "src/lib/hermes-api.test.ts",
    ):
        path = desktop / relative
        assert path.is_file(), f"gated workflow test disappeared: {relative}"
        assert relative.startswith("src/") and relative.endswith((".test.ts", ".test.tsx")), (
            f"{relative} no longer matches the ui include glob, so the default "
            "run would skip it"
        )

    # (4) The structured-api contracts use the node:test runner, so vitest
    #     cannot run them; they must be covered by the dedicated script or they
    #     are executed by nothing.
    assert "electron/structured-api-channel.test.ts" in scripts["test:electron:node"]
    assert "electron/structured-api-response.test.ts" in scripts["test:electron:node"]


def test_workflow_docs_describe_bundled_showcase_desktop_policy() -> None:
    docs = (
        Path(__file__).parents[1]
        / "website/docs/user-guide/features/workflows.md"
    ).read_text(encoding="utf-8")

    assert "Bundled showcase" in docs
    assert "Verified bundle" in docs
    assert re.search(
        r"approval-gate[^.]*Attention[^.]*Approve", docs, re.IGNORECASE
    )
    assert re.search(r"laptop-diagnostic[^.]*CLI", docs, re.IGNORECASE)
    assert re.search(r"ai-extensions[^.]*CLI", docs, re.IGNORECASE)
    assert re.search(r"scheduling[^.]*CLI", docs, re.IGNORECASE)
    assert "trust the bundled showcase" not in docs.lower()
    assert re.search(
        r"Approval and rejection[^.]*input require an expected state version",
        docs,
        re.IGNORECASE,
    )
    assert re.search(
        r"Retry and reconciliation accept one", docs, re.IGNORECASE
    )
    assert "resume, cancel, and abandon use compare-and-set" not in docs.lower()
