# Phase 3 Task 2 Quality Rereview 1

**Reviewed commit:** `ad2157a8ef217cbe540ed89f826f96062fa80bcb`

**Reviewed tree:** `0990f4e994aeb16ab47f6c81c0ab8801fa6194d3`

**Fix baseline:** `b820d53ee83b782692b5df58f3b79d17982970db`

**Verdict:** APPROVED

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 0 |
| Minor | 0 |

## Closure of the original findings

### 1. Canonical numeric and byte authority — closed

The execution-semantics reader now requires the canonical float type for every
non-null seconds field. More importantly, the scheduler reserializes the
complete authenticated `resources.json` document with the same sorted,
compact, finite JSON contract used at admission and compares those bytes before
reading the Phase 3 projection. This closes equivalent integer, exponent,
decimal, whitespace, field-order, duplicate/noncanonical, and non-finite
representation paths rather than relying on Python value equality.

The new behavior tests reseal and reject integer, exponent, alternate-decimal,
whitespace, and field-order mutations before claim. A direct rereview repro of
the original `300.0` -> `300` case now returns
`workflow_execution_semantics_mismatch` with the canonical-number diagnostic.

### 2. Scheduled mismatch evidence and atomicity — closed

`RunScheduler._prepare_run_package()` now catches
`WorkflowExecutionSemanticsError` before the generic scheduled-package path.
For scheduled work it uses the existing bounded `_fail_package_validation()`
transaction, which verifies the expected state version, refuses any projected
or indexed claim, consumes the opaque promotion authorization, writes the
stable `workflow_execution_semantics_mismatch` code/path, terminalizes nodes,
appends the bounded `run_failed` evidence, updates the integrity index, and
commits atomically.

The focused scheduled test exercises `advance()`, proves the run fails with the
stable code, proves the authorization was consumed, proves no attempt/claim or
executor call occurred, and corroborates the journal event. Unrelated
scheduled preparation failures retain `schedule_revalidation_failed`.

### 3. V3 current-config authority read — closed

The scheduler selects the authenticated Archon-v3 branch before invoking the
legacy resolver. The new v3 helper seeds the five semantic fields from the
sealed projection, filters those names out of sidecar re-resolution, and
resolves only the pre-existing non-Phase-3 scheduling, process-resource, and
shutdown controls. The legacy branch still calls the unchanged
`_run_execution_limits()` path.

The changed-config resume test makes a call to the legacy resolver fatal,
changes the current semantic values, includes a conflicting sealed sidecar
semantic value, and still recovers the admitted five-field authority. It also
proves non-Phase-3 sidecar resource/parallel controls and scheduler shutdown
controls retain their existing behavior.

### 4. Six-boundary canonical parity and scheduled resume — closed

One integration fixture now uses the same Archon package, profile, authenticated
sidecar limits, representative Bash/script/AI semantics, and input bytes across
CLI, API, Gateway, showcase, scheduled admission, and direct store. It compares
the complete canonical execution projection, full `resources.json` digest,
persisted `input_manifest_digest`, and authenticated scheduled-resume bytes
after restart under changed configuration.

The catalog completeness test now routes both language and execution mismatch
codes through real admitted-run scheduler resume failures rather than calling
the verifier helpers directly. The assertions are additive behavioral
relationships, do not inspect source text, and do not freeze catalog sizes or
unrelated enumerations.

## Compatibility and regression assessment

- Canonical-resource enforcement is gated to Archon normalizer v3.
- New unversioned and `hermes-legacy` snapshots do not receive the Phase 3
  projection and continue through the prior limit resolver.
- Existing v1/v2 snapshot and compatibility tests remain green.
- The fix introduces no new persistence channel, API surface, model tool,
  provider response, path-taking endpoint, or Phase 4/5 behavior.
- The scheduled failure path remains claim-free and uses existing transactional
  journal/integrity machinery; no new concurrency or partial-write seam was
  introduced.

## Verification evidence

The exact Task 2 gate passed through the repository wrapper with file retries
disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_execution_semantics.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_language_snapshot.py \
  tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_api_runtime.py \
  tests/plugins/workflow/test_scheduled_runs.py \
  tests/plugins/workflow/test_showcase_schedule_e2e.py \
  tests/plugins/workflow/test_crash_recovery.py

8 files, 299 tests passed, 0 failed; no retries.
```

Additional scheduler, store, deadline, retry, admission, security-boundary,
and compatibility coverage also passed:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_scheduler.py \
  tests/plugins/workflow/test_store.py \
  tests/plugins/workflow/test_deadlines.py \
  tests/plugins/workflow/test_retry.py \
  tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_security_boundaries.py \
  tests/plugins/workflow/test_compat_matrix.py

7 files, 151 tests passed, 0 failed; no retries.
```

Ruff passed on every modified production/test file. `git diff --check` passed
for the complete fix range. The reviewed worktree was clean before this report
was written, and this rereview modified no production or test files.
