# Phase 3 Task 1 Final Specification Closure Rereview

**Reviewed HEAD:** `896afabd407696e1fc9fc3d06f0b97458be716fe`

**Reviewed tree:** `1d36ad36595e3201a891d3cec4f11bceef1e3fca`

**Fix-round-2 baseline:** `473ea197b5e2f7322a136ad9b877edd3fbaf5cac`

**Planning baseline:** `cffc23cecd801d3aed08ba66d596bec4a365a43a`

**Verdict:** PASS

**Findings:** 0 Critical, 0 Important, 0 Minor

## Scope and evidence

I read the Task 1 quality rereview, rechecked the approved design and Task 1 plan applicability contract, and inspected the complete fix-round-2 diff plus the complete planning-baseline-to-HEAD Task 1 range. The production fix is limited to compatibility migration text in `plugins/workflow/language.py`; its tests are limited to the existing Task 1 language suite. No later-task execution, persistence, resolver, scheduler, Bash, session, API, Desktop, loop, or include behavior was introduced.

## Remaining quality finding — closed

The previous rereview found that valid legacy definitions could be advised to author timeout/retry fields on node kinds where Archon v3 rejects them. The new branching at `plugins/workflow/language.py:580-638` now follows the exact Archon v3 applicability matrix:

- `idle_timeout` on command/prompt retains seconds-to-milliseconds conversion guidance;
- `idle_timeout` on every other legacy node kind says it cannot migrate under v3 and must be removed or redesigned;
- retry on command/prompt/Bash/script retains the previously reviewed AI, deterministic-one-attempt, and `N - 1` guidance; and
- retry on approval/cancel says it cannot migrate under v3 and must be removed or redesigned.

This ordering is node-kind first, so an approval/cancel `max_attempts: 1` cannot accidentally receive deterministic “omit retry” advice and a greater value cannot receive `N - 1` advice. Legacy loop retry remains structurally invalid and therefore does not need a migration branch.

Behavior tests at `tests/plugins/workflow/test_phase3_language.py:471-536` cover:

- approval retry at one and two legacy total attempts;
- cancel retry at one and two legacy total attempts;
- Bash `idle_timeout`; and
- loop `idle_timeout`.

They assert both the required removal/redesign advice and the absence of the inapplicable `N - 1`, `omit retry`, or multiplication advice. Existing tests continue to prove the admitted command/prompt/Bash/script guidance.

## Regression and scope audit

All previously closed Task 1 requirements remain intact at this HEAD:

- profile-specific v2/v3 normalizer selection and exact admitted v1/v2 reload;
- bounded requested timeout/retry normalization and exact v3 snapshot identity;
- stable null/malformed retry failures;
- exact legacy and admitted-v1/v2 trust digests with Archon v3 retrust;
- complete direct-dependency, typed-condition, structured-output, retry, timeout, and Bash migration guidance;
- supported-v3 CLI behavior with later-phase blockers retained;
- additive durable-code catalog relationships; and
- exact unversioned and `hermes-legacy` execution behavior.

Fix round 2 changes only advisory migration strings and their behavioral tests. It does not alter compatibility severity/code/path, trust hashing, snapshot bytes, normalization output, execution semantics, tool schemas, prompt contents, or API/evidence projections.

## Verification

The exact expanded Task 1 gate was run only through the required wrapper with flaky file retries disabled:

```text
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python \
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_language.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_language.py \
  tests/plugins/workflow/test_language_snapshot.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_trust_policy.py \
  tests/plugins/workflow/test_compat_matrix.py \
  tests/plugins/workflow/test_doctor.py \
  tests/plugins/workflow/test_cli.py

Result: 10 files, 940 tests passed, 0 failed, no retries.
```

Both `473ea197b..896afabd4` and `cffc23cec..896afabd4` pass `git diff --check`. The worktree was clean before this report was written.

## Conclusion

The node-aware migration finding is fully closed without regression or scope drift. Task 1 has final specification closure at the requested HEAD and is ready for final quality closure/controller verification.
