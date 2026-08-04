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

## Fix Round 1

### Authenticated review base and disposition

Fix Round 1 started from clean reviewed base
`3406b1fe087797eb46dd88c74938db83326c2220`. It addresses every concrete
finding in `task-13-spec-review-1.md` and `task-13-quality-review-1.md`:

- Public status, attempt, event/timeline, recovery evidence, and authenticated
  Desktop/API projections now omit the exact recovered session ID and exact
  cache fingerprint. The exact values remain available only to private
  same-run execution authority and registry reconciliation.
- Live completion and rebuild require an exact private candidate corroborator,
  exact boolean recovery selection, exact winner/run/workflow/scope/provider/
  profile/generation/session/fingerprint authority, and exact selected-recovery
  digest/source/zero-provider evidence. Field-by-field substitution fails
  closed.
- Real profile-local `SessionDB` corruption maps to
  `persistent_session_recovery_unavailable` before worker spawn or provider
  use.
- `recovery_pending` advertises and accepts `resume` and `cancel`; it does not
  advertise `archive`.
- Independent persistent nodes can complete concurrently into a bounded
  collection of per-attempt registry obligations. Reconciliation is
  deterministic and idempotent, and finalization waits until every obligation
  is resolved.
- Registry construction remains lazy for schedulers without an agent runner and
  occurs on demand only for a validated pending obligation.
- The isolated `PluginAgentRunner` now journals spawn intent before allocation,
  records allocation failure, binds the real managed-process identity, and
  records cleanup at the actual process boundary. A crash after AI-worker
  allocation is outcome-uncertain even if that worker was cleanly reaped: reap
  proves termination, not that the provider had zero effect, so silent replay
  remains forbidden.
- The replacement fresh request is resealed against the immutable deadline and
  cancellation immediately before both typed-exception and child-result-race
  launches.
- Registry keys, rows, recovery selections, candidates, and CAS inputs now have
  explicit type and size bounds. Strict v3 rejects malformed/noncanonical
  fingerprints before provider use. The registry schema initialization is also
  protected by the cross-process lock.
- The test matrix now uses a real corrupt `SessionDB`, real isolated worker
  lifecycle callbacks, crash-cut store recovery, concurrent persistent nodes,
  and separate spawned registry processes racing the generation CAS.

The parent explicitly extended the fix scope to the generic agent lifecycle and
Desktop/action surfaces. No workflow sanitizer, evidence schema, language
schema, model tool, prompt, history, toolset, user configuration, or unrelated
file was changed in this round.

### Fix-round TDD evidence

All Python tests below were invoked through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`; no direct pytest invocation or retry was used.

- Privacy and corruption tests first failed **7 tests / 16 passed**, then the
  focused recovery suite passed **23/23**.
- Generic isolated-worker lifecycle tests first failed **2 tests / 121
  passed**, then the combined agent/recovery gate passed **149/149**.
- The two-node concurrent-obligation test failed with the prior singular-slot
  error, then the recovery suite passed **27/27**.
- The lazy-scheduler regression failed because construction opened the registry,
  then the recovery suite passed **28/28**.
- Operator action/API discovery failed **1 test / 14 passed**, then the
  action/Desktop gate passed **170/170**.
- Malformed row/candidate cases failed **8 tests / 28 passed**, then the
  relevant gate passed **42/42**.
- The post-spawn crash case failed by producing `interrupted` rather than
  `paused`, then recovery/crash tests passed **66/66**.
- The separate-process CAS test exposed `sqlite3.OperationalError: database is
  locked`; locking schema initialization made the recovery suite pass **39/39**.
- Final self-review added the previously uncovered crash after clean AI-worker
  reap but before durable node completion. RED was **40 passed / 1 failed**;
  after separating termination proof from provider-outcome proof, recovery and
  crash suites passed **70/70**.

No test was weakened, deleted, skipped, mocked away, or marked flaky.

### Final Fix Round 1 verification

The exact required ten-file command was:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_journal_reserve_fanout.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_coordinator_multiprocess.py tests/plugins/workflow/test_evidence_api.py
```

Final result: **10 files, 263 passed / 0 failed**.

The expanded changed-seam/scheduler/Desktop command was:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_run_queries.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_cli.py
```

Final result: **9 files, 502 passed / 0 failed**.

Ruff passed on every changed Python source/test file, and `git diff --check`
reported no whitespace errors.

### Fix Round 1 changed files

- `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-13-report.md`
- `agent/plugin_agent.py`
- `plugins/workflow/actions.py`
- `plugins/workflow/executors/ai.py`
- `plugins/workflow/executors/base.py`
- `plugins/workflow/scheduler.py`
- `plugins/workflow/sessions.py`
- `plugins/workflow/store.py`
- `tests/agent/test_plugin_agent.py`
- `tests/plugins/workflow/test_desktop_api.py`
- `tests/plugins/workflow/test_persistent_session_recovery.py`
- `tests/plugins/workflow/test_run_queries.py`

### Fix Round 1 self-review

- Strict v3 behavior remains gated to the admitted execution semantics; legacy,
  v1, and v2 paths retain their existing registry/CAS behavior.
- The system prompt, tool schemas, prior messages, and per-conversation toolset
  remain byte-stable. No core model tool or workflow-specific prompt surface was
  added.
- The bounded obligation collection preserves the singular on-disk form for one
  candidate and promotes to a maximum-64 mapping only when concurrency requires
  it. One persisted retry wake applies to one deterministic obligation at a
  time; provider execution is never repeated by reconciliation.
- Exact private session authority is removed only at public store/API/event
  boundaries. Existing typed-publication session metadata remains governed by
  its separate established contract.
- No blocking concerns remain.

## Fix Round 3 — Post-resolution authority, event privacy, delivery, and cancellation

Fix Round 3 was applied from authenticated base
`81bc8c316af7d6c283dcbf9427795955001c0fdf`. It addresses every Critical and
Important item in the fresh specification and quality rereviews without
entering Task 14.

### Corrections

- Private recovery authority is now validated independently of whether a
  registry obligation remains pending. Selection anchors use schema v2 with
  an activation event sequence, so historical pre-selection frames remain
  reconstructible while every active selected, fresh-failed, still-running,
  and terminal recovery-bearing projection must match its insert-only private
  anchor. Successful winner anchors must retain the exact marker, attempt/node
  session and fingerprint, typed-artifact session, recovery digests, and
  canonical CAS candidate.
- Public protection is derived from the private winner anchor as well as the
  mutable journal marker. Marker removal therefore cannot expose a recovered
  typed-artifact session through status, tail events, latest event pages,
  evidence, or the real `workflow events --json` CLI path. Ordinary
  legacy/v1/v2 projections remain unchanged.
- Provider authorization and provider-start receipt are now separate durable
  states. The child acknowledges the exact nonce-bound start frame, the parent
  durably records `provider_start_delivered`, and only then sends the exact
  nonce-bound execute release. Recovery treats authorization without delivery
  as known zero-effect and treats only delivered provider work as potentially
  effectful. Pre-provider validation failures may still return before the
  handshake without being misclassified.
- Provider-ready handling rechecks cancellation immediately before
  authorization and again before delivery. Both store transitions also
  atomically require a running projection with no desired terminal state while
  holding the run lock, so cancellation wins both possible orderings.
- The real coordinator-death matrix now contains nine cuts, including the
  exact durable-authorization/before-delivery interval. The cancellation proof
  uses a real managed worker and pauses only the real termination call after
  `desired_status=cancelled` is durable, then exercises both transition races
  under the actual run lock.

No model tool, system prompt, prior message, toolset, workflow language
surface, user configuration, or unrelated product area changed.

### Exact Fix Round 3 RED/GREEN evidence

Every Python test command below used the repository harness with retries
disabled. No direct pytest invocation, fallback, test weakening, skipped test,
or retry was used.

#### Post-resolution and pre-obligation private anchors

Exact RED and GREEN command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py -k 'resolved_terminal_recovery or resolved_running_recovery or selected_preobligation_recovery or fresh_failed_recovery_requires'
```

RED: **0 passed / 8 failed**; all eight damaged recomputed heads were accepted.
GREEN: **8 passed / 0 failed**. The still-running cases enter the actual
scheduler and also prove zero downstream shared-context provider requests.
The complete recovery file passed **67/67** at this checkpoint.

#### Latest event page and real CLI event privacy

Exact RED and GREEN command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py -k 'recovered_typed_output_session_is_private or postresolution_event_privacy'
```

RED: **0 passed / 2 failed** because the recovered session remained visible in
`latest_event_page()` and the CLI path. GREEN after private-anchor-aware event
redaction: **2 passed / 0 failed**, including a markerless recomputed terminal
event with a typed publication.

#### Durable provider-start delivery

Exact child-ack RED command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py -k 'records_delivery_only_after_child_acknowledges_start'
```

RED: **0 passed / 1 failed** because no durable delivery callback/protocol
state existed.

Exact authorization-to-delivery crash RED command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py -k 'killed_coordinator_restart'
```

RED: **8 passed / 1 failed**; the new real kill cut recovered as paused and
uncertain instead of interrupted with known-zero effect.

Exact combined GREEN command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py tests/plugins/workflow/test_persistent_session_recovery.py -k 'records_delivery_only_after_child_acknowledges_start or authorizes_provider_after_child_is_ready or killed_coordinator_restart'
```

GREEN: **11 passed / 0 failed** across two real worker handshakes and all nine
real coordinator-death cuts.

#### Cancellation ordering at provider readiness

Exact parent readiness RED/GREEN command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py -k 'rechecks_cancellation_at_provider_ready'
```

RED: **0 passed / 1 failed** because dispatch occurred after cancellation was
observable. GREEN: **1 passed / 0 failed** with no dispatch callback.

Exact durable store race RED/GREEN command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py -k 'provider_dispatch_cannot_cross_durable_cancellation'
```

RED: **0 passed / 2 failed**; both cancel-before-authorization and
cancel-after-authorization/before-delivery crossed the durable cancellation
state. GREEN: **2 passed / 0 failed**.

#### Full-file regression discovered during validation

The first complete modified-files run reported **193 passed / 4 failed**:
two legitimate pre-provider validation results were rejected before readiness,
and two established-effect recovery tests still modeled authorization alone as
provider launch. The parent now accepts only the safe no-authorization early
result, continues to reject authorization-without-delivery protocol results,
and the two established-effect fixtures explicitly cross delivery.

Exact focused GREEN commands:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py -k 'real_worker_classifies_session_deleted_after_parent_preflight_without_side_effects or real_workers_are_process_isolated_and_unknown_tools_fail_before_billing'
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py -k 'crash_after_provider_worker_spawn_is_never_replayed_as_prelaunch or crash_after_reaped_provider_worker_still_requires_reconciliation'
```

Result: **4 passed / 0 failed**. The complete modified-files rerun passed
**197/197**: `test_plugin_agent.py` **126/126** and persistent recovery
**71/71**.

### Final Fix Round 3 verification

The exact required ten-file command was:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_journal_reserve_fanout.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_coordinator_multiprocess.py tests/plugins/workflow/test_evidence_api.py
```

Final result: **10 files, 293 passed / 0 failed**, 14 workers, 16.6 seconds.

The exact expanded changed-seam/scheduler/Desktop command was:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_run_queries.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_cli.py
```

Final result: **9 files, 537 passed / 0 failed**, 14 workers, 66.8 seconds.
The authenticated Desktop API file passed **157/157**.

Exact static lint command:

```bash
../../.venv/bin/ruff check agent/plugin_agent.py agent/plugin_agent_worker.py plugins/workflow/executors/ai.py plugins/workflow/executors/base.py plugins/workflow/scheduler.py plugins/workflow/store.py tests/agent/test_plugin_agent.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_persistent_session_recovery.py
```

Result: `All checks passed!`. `git diff --check` is clean.

### Fix Round 3 changed files

- `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-13-report.md`
- `agent/plugin_agent.py`
- `agent/plugin_agent_worker.py`
- `plugins/workflow/executors/ai.py`
- `plugins/workflow/executors/base.py`
- `plugins/workflow/scheduler.py`
- `plugins/workflow/store.py`
- `tests/agent/test_plugin_agent.py`
- `tests/plugins/workflow/test_persistent_session_recovery.py`

### Fix Round 3 self-review

- Historical journal frames remain valid because v2 selection anchors activate
  at the exact event sequence that first publishes recovery evidence; v1
  private anchors retain conservative compatibility behavior.
- Winner anchors activate only when the bound attempt is succeeded. From that
  point forward, journal marker removal and exact session/fingerprint/recovery
  substitution fail closed even after the pending obligation is cleared.
- Public redaction consults the independent winner anchor, while private
  scheduler/CAS paths retain exact continuation authority.
- The provider protocol preserves nonce binding and no replay. Authorization
  without child receipt is known zero; delivery is recorded before the child
  receives its execute release; only delivered attempts become uncertain after
  coordinator loss.
- Cancellation rejection is enforced both before callbacks in the parent and
  under the same run lock that orders `desired_status`, including idempotent
  transition calls.
- No push, publication, merge, branch deletion, worktree deletion,
  literal-`main` mutation, or shared `base` checkout mutation was performed.
- No blocking concerns remain.

## Fix Round 2

### Authenticated review base and disposition

Fix Round 2 started from the clean reviewed base
`b1587ccadd91e59231b7181a5bda886da5558145`. It addresses every remaining
finding in `task-13-spec-rereview-1.md` and
`task-13-quality-rereview-1.md`:

- Protected recovered-session authority is now removed from public typed
  artifacts, node-completion artifact payloads, status, event/timeline,
  artifact evidence, and authenticated Desktop/API detail. Redaction is bound
  to attempts carrying the protected v3 registry authority, so ordinary
  legacy/v1/v2 session and fingerprint fields retain their exact prior public
  behavior.
- Selection and winner authority now have separate, insert-only SQLite
  anchors. The selection anchor retains the private missing session ID needed
  to authenticate its public digest; the winner anchor retains the canonical
  exact CAS candidate. Journal rebuild batch-loads and validates those anchors
  and rejects lockstep substitution of the pending obligation, attempt copy,
  node metadata, or recovery digest.
- A real same-run predecessor `SessionDB` corruption now maps to
  `persistent_session_recovery_unavailable` with zero provider attempts,
  matching the existing cross-run operational classification.
- The pending-obligation bound now equals the admitted workflow-definition
  maximum of 512 nodes. A valid 65-node replenished workflow can therefore
  retain its 65th successful provider result when the registry remains
  unavailable.
- The isolated worker protocol now has a two-phase provider-start handshake.
  The child prepares through the last pre-provider boundary and emits a nonce-
  bound `provider_ready`; the coordinator durably records
  `provider_dispatch_authorized` before sending the exact nonce-bound start
  frame. Registered-but-undispatched workers are known zero-effect, while a
  dispatched provider worker remains outcome-uncertain after coordinator loss.
- The crash proof now kills a separate coordinator OS process at eight cuts:
  before selection; after selection; after spawn intent; after real process
  registration but before provider dispatch; after provider launch; after
  atomic completion but before CAS; after CAS but before outcome journaling;
  and after the outcome. The test uses a real `ManagedProcessTree` provider
  child, restarts `RunStore`, and verifies zero replay, finalization blocking,
  idempotent CAS recovery, and exact terminal state.
- Scope, profile, and provider substitutions fail closed; recovery history
  rejects a seventh record before creating private authority; exact legacy
  normalizer v1/v2 store projection and authenticated API parity are covered.

The rereview explicitly extended scope to `agent/plugin_agent_worker.py` for
the required child-ready/provider-start protocol. No model tool, system prompt,
prior message, toolset, user configuration, workflow language surface, or
unrelated product area changed.

### Exact Fix Round 2 RED/GREEN evidence

Every Python test command below used the repository harness with retries
disabled. No direct pytest invocation or fallback was used.

#### Typed artifact privacy and legacy/API parity

Exact RED and GREEN command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_desktop_api.py
```

RED: **196 passed / 5 failed**. The exact protected fresh session ID remained
in typed artifact status, event/timeline, artifact evidence, and authenticated
API responses, while unconditional redaction removed legacy fields. GREEN
after attempt-bound artifact/event redaction and legacy gating: **201 passed /
0 failed**.

#### Independent winner and missing-session authority

Exact focused file command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py
```

RED: **44 passed / 2 failed**. A recomputed frame could substitute the pending
candidate and attempt authority in lockstep, and the missing-session hash had
no private raw-value anchor. GREEN after the separate canonical SQLite anchors
and rebuild verification: **46 passed / 0 failed**.

#### Same-run real session database corruption

Exact RED and GREEN command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py -k 'same_run_real_session_database_failure'
```

RED: **0 passed / 1 failed** with real `sqlite3.DatabaseError: file is not a
database`. GREEN after source-independent strict-v3 database-error
normalization at the same-run preflight: **1 passed / 0 failed**.

#### Admitted 65th obligation under scheduler replenishment

Exact RED and GREEN command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py -k 'scheduler_replenishment_retains'
```

RED: **0 passed / 1 failed** after provider completion with
`StorageQuotaError: session registry obligation capacity is exhausted`.
GREEN after binding capacity to the 512-node admission invariant: **1 passed /
0 failed**; the actual scheduler retained all 65 obligations with no provider
replay or post-effect completion exception.

#### Child-ready/durable-dispatch/provider-start handshake

Exact runner RED command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py -k 'authorizes_provider_after_child_is_ready'
```

RED: **0 passed / 1 failed** because `PluginAgentRunner` had no durable
provider-dispatch seam.

Exact store recovery RED command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py -k 'reaped_provider_worker or reaped_real_worker'
```

RED: **0 passed / 2 failed** because there was no dispatch record and a real
registered-but-undispatched worker recovered as outcome-uncertain.

Exact combined GREEN command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py tests/plugins/workflow/test_persistent_session_recovery.py -k 'authorizes_provider_after_child_is_ready or reaped_provider_worker or reaped_real_worker'
```

GREEN: **3 passed / 0 failed**. The full plugin-agent file subsequently passed
**124/124**.

#### Real killed-coordinator crash cuts

Exact final command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py -k 'killed_coordinator_restart'
```

Final result: **8 passed / 0 failed**. The first six-cut matrix passed **6/6**;
final diff review strengthened it with real spawn-intent and registered-before-
dispatch coordinator deaths, and the expanded matrix remained green.

#### Scope/profile/provider separation and bounded history

Exact command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py -k 'history_is_bounded or substituted_registry_authority'
```

Result: **9 passed / 0 failed** across live and rebuilt authority substitution
plus the seventh-record capacity boundary.

#### Regression found by the required gate

The first Round 2 required-gate run reported **277 passed / 2 failed**:

- `test_spawn_intent_without_process_identity_is_outcome_uncertain`
- `test_foreground_owner_death_with_unresolved_outward_spawn_reconciles`

The provider handshake correctly made replay-safe intent-only attempts
`not_started`, but had over-broadened that classification to established
outward-effect attempts. The correction retains outcome uncertainty for the
legacy outward path while using the new provider-dispatch marker only for
provider workers.

Exact focused GREEN command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_coordinator_multiprocess.py
```

Result: **2 files, 37 passed / 0 failed**.

### Final Fix Round 2 verification

The exact required ten-file command was:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_journal_reserve_fanout.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_coordinator_multiprocess.py tests/plugins/workflow/test_evidence_api.py
```

Final result: **10 files, 281 passed / 0 failed**, 14 workers, 12.6 seconds.

The exact expanded changed-seam/scheduler/Desktop command was:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_run_queries.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_cli.py
```

Final result: **9 files, 523 passed / 0 failed**, 14 workers, 59.6 seconds.
The final focused recovery file passed **59/59** and the authenticated Desktop
API file passed **157/157**.

Exact static lint command:

```bash
../../.venv/bin/ruff check agent/plugin_agent.py agent/plugin_agent_worker.py plugins/workflow/executors/ai.py plugins/workflow/executors/base.py plugins/workflow/scheduler.py plugins/workflow/store.py tests/agent/test_plugin_agent.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_persistent_session_recovery.py
```

Result: `All checks passed!`. `ruff format --check` reports whole-file format
drift in eight of those large files; running the same check on each `HEAD`
version reports the identical eight-file baseline drift, so no unrelated bulk
format rewrite was made. `git diff --check` is clean.

### Fix Round 2 changed files

- `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-13-report.md`
- `agent/plugin_agent.py`
- `agent/plugin_agent_worker.py`
- `plugins/workflow/executors/ai.py`
- `plugins/workflow/executors/base.py`
- `plugins/workflow/scheduler.py`
- `plugins/workflow/store.py`
- `tests/agent/test_plugin_agent.py`
- `tests/plugins/workflow/test_desktop_api.py`
- `tests/plugins/workflow/test_persistent_session_recovery.py`

### Fix Round 2 self-review and deviations

- The system prompt, prior messages, model tools, and per-conversation toolset
  remain byte-stable. The handshake is transport/lifecycle authority only.
- Public redaction is narrowly activated by protected registry authority;
  private execution and CAS reconciliation retain the exact session ID and
  fingerprint, while legacy/v1/v2 public fields remain exact.
- The two private authority tables are insert-only through store APIs,
  canonicalized, independently checksummed, bounded by run/attempt admission,
  batch-validated during rebuild, and removed by the existing run foreign-key
  cascade.
- A pending registry obligation is bounded by the maximum admitted node count,
  not instantaneous scheduler concurrency, so provider work cannot outrun
  durable completion capacity.
- Two intermediate capacity-fixture designs hit unrelated lease/journal reserve
  limits before the 65th obligation. They were not counted as behavioral
  evidence; the final test reconstructs 64 fully corroborated durable
  obligations, restarts the store to validate them, and uses the actual
  scheduler/provider seam for the 65th completion.
- One node-id-style harness invocation reported `No test files to run`; the
  supported `-k` form was used thereafter. No direct pytest invocation,
  harness change, retry, test deletion, test weakening, or flaky marker was
  used. The real `SIGTERM`/`SIGKILL` coordinator matrix is guarded only on
  native Windows because its asserted OS semantics are POSIX-specific.
- No push, publication, merge, branch deletion, worktree deletion,
  literal-`main` mutation, or shared `base` checkout mutation was performed.
- No blocking concerns remain.

## Current disposition after Fix Round 3

The complete Fix Round 3 correction, RED/GREEN history, exact no-retry
commands, 293-test required gate, 537-test expanded gate, changed-file list,
and self-review are recorded in the dedicated Fix Round 3 section above. This
closing disposition supersedes the preserved Fix Round 2 implementation
record: all findings in `task-13-spec-rereview-2.md` and
`task-13-quality-rereview-2.md` are addressed, static checks are clean, and
Task 14 was not entered.

## Fix Round 4 — Immutable winner authority and true provider-release boundary

Fix Round 4 was applied from authenticated base
`c8ec1f8a8ad3dc325ae427e06fa69b77947872f0`, tree
`0a7d6490cf3d9458c656fcb10126a20a759fc1ea`. It addresses the Critical and
three Important findings in the independent Fix Round 3 specification and
quality rereviews without entering Task 14.

### Corrections

- New private winner authorities use schema v2 with an immutable activation
  event sequence and a nested exact candidate. Before activation, historical
  journal frames remain valid. At and after activation, the exact succeeded
  attempt, public marker/corroboration, session, cache fingerprint, typed
  artifact, recovery digests, and selection authority remain mandatory even
  if mutable attempt state or public marker data is damaged. Existing schema
  v1 rows retain their prior compatibility behavior.
- Public redaction protects every valid privately anchored winner independently
  of mutable attempt state or marker presence. Attempt-state downgrade,
  marker removal, and session substitution therefore cannot expose either the
  original or substituted private continuation identity through event pages.
- The provider protocol now has a separate nonce-bound execute receipt and
  release. The child acknowledges the preparatory execute frame while it is
  still blocked. The parent durably records that known-zero receipt, rechecks
  cancellation, atomically records the execution-release decision, and only
  then sends the final release frame. Recovery treats only the durable
  `released` state as potentially effectful; `authorized`, `delivered`, and
  `execute_received` remain known zero.
- The durable execution-release transition uses the existing run lock and
  execution fence and requires `status == running` with no desired terminal
  state. Cancellation before release wins and no final permission is sent;
  release committed before cancellation is the opposite valid ordering.
- Private authority reads now fail closed on SQLite access errors, checksum
  mismatch, malformed JSON, and noncanonical JSON. Normalizer-v3 projections
  also enforce the reverse invariant: every public recovery selection must
  have its exact readable private anchor. A journal-fsync/SQLite-rollback cut
  consequently fails closed instead of accepting journal-only selection
  authority.

No system prompt, prior message, toolset, model-tool schema, workflow language
surface, unversioned compatibility behavior, or unrelated product area
changed.

### Exact Fix Round 4 RED/GREEN evidence

All Python tests used `scripts/run_tests.sh` with the requested interpreter and
`HERMES_TEST_FILE_RETRIES=0`. No direct pytest invocation, retry, skipped test,
or test weakening was used.

The initial combined RED command was:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py tests/plugins/workflow/test_persistent_session_recovery.py -k 'durably_orders_execute_receipt or orders_cancellation_at_true_execute_boundary or immutable_winner_anchor or active_selection_fails_closed or journal_selection_without_committed or recovery_effect_boundary or real_coordinator_death_before_execute_receipt'
```

RED: **0 passed / 13 failed** before production edits. GREEN after the bounded
implementation: **13 passed / 0 failed**.

The complete modified behavioral files initially exposed three legitimate
fixture/compatibility regressions: a schema-v2 winner was enforced against the
historical frame immediately before its activation event, bounded synthetic
recovery history omitted the newly required private selection anchors, and
legacy schema-v1 winner fixtures needed their prior activation rule. The
correction introduced the explicit event boundary, retained schema-v1
compatibility, and made the synthetic history carry valid private anchors.

The final complete modified-files command was:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py tests/plugins/workflow/test_persistent_session_recovery.py
```

Final result: **2 files, 213 passed / 0 failed** (`129 + 84`).

The focused release/cancellation command was:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py tests/plugins/workflow/test_persistent_session_recovery.py -k 'orders_cancellation_at_true_execute_boundary or provider_dispatch_cannot_cross_durable_cancellation'
```

Result: **5 passed / 0 failed**, including cancellation before durable release,
cancellation after release linearization, and the store-side desired-status
race after execute receipt.

The focused real coordinator-death/restart matrix was:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py -k 'killed_coordinator_restart or real_coordinator_death_before_execute_receipt'
```

Result: **10 passed / 0 failed**, including the real parent/child cut after
durable start receipt while the child remained blocked before execute receipt.

### Final Fix Round 4 verification

The exact required ten-file command was:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_journal_reserve_fanout.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_coordinator_multiprocess.py tests/plugins/workflow/test_evidence_api.py
```

Final result: **10 files, 306 passed / 0 failed**, 14 workers, 18.3 seconds.

The exact expanded changed-seam/scheduler/Desktop command was:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_run_queries.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_cli.py
```

Final result: **9 files, 553 passed / 0 failed**, 14 workers, 67.0 seconds.
The authenticated Desktop API file passed **157/157**.

Exact static command:

```bash
../../.venv/bin/ruff check agent/plugin_agent.py agent/plugin_agent_worker.py plugins/workflow/executors/ai.py plugins/workflow/executors/base.py plugins/workflow/scheduler.py plugins/workflow/store.py tests/agent/test_plugin_agent.py tests/plugins/workflow/test_persistent_session_recovery.py
```

Result: `All checks passed!`. `git diff --check` is clean.

### Fix Round 4 changed files

- `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-13-report.md`
- `agent/plugin_agent.py`
- `agent/plugin_agent_worker.py`
- `plugins/workflow/executors/ai.py`
- `plugins/workflow/executors/base.py`
- `plugins/workflow/scheduler.py`
- `plugins/workflow/store.py`
- `tests/agent/test_plugin_agent.py`
- `tests/plugins/workflow/test_persistent_session_recovery.py`

### Fix Round 4 self-review

- The execute protocol remains nonce-bound, process-isolated, bounded by the
  existing time/resource limits, and cleanup-safe. The final release is never
  sent unless its durable transition succeeded.
- A coordinator loss before the durable release is known zero; after the
  release decision it is conservatively uncertain and never replayed. This
  closes the reviewed start-delivery-to-execute-receipt cut without creating a
  replay window.
- The release transition is the cancellation linearization point. The store
  rejects a release after desired cancellation is durable; cancellation after
  a committed release does not retroactively convert the authorized effect
  into a replay-safe attempt.
- Private winner and selection rows remain insert-only, canonical, bounded by
  admitted attempts, and deleted by the existing run foreign-key cascade.
  Journal-only selection after a cross-store crash is intentionally rejected
  rather than silently trusted.
- Existing unversioned, Hermes legacy, normalizer-v1/v2, and schema-v1 private
  authority behavior passed the complete focused and expanded gates.
- No push, publication, merge, branch deletion, worktree deletion,
  literal-`main` mutation, or shared `base` checkout mutation was performed.
- No blocking concerns remain. Task 14 was not entered.
