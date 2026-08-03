# Continue — Phase 3 / Task 13

## Last action

Closed Task 12 at reviewed implementation
`b9c57e31cd42bc77685a31ce0f7bd9808deb6d1e` (tree
`fda7669405741e23c19a68a06bb939f794d857b9`). Fresh independent specification
and quality closure reviews both passed with 0 Critical, 0 Important, and 0
Minor findings. Controller verification passed 863 focused tests with retries
disabled, strict customization, Ruff, and diff checks; the canonical base gate
passed 4,098 Python tests, one installed-distribution test, and 155 Desktop
tests with `TESTED_BASE_SHA=b9c57e31cd42bc77685a31ce0f7bd9808deb6d1e`.

## Next action

Read `AGENTS.md`, the approved Phase 3 design and plan, this handoff, and the
final Task 12 closure reviews; verify the exact branch/HEAD/tree and clean
worktree; then begin Task 13 with source-sensitive recovery tests in
`tests/plugins/workflow/test_persistent_session_recovery.py`. Prove RED through
`scripts/run_tests.sh` with retries disabled before editing production.

## Why

Task 12 now supplies the generic typed classification seam Task 13 requires:
confirmed absence is distinct from database/open/read/ambiguous failures.
Task 13 owns the workflow-specific recovery choice, durable pre-provider
selection boundary, private CAS obligation, and idempotent reconciliation.

## Open threads

- Tasks 13–16 remain pending; no Task 13 production or test work has begun.
- Task 13 begins with same-run versus confirmed-missing cross-run recovery,
  then durable reserve/selection, private `SessionRegistryUpdateCandidate`,
  atomic completion obligations, compare-and-set-or-observe reconciliation,
  crash/cancellation ordering, sanitized evidence, and code-catalog coverage.
- The shared `base` checkout remains at `5b974a53593fc880d18417ee2fc0e5eaff5599f4`
  with unrelated user-owned changes.

## Do not

- Do not run direct `pytest`; use `scripts/run_tests.sh` and
  `HERMES_TEST_FILE_RETRIES=0` for authoritative gates.
- Do not add workflow imports or fresh-session policy to the generic agent
  layer; Task 13 recovery belongs only in workflow-owned code.
- Do not expose session IDs, registry keys, fingerprints, history, provider
  responses, paths, or private CAS candidates through evidence or public
  projections.
- Do not begin Task 14, Phase 4 loops/includes, MCP/skills node kinds, or new
  artifact/provider surfaces.
- Do not modify the shared base checkout, literal `main`, push, publish, or
  delete branches/worktrees.
