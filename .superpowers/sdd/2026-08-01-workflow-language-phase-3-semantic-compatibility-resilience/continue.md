# Continue — Phase 3 handoff superseded by Phase 4 feature-branch activation

## Supersession — 2026-08-06

This historical Phase 3 handoff is superseded by the completed Phase 4 development
branch `feat/workflow-language-phase-4-ordinary-loops-immutable-includes`. Phase 4
current-version activation is committed at
`7bf55d5d680faea8d82474c1d3e2a3dd8f69a096`; current Archon selects v4, legacy remains
v2, and explicit/sealed v3 remains supported. This statement does **not** claim
integration into `base`: no merge, push, rebase, publication, or cleanup occurred.

## Last action

Confirmed and resolved all one CRITICAL and seven HIGH findings from the
post-completion adversarial review of immutable candidate `8a1fe704`. The
repaired production candidate is `060f60f5429c5250d018e4efe61f1a22edc05102`.
The clean allowed base gate passed 3,857 Python tests, the installed-wheel test,
and 159 Desktop tests. The upstream-customization checker and diff checks also
passed. The prohibited threat-model/security suites remained excluded.

## Next action

Wait for explicit user authorization before integrating the Phase 4 feature branch.
If authorized, use the project rule that developer shorthand `main` means `base`; do
not target literal `main`. Reverify the Phase 4 activation/evidence commits, clean
worktree, and current `base` state before any merge, push, PR, release, branch
deletion, or worktree removal.

## Why

Tasks 1–16 implement, publish, and verify the bounded Phase 3 contract. The
post-completion remediation additionally closes Bash arithmetic admission and
aggregate-size gaps, pause/retry and reference-wait accounting defects, the
session journal crash window, post-provider outcome misclassification, loop
double rendering, and upstream ownership of provider-attempt transport seams.

## Open threads

- Tasks 1–16 and all adversarial remediation findings are complete; no known
  CRITICAL or HIGH finding remains open.
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
- Phase 4 loops/includes are complete on their feature branch; do not start a new
  follow-on surface or imply `base` integration without separate authorization.
- Do not merge, push, publish, propagate brand refs, delete the branch, or
  remove the feature worktree without separate user authorization.
- Do not modify the shared base checkout, literal `main`, push, publish, or
  delete branches/worktrees.
