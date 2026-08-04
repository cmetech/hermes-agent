# Continue — Phase 3 / Task 15

## Last action

Closed Task 14 at reviewed implementation
`b8be4a3182eb9a2c03834a32aecf8beb4c531df3` (tree
`95f1ab3eaf6e8f00a479755a5d16e218880896aa`). Fresh independent specification
and quality closure rereviews both passed with 0 Critical, 0 Important, and 0
Minor findings. Controller verification passed the exact backend matrix at
321/321 and the exact Desktop matrix at 114/114; Desktop typecheck, Ruff,
Prettier, and diff checks passed, and scoped ESLint reported zero errors.
Threat-model/security testing and validation were explicitly excluded by user
instruction and must remain excluded for subsequent Phase 3 work.

## Next action

Read `AGENTS.md`, the approved Phase 3 design and plan, this handoff, and the
final Task 14 closure rereviews; verify the exact branch/HEAD/tree and clean
worktree; then begin Task 15 with failing generated-contract and documentation
assertions before editing descriptors or prose. Update the central language
descriptors first, inspect both dynamic schema profiles through
`./hermes workflow schema`, and update prose from that authority. Use
`scripts/run_tests.sh` with retries disabled for authoritative Python gates.

## Why

Task 14 now projects the bounded Phase 3 backend truth through the existing API
and Desktop surfaces. Task 15 publishes that same contract through central
schema descriptors, operator/author documentation, the workflow-builder skill,
and installed-distribution fixtures without inventing a checked-in generated
schema or a prose-only stable-code list.

## Open threads

- Tasks 15–16 remain pending; no Task 15 production or test work has begun.
- Task 15 begins with generated/editor/author contract RED assertions, then
  central descriptors, dynamic schema checks, prose, and installed fixtures.
- The shared `base` checkout remains at `5b974a53593fc880d18417ee2fc0e5eaff5599f4`
  with unrelated user-owned changes.

## Do not

- Do not run direct `pytest`; use `scripts/run_tests.sh` and
  `HERMES_TEST_FILE_RETRIES=0` for authoritative gates.
- Do not perform threat-model analysis, threat-model/security test execution,
  or threat-model/security validation. Use ordinary functional, regression,
  compatibility, and code-quality checks only.
- Do not hand-maintain a checked-in generated schema or a second prose-only
  stable-code list; derive public guidance from the registered authority.
- Do not document behavioral configuration through a raw environment variable.
- Do not remove MCP/skills as documented options or promote loops/includes
  before Phase 4.
- Do not begin Task 16, Phase 4 loops/includes, or new artifact/provider
  surfaces.
- Do not modify the shared base checkout, literal `main`, push, publish, or
  delete branches/worktrees.
