# Task 8 report: durable loop signal decisions

Status: DONE

## Outcome

Implemented the exact durable `loop_signal_confirmation` interaction and reused the
existing `approve_run()`, `provide_loop_input()`, and cancellation surfaces. A
non-final signal advertises `status`, `events`, `approve`, `provide-input`, and
`cancel`; a final signal omits `provide-input`.

Signal approval now authenticates the recorded attempt-owned result artifact under
the run lock, completes the paused attempt/node without parsing a definition or
re-entering an executor/provider, records bounded approval comments only as audit
metadata, journals `loop_signal_accepted`, and requests downstream scheduling.
Signal feedback is a compare-and-set transition that rejects empty/final feedback,
writes the existing loop-input artifact form, makes the node ready, and journals
`loop_feedback_provided`.

Existing definition-dependent approvals now authenticate and parse the sealed run
snapshot through the recorded profile, normalizer, and snapshot format. They no
longer reopen `definition.yaml` with an unversioned default parser.

## Files changed

- `plugins/workflow/models.py`
  - Added the bounded exact `LoopSignalConfirmation` value object and deterministic
    identity derivation.
- `plugins/workflow/actions.py`
  - Added authoritative non-final/final action projection with malformed signals
    failing closed to inspection plus cancellation.
- `plugins/workflow/store.py`
  - Added publication/recovery validation, artifact authentication, locked
    approval/feedback transitions, stable events, and sealed-snapshot approval
    loading.
  - Added a narrow v1 Archon recovery compatibility branch: v1 snapshots
    intentionally omit `structured_outputs`, while v2-v4 retain the strict typed
    publication authority check.
- `tests/plugins/workflow/test_phase4_loop_interactions.py`
  - Added exact shape/action, identity, publication, tamper, CAS, duplicate, race,
    recovery, audit-comment, feedback, and v1-v3 compatibility coverage.
- `tests/plugins/workflow/test_phase4_defensive_invariants.py`
  - Added stale/cross-run/final no-mutation defenses and a concurrent
    approve-versus-feedback invariant.
- `tests/plugins/workflow/test_approval.py`
  - Added sealed normalizer/definition tests for v4 and a v1-v3 compatibility
    matrix with deleted live source/sidecar files.
- `tests/plugins/workflow/test_run_queries.py`
  - Added the non-final/final public action matrix.

`tests/plugins/workflow/test_approval_races.py` and
`tests/plugins/workflow/test_phase4_snapshot.py` required no edits; both remained in
the mandatory broad gates.

## State and CAS decisions

- Durable pending state has an exact seven-field shape. Message/path byte bounds,
  lower-case SHA-256 values, positive bounded iterations, and safe canonical
  relative paths are validated before publication and during recovery.
- Interaction identity uses length-prefixed UTF-8 fields and binds run ID, node ID,
  iteration, cleaned result SHA-256, and gate message. It deliberately excludes the
  artifact path, maximum iteration count, host state, and mutable/live source state.
- Both approval and feedback require the exact interaction ID. Signal approval also
  explicitly requires `expected_state_version`; feedback retains its existing
  required version parameter. Both compare under the run/admission locks.
- Approval-versus-feedback races have one durable winner. Feedback records the
  consumed signal identity in `approval_last_decision`, allowing a losing approval
  to resolve idempotently without a second transition. Stale, wrong, cross-run,
  final-iteration, and duplicate actions do not mutate the projection.
- Result acceptance requires the latest paused attempt, exact projected artifact
  tuple, attempt-owned relative path, recorded size, on-disk bytes, and matching
  SHA-256. Approval comments are sanitized/bounded `loop_signal_accepted` audit
  metadata and are never written to the next-iteration input artifact.
- Journal recovery revalidates the pending identity and paused attempt/loop state;
  public actions are derived from the durable pending value rather than inferred
  from status alone.

## TDD evidence

All Python tests were invoked through `scripts/run_tests.sh`.

1. Initial action-list RED:
   - Command: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_approval_races.py tests/plugins/workflow/test_run_queries.py`
   - Result: 18 passed, 1 failed. The unknown interaction returned only inspection
     and cancellation actions.
2. Minimal action-list GREEN:
   - Same command.
   - Result: 19 passed, 0 failed.
3. Exact-shape RED:
   - Command: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loop_interactions.py`
   - Result: 1 passed, 13 failed. Malformed confirmations still advertised mutation
     actions.
4. Exact model/action GREEN:
   - Command: focused interaction and query files.
   - Result: 29 passed, 0 failed.
5. Real store-boundary RED:
   - Command: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loop_interactions.py`
   - Result: 17 passed, 4 failed. The store did not yet bind identity, avoid mutable
     definition parsing, authenticate tampered results, or accept signal feedback.
6. Store transition GREEN:
   - Same focused command after implementation: 21 passed, 0 failed.
   - Expanded duplicate/race/recovery coverage: 24 passed, 0 failed.
   - Final feature/compatibility file: 27 passed, 0 failed.
7. Audit-comment self-review RED/GREEN:
   - Command: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loop_interactions.py -k authenticates_result`
   - RED: 0 passed, 1 failed because the acceptance event omitted audit metadata.
   - GREEN: 1 passed, 0 failed after adding the bounded event comment.

## Final verification

- Required interaction/query/race gate:
  - `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_approval_races.py tests/plugins/workflow/test_run_queries.py`
  - 47 passed, 0 failed.
- Required broad Phase 4 gate:
  - `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_approval_races.py tests/plugins/workflow/test_phase4_snapshot.py`
  - 72 passed, 0 failed.
- Explicit v1-v3 compatibility matrix:
  - `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_approval.py -k v1_through_v3`
  - 6 passed, 0 failed (three approval plus three loop-input cases).
- Ruff on all touched Python files: passed.
- `git diff --check`: passed.
- `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` remains pinned to `3`.

## Self-review and concerns

- No Task 9 executor/outcome, provider, pause-publication, UI, endpoint, core-tool,
  telemetry, or security-review work was added.
- No live workflow source is reopened by approval decisions; the new approval matrix
  deletes live source and sidecar files before deciding.
- The v1 typed-publication recovery adjustment is adjacent to the primary feature but
  directly required by the mandated compatibility matrix. It restores the historical
  v1 omission only; newer normalizers remain strict.
- The implementation intentionally authenticates result bytes at acceptance time.
  A valid pending signal with a subsequently missing or modified artifact remains
  visible for inspection/cancellation, but approval fails without state mutation.
- No unresolved concerns remain for Task 8.
