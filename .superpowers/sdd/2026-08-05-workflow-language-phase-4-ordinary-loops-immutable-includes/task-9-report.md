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

## Review convergence: fix round 2 of 5

Status: DONE

The second external review found two crash-recovery gaps. Both were reproduced
through the authentic scheduler/store path before production edits.

### Durable recovery ownership

A scheduler no longer treats the mere presence of a recorded loop decision as
takeover authority. Recovery now reuses the existing durable worker-claim row and
per-run/admission locks. The store permits a transactional recovery-claim CAS only
after the original lease is expired (or its coordinator fence is durably
superseded) and the attempt has stopped/not-started evidence. The CAS transfers the
existing claim owner and lease to one recoverer, records bounded takeover history,
and returns authority only to that winner while its lease is fresh.

The original worker's loop-decision, process-spawn, and terminal-publication paths
also compare the active owner, not only the immutable attempt ID, so a worker that
resumes after losing its lease cannot publish over the recovery winner. Concurrent
recoverers retain exactly-once publication and can recover another failed recovery
only after its transferred lease expires and its predicate process is proven
stopped.

### Feedback-bound pending predicates

When an `until_bash` decision is pending, the exact one-shot feedback descriptor is
now bound into the private decision by relative path, bounded byte size, and
SHA-256. The store authenticates the unique projected `text/plain` descriptor and
opens it through the descriptor-relative no-follow reader before the iteration is
journaled, again before recovery authority is granted, and again before the
predicate is dispatched. Recovery restores both authenticated `LOOP_PREV_OUTPUT`
and `LOOP_USER_INPUT`.

The node retains `loop_user_input_artifact` while the predicate outcome is pending.
It is removed only in the same compare-and-set journal transition that records the
final predicate decision. Feedback bodies remain absent from events, recovery
history, and public evidence.

### Fix-round TDD evidence

1. Fresh live-executor takeover:
   - RED: the second scheduler changed the live run from `running` to `paused` and
     reconciled the pending predicate while the original scheduler was blocked
     immediately after `record_loop_iteration` returned.
   - GREEN: the second scheduler leaves the original claim and run untouched, makes
     zero provider calls, and performs no predicate/recovery/terminal transition.
2. Feedback-dependent predicate recovery:
   - RED: `record_loop_iteration` removed `loop_user_input_artifact`, producing a
     `KeyError` before recovery could restore the accepted feedback.
   - GREEN: after an expired-lease restart, iteration two completes by
     `until_bash` with zero provider replay and the descriptor is cleared only after
     the final decision is durable.
3. Feedback integrity:
   - Same-size content substitution and a same-content symbolic link are both
     rejected before predicate dispatch. The predicate counter remains unchanged
     and feedback text is absent from journal events.

### Fix-round final verification

- Complete crash-recovery file: 48 passed, 0 failed.
- Mandatory Task 9 eight-file gate: 220 passed, 0 failed.
  - v4 loops 56; loop executor 21; interactions 28; defensive invariants 14;
    crash recovery 48; shutdown recovery 5; parallel scheduler 19; evidence API 29.
- Required Task 8 broad action/store gate: 74 passed, 0 failed.
- Direct approval/store regression gate: 35 passed, 0 failed.
- Explicit v1-v3 compatibility subset: 7 passed, 0 failed.
- Ruff on all touched Python files: passed.
- `git diff --check`: passed.
- `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` remains `3`.
- Normalizer v4 activation, Task 10 diagnostics, and security-review scope remain
  untouched.

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

## Review convergence: fix round 3 of 5

Status: DONE

The third external review found two ownership-boundary gaps. Both were reproduced
through real scheduler/store transitions before production edits.

### Recorded decisions survive generic lease recovery

Generic stale-claim expiry now leaves a claim carrying a private
`_pending_loop_decision` intact. The scheduler can therefore acquire that same
expired attempt through the existing transactional recorded-decision recovery CAS
instead of replaying the provider or losing the recorded outcome.

Expired foreground adoption likewise recognizes the recorded decision. In the
same fenced adoption transaction it preserves the attempt and decision, compares
and expires the durable worker-claim row, and excludes that attempt from generic
ready/pause reconciliation and claim release. The background scheduler then uses
the existing recovery CAS; no parallel recovery state machine was added.

### Terminal mutations are owner-fenced

`schedule_retry()` and `block_cleanup_failed()` now compare both the immutable
attempt ID and the active claim owner. A recoverer that loses its transferred lease
to another recoverer cannot schedule a retry, set cleanup failure, append an event,
or release/overwrite the winner's worker claim. Legacy and non-recovery callers
retain their existing path when both fields match.

### Fix-round TDD evidence

1. Generic expiry boundary:
   - RED: `expire_stale_claims()` returned `('refine',)` and removed the recorded
     decision's claim.
   - GREEN: expiry returns no generic work, preserves the exact claim/decision, and
     the real scheduler completes the original attempt with provider replay
     forbidden.
2. Foreground adoption boundary:
   - RED: authentic expired-owner adoption removed the claim (`KeyError: 'claim'`).
   - GREEN: fenced adoption preserves the claim/decision, atomically expires the
     worker row, and background recovery completes the original attempt with zero
     provider calls.
3. Stale retry writer:
   - RED: recoverer A did not raise after expired recoverer B took ownership.
   - GREEN: A is rejected as `stale node completion`; the projection, event journal,
     and B's worker owner/lease remain unchanged.
4. Stale cleanup-failure writer:
   - RED: recoverer A did not raise after B took ownership.
   - GREEN: A is rejected as `stale cleanup failure`; the projection, event journal,
     and B's worker owner/lease remain unchanged.

### Fix-round final verification

- Complete crash-recovery file: 52 passed, 0 failed.
- Mandatory Task 9 eight-file gate: 224 passed, 0 failed.
  - v4 loops 56; loop executor 21; interactions 28; defensive invariants 14;
    crash recovery 52; shutdown recovery 5; parallel scheduler 19; evidence API 29.
- Required Task 8 broad action/store gate: 74 passed, 0 failed.
- Combined explicit v1-v3 compatibility gates: 13 passed, 0 failed.
- Ruff on all touched Python files: passed.
- `git diff --check`: passed.
- `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` remains `3`.
- Normalizer v4 activation, Task 10 diagnostics, and security-review scope remain
  untouched.

## Review convergence: fix round 1 of 5

Status: DONE

The first external review found four correctness gaps. All four were reproduced
before production edits and closed without activating normalizer v4 or entering
Task 10/security-review scope.

### Durable post-iteration decisions

Every v4 iteration now records one strict private `_pending_loop_decision` before
the executor publishes, pauses, fails, or continues. The store canonicalizes an
exact per-kind shape and binds it to run node, active attempt, iteration, cleaned
artifact path, byte size, and SHA-256. Recovery rejects malformed, foreign, or
ambiguous authority and authenticates the artifact again through the bounded
descriptor-relative no-follow reader.

Final signal success, signal confirmation, ordinary input, `until_bash` success or
failure, and hard-limit failure publish through the original winning attempt. This
includes the normal typed-output publication path; recovery does not synthesize a
new publication authority. A recorded noninteractive continuation releases the
old attempt and begins only the next provider iteration. Concurrent final
reconciliation is compare-and-set/idempotent and produces one node terminal event.

`until_bash` is the one deliberately documented crash window: after the provider
iteration is durable but before the predicate outcome is durable, recovery may
re-evaluate the sealed predicate. It first proves any earlier predicate process is
stopped, archives bounded process evidence, and retains the existing timeout,
resource, cancellation, no-follow input, and process-lifecycle controls. The final
predicate outcome is CAS-journaled before publication. Once that final marker is
durable, recovery never executes the predicate again. This is not a claim of
exactly-once shell execution.

### Authenticated feedback input

V4 loop feedback is now accepted by the provider only when exactly one projected
`text/plain` input descriptor matches the node, path, null attempt ownership,
bounded byte size, and SHA-256. The scheduler opens the relative path no-follow,
checks size and digest, and decodes UTF-8 before dispatch. Tampering or a symlink
fails `loop_input_invalid` with zero additional provider attempts. This applies to
ordinary loop input and signal-confirmation feedback.

### Marker cleanup and attempt ownership

V4 strips both tagged and plain terminal signals from the retained artifact; v1-v3
plain-signal bytes remain unchanged. Pending signal approval now accepts only the
exact active attempt path, including the safe nested iteration path. A same-node
artifact from another attempt is rejected before acceptance.

### Adjacent Task 8 regression restored

The combined Task 8 action/store gate exposed two deterministic regressions caused
by the feature branch's new all-terminal fast path applying to generic workflow
approvals. Both exact tests passed on current `base` (1/1 each), proving the base
contracts. The fast path is now restricted to
`loop_signal_confirmation`; generic approval continuation and capacity queuing use
their pre-feature path again.

### Fix-round TDD evidence

1. Plain-signal compatibility:
   - RED: 1 passed, 2 failed; both v4 artifacts retained `DONE`.
   - GREEN: 3 passed, 0 failed; v3 remains byte-identical and both v4 paths strip
     the marker.
2. Exact attempt ownership:
   - RED: 0 passed, 1 failed; a same-node foreign-attempt result was accepted.
   - GREEN: the focused foreign-attempt test and authentic-result control both
     pass, as does the three-case real nested runtime matrix.
3. Feedback descriptor authentication:
   - RED: 0 passed, 4 failed; ordinary/signal feedback, each tampered/symlinked,
     reached the provider.
   - GREEN: 4 passed, 0 failed with provider dispatch forbidden.
4. All recorded v4 outcomes:
   - RED: 1 passed, 3 failed; only signal confirmation recovered. Immediate signal,
     ordinary input, and `until_bash` remained running after restart.
   - GREEN: the expanded six-outcome matrix passes, including hard limit and a
     false `until_bash` followed by ordinary input.
5. Recovery hardening:
   - Malformed/foreign authority, concurrent publication, typed publication,
     noninteractive continuation, and both sides of the `until_bash` final-decision
     crash window pass. Before-final recovery visibly re-evaluates once; after-final
     recovery does not.
6. Task 8 regression RED/GREEN:
   - Feature worktree RED: combined gate 89 passed, 2 failed; each exact node also
     failed 0/1 independently.
   - Current `base` read-only reproduction: each exact node passed 1/1.
   - GREEN after the loop-signal-only guard: each exact node passed 1/1 and the
     combined gate passed 91/91.

### Fix-round final verification

- Mandatory Task 9 eight-file gate: 216 passed, 0 failed.
  - v4 loops 56; loop executor 21; interactions 28; defensive invariants 14;
    crash recovery 44; shutdown recovery 5; parallel scheduler 19; evidence API 29.
- Combined Task 8 action/store gate: 91 passed, 0 failed.
- Explicit v1-v3 compatibility subset: 7 passed, 0 failed.
- Ruff on every touched Python file: passed.
- `git diff --check`: passed.
- `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` remains `3`.
