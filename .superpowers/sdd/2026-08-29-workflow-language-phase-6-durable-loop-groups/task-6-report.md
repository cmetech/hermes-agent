# Task 6 Report: Durable Loop-Group Recovery

Status: COMPLETE

## Changes

- Reused the ordinary-loop pure completion-marker and effective-interactivity helpers for loop-group decisions.
- Made primary-sink completion, exact signal stripping, `until_bash`, pause, next-iteration creation, hard-limit failure, and outer completion durable controller decisions guarded by generation, iteration, state version, and execution fence.
- Preserved structured and typed primary-sink contracts, scoped child publications, persistent sessions, previous outputs, and between-iteration input artifacts.
- Bound approval, input, signal-confirmation, and reconciliation mutations to authenticated run/group/generation/iteration/body/attempt/artifact identity while retaining the existing public action vocabulary.
- Reused existing nested cancellation, effect classification, reconciliation, process cleanup, stale-write rejection, typed-publication recovery, and journal integrity paths.
- Added scoped private-event sanitization so execution content, feedback, commands, output data, credentials, environment values, and private paths do not enter the journal.
- Corrected journal loading so integrity is synchronized before typed-publication validation and full journal reads are reserved for retired scoped-publication or mirror recovery.

## RED Evidence

The new Task 6 suite was added before implementation. The initial focused run exposed these incomplete boundaries:

- `test_signal_completion_strips_only_the_exact_marker_and_skips_until_bash`: loop groups continued instead of producing the cleaned outer result.
- `test_until_bash_completes_only_when_the_sink_has_no_signal`: no group completion decision existed.
- `test_hard_maximum_fails_without_a_dead_interaction`: the maximum boundary did not terminalize the group.
- `test_body_approval_resumes_exact_child_without_replaying_succeeded_sibling`: interaction lookup was top-level only.
- `test_child_events_are_scoped_and_drop_private_execution_content`: nested events lacked authenticated scope and private-payload filtering.
- Fault injection also exposed `test_middle_frame_corruption_cannot_replace_the_index_integrity_baseline` and `test_healthy_load_validates_only_tail_and_does_not_rewrite_integrity_row`, locating the integrity-index/full-journal-read boundary.

The exact 100-iteration test initially stopped at iteration 76 because its default 30-second foreground lease expired. The production transition was unchanged; the test now renews the existing authenticated lease to 120 seconds and reaches the hard boundary.

## GREEN Evidence

- Focused maximum boundary: `1 passed in 59.14s`.
- Prescribed Task 6 gate: `250 passed, 0 failed, 2 skipped in 65.1s` across the eight required files.
- Task 5 scheduler/store/publication/session compatibility: `269 passed, 0 failed in 41.3s` across seven files.
- Ruff on every changed Python file: `All checks passed!`.
- Python compilation and `git diff --check`: passed.

## Fault / Recovery / Cancellation Matrix

| Boundary | Evidence |
| --- | --- |
| Child claim/start/completion and stale recovery | `test_fault_injection.py`, `test_crash_recovery.py`, scoped child-event assertions |
| Signal vs. contained Bash decision | Exact signal-wins and no-signal `until_bash` tests |
| Iteration decision and next iteration | Between-iteration input test plus Task 5 scheduler/store suites |
| Hard maximum | Exact `max_iterations: 100` test; categorical `loop_group_max_iterations`; no pending interaction |
| Approval/input/confirmation | Exact scoped approval, input, and signal-confirmation tests |
| Outward/unknown effect | Scoped reconciliation test rejects unsafe retry and terminalizes confirmed failure |
| Cancellation/process cleanup | Nested cancellation/stale-completion test plus cancel-node and process-lifecycle suites |
| Journal corruption/restart | Fault-injection and crash-recovery suites, including middle-frame corruption and tail-only healthy load |
| Typed publication/session rollover | Typed-publication, typed-publication-recovery, phase-6 scheduler, and persistent-session suites |

## Exact Tests

```text
scripts/run_tests.sh tests/plugins/workflow/test_phase6_interactions_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_cancel_node.py tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_process_lifecycle_soak.py -v

scripts/run_tests.sh tests/plugins/workflow/test_phase6_scheduler.py tests/plugins/workflow/test_phase6_store.py tests/plugins/workflow/test_phase6_execution_context.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_persistent_session_recovery.py -v
```

## Self-review

- No second loop executor, scheduler/pool, action, endpoint, repository, table, grammar surface, or core tool was added.
- Ordinary-loop helpers preserve the legacy fallback and the sealed v4/v5 semantics.
- Controller decisions authenticate the selected primary sink and reject missing or stale state instead of inferring success from files.
- Public actions keep their old names and top-level interaction shapes; scope fields are optional and private.
- The changed-file lint, compatibility suites, fault suites, cancellation suites, and process lifecycle soak are green.

Concerns: None known.
