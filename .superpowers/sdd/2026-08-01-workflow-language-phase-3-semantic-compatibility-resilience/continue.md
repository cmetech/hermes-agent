# Continue — Phase 3 / Task 16

## Last action

Closed Task 15 at reviewed implementation
`b10ae833f40a2f09d529bc24744e8a3c8432c712` (tree
`5a2f00e60b2c1e09d9284531330d78c6de7ed5f5`). Fresh independent specification
and quality closure rereviews both passed with 0 Critical, 0 Important, and 0
Minor findings. Controller verification passed the exact Task 15 matrix at
1,764/1,764; the installed-wheel integration passed 1/1; dynamic Archon and
legacy wire probes resolved all 96 references; scoped Ruff, diff, branch,
commit, tree, and clean-worktree checks passed. Threat-model/security testing
and validation were explicitly excluded by user instruction and must remain
excluded from Task 16.

## Next action

Read `AGENTS.md`, the approved Phase 3 design and plan, this handoff, and the
final Task 15 closure rereviews; verify the exact branch/HEAD/tree and clean
worktree; then execute Task 16's final Phase 3 functional/regression,
customization, review, and release-rehearsal gates. Remove or skip every
historical Task 16 item that requests threat-model/security analysis, testing,
or validation. Use `scripts/run_tests.sh` with retries disabled for
authoritative Python gates, and record the exact green candidate identity
before final specification and quality reviews.

## Why

Tasks 1–15 now implement and publish the bounded Phase 3 contract through the
runtime, API, Desktop, generated schema, documentation, skill, and installed
distribution surfaces. Task 16 is the final convergence gate before Phase 3 can
be declared complete.

## Open threads

- Task 16 remains pending; Tasks 1–15 are complete.
- Task 16 must run final ordinary functional/regression, Desktop,
  customization, base-merge, and release-rehearsal gates, then obtain clean
  specification and quality reviews against the exact final tree.
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
- Do not begin Phase 4 loops/includes or new artifact/provider surfaces.
- Do not modify the shared base checkout, literal `main`, push, publish, or
  delete branches/worktrees.
