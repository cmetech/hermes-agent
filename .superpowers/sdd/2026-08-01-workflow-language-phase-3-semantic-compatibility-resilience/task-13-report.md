# Task 13 Report: Persistent Session Recovery

## Outcome

Implemented source-sensitive recovery for confirmed-missing cross-run persistent
sessions under the Archon v3 execution contract. Same-run missing shared context
fails closed, ambiguous or operational registry failures never trigger a fresh
provider request, and legacy profiles retain their prior behavior.

Successful execution now returns a private generation-CAS candidate. The store
atomically records the winning node completion and a corroborated private
registry obligation before the coordinator applies it. Pre-provider journal
capacity transfers from the worker claim to that obligation, survives rebuild,
and is released only after a durable reconciliation outcome.

Coordinator reconciliation distinguishes replacement, idempotent prior apply,
and a newer winner; retries operational failures at 1, 2, 4, 8, and 16 seconds;
then leaves the run in `recovery_pending` until ordinary operator resume.
Cancellation and finalization wait for the winning obligation, and provider work
is never replayed.

Public evidence contains bounded identifiers and hashes only. Raw session IDs,
registry keys, fingerprints, histories, paths, and provider content remain out
of public projections and evidence. The Phase 3 compatibility catalog contains
the new missing-session selection, failure, and pending codes.

## TDD Evidence

- RED: confirmed absence/source classification, active-claim selection,
  pre-provider reserve refusal, atomic completion/CAS, exact retry ordering,
  cancellation ordering, and fail-closed rebuild tests failed before their
  respective implementations.
- RED: the obligation-reserve invariant exposed that worker-claim release also
  released post-completion journal capacity; a durable obligation reserve and
  crash rebuild closed the gap.
- RED: a fresh-worker exception left selection evidence at
  `fresh_start_selected`; exception paths now durably record
  `fresh_execution_failed` without creating a registry obligation.

## Verification

Focused recovery suite: 16 passed, 0 failed.

Required ten-file gate via `scripts/run_tests.sh`: 238 passed, 0 failed.

Ruff passed on every changed Python source/test file, and `git diff --check`
reported no whitespace errors.

## Concerns

None blocking. Reconciliation remains deliberately fail-closed: registry
operational errors require bounded retries/operator resume rather than treating
uncertainty as confirmed absence.

## Evidence-only completion

This section records the exact authoritative Task 13 test commands and observed
results. Every Python test invocation used `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`. Retries were disabled and therefore unavailable.
No direct `pytest` command was run. The `python -m pytest ...` text visible in
some failed-run output was the parallel runner's generated reproduction hint,
not an executed command.

### Source-sensitive recovery RED/GREEN

Exact command for both the behavioral RED and GREEN:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_ai_executor.py
```

Behavioral RED: **114 passed / 3 failed**. The expected failures were:

- `test_confirmed_missing_cross_run_session_starts_fresh_once`
- `test_same_run_shared_session_missing_fails_without_fresh_request`
- `test_cross_run_session_probe_failure_is_not_treated_as_confirmed_absence`

They failed because the workflow executor could not distinguish same-run
predecessor authority from cross-run registry authority and had no bounded
confirmed-absence recovery path. GREEN after the source-sensitive executor
change: **117 passed / 0 failed**.

The first authored invocation of these tests reached the same three names but
stopped at schema validation because the new test helper emitted unsupported
Archon top-level `options`. That fixture-only defect was corrected before the
behavioral RED was accepted and before production was edited.

### Pre-provider selection and reserve RED/GREEN

Exact command for both RED and GREEN:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_journal_reserve_fanout.py tests/plugins/workflow/test_crash_recovery.py
```

RED: **37 passed / 2 failed**. Expected failures:

- `test_recovery_selection_is_durable_before_fresh_provider_launch`
- `test_recovery_reserve_refusal_happens_before_provider_allocation`

The active-claim selection callback and complete pre-provider recovery reserve
did not exist. GREEN after adding both boundaries: **39 passed / 0 failed**.

### Private registry candidate RED/GREEN

Exact RED command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_ai_executor.py
```

RED: **119 passed / 1 failed** at
`test_fresh_execution_returns_private_registry_candidate_without_writing`.
This was expected because successful fresh execution still mutated the registry
inside the executor instead of returning a private
`SessionRegistryUpdateCandidate`.

The matching recovery-file GREEN command was:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py
```

Result after the candidate path and its adjacent store/reconciliation work:
**10 passed / 0 failed**. The candidate test also remained green in the final
ten-file gate.

One attempted node-id invocation is deliberately excluded from evidence:
`scripts/run_tests.sh` accepts files, not pytest node IDs. The mixed node-id plus
AI-file attempt ran only `test_ai_executor.py` (**108 passed**) and the node-only
attempt reported `No test files to run`. Neither was treated as candidate GREEN,
and no direct pytest fallback was used.

### Atomic completion obligation RED/GREEN

Exact RED command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_crash_recovery.py
```

After earlier source regressions were corrected, the focused RED was
**51 passed / 1 failed** at
`test_success_and_registry_obligation_are_atomic_before_cas`. The failure was
expected because successful node completion and its private registry obligation
were not one store-owned durable transition.

Exact expanded GREEN command over the same three files:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_store.py
```

GREEN after atomic persistence, validation, finalization blocking, and rebuild
corroboration: **60 passed / 0 failed**.

### Registry CAS and operational backoff RED/GREEN

Exact RED command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_crash_recovery.py
```

RED: **43 passed / 2 failed**. Expected failures:

- `test_registry_reconciliation_retries_exactly_then_requires_operator_resume`
- `test_reconciliation_observes_prior_apply_and_never_clobbers_newer_entry`

The boolean registry CAS could not distinguish an already-applied identity from
a newer winner, and coordinator reconciliation/backoff did not yet exist. The
matching recovery-file GREEN command was:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py
```

GREEN after `compare_and_set_or_observe()`, exact 1/2/4/8/16-second scheduling,
`recovery_pending`, and ordinary resume: **10 passed / 0 failed**.

### Sanitized evidence and durable-code catalog RED/GREEN

Exact command for both RED and GREEN:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_evidence_api.py
```

RED: **54 passed / 4 failed**. Expected failures:

- `test_registry_read_failure_is_recovery_unavailable_before_provider`
- `test_recovery_selection_is_durable_before_fresh_provider_launch`
- `test_failed_fresh_recovery_records_outcome_without_registry_obligation`
- `test_phase3_catalog_registers_real_session_recovery_codes_and_event`

The operational-failure mapping, durable sanitized outcomes, evidence
projection, and catalog entries were incomplete. GREEN after completing those
paths: **58 passed / 0 failed**.

### Cancellation ordering RED/GREEN

Exact RED command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py
```

RED: **13 passed / 1 failed** at
`test_cancellation_waits_for_winning_registry_obligation`. This was expected
because cancellation could publish a terminal result before the internal
winning obligation was reconciled.

Exact GREEN command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_shutdown_recovery.py
```

GREEN after cancellation was made obligation-aware: **48 passed / 0 failed**.

### Fail-closed rebuild RED/GREEN

Exact RED command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_crash_recovery.py
```

RED: **43 passed / 1 failed** at
`test_uncorroborated_registry_obligation_fails_closed_during_rebuild`. The
projection validator accepted an obligation whose winning attempt was not
corroborated by the succeeded node attempt.

Exact GREEN command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_store.py
```

GREEN after exact winning-attempt/recovery corroboration: **60 passed / 0
failed**.

Supplementary multiprocess/coordinator command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_coordinator_multiprocess.py
```

Result: **23 passed / 0 failed**.

### Full-gate reserve-consumption regression and correction

Exact full command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_journal_reserve_fanout.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_coordinator_multiprocess.py tests/plugins/workflow/test_evidence_api.py
```

The first full run was a self-induced regression: **225 passed / 12 failed**.
All failures raised `StorageQuotaError` while consuming a terminal journal
reserve that had already been removed with the worker claim. The exact failures
were:

- `test_persistent_typed_output_journals_and_exposes_only_completed_mirror`
- `test_concurrent_persistent_runs_retain_immutable_mirror_history`
- `test_confirmed_missing_cross_run_session_starts_fresh_once`
- `test_same_run_shared_session_missing_fails_without_fresh_request`
- `test_cross_run_session_probe_failure_is_not_treated_as_confirmed_absence`
- `test_recovery_selection_is_durable_before_fresh_provider_launch`
- `test_failed_fresh_recovery_records_outcome_without_registry_obligation`
- `test_success_and_registry_obligation_are_atomic_before_cas`
- `test_registry_reconciliation_retries_exactly_then_requires_operator_resume`
- `test_reconciliation_observes_prior_apply_and_never_clobbers_newer_entry`
- `test_cancellation_waits_for_winning_registry_obligation`
- `test_uncorroborated_registry_obligation_fails_closed_during_rebuild`

No test was weakened. Removing the invalid post-completion attempt-reserve
consumption restored the focused exact command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_persisted_sessions.py
```

Focused correction result: **21 passed / 0 failed**. The same full ten-file
command then passed **237 passed / 0 failed**.

### Durable obligation-reserve self-review RED/GREEN

Although the 237-test gate was green, self-review found that the pre-provider
reserve itself still disappeared with worker-claim release. A new invariant
required capacity to transfer to the durable obligation, survive a missing-row
crash/rebuild gap, fund every deferral/outcome frame, and disappear only after
resolution.

Exact command for RED and GREEN:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py
```

RED: **13 passed / 2 failed** at:

- `test_success_and_registry_obligation_are_atomic_before_cas`
- `test_registry_reconciliation_retries_exactly_then_requires_operator_resume`

Both failed because `obligation_journal_reserves` did not yet exist. GREEN after
the reserve transfer/rebuild/consumption implementation: **15 passed / 0
failed**. The crash-gap rebuild assertion was then added and the same command
remained **15 passed / 0 failed**.

### Fresh-exception outcome evidence RED/GREEN

Final self-review found that a fresh worker returning a failed result recorded
`fresh_execution_failed`, but a fresh worker raising an exception left the
durable selection at `fresh_start_selected`.

Exact command for RED and GREEN:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py
```

RED: **15 passed / 1 failed** at
`test_fresh_recovery_exception_records_failed_outcome`. GREEN after normalizing
all post-selection failed results/exceptions through the same outcome path:
**16 passed / 0 failed**.

### Final exact verification

Exact final ten-file command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_journal_reserve_fanout.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_coordinator_multiprocess.py tests/plugins/workflow/test_evidence_api.py
```

Final result: **10 files, 238 passed / 0 failed**, 14 workers, 4.9 seconds.
The focused recovery file immediately before that gate passed **16/16**. Ruff
passed on all changed Python files and `git diff --check` was clean.

### Changed files

Implementation commit `1bc2d5491` changed exactly these files:

- `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-13-report.md`
- `plugins/workflow/evidence.py`
- `plugins/workflow/executors/ai.py`
- `plugins/workflow/executors/base.py`
- `plugins/workflow/language_schema.py`
- `plugins/workflow/scheduler.py`
- `plugins/workflow/sessions.py`
- `plugins/workflow/store.py`
- `tests/plugins/workflow/test_persistent_session_recovery.py`

No agent-core, prompt, history, toolset, model-tool, user configuration,
Task 14, Phase 4, or unrelated file changed.

### Self-review and deviations

- The implementation stayed in workflow-owned code and consumed Task 12's
  generic typed absence seam without adding workflow policy to agent core.
- Exact session IDs, registry keys, fingerprints, histories, paths, provider
  responses, and the private CAS candidate remain out of public status and
  evidence; public recovery data uses bounded identifiers and SHA-256 digests.
- Same-run absence never becomes fresh execution. Cross-run fresh recovery is
  selected only after confirmed zero-provider absence. Operational ambiguity
  remains `persistent_session_recovery_unavailable`.
- Node completion plus the private winning obligation is durable before CAS;
  CAS is generation-safe and idempotent; retries do not replay provider work;
  cancellation/finalization wait for reconciliation.
- The invalid initial test fixture and unsupported node-id runner attempt were
  test-authoring deviations only. Neither was counted as behavioral evidence,
  neither caused a direct pytest fallback, and neither changed the harness.
- The 12 reserve-consumption failures were an implementation regression caused
  by attaching post-completion frames to the already-released attempt reserve.
  They were diagnosed at the full gate, fixed without weakening tests, then
  superseded by the stronger durable obligation-reserve design found in
  self-review.
- The exception-path evidence gap was found after the first green full gate.
  A dedicated RED proved the gap; the shared failure-normalization path closed
  it for raised exceptions as well as failed worker results.
- No tests were deleted, skipped, marked flaky, retried, or relaxed. No direct
  pytest invocation, push, publication, merge, branch deletion, worktree
  deletion, literal-`main` mutation, or shared `base` checkout mutation was
  performed.
