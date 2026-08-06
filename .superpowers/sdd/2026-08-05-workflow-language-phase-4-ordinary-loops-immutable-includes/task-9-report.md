# Task 9 report: execute and recover the v4 loop state machine

Status: DONE

## Outcome

Implemented the sealed normalizer-v4 ordinary-loop runtime while leaving the
Archon current normalizer pinned to v3. The scheduler now injects the authenticated
loop semantic projection into a runtime-only node copy after verified package load;
the authored definition and sealed identity remain unchanged.

V4 inline prompts execute authored text. Command-backed prompts resolve only the
authenticated sealed command binding. Each iteration restores authenticated cleaned
prior output, exposes optional feedback for one iteration, executes through the
existing agent/session path, strips the completion marker, republishes the cleaned
artifact identity, and records the iteration before any outcome decision.

Signal precedence is absolute. Effective `signal_completes: true` succeeds
immediately; effective false publishes the exact Task 8
`loop_signal_confirmation`. Approval completes the recorded result without another
executor/provider call. A no-signal `until_bash` success completes without
confirmation, ordinary interactive input is offered only when another iteration
exists, and the final no-signal iteration fails `loop_max_iterations`.

Journaled signal transitions recover without provider replay. Feedback readiness,
acceptance, and downstream scheduling survive store restart, while result artifacts
and prior output are read through bounded no-follow authenticated paths. Evidence
projects bounded state-machine facts without prompt, result, or feedback bodies.

## Files changed

- `plugins/workflow/executors/loop.py`
  - Consumed sealed v4 loop semantics and authenticated command bodies.
  - Implemented exact signal/no-signal ordering, cleaned artifact identity,
    one-shot feedback, final hard failure, and no-follow prior-output restoration.
- `plugins/workflow/scheduler.py`
  - Injected verified loop semantics into runtime-only nodes.
  - Reconciled journaled signal transitions before execution scheduling.
- `plugins/workflow/store.py`
  - Authenticated v4 iteration artifact/state boundaries and consumed feedback only
    after durable iteration recording.
  - Published/recovered exact signal confirmations, released recovered claims, and
    terminalized accepted single-node runs or requested downstream scheduling.
  - Added bounded required/accepted/feedback transition evidence.
- `plugins/workflow/evidence.py`
  - Included the bounded loop state-machine interaction events.
- `tests/plugins/workflow/test_phase4_loops.py`
  - Added the counted-provider outcome matrix, sealed command execution, marker
    cleanup/digest, feedback restart/one-shot use, signal precedence, final failure,
    and bounded evidence coverage.
- `tests/plugins/workflow/test_loop_executor.py`
  - Added a v4 no-follow persisted-output regression.
- `tests/plugins/workflow/test_crash_recovery.py`
  - Added journal-before-pause recovery and post-acceptance downstream restart tests
    with provider replay forbidden.
- `tests/plugins/workflow/test_phase4_defensive_invariants.py`
  - Added counted-provider acceptance across a store restart.

No changes were required in the Task 8 interaction actions,
`test_shutdown_recovery.py`, `test_parallel_scheduler.py`, or
`test_evidence_api.py`; all remained in the mandatory final gate.

## State-machine and recovery decisions

- The authenticated language snapshot remains the sole authority for prompt source,
  command binding, effective interactivity, and signal completion behavior.
- Runtime semantic injection overwrites the reserved internal option after verified
  package loading, so authored input cannot become execution authority or mutate the
  sealed snapshot identity.
- V4 loop state binds the current output path, byte size, and SHA-256. Restored prior
  output uses the existing descriptor-relative no-follow reader and must match those
  exact values before the provider can run.
- The cleaned iteration artifact and any pending signal confirmation are recorded in
  one durable projection transition. A private recovery marker is present only in the
  journal recovery projection and is removed when the public pause is published.
- Restart reconciliation authenticates the exact projected result artifact and
  confirmation identity, publishes one pause, and releases the old claim/reserve.
  Acceptance never calls the loop executor/provider; a downstream graph becomes
  runnable through the existing scheduler path.
- Feedback remains a Task 8 compare-and-set action. Its bounded artifact is consumed
  for exactly the next durably recorded iteration, then cleared in the same store
  transition.
- Public evidence includes iteration/max iteration, completion mechanism, relative
  result artifact identity, interaction ID, and supported actor/channel fields. It
  excludes prompt text, result bytes, feedback text, absolute paths, and secrets.
- V1-v3 source/schema and v3 immediate-signal behavior remain unchanged.

## TDD evidence

Every Python test command used `scripts/run_tests.sh`.

1. Counted-provider outcome matrix RED/GREEN:
   - Command: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py -k counted_provider_signal_outcomes_follow_sealed_loop_semantics`
   - RED: 2 passed, 1 failed because the default v4 signal succeeded instead of
     pausing.
   - GREEN: 3 passed, 0 failed after sealed semantic execution.
2. Sealed command and final-iteration RED/GREEN:
   - Command: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py -k 'executes_only_its_authenticated_command_body or final_interactive_iteration_fails'`
   - RED: 0 passed, 2 failed.
   - GREEN: 2 passed, 0 failed after authenticated command resolution and final hard
     failure ordering.
3. Feedback/prior-output RED/GREEN:
   - Command: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py -k feedback_resume_authenticates_prior_output_and_consumes_input_once`
   - RED: 0 passed, 2 failed because resumed v4 prompts omitted cleaned prior output.
   - GREEN: 2 passed, 0 failed; the final verification also recreates `RunStore`
     before the resumed iteration.
4. Signal versus `until_bash`:
   - Command: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py -k signal_precedes_until_bash`
   - GREEN: 2 passed, 0 failed.
5. Bounded evidence RED/GREEN:
   - Command: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py -k evidence_projects_only_bounded_state_machine_facts`
   - RED: 0 passed, 1 failed because loop transition events were absent.
   - GREEN: 1 passed, 0 failed.
6. Journal-before-pause crash RED/GREEN:
   - Command: `scripts/run_tests.sh tests/plugins/workflow/test_crash_recovery.py -k restart_publishes_journaled_loop_signal_without_provider_replay`
   - RED: 0 passed, 1 failed because restart left the run running.
   - GREEN: 1 passed, 0 failed with provider replay forbidden.
7. No-follow prior output RED/GREEN:
   - Command: `scripts/run_tests.sh tests/plugins/workflow/test_loop_executor.py -k symlinked_previous_output`
   - RED: 0 passed, 1 failed because a same-content symbolic link was followed and
     the provider ran.
   - GREEN: 1 passed, 0 failed after descriptor-relative reading.

## Final verification

- Complete mandatory Task 9 gate:
  - `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_loop_executor.py tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_evidence_api.py`
  - 195 passed, 0 failed.
  - Breakdown: v4 loops 49; loop executor/legacy behavior 21; interactions 27;
    defensive invariants 14; crash recovery 31; shutdown recovery 5; parallel
    scheduler 19; evidence API 29.
- Explicit v1-v3 compatibility subset:
  - `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py -k 'v1_through_v3 or v3_inline'`
  - 7 passed, 0 failed.
- Ruff on all touched Python files: passed.
- `git diff --check`: passed.
- `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` remains `3`.

## Self-review and concerns

- No normalizer activation, Task 10 diagnostics, public action/wire surface, core
  tool, telemetry, prompt-cache mutation, or security-review work was added.
- Command execution never falls back to a live project/profile/home resource.
- Signal approval and feedback continue to use the Task 8 CAS mechanisms; there is no
  alternate mutation path.
- Recovery state is private to the durable projection and removed before public pause
  evidence. Public event views strip recovery projections and expose only bounded
  payloads.
- No unresolved concerns remain for Task 9.
