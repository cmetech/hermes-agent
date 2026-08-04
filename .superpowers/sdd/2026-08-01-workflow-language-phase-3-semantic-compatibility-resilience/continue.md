# Continue — Phase 3 complete / integration handoff

## Last action

Closed Task 16 and Phase 3 at reviewed production implementation
`8a1fe704484bf63e0e84f536f7fb690a2f024ccf` (tree
`94f4fd4572b63ba6dd496213b603e67748b41b46`). Independent final specification
and quality reviews both passed with 0 Critical, 0 Important, and 0 Minor
findings. The explicit ordinary functional, Desktop, schema, customization,
merge, and manual integration-only upstream/OTTO/LOOP24 gates passed. Broad
discovery and the generic ledger-validation stage were superseded because they
could select security/threat-focused tests prohibited by the user. No such
evidence is used for completion.

## Next action

Wait for explicit user authorization before integration. If authorized, use
the project branch rule that developer shorthand `main` means `base`; do not
target literal `main`. Reverify the reviewed production identity, the
report-only closure identity, clean worktree, and current `base` state before
any merge, push, PR, release, branch deletion, or worktree removal.

## Why

Tasks 1–16 implement, publish, and verify the bounded Phase 3 contract through
the runtime, API, Desktop, generated schema, documentation, skill, installed
distribution, customization gates, and disposable brand integration rehearsal.

## Open threads

- Tasks 1–16 are complete; no implementation or review finding remains open.
- Integration, push, publication, release propagation, and cleanup remain
  unauthorized until the user requests them separately.
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
- Do not merge, push, publish, propagate brand refs, delete the branch, or
  remove the feature worktree without separate user authorization.
- Do not modify the shared base checkout, literal `main`, push, publish, or
  delete branches/worktrees.
