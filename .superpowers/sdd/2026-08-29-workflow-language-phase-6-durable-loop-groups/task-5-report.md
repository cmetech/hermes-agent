# Task 5 Report: Feed body work into the existing fair scheduler

## Status

Complete. Initial atomic implementation commit:
`11ae462c4e5a41cec4857b74a184eb0d525322e9 feat(workflow): schedule loop group bodies`.
Fix round 1 was recovered after the interrupted workstation lost power and is
committed atomically with this report as
`fix(workflow): preserve scoped loop execution contracts`.
One prior-task test-fixture defect discovered during the recovery audit was
isolated in
`0bd5299094 test(workflow): use self-contained v6 parity fixture`.

## Changes

- Added frozen scheduler-local `SchedulerWorkItem` and one shared execution path
  for top-level and scoped body work. `advance_all()` retains its single bounded
  pool and run-fair cursor; bounded `advance()` consumes the same work-item path.
- Made the workerless outer controller initialize Task 4 state, commit a fully
  successful iteration before exposing the next iteration, and fail through the
  existing controller transition. Final signal/until success remains Task 6.
- Collected ordinary and body candidates in authored source order and claimed
  children through `claim_loop_group_child()` with the same profile-global,
  per-run, and scheduler ceilings as top-level claims.
- Reused all existing executor instances with semantic `group/body` authority,
  merged root/group/body options, provider/effect/structured-output semantics,
  lifecycle callbacks, retry accounting, and scoped Task 3 directories.
- Added contained attempt and publication roots at the exact required paths and
  rejected either path if resolution escaped the authenticated run directory.
- Extended `VariableContext` with explicit current-body, allowed-outer, and
  previous-body maps. Current and outer values remain dependency-scoped; prior
  values are resolved on demand from corroborated Task 4 descriptors and never
  enter the outer global node-ID map.
- Added private `$LOOP_PREV.<body>.output[.<field>]` rendering to the existing
  prompt and Bash substitution paths without changing the public grammar.
- Corrected format-2 resource binding verification to compare authenticated
  scoped semantic nodes, allowing nested command resources while preserving the
  sealed logical identity check.

## Files

- `plugins/workflow/scheduler.py`
- `plugins/workflow/resources.py`
- `plugins/workflow/bash_rendering.py`
- `tests/plugins/workflow/test_phase6_scheduler.py`

The Task 4 store transitions and existing output resolver were sufficient; no
additional public store or output-resolution surface was added.

## TDD evidence

Prescribed RED command:

```bash
export HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
scripts/run_tests.sh tests/plugins/workflow/test_phase6_scheduler.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_phase4_references.py -v
```

Observed RED output: 3 files, 41 tests passed and all 8 new Task 5 tests failed.
Seven failures reached the missing scheduler boundary: the scheduler submitted
the outer `loop_group` as a worker and raised
`KeyError: execution_semantics.nodes['group']`. The mixed-executor test also
exposed the format-2 loader's top-level-only resource-binding lookup as
`workflow_snapshot_integrity_mismatch: resource binding origin changed`.

Focused GREEN after root-cause implementation:

```bash
export HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
$HERMES_PYTHON -m pytest tests/plugins/workflow/test_phase6_scheduler.py -q
```

Focused output: 9 passed, 0 failed. This includes bounded `advance()` coverage
in addition to the original eight RED contracts.

Required GREEN command:

```bash
export HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
scripts/run_tests.sh tests/plugins/workflow/test_phase6_scheduler.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_phase4_references.py tests/plugins/workflow/test_phase6_store.py tests/plugins/workflow/test_phase6_execution_context.py -v
```

Required GREEN output: 6 files, 112 tests passed, 0 failed. Per file:

- `test_phase6_scheduler.py`: 9 passed
- `test_parallel_scheduler.py`: 19 passed
- `test_scheduler.py`: 40 passed
- `test_phase4_references.py`: 22 passed
- `test_phase6_store.py`: 13 passed
- `test_phase6_execution_context.py`: 9 passed

Additional verification:

```bash
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python -m ruff check plugins/workflow/scheduler.py plugins/workflow/resources.py plugins/workflow/bash_rendering.py tests/plugins/workflow/test_phase6_scheduler.py
git diff --check
```

Output: Ruff reported `All checks passed!`; diff check was clean.

## Behavioral evidence

- With both worker ceilings set to one, two body nodes complete without a
  controller claim or deadlock, and instrumentation observes one `advance_all`
  execution pool.
- Two group runs and one ordinary run rotate fairly while the real
  `worker_claims` table never exceeds existing global/per-run capacity.
- Independent body children overlap at capacity; authored `z-first` precedes
  lexical `a-second` under one worker.
- Iteration 2's first child claim follows both iteration 1 child success and the
  durable `loop_group_iteration_committed` event.
- Prompt, command, Bash, inline script, approval, and ordinary-loop children
  reach the existing registered executor with semantic authority and exact
  attempt/publication roots.
- Current direct-body and approved outer outputs render, hidden outer outputs
  fail closed, iteration-one whole `$LOOP_PREV` is empty, iteration-two prior
  output is authenticated, and unavailable structured fields fail before the
  executor receives the child.
- A failed second iteration leaves prior descriptors internal, keeps outer
  downstream work pending, and does not expose a group output.

## Self-review against locked constraints

- No scheduler, pool, public scheduler API, database object, core tool, grammar,
  transition engine, or global child-output map was added.
- The waiting controller consumes no worker row or pool slot; only body nodes
  claim the existing `worker_claims` table.
- One fair cursor remains run-scoped, and futures still map to run IDs for
  reconciliation and active-run accounting.
- Top-level v1-v5 execution continues through the original `_execute_claim()`
  compatibility wrapper and passed all prescribed scheduler/reference tests.
- Child identity uses validated authored IDs plus store-owned positive
  generation/iteration values. Paths are resolved and checked beneath the run
  directory before executor dispatch.
- Provider routes, effect classification, execution semantics, structured
  output decisions, and shared-context authority use semantic `group/body`
  identifiers; body declarations remain frozen.
- Current, outer, and previous output authority are separate maps. Previous
  bytes are loaded only from exactly one scope/digest/size/path-correlated
  artifact descriptor.
- Task 6 remains the sole owner of until/signal/maximum completion and final
  group output publication.

## Concerns

None within Task 5. The controller intentionally remains running after the
final body iteration until Task 6 evaluates the terminal decision.

## Fix round 1: outage recovery and reviewer closure

### Recovery

The power outage left the complete fix wave as an uncommitted seven-file diff.
Recovery began by preserving that diff, reading the binding Phase 6 design,
approved Task 5 plan and brief, this report, every changed helper and its
callers, and the new real-path tests. Git integrity had already been validated
before handoff with `git fsck`, ref/lock checks, and a diff check. No file was
reset, discarded, or reconstructed from memory.

### RED evidence carried through the outage

The interrupted review wave had already established the five regressions
against the initial Task 5 commit:

- existing deadline tests failed because top-level `advance()` and
  `advance_all()` bypassed the established `_execute_claim()` dispatch hook;
- v1-v5 prompt and Bash handling masked the private `$LOOP_PREV` token instead
  of taking the unchanged unsupported-reference path;
- scoped child completion dropped persistent-session registry updates and typed
  publication candidates before the authoritative journal/recovery path;
- shared-context predecessor evidence used outer visibility rather than the
  current body and original body dependency graph; and
- Bash and named-script children allowed variable environment values to replace
  the executor-owned scoped `ARTIFACTS_DIR`.

The saved RED tests exercise the actual paths rather than replacing the store or
executor boundary: real `PersistentRunner` session reuse with
`fresh_context: false`, real typed publication and reopen validation, real Bash
and named-script writes, both scheduler entry points, and strict v1-v5
reference failures.

### Changes

- Restored exact top-level compatibility dispatch through `_execute_claim()` in
  both scheduler entry points and retained lexical v1-v5 ready-node ordering.
  Authenticated v6 scoped children continue to use authored source order and the
  scoped `SchedulerWorkItem` path.
- Gated private `$LOOP_PREV` token recognition, masking, and rendering on the
  sealed normalizer-v6 context. Versions 1-5 retain the public unsupported-path
  error contract.
- Routed scoped child success/failure through the existing authoritative
  `complete_node()` journal transition, including session-registry candidates,
  typed publication candidates, semantic `group/body` ownership, worker-event
  identity, replay, and recovery validation.
- Derived shared-context predecessor sessions only from completed nodes in the
  current scoped body and the original body's direct dependency declarations.
  Approved outer outputs remain a separate rendering scope and cannot supply
  predecessor-session authority.
- Passed each child's scoped publication directory into `VariableContext` and
  made `NodeExecutionContext`'s executor-owned workflow and `ARTIFACTS_DIR`
  values override user variable environment values for Bash and named scripts.

### Fresh GREEN verification

Focused regression gate:

```bash
export HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
scripts/run_tests.sh tests/plugins/workflow/test_phase6_scheduler.py tests/plugins/workflow/test_phase4_references.py tests/plugins/workflow/test_deadlines.py -v
```

Output: 3 files, 66 passed, 0 failed.

Required Task 5 plus deadline gate:

```bash
export HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
scripts/run_tests.sh tests/plugins/workflow/test_phase6_scheduler.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_phase4_references.py tests/plugins/workflow/test_phase6_store.py tests/plugins/workflow/test_phase6_execution_context.py tests/plugins/workflow/test_deadlines.py -v
```

Output: 7 files, 147 passed, 0 failed.

Compatibility and shared-helper gate:

```bash
export HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
scripts/run_tests.sh \
  tests/plugins/workflow/test_language.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_language_snapshot.py \
  tests/plugins/workflow/test_phase3_language.py \
  tests/plugins/workflow/test_phase4_language.py \
  tests/plugins/workflow/test_phase5_language.py \
  tests/plugins/workflow/test_bash_e2e.py \
  tests/plugins/workflow/test_phase3_bash_descriptor_faults.py \
  tests/plugins/workflow/test_phase3_bash_lexer_security.py \
  tests/plugins/workflow/test_phase3_bash_reference_ordering.py \
  tests/plugins/workflow/test_phase3_bash_substitution.py \
  tests/plugins/workflow/test_script_executor.py \
  tests/plugins/workflow/test_persistent_session_recovery.py \
  tests/plugins/workflow/test_typed_publication.py \
  tests/plugins/workflow/test_typed_publication_recovery.py \
  tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_shutdown_recovery.py \
  tests/plugins/workflow/test_structured_output_language.py -v
```

Output: 18 files, 2,742 passed, 0 failed.

Static checks:

```bash
$HERMES_PYTHON -m ruff check \
  plugins/workflow/bash_rendering.py \
  plugins/workflow/executors/script.py \
  plugins/workflow/resources.py \
  plugins/workflow/scheduler.py \
  plugins/workflow/store.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_phase4_references.py \
  tests/plugins/workflow/test_phase6_scheduler.py
git diff --check
```

Output: Ruff reported `All checks passed!`; diff check was clean.

The first broad run found one binding v6 schema/loader parity failure. The test
was reproduced in detached clean worktrees at both pre-fix Task 5 HEAD
`11ae462c4` and pre-Task-5 implementation commit `6636352ea`; it was therefore
not caused by the recovered diff or by test order. Inspecting the complete
`WorkflowValidationError` showed `missing_command` for `run`, not an
interactivity mismatch: the in-memory structural fixture named a command but
provided no `commands/run.md`. The loader correctly applied the existing body
command resource contract while JSON Schema could only validate shape. The
authorized one-line fixture correction replaced that unrelated named command
with inline `bash: true`, preserving the interactivity/gate parity purpose.

Fixture verification before its separate commit:

```bash
scripts/run_tests.sh 'tests/plugins/workflow/test_language_schema.py::test_explicit_v6_group_interactivity_has_schema_loader_parity' -v
scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py -v
```

Output: 2 targeted cases passed, then all 645 schema tests passed.

### Fix-round self-review

- The normalizer-v6 additions stay dormant for v1-v5; no public grammar or
  private token was exposed to legacy profiles.
- Top-level scheduling preserves the historical dispatch hook and lexical
  order. Only authenticated scoped body work uses source order and scoped
  identity.
- Session and typed publication state use existing completion, journal,
  authority, replay, and recovery mechanics. No scheduler, pool, authority,
  session, or publication subsystem was added.
- Outer output visibility, current-body output visibility, and predecessor
  session evidence remain separate authority sets.
- The script executor change is limited to environment precedence for values
  owned by the authenticated execution context.
- All Task 5 fix modifications remain within the seven files named by the
  recovery handoff; the ignored task report is the only artifact added to that
  atomic fix commit. The authorized prior-task fixture correction is isolated
  in its own one-file commit.

### Fix-round concerns

No concern remains. The v6 schema/loader fixture defect was corrected without
weakening the runtime loader or broadening the Task 5 implementation.

## Fix round 2: typed publication rollover recovery

### Root cause and RED evidence

`record_loop_group_iteration()` intentionally carries only authenticated
primary-output descriptors into `previous_outputs`, increments the iteration,
and replaces the completed body's attempts with bounded iteration N+1 state.
The typed publication descriptor remains in the run's artifact projection and
its exact scoped success remains in the immutable journal. However,
`_journaled_typed_publications()` accepted a descriptor only when its attempt
still appeared as succeeded in the current body. The first store load after a
rollover therefore rejected valid iteration-N evidence.

A real normalizer-v6 two-iteration group with one typed Bash child was added
before production changes. It executes iteration one through `RunScheduler`,
commits the rollover, reopens a fresh `RunStore`, executes iteration two,
reopens again, and reads both immutable publications.

Focused RED command:

```bash
export HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
scripts/run_tests.sh tests/plugins/workflow/test_phase6_scheduler.py::test_scoped_typed_publications_recover_across_iteration_rollover -v
```

The first RED failed after `loop_group_iteration_committed` while
`_resolve_graph()` reopened the run:
`JournalRecoveryError: typed publication winning attempt is not corroborated`.
After the rollover validator was implemented, extending the same test to call
`lookup_publication()` for both iterations produced the second RED:
`PublicationIntegrityError: publication descriptor or content is not
corroborated`. That exposed the selected-descriptor caller still requiring a
historical requested descriptor to be the current iteration's winner.

### Minimal fix

- `_journaled_typed_publications()` now receives the journal events already
  read and validated by each store caller. It consults them only when a scoped
  descriptor's attempt has retired from the current bounded body.
- Retired evidence is accepted only when exactly one earlier
  `loop_group_child_succeeded` frame agrees on run, group, controller
  generation, iteration, body, worker key, attempt, exact event artifact,
  successful scoped attempt snapshot, exact projected descriptor, event
  sequence, and projection digest.
- Scoped descriptors may accumulate only through the already-admitted bounded
  iteration/artifact products. Every descriptor must match the sealed output
  declaration, and a currently succeeded scoped child must still have exactly
  one descriptor for its current successful attempt.
- Top-level publication corroboration remains unchanged: it still requires
  exactly one current succeeded attempt and exactly one winning descriptor.
- `lookup_publication()` validates the complete descriptor projection first and
  only then selects the requested publication ID, so an older scoped
  publication stays readable without bypassing the current-winner or sibling
  integrity checks.

No child attempt history, table, schema, repository, flattened child state, or
new authority mechanism was added.

### Caller audit

All `_journaled_typed_publications()` paths now consume the same journal-backed
scoped corroboration:

- `_validate_typed_publication_metadata()` on ordinary load and journal replay;
- `_recover_typed_publications_locked()` for bundle/body verification;
- `_recover_typed_mirrors_checked_locked()` for persistent typed mirrors; and
- `_requested_publication_descriptor()` for direct publication lookup.

Replay passes the already-read bounded event list; mirror recovery reuses its
existing event read; load, body recovery, and direct lookup use the existing
validated journal reader. Future events cannot authorize an earlier projection
because the corroborating sequence must not exceed that projection's journal
head.

### GREEN verification

Focused rollover/reopen/lookup regression:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_scheduler.py::test_scoped_typed_publications_recover_across_iteration_rollover -v
```

Output: 1 passed, 0 failed.

Required Task 5 plus deadline gate:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_scheduler.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_phase4_references.py tests/plugins/workflow/test_phase6_store.py tests/plugins/workflow/test_phase6_execution_context.py tests/plugins/workflow/test_deadlines.py -v
```

Output: 7 files, 148 passed, 0 failed.

Typed publication and recovery caller gate:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_scheduler.py \
  tests/plugins/workflow/test_typed_publication.py \
  tests/plugins/workflow/test_typed_publication_recovery.py \
  tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_persistent_session_recovery.py \
  tests/plugins/workflow/test_shutdown_recovery.py -v
```

Output: 6 files, 315 passed, 0 failed.

Static verification:

```bash
$HERMES_PYTHON -m ruff check \
  plugins/workflow/store.py \
  tests/plugins/workflow/test_phase6_scheduler.py
git diff --check
```

Output: Ruff reported `All checks passed!`; diff check was clean.

### Round-2 self-review

- The fallback is reachable only for a descriptor carrying authenticated v6
  `LoopGroupChildScope`; v1-v5 and top-level winner rules do not change.
- The controller generation must still match, and only a strictly earlier
  scoped iteration can use retired journal evidence.
- Both the event payload and the event's checksummed projection independently
  corroborate the descriptor and successful attempt.
- Multiple scoped descriptors are iteration/attempt identities, not competing
  winners; exactly one descriptor must match the current successful attempt.
- The change reuses the existing journal, projection digest, sealed
  declaration, publication body, and mirror validators without adding durable
  surface.

### Round-2 concerns

None.
