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
## Integrity exception round 7 (integrity-only)

`RunStore._decide_run` now calls `RunScheduler._load_verified_run_package(run_id)`
before an approved `loop_signal_confirmation` can publish a typed loop output.
This re-validates the full format-2 sealed closure (`definition.yaml`,
`resources.json`, optional `policy.yaml`, and sealed-tree digests) before any
promotion step.

Net effect: tampering with sealed-closure content while preserving loop metadata
(`id/output_type`) cannot reach typed output publication on approval.

## Review convergence: fix round 5 of 5

Status: DONE

The final external review found that same-attempt recovery transferred ownership,
but several public active-claim mutations still authenticated only the immutable
attempt ID. The invariant was reproduced across the complete public `NodeClaim`
mutation surface before the remaining production guards were changed.

### One exact active-claim authority guard

`_active_claim_matches()` now defines active authority once: both the attempt ID
and owner ID must match the current projected claim. Every public active-claim
mutation uses that guard before success, including persistent-session selection
and outcome, all provider-boundary transitions, action-grant consumption, claim
release/expiry, loop transitions, retries, cleanup failure, completion, renewal,
and process lifecycle mutations.

Provider transitions perform this check before their idempotent-state returns. A
released provider dispatch therefore cannot return success to stale owner A after
same-attempt recovery transfers authority to owner B. A stale same-attempt
`complete_node()` call likewise cannot append its historical stale-completion audit
to B's projection. The different-attempt stale-completion audit remains unchanged
and is covered by the existing crash-recovery regression.

`record_process_stopped()` retains its deliberate cleanup exception. An immutable
old attempt with no active same-attempt claim may still record identity-matched
cleanup after lease expiry and release its old worker row. If that exact attempt is
active under a transferred owner, the stale owner is rejected before projection,
journal, recovery history, or worker-row mutation.

### Exhaustive `NodeClaim` mutation audit

The deterministic transfer matrix covers all 20 public store mutations whose
signature directly accepts `NodeClaim`:

1. `release_claim_before_execution`
2. `mark_node_started`
3. `record_persistent_session_recovery_selection`
4. `record_persistent_session_recovery_outcome`
5. `record_spawn_intent`
6. `record_spawn_failed`
7. `record_process_started`
8. `record_process_stopped`
9. `record_provider_dispatch`
10. `record_provider_start_delivered`
11. `record_provider_execute_received`
12. `record_provider_execute_released`
13. `complete_node`
14. `record_loop_iteration`
15. `record_loop_decision`
16. `block_cleanup_failed`
17. `schedule_retry`
18. `renew_claim`
19. `release_or_expire_claim`
20. `consume_action_grant`

For each case the fixture creates a real phase-4 run, action grant, persistent
session authority where required, spawn/process/provider lifecycle, journaled loop
iteration, and transactional same-attempt transfer A to B. Stale A is rejected and
the exact projection, event journal, B claim owner/lease/recovery history, worker
row, provider/action state, and private session-authority row remain byte-for-byte
equivalent. The case then proves B succeeds where the current state permits it.

The remaining methods that directly accept `NodeClaim` are non-public helpers and
do not form independent active-claim mutations: `_assert_claim_execution_fence`
validates the coordinator fence, `_terminal_completion_guard` translates terminal
reserve exhaustion, and `_bound_loop_decision` / `_validated_bound_loop_decision`
canonicalize authenticated values without store I/O. The two public
`RecordedLoopDecision` transitions carry a nested claim rather than accepting one
directly; `resume_recorded_loop_continuation` and
`prepare_recorded_loop_predicate_recovery` also use the same exact active-claim
guard and remain covered by the mandatory recovery suite.

### Fix-round TDD evidence

1. Reviewer-listed and direct-sibling matrix:
   - RED: 5 passed, 10 failed. Mark-start, both persistent-session paths,
     process-stop, all four provider transitions, release/expiry, and action-grant
     consumption accepted stale A or mutated B's authority.
   - GREEN: 15 passed, 0 failed after centralizing attempt-plus-owner checks.
2. Exhaustive public mutation extension:
   - RED: 19 passed, 1 failed. Same-attempt stale `complete_node()` rejected but
     appended a stale-completion event and changed B's projection.
   - GREEN: all 20 public mutations reject stale A without changing B, and the
     winning owner succeeds where appropriate.
3. Post-expiry process cleanup controls:
   - `test_expired_outward_attempt_preserves_identity_and_requires_reconciliation`
     and
     `test_live_replay_safe_attempt_cannot_resume_until_termination_is_proven`
     both pass, proving identity-matched cleanup remains available after genuine
     claim expiry.

Every Python test command used `scripts/run_tests.sh`.

### Fix-round final verification

- Exhaustive transferred-owner matrix: 20 passed, 0 failed.
- Post-expiry cleanup controls: 2 passed, 0 failed.
- Complete crash/shutdown/parallel recovery gate: 98 passed, 0 failed.
  - Crash recovery 74; shutdown recovery 5; parallel scheduler 19.
- Mandatory Task 9 eight-file gate: 246 passed, 0 failed.
  - V4 loops 56; loop executor 21; interactions 28; defensive invariants 14;
    crash recovery 74; shutdown recovery 5; parallel scheduler 19; evidence API 29.
- Required Task 8 broad action/store gate: 74 passed, 0 failed.
- Persistent-session/provider gate: 143 passed, 0 failed with the one known
  unrelated baseline case excluded. The excluded
  `test_recomputed_contiguous_pre_activation_order_damage_is_value_safe[prefix-delete]`
  fails identically on this branch and current `base`: both report
  `projection is ahead of its journal` where the test expects
  `private session journal order is invalid`.
- Action-grant/approval suite: 19 passed, 0 failed as part of the Task 8 gate.
- Combined explicit v1-v3 compatibility gates: 13 passed, 0 failed.
- Ruff on all touched Python files: passed.
- `git diff --check`: passed.
- `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` remains `3`.
- Normalizer v4 activation, Task 10 diagnostics, and security-review scope remain
  untouched.

## Final self-review and concerns

- Active-claim success can no longer be inferred from attempt identity alone; the
  current projected owner is required uniformly.
- Idempotent provider states are checked only after active authority, so they do not
  leak false success to a superseded owner.
- The post-expiry cleanup exception is limited to immutable identity-matched old
  attempts and cannot cross a same-attempt ownership transfer.
- No unresolved Task 9 concerns remain. The only non-green test is independently
  reproduced on current `base` and is outside this fix-round invariant.

## Review convergence: fix round 4 of 5

Status: DONE

The fourth external review found one remaining pre-execution owner fence and one
over-broad exception boundary in recovered predicate publication. Both failures
were reproduced through authentic scheduler/store paths before production edits.

### Fence-loss release preserves a recovery winner

`release_claim_before_execution()` now compares both the attempt ID and active
owner before making a claim retryable. This matches the terminal mutation fences
and prevents an expired coordinator's executor from removing a newer recoverer's
same-attempt claim or worker row.

The regression test creates coordinator A, journals a provider iteration, expires
A's lease, and has coordinator B transactionally take over that exact recorded-loop
attempt. B is paused after takeover. Resuming A through `_execute_claim()` exercises
the real failed execution-fence renewal and release helper; the helper now leaves
B's projection, decision, takeover history, event journal, claim owner/lease, and
worker row unchanged. The provider remains at one original call.

### Predicate journal failures propagate without redispatch

The store now raises a typed `StaleLoopDecisionError` only when
`record_loop_decision()` loses its exact active attempt-owner comparison. Recovery
suppresses only that expected convergence signal. Quota, integrity, journal, and
other persistence failures propagate immediately.

The adjacent recovery-result publication catch was audited: it already re-raises
every runtime failure except the explicit stale-completion outcome, so it required
no change. No generic storage or recovery exception is converted into convergence.

### Fix-round TDD evidence

1. Stale fence-loss release:
   - RED: after B's recorded-loop takeover, resuming stale A through the real
     execution-fence loss branch changed B's projection and released its claim.
   - GREEN: the active-owner comparison makes A's release a no-op; projection,
     events, recovery history, worker owner/lease, and provider count are unchanged.
2. Recovered predicate storage fault:
   - RED: an injected `StorageQuotaError` after the recovered `until_bash` process
     was swallowed. Recovery recursively re-ran the side-effecting predicate until
     `JournalRecoveryError` reported that the bounded recovery history was exhausted.
   - GREEN: `StorageQuotaError` propagates from the first failed decision journal,
     the predicate counter contains exactly one increment, the provider is not
     replayed, and the pending decision remains fail-closed.

### Fix-round final verification

- Complete crash-recovery file: 54 passed, 0 failed.
- Mandatory Task 9 eight-file gate: 226 passed, 0 failed.
  - v4 loops 56; loop executor 21; interactions 28; defensive invariants 14;
    crash recovery 54; shutdown recovery 5; parallel scheduler 19; evidence API 29.
- Required Task 8 broad action/store gate: 74 passed, 0 failed.
- Combined explicit v1-v3 compatibility gates: 13 passed, 0 failed.
- Ruff on all touched Python files: passed.
- `git diff --check`: passed.
- `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` remains `3`.
- Normalizer v4 activation, Task 10 diagnostics, and security-review scope remain
  untouched.

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

## Review convergence: fix round 6

Status: DONE

The sixth external review found that a normalizer-v4 loop with a declared
`output_type` staged a confirmed result while paused, but signal approval changed
the attempt to succeeded without activating the typed publication. The resulting
projection violated the existing invariant that every succeeded declared output
has exactly one winning typed descriptor.

### Owner-fenced staging and approval-time activation

The scheduler now retains a declared primary output for one additional exact case:
a paused loop carrying `loop_signal_confirmation`. It applies the same text-to-
Markdown canonicalization as the immediate-success path and stores the existing
bounded, body-free `primary_output_candidate` identity in the original attempt's
metadata. All other paused results remain ineligible.

`complete_node()` stages that identity only while the original claim still matches
both attempt and owner. It binds the staged candidate to the exact confirmation
artifact and immutable sealed output/schema authority but creates no publication
bundle while the node is paused.

Approval continues to avoid live workflow-definition loading and provider/executor
re-entry. Under the admission and run locks it now corroborates the raw projection
against the journal head, reauthenticates the recorded result bytes, restores the
latest paused attempt's exact staged candidate, checks the immutable run-snapshot
authority and one projected artifact, and reuses the existing secure atomic typed
publisher. The publication fields and succeeded attempt/node state are committed in
the same `loop_signal_accepted` journal projection. Duplicate and concurrent
approvals serialize through the existing interaction compare-and-set and cannot
publish twice.

The sealed structured-output requirement calculation was extracted from journal
recovery and reused by staging/activation. The valid immediate-success path retains
its original publisher and validation ordering; no new publication lifecycle or
authority was added.

### Fix-round TDD evidence

Every Python test command used `scripts/run_tests.sh`.

1. Normal and restart activation:
   - RED: 0 passed, 2 failed. The authentic counted-provider run paused correctly,
     approval marked it succeeded, and `load_run()` raised
     `typed publication requires exactly one winning descriptor`.
   - GREEN: both cases retain the original attempt, publish exactly one typed
     bundle, resolve `$produce.output`, run the downstream node, and keep the
     provider count at one.
2. Concurrent and duplicate approval:
   - RED: the two concurrent decisions converged to `applied` and
     `already_decided`, but the run still had no descriptor.
   - GREEN: the concurrent pair plus another duplicate leave exactly one projected
     publication and one physical bundle.
3. Stale, wrong, and cross-run authority:
   - Stale state, wrong interaction, and a foreign run's interaction all fail before
     filesystem publication. Correct approval publishes only the intended run.
4. Tamper defenses:
   - RED: all three cases first exposed that paused attempts had no staged candidate.
   - GREEN: changed result bytes, a forged candidate output type, and a foreign
     attempt path all fail closed, leave the node paused, create no publication
     directory, and make no additional provider call.
5. Immediate-path compatibility:
   - The first broad run exposed one changed error ordering for oversized producer
     metadata. The new authority precheck was narrowed to paused staging, restoring
     the original immediate publisher's `typed publication candidate is invalid`
     classification. The evidence and typed files then passed 53/53.

### Fix-round final verification

- Focused round-6 activation/concurrency/authority/tamper matrix: 7 passed, 0
  failed.
- Complete typed-publication file: 24 passed, 0 failed.
- Dedicated typed-publication recovery file: 40 passed, 0 failed.
- Mandatory Task 9 eight-file gate plus typed publication: 270 passed, 0 failed.
  - V4 loops 56; loop executor 21; interactions 28; defensive invariants 14;
    crash recovery 74; shutdown recovery 5; parallel scheduler 19; evidence API
    29; typed publication 24.
- Required Task 8 broad action/store/query gate: 91 passed, 0 failed.
- Combined explicit v1-v3 compatibility gates: 13 passed, 0 failed.
- Ruff on all touched Python files: passed.
- `git diff --check`: passed.
- `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` remains `3`.
- Normalizer-v4 activation, Task 10 diagnostics, Task 8 wire/actions, mutable live
  definition loading, result bodies, and security-review scope remain untouched.

## Round 6 self-review and concerns

- The paused projection contains only the canonical candidate identity; publication
  IDs and bundle bodies appear only after the exact approval wins.
- Journal corroboration prevents a syntactically valid raw candidate rewrite from
  becoming approval authority, while source bytes are still read no-follow and
  rehashed at activation.
- A restart uses the same original attempt and never re-enters the loop executor or
  provider. Existing transferred-owner coverage proves stale same-attempt claims
  cannot call `complete_node()` to stage or replace candidate metadata.
- No unresolved round-6 concerns remain.

## Integrity exception round 8 (exact projection and authority bytes)

Status: DONE

### Root cause and fix

Round 7 invoked `RunScheduler._load_verified_run_package(run_id)` before the
decision locks. That verifier called `RunStore.load_run()`. When a forged staged
candidate made `run.json` disagree with the journal, `load_run()` quarantined the
raw projection and rebuilt it from the journal. `_decide_run()` then reloaded the
repaired projection and approved it, so the verification step masked the exact
tampering it was meant to reject.

Signal approval now authenticates under the existing admission-then-run locks.
It first corroborates the exact raw projection against the journal, then passes that
projection to the verifier through a projection-aware interface that performs no
repair or recovery. A signal transition hidden by a changed preliminary projection
is rejected rather than allowed to cross into the repairing non-signal path.
Duplicate signal approvals retain their existing idempotent result and authenticate
the exact current closure before returning.

The verifier's authenticated sealed-byte mapping is also the publication authority.
Staged typed declaration resolution parses the captured `definition.yaml` bytes
instead of reopening the live path after verification. The shared declaration parser
keeps existing typed-publication recovery behavior unchanged for all other callers.

### Real-behavior regressions and TDD evidence

Every Python test command used `scripts/run_tests.sh`.

1. Exact projection masking RED:
   - Command: `scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py`
   - RED on `d48137f03`: 22 passed, 2 failed. Both
     `candidate_output_type` and `candidate_attempt_path` completed approval instead
     of raising `ArchonOutputIntegrityError`.
   - The regression now also asserts that rejection leaves the forged candidate in
     the raw projection and creates no `run.json.corrupt-*` quarantine, proving no
     repair path replaced the evidence.
2. Full format-2 sealed-closure coverage:
   - Command: `scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py -k tampered_confirmed_loop_sealed_closure_publishes_nothing`
   - Initial result: 4 passed, 0 failed for post-pause changes to
     `definition.yaml`, `resources.json`, `policy.yaml`, and the sealed-tree-only
     `dependencies.json` member. This test was added after the exploratory round-7
     closure check already existed, so that round-7 production change predates the
     regression; each case still asserts observable rejection, paused state, zero
     publication, and zero provider replay.
3. Exact projection GREEN:
   - Command: `scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py -k 'tampered_confirmed_loop_result_or_staged_candidate_publishes_nothing and (candidate_output_type or candidate_attempt_path)'`
   - GREEN: 2 passed, 0 failed, including the no-repair assertions.
4. Complete typed-publication file:
   - Command: `scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py`
   - GREEN: 28 passed, 0 failed.

### Round-8 verification

- Mandatory Task 9 runtime/recovery gate plus typed publication:
  - `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_loop_executor.py tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_typed_publication.py`
  - 274 passed, 0 failed.
- Relevant Task 8 action/store regression gate:
  - `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_approval_races.py tests/plugins/workflow/test_phase4_snapshot.py`
  - 74 passed, 0 failed.
- Explicit v1-v3 compatibility matrix:
  - `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_approval.py -k 'v1_through_v3 or v3_inline'`
  - 13 passed, 0 failed.
- Ruff on `plugins/workflow/store.py`, `plugins/workflow/scheduler.py`, and
  `tests/plugins/workflow/test_typed_publication.py`: passed.
- `git diff --check`: passed.
- `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` remains `3`.

### Round-8 self-review and concerns

- Existing lock order, non-signal approval definition loading, the original attempt,
  concurrent/duplicate compare-and-set behavior, and provider-call count remain
  unchanged.
- Approval uses no live workflow-definition read for typed declaration authority
  after the authenticated closure is captured.
- No Task 10 diagnostics, security review, normalizer activation, public action/wire
  surface, telemetry, or core-tool work was added.
- No unresolved round-8 concerns remain.
