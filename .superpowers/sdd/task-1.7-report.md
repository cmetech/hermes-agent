# Task 1.7 report — Workflow API catalog selection

## Reader and action

**Reader:** an API client author who can see a bundled showcase and a user
workflow with the same name.

**Post-read action:** send `catalog_source=showcase` on detail or run requests
when the verified bundled showcase is the intended package; leave it absent
only when the user-precedence package is intended.

## Before state

The user guide explained that Desktop displays colliding user and showcase
packages as separate rows and that the badges identify the View/Run target.
Its CLI/API-adjacent discovery section did not explain the name-only behavior
of API detail and run requests, so a new API client could select the wrong
package in a name collision.

## Change

Added one short `Select a package through the API` subsection immediately
after the CLI discovery commands. It states both selection rules without
paths or line numbers: name-only detail/run uses the user-precedence package;
`catalog_source=showcase` selects the verified bundled showcase.

The customization manifest already lists the guide under the workflow
Desktop/catalog entries, so this documentation-only surface did not require a
ledger amendment.

## Reader test and self-review

Cold read: a client author learns that a colliding name alone does **not** mean
the verified showcase, and has the exact selector needed to request it. The
note is adjacent to the existing CLI/API discovery guidance, distinguishes the
default from the explicit action, and does not imply a trust-state change or
expose implementation paths.

## Verification

| Command | Result |
| --- | --- |
| `scripts/run_tests.sh tests/test_desktop_workflow_test_gate.py -q` | Passed: 2 tests, 0 failures. |
| CI-equivalent skill extraction/generation, then `npm run lint:diagrams` in `website/` | Passed: 363 files checked, 0 errors. Extraction reported the pre-existing absent unified skill index and used the legacy cache. |
| `npm run build` in `website/` | Passed after `npm ci`; Docusaurus completed the production build. Local prebuild fell back to empty generated skill/blueprint indexes because its system Python lacks PyYAML, but the build completed and left no tracked generated changes. |
| `.venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml` | Passed with no findings; the existing manifest already covers the guide. |
| `git diff --cached --check` | Passed with no output. |

## Files

- `website/docs/user-guide/features/workflows.md`
- `.superpowers/sdd/task-1.7-report.md`

## Concerns

None for the guide change. The local website prebuild's Python fallback is an
environment limitation only; the focused docs contract, diagram lint, and
production build all completed successfully.
