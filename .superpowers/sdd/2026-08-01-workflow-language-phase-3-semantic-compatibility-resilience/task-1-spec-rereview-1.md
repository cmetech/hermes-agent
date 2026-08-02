# Phase 3 Task 1 Independent Specification Closure Rereview

**Reviewed HEAD:** `473ea197b5e2f7322a136ad9b877edd3fbaf5cac`

**Reviewed tree:** `4408182f1f48585f624e880649e54f3dd717ebd4`

**Original implementation:** `b677f770a182492ea7e3723733d278600309f3e7`

**Planning baseline:** `cffc23cecd801d3aed08ba66d596bec4a365a43a`

**Verdict:** PASS

**Findings:** 0 Critical, 0 Important, 0 Minor

## Scope

I reread the approved Phase 3 design and Task 1 plan contract, the original Task 1 specification review, the independent quality review, and the complete fix diff from `b677f770a` through the requested HEAD. I then rechecked the complete Task 1 baseline-to-HEAD file set for goal coverage and scope drift.

The fix is bounded to Task 1 normalization, compatibility guidance, trust identity, and directly affected tests. It does not implement effective execution semantics, strict runtime references, typed condition execution, Bash materialization, retry scheduling, or persistent-session recovery from later tasks. The two review reports were committed separately as documentation evidence; production/test closure is isolated in `473ea197b`.

## Original specification findings

### I-1 — Closed: legacy and admitted v1/v2 trust identity remains exact

`plugins/workflow/trust.py:505-533` now retains the historical Phase 2 risk document for every v1/v2 package and adds `language_identity` only for `archon-2026-07` normalizer v3. This creates the required v2-to-v3 Archon retrust boundary without changing existing legacy or admitted-version trust records.

The regression tests prove both sides:

- `tests/plugins/workflow/test_trust_policy.py:88-108` proves unchanged source produces different Archon v2/v3 risk identities;
- `tests/plugins/workflow/test_trust_policy.py:111-150` independently reconstructs the Phase 2 hash document and proves Archon v1/v2 equality; and
- `tests/plugins/workflow/test_trust_policy.py:153-166` pins a real legacy v2 fixture to its retained Phase 2 digest.

This closes the approved design requirement that legacy acquires no trust-digest change while new Archon v3 semantics require review.

### I-2 — Closed: complete backend-authored migration guidance

`plugins/workflow/language.py:562-642` now covers every migration item required by the design and Task 1:

- seconds-to-milliseconds conversion;
- `N - 1` retries-after-initial conversion and sealed-cap reminder;
- deterministic one-attempt omission of `retry`;
- the AI one-attempt no-compatible-opt-out warning;
- direct `depends_on` declarations;
- `output_format` before `.field` references;
- replacement of coercive conditions with a structured scalar decision value; and
- the 32,768-byte UTF-8 Bash boundary with pathname assumptions removed.

The stale “wait for Phase 2” output guidance was removed. `tests/plugins/workflow/test_phase3_language.py` now exercises the full guidance by exact node path and behavior distinction instead of checking only two substrings.

## Overlapping quality findings

All four quality findings are closed:

1. Explicit `retry: null` is distinguished from omission and rejected with `archon_retry_invalid`; null nested retry values also take stable validation paths (`plugins/workflow/language.py:416-497`). Parser-level tests cover AI and deterministic nodes for null block, max-attempt, delay, and on-error shapes.
2. The trust-digest regression is closed by the version-gated hash document and Phase 2 compatibility tests described above.
3. The affected CLI suite now treats timeout/retry as supported v3 authoring and retains blocking validation/trust/run/doctor coverage on Phase 5 fields. The formerly failing CLI file passes in the expanded gate.
4. The durable-code tests retain uniqueness, bounded metadata, and profile/version relationships while Task 1 behavior cases assert only that their emitted codes have catalog metadata. They no longer require every future Phase 3 code to be a normalization/runtime/non-evidence code or to equal Task 1's fixed case set. This permits later condition, reference, Bash, session, and evidence registrations without rewriting Task 1 expectations.

## Complete Task 1 contract

The closure diff does not disturb the already reviewed implementation. At this HEAD:

- new unversioned and explicit `hermes-legacy` packages remain on normalizer v2;
- new `archon-2026-07` packages select v3;
- explicit/admitted v1 and v2 snapshots reload without v3 fields or upgrades;
- v3 requested timeout/retry semantics normalize once with positive finite bounds, the 120-second deterministic default, AI/deterministic retry defaults, exact retries-after-initial accounting, stable applicability failures, canonical ordering, and bounded exact snapshot parsing;
- structured-output and requested-semantics projections participate in v3 digest and semantic fingerprint identity;
- implemented Archon blockers are removed while later-phase blockers remain;
- the durable-code catalog is dependency-neutral, bounded, version/profile scoped, and behavior-linked for every Task 1 emitter; and
- no later-task production behavior, core tool, prompt mutation, MCP/skills node kind, Phase 4 loop/include behavior, API path surface, or raw evidence projection was introduced.

## Verification

The expanded focused gate was run only through the required repository wrapper with flaky file retries disabled:

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

Result: 10 files, 934 tests passed, 0 failed, no retries.
```

Both the fix range and complete Task 1 range pass `git diff --check`. The worktree was clean before this report was written.

## Conclusion

Every Task 1 specification requirement and all original specification/quality findings are closed at the requested HEAD. Task 1 is ready for controller verification and the next planned task.
