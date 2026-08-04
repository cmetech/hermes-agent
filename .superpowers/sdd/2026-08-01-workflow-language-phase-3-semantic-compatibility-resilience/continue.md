# Continue — Phase 3 / Task 14

## Last action

Closed Task 13 at reviewed implementation
`b7cba382c2eaeff370559fdf049a47ed96b6441e` (tree
`8725359b7331beecaeb8c0e9a4afc4141cb2ee41`). Fresh independent specification
and quality closure rereviews both passed with 0 Critical, 0 Important, and 0
Minor findings. Controller verification passed 869 functional tests across 20
non-overlapping files with retries disabled; Ruff and diff checks passed.
Threat-model/security testing and validation were explicitly excluded by user
instruction and must remain excluded for subsequent Phase 3 work.

## Next action

Read `AGENTS.md`, `apps/desktop/AGENTS.md`, the approved Phase 3 design and
plan, this handoff, and the final Task 13 closure rereviews; verify the exact
branch/HEAD/tree and clean worktree; then begin Task 14 with backend projection
tests before touching API or Desktop production. Prove RED through
`scripts/run_tests.sh` and scoped Desktop Vitest with retries disabled where
applicable before editing production.

## Why

Task 13 now supplies the bounded backend recovery truth Task 14 must project:
normalizer v3, persistent-session recovery evidence, private obligations, and
stable error/outcome fields are durable authorities. Task 14 owns additive API
and Desktop projection only; it must not recreate recovery or language
semantics in the renderer.

## Open threads

- Tasks 14–16 remain pending; no Task 14 production or test work has begun.
- Task 14 begins with backend normalizer-v3 and bounded recovery projection,
  then Desktop generic recovery rendering and old/new additive compatibility.
- The shared `base` checkout remains at `5b974a53593fc880d18417ee2fc0e5eaff5599f4`
  with unrelated user-owned changes.

## Do not

- Do not run direct `pytest`; use `scripts/run_tests.sh` and
  `HERMES_TEST_FILE_RETRIES=0` for authoritative gates.
- Do not perform threat-model analysis, threat-model/security test execution,
  or threat-model/security validation. Use ordinary functional, regression,
  compatibility, and code-quality checks only.
- Do not expose session IDs, registry keys, fingerprints, history, provider
  responses, paths, or private CAS candidates through evidence or public
  projections.
- Do not add renderer-side parsers, retry calculators, session probes, or
  filesystem access. Backend truth remains authoritative.
- Do not begin Task 15, Phase 4 loops/includes, MCP/skills node kinds, or new
  artifact/provider surfaces.
- Do not modify the shared base checkout, literal `main`, push, publish, or
  delete branches/worktrees.
