# Adversarial code review — Workflow Language Phase 3

Reviewer model: Claude Fable 5 (`fable-5`)
Review date: 2026-08-04

## 1. Exact immutable scope, tree, platform, and dependencies

| Meaning | Commit | Verified |
|---|---|---|
| Phase 2/base predecessor | `5b974a53593fc880d18417ee2fc0e5eaff5599f4` | exists; ancestor of `cffc23ce` |
| Approved Phase 3 baseline | `cffc23cecd801d3aed08ba66d596bec4a365a43a` | exists; ancestor of `8a1fe704` |
| **Final production candidate (verdict target)** | `8a1fe704484bf63e0e84f536f7fb690a2f024ccf` | exists; tree `94f4fd4572b63ba6dd496213b603e67748b41b46`; parent `e564259811a2ac063596d1d3c48c6a08217f9f8c`; subject `fix(workflow): keep schema startup read-only` |
| Report-only closure | `9aa8d8323d25df4cc1afd6a9fb646a995f71a7c3` | exists; descendant of `8a1fe704`; touches only `.superpowers/sdd/**` |

Primary production range `cffc23cecd801d3aed08ba66d596bec4a365a43a..8a1fe704484bf63e0e84f536f7fb690a2f024ccf`
independently measured at **132 commits, 221 changed files, 61,199 insertions, 967 deletions** —
matching the prompt's stated counts exactly. `git diff --check` reports no whitespace defects.

Changed-path classification (221 files): 118 retained SDD evidence files under
`.superpowers/sdd/2026-08-01-.../`; 45 production runtime/API/Desktop/generated-contract files
(`plugins/workflow/**` 26 incl. 4 new, `agent/**` 6 incl. 1 new, `apps/desktop/src/**` 6,
`tools/managed_process.py`, `hermes_cli/main.py`, `hermes_state.py`, `run_agent.py`,
`docs/upstream-customizations/workflow-orchestration.yaml`, 3 docs/skill references,
`scripts/test_workflow_merge_gate.sh`); the remainder are tests.

Review performed in a detached worktree at the exact production candidate. Platform: macOS
(Darwin 25.5.0, arm64), Python 3.11.15, pytest 9.0.2 (the phase-3 branch `.venv`), `/bin/sh` = bash.
No repository file was modified, no ref advanced, no branch switched, no worktree removed other than
the one created for this review. The single authorized write is this document.

**Note on branch state:** the mutable branch `feat/workflow-language-phase-3-semantic-compatibility-resilience`
has advanced beyond the production candidate (tip `ba3e84c30` at review time). Per the prompt, the
verdict targets the immutable commit `8a1fe704`, not the branch tip.

## 2. Verdict

```text
BLOCK
```

One CRITICAL and seven HIGH findings. The CRITICAL and three of the HIGH findings were reproduced
end-to-end with ordinary, offline, synthetic behavior against the production code at `8a1fe704`.

## 3. Findings

| ID | Severity | Title | Task(s) | Reproduced |
|---|---|---|---|---|
| C1 | CRITICAL | Arithmetic-imposing builtins (`let`, `declare -i`, `typeset -i`, `local -i`) are classified as safe simple-token contexts, so node-output bytes execute | 11 | Yes — executed |
| H1 | HIGH | v3 interaction pauses are charged as sealed workflow attempts, wedging every approval-rework, loop-input, and action-grant resume | 9 | Yes — executed |
| H2 | HIGH | The durable transient-wait protocol and its terminal-failure transition are unreachable for any consumer that already consumed an attempt; the scheduler swallows the resulting `RuntimeError` | 6 | Yes — executed |
| H3 | HIGH | The "one atomic journal transition" spans a journal fsync and a later SQLite commit; an ordinary crash in that window makes the run permanently unloadable | 13 | Interleaving proof + repo's own test |
| H4 | HIGH | Post-provider failures on the cross-run session path are recorded as `provider_attempts: 0 (exact)` with `known_no_effect: True` | 12, 13 | Trace |
| H5 | HIGH | The sealed provider-attempt transport seams live in five unledgered upstream-owned files; the checker mode that would catch this is never run | 16 | Trace + grep |
| H6 | HIGH | Inline Bash substitution has no aggregate bound; the rendered `argv` element exceeds the Linux per-argument limit and surfaces as untyped `executor_crash` | 11 | Yes — measured |
| H7 | HIGH | Loop `until_bash` is rendered twice, re-scanning and re-substituting already-rendered, model-controlled bytes under an empty dependency set | 7 | Trace + executed renderer probe |

---

### C1 — CRITICAL — Arithmetic-imposing builtins are admitted as safe contexts, so substituted node-output bytes execute

**Affected task:** 11 (verified large-value Bash substitution).

**Exact production location at `8a1fe704`:**
- `plugins/workflow/bash_rendering.py:792` — `declare`/`export`/`local`/`readonly`/`typeset` are recognised only as `top_level_assignment_builtin`; no arithmetic meaning is attached.
- `plugins/workflow/bash_rendering.py:780-781` — `if assignment_word(word): return` ends analysis of the word with no rejection.
- `plugins/workflow/bash_rendering.py:1274-1297` — the in-frame keyword lists; the token `let` appears nowhere in the file.
- `plugins/workflow/bash_rendering.py:1795-1807` — the admitted span is then emitted as `"${VAR}"` / `${VAR}` / `'"${VAR}"'`.

**Violated invariant:** Non-negotiable invariant 8 — "Large values … only enter **proven lexical
contexts**"; design §6 ("admitted values are data, not syntax"). It is also the prompt's own CRITICAL
trigger: *execution of bytes other than those admitted*.

**Realistic trigger and step-by-step production path:**
1. An author writes an ordinary arithmetic-looking v3 Bash node — `let "total = $producer.output"`,
   or `declare -i total=$producer.output`. Nothing about this is exotic; it is the natural way to do
   integer arithmetic in shell, and the node passes admission.
2. `classify_bash_reference_spans` walks the template. `let` matches no keyword, wrapper, or builtin
   list, so `finish_top_level_word` leaves `top_level_command_position = False` and the span is
   recorded as an ordinary token. For `declare -i x=$ref`, `declare` sets
   `top_level_assignment_builtin` (`:792`) and the following word `x=$ref` hits the
   `assignment_word(word) → return` early exit (`:780`). Either way the span is **admitted**.
3. `render_v3_bash` substitutes the resolved producer output, correctly quoted.
4. `/bin/sh` performs the quoted expansion — so there is no word-splitting injection — and then
   `let` / `declare -i` hand the **resulting string to the arithmetic evaluator**, which expands
   array subscripts. Command substitution inside a subscript executes.

**Concrete wrong result and consequence:** a prior node's output — for an AI or tool node, that is
model-controlled content — is executed as a shell command inside the workflow sandbox. This is the
exact boundary the Phase 3 lexer exists to hold: `$(( ))`, `((`, `$[ ]`, `[[ ]]`, backticks, `$( )`,
heredocs, and arithmetic array subscripts in assignments are all deliberately rejected. `let` and
`declare -i` are the same semantic class and are silently admitted. `/bin/sh` is bash on macOS and on
the RHEL/Fedora family, and the Windows path resolves bash via `_find_bash()`
(`plugins/workflow/executors/bash.py:124-126`) — i.e. the OTTO and LOOP24 desktop targets.
Debian-family Linux (`/bin/sh` = dash) has no `let`/`declare` and degrades to a failed command.

**Evidence — ordinary bounded reproduction (executed by this reviewer at `8a1fe704`, macOS
`/bin/sh`, benign `touch` marker only, synthetic values, no network):**

```text
ADMITTED  inline EXECUTED!  rc=0 | let "x = $n1.output"; echo done
ADMITTED  inline EXECUTED!  rc=0 | let x=$n1.output; echo done
ADMITTED  inline EXECUTED!  rc=0 | declare -i x=$n1.output; echo done
ADMITTED  inline EXECUTED!  rc=0 | typeset -i x=$n1.output; echo done
ADMITTED  inline EXECUTED!  rc=0 | f(){ local -i x=$n1.output; }; f; echo done
REJECTED  classifier: bash_reference_context_unsupported | echo $(( $n1.output ))
REJECTED  classifier: bash_reference_context_unsupported | x[$n1.output]=1
```

Substituted value: `a[$(touch <tmp>/MARKER)]`. Every `EXECUTED!` line means the marker file was
created — bytes from a prior node's output ran as a command. The final two lines are controls: the
constructs the design *does* reject are correctly rejected, which isolates the defect to the
arithmetic-builtin class rather than to the classifier as a whole. Driven through the production
`classify_bash_reference_spans` and `render_v3_bash` entry points.

**Why existing tests and gates do not catch it:** `tests/plugins/workflow/test_phase3_bash_substitution.py`
(3,125 lines, ~1,124 test cases, all passing) exercises only *lexical* constructs — heredocs,
comments, continuations, extglob, brace expansion, process substitution, `$(( ))`, and `x[$ref]=`
subscripts. `grep -n "\blet\b\|declare -i\|typeset -i" tests/plugins/workflow/*.py` returns nothing.
`test_v3_bash_rejects_arithmetic_array_subscripts_at_admission` proves the intent to reject
arithmetic-evaluation contexts, but only for the `NAME[...]=` spelling. Nothing validates Bash node
command text at admission. The gap is a missing *class member*, not a missing assertion, so a green
suite is uninformative here.

**Smallest safe remediation that fixes the whole bug class:** in
`_classify_authored_bash_reference_spans`, mark the operand region unsupported for (a) any word
position following the `let` command word, and (b) any assignment word under a declaration builtin
(`declare`/`typeset`/`local`/`readonly`) once an option word containing `i` has been seen — reusing
the existing `unsupported_range(...)` mechanism already wired for `_assignment_subscript_bounds`
(`bash_rendering.py:771-779, 1195-1210`). The required state (`top_level_assignment_builtin`,
`finish_top_level_word`, `frame.command_position`) already exists. Treating the whole declaration-builtin
operand as unsupported regardless of flags would be safer still and costs only authoring convenience.

**Required regression test:** mirror `_ARRAY_SUBSCRIPT_CONTEXTS` — a parametrized admission test
asserting `bash_reference_context_unsupported` for `let "x = {ref}"`, `let x={ref}`, `let '{ref}'`,
`declare -i x={ref}`, `typeset -i x={ref}`, and `f(){ local -i x={ref}; }` across `$USER_MESSAGE` and
`$producer.output`; plus a real-`/bin/sh` execution test rendering those templates with value
`a[$(touch marker)]` at both inline and >32,768-byte sizes, asserting the marker never exists.

---

### H1 — HIGH — v3 interaction pauses are charged as sealed workflow attempts, wedging every approval-rework, loop-input, and action-grant resume

**Affected task:** 9 (one non-multiplying provider/workflow retry ledger).

**Exact production location at `8a1fe704`:**
- `plugins/workflow/scheduler.py:3441-3444` — the charge guard is `result.status not in {"cancelled", "interrupted"}`; a `paused` result is charged.
- `plugins/workflow/scheduler.py:3448-3476` — `retry_grant.charge(...)` runs and its evidence (including `retry_consumed`) is merged into the result metadata.
- `plugins/workflow/store.py:12018` — `complete_node` copies `retry_consumed` onto the durable node record for any status, including `paused`.
- `plugins/workflow/scheduler.py:3031-3066` — the resume re-claim reads `retry_consumed`, finds `remaining_attempts <= 0`, and persists `retry_budget_exhausted`.
- `plugins/workflow/execution_semantics.py:241-264` — nodes with no retry entry project `effective_total_attempts = 1`; `plugins/workflow/language.py:416-443` emits retry semantics only for `command`/`prompt`/`bash`/`script`, so `approval` and `loop` nodes get exactly one attempt.

**Violated invariant:** Non-negotiable invariant 4 — the ledger charges *executed* work; design §3
("charged = 1 workflow attempt … **After execution**") and its rule that non-execution outcomes do
not consume the grant. An interaction pause is the normal wait state of a gate, not an execution.

**Realistic trigger and step-by-step production path:** an Archon v3 workflow with an `approval` node
carrying the documented `on_reject` rework field (`plugins/workflow/language_schema.py:1561-1565`):
1. The scheduler advances; the gate pauses awaiting a human decision.
2. `_persist_result` charges the sealed ledger — `retry_consumed = 1` of an effective total of 1.
3. The operator rejects with a reason; `store.py:14851-14852` sets the node back to `ready` and
   records `approval_rework`.
4. The next advance re-claims the node, finds the grant exhausted, and persists
   `retry_budget_exhausted` — the authored rework prompt never runs.
5. The exhausted result carries `known_no_effect = False` (not `None`), so `classify_failure`
   (`scheduler.py:320-326`) routes it to `UNKNOWN_OUTCOME` and the node is parked in a
   **reconciliation pause** demanding operator attention for an attempt that never executed.

**Concrete wrong result and consequence:** three authored v3 human-in-the-loop features are
inoperative — approval rework, interactive loop input (`store.py:14989`), and AI action-grant
approval (`store.py:14832-14833`, which additionally charges `1 + additional_provider_attempts` per
pause and so wedges after at most two approval cycles even when every action is approved). Each run
requires manual reconciliation. This is the "double-charge causing premature terminal failure" case
the ledger was built to prevent.

**Evidence — ordinary bounded reproduction (executed by this reviewer at `8a1fe704`, synthetic
package, temporary store, no network):**

```text
=== Repro A: v3 approval pause charges the sealed retry ledger ===
after first advance:    run status=paused  node state=paused  retry_consumed=1
after reject:           run status=running node state=ready   retry_consumed=1  approval_rework={'reason': 'please rework'}
after resume advance:   run status=paused  node state=paused
attempt states: ['paused', 'paused']   attempt errors: [None, 'retry_budget_exhausted']
```

The workflow is a single `approval` node with `on_reject: {prompt: …, max_attempts: 3}` and an
`archon-2026-07` sidecar, driven through the real `RunScheduler` and `RunStore`.

**Why existing tests and gates do not catch it:** every scheduler-driven pause→resume→re-claim test
is a **legacy-profile** run, where `execution_semantics is None` and no charging occurs —
`tests/plugins/workflow/test_approval.py:532`, `:585`, `:844`. The only v3 rework test
(`test_approval.py:340`) calls `ApprovalExecutor().execute()` directly, bypassing the ledger. The v3
approval E2E (`test_approval.py:100`) exercises pause→**approve**, and `approve` for
`workflow_approval` marks the node `succeeded` in the store without any re-claim — the single
interaction path that never re-enters `_execute_claim`. The v3 loop test runs to completion with no
input pause. The `paused` assertions in `test_retry.py` are reconcile pauses, where the charge is
intended.

**Smallest safe remediation:** in `_persist_result`, exclude interaction pauses from the attempt
charge — extend the exclusion to results whose pause is an interaction gate (`pending_interaction`
of type `workflow_approval` / `loop_input` / worker `approval`), leaving the failed-path reconcile
flow (which writes its own `retry_consumed` at `scheduler.py:3668`) unchanged. For AI action-grant
pauses, charge at most the provider attempts actually made, never the `+1` workflow attempt.

**Required regression test:** three scheduler-level v3 tests — approval `on_reject` reject→rework
runs and re-pauses; interactive loop pause→`provide_loop_input`→resume completes; AI action-grant
pause→approve→resume re-executes — each asserting `retry_consumed` reflects only provider calls
actually made and that no `retry_budget_exhausted` or reconcile pause appears.

---

### H2 — HIGH — The durable wait protocol and its terminal-failure transition are unreachable once a consumer has consumed an attempt; the scheduler swallows the resulting `RuntimeError`

**Affected task:** 6 (durable bounded transient-reference waits).

**Exact production location at `8a1fe704`:**
- `plugins/workflow/store.py:13005-13006` — `defer_output_resolution` raises `RuntimeError("output resolution wait consumed an executor attempt")` when `node.get("attempts") or int(node.get("retry_consumed", 0)) != 0`.
- `plugins/workflow/store.py:12915-12916` — `transition_v3_reference_node` raises `RuntimeError("reference failure already consumed an attempt")` under the identical condition.
- Unprotected call sites: `plugins/workflow/scheduler.py:1632`, `:1639` (`_preflight_strict_node_references`), reached from the ready-node preflight loops at `scheduler.py:3779` and `:4037`.
- `plugins/workflow/scheduler.py:3916-3926` — `submit()` runs `advance` on a pool whose future result is never read.

**Violated invariant:** Non-negotiable invariant 7 — a transient read "ends in success or a stable
terminal failure"; and invariants 5/6 terminality — a strict-resolver failure must become a typed
terminal failure, never an unrecorded crash.

**Realistic trigger and step-by-step production path:**
1. A v3 node consumes an output reference and holds a retry grant. This is the **default** for every
   AI node (`plugins/workflow/language.py:434-436` grants `command`/`prompt` nodes 2 retries with no
   authoring at all) and applies to `bash`/`script` nodes with an authored `retry` block.
2. Attempt 1 fails retryably — a provider stall or rate limit, the most common production failure.
   The node becomes `waiting_retry` with `retry_consumed >= 1`.
3. `wake_due_retries` returns the node to `ready` with its attempt history intact.
4. Preflight re-resolves the node's references. With a cold cache — coordinator restart, LRU
   eviction under the 16 MiB bound, or multiprocess adoption by a second coordinator, all first-class
   supported scenarios — the producer output is re-read from disk.
5. The read hits a retryable host errno (`ESTALE` on NFS failover, `EMFILE`/`ENFILE` under fd
   pressure, `EIO`, `ENOMEM`, `EAGAIN` — `output_resolution.py:28-36`), producing
   `output_reference_temporarily_unavailable`.
6. Preflight calls `store.defer_output_resolution`, which **raises** instead of entering the wait.

**Concrete wrong result and consequence:** the exception unwinds `advance`. In the background path
the future is discarded, so it is **silently swallowed**: no node transition, no `last_error`, no
wait scheduled. The node stays `ready`, the run stays `running`, and every subsequent sweep
re-crashes identically — a durable, invisible livelock for as long as the host condition persists (a
stale NFS handle can persist indefinitely). In `advance_all` the raise also aborts the sweep
mid-batch, starving other runs' candidate selection for that iteration. In a foreground CLI
`advance` the operator sees a raw `RuntimeError` instead of a typed workflow error.

**Evidence — ordinary bounded reproduction (executed by this reviewer at `8a1fe704`):**

```text
=== Repro B: durable-wait deferral after a consumed attempt raises ===
after attempt 1:  state=waiting_retry  retry_consumed=1  attempts=1
after retry wake: state=ready          retry_consumed=1  attempts=1
defer_output_resolution raised RuntimeError: output resolution wait consumed an executor attempt
transition_v3_reference_node raised RuntimeError: reference failure already consumed an attempt
```

A two-node v3 package (`producer` → `consumer` with `retry: {max_attempts: 2}`) driven through the
real `RunScheduler`: the producer succeeds, the consumer's first attempt genuinely fails, the retry
is woken, and both durable transitions are then unreachable.

**Why existing tests and gates do not catch it:** every wait-protocol test in
`tests/plugins/workflow/test_phase3_resolution_waits.py` asserts `consumer["attempts"] == []` before
deferral (lines 218, 250, 407, 460, 505, 572, 675…) — the protocol is only ever exercised on
zero-attempt consumers. No retry suite injects a resolver failure on a second attempt, and the
failure mode is an exception no assertion observes because the submission pool discards it.

**Smallest safe remediation:** the guards conflate two invariants. What must hold is "the wait or
terminal transition itself charges no attempt", not "the node has never attempted". Stop raising on
prior consumption and preserve the existing `retry_consumed` instead of asserting it is zero — i.e.
do not execute `node["retry_consumed"] = 0` at `store.py:13041` for a consuming node, and likewise at
`store.py:12919`/`12959`.

**Required regression test:** (a) fail attempt 1 of a `retry: {max_attempts: 2}` consumer, wake the
retry, force `resolve_node_output` to raise `ArchonOutputUnavailableError`, and assert the node
enters `waiting_resolution` with `retry_consumed` unchanged and one attempt retained; (b) the same
setup raising an integrity error, asserting a terminal `failed` with the typed code and unchanged
attempt accounting.

---

### H3 — HIGH — The "one atomic journal transition" spans a journal fsync and a later SQLite commit; an ordinary crash in that window makes the run permanently unloadable

**Affected task:** 13 (durable cross-run persistent-session recovery).

**Exact production location at `8a1fe704`:**
- `plugins/workflow/store.py:2761-2779` — `_execution_fence_transaction` calls `connection.commit()` only at context exit; any exception rolls the SQLite half back.
- `plugins/workflow/store.py:10110-10151` — `record_persistent_session_recovery_selection` writes the private selection authority on the still-uncommitted fence connection, then `_append_locked` fsyncs the public journal event.
- `plugins/workflow/store.py:11893-11920`, `:12071-12088` — `complete_node` does the same for the winner authority, then fsyncs `node_succeeded` plus the pending registry obligation.
- Failure surface on reload: `plugins/workflow/store.py:7276-7279` raises `JournalRecoveryError`; `store.py:5313-5337` and `:7135-7144` require a committed authority for every recovery/obligation entry; `store.py:6768-6785` re-raises on every subsequent load.

**Violated invariant:** Non-negotiable invariant 10 — a winning result and its obligation are
journaled **atomically**, and crash "cannot … discard the obligation"; design §8 crash rules, which
specify "after selection but before provider launch, ordinary zero-effect interrupted claim recovery
applies" and "after atomic completion but before registry CAS, recovery applies the durable
obligation without rerunning the provider". Neither outcome is "the run becomes unreadable".

**Realistic trigger and interleaving proof (deterministic reproduction requires a kill at a
specific instruction, so the proof is by construction over the shipped ordering):**
1. Background execution holds an `ExecutionFence`, so `fence_connection is not None` and the private
   authority row is written **inside the still-open transaction**.
2. `_append_locked` durably fsyncs the public journal event and rewrites `run.json`.
3. The process dies before `connection.commit()` — SIGKILL, power loss, OOM kill — or the commit
   itself fails (a disk-full WAL append suffices; `BaseException → rollback()` at `store.py:2775-2777`).
4. Durable state: the journal asserts the selection or completion; SQLite has no matching authority
   row. The exact candidate values existed only in memory and cannot be reconstructed.
5. The next `load_run` quarantines `run.json`, replays the journal, fails authority matching, and
   raises. Every subsequent `load_run` / `get_run_status` / evidence query for that run raises forever.

**Concrete wrong result and consequence:** at the selection site, a pre-provider crash converts a
running run into a permanently unloadable one where the design promised ordinary zero-effect
recovery. At the completion site it is worse: a **journaled winning provider result** — possibly
with outward side effects — and its registry obligation become permanently unappliable and
unreadable. That is durable loss of a committed result in a one-commit-wide ordinary-crash window,
which is precisely the class Task 13 exists to survive.

**Evidence:** the repository's own suite constructs this exact durable state and **pins the failure
as desired**: `tests/plugins/workflow/test_persistent_session_recovery.py:3088`
(`test_journal_selection_without_committed_private_anchor_fails_closed`, described in-test as "a crash
after journal fsync but before SQLite commit") asserts `load_run` raises `JournalRecoveryError`; the
completion site is pinned identically at `:3399`. Fail-closed is the correct posture against a
*forged* journal entry, but the identical state arises from a plain crash, and the spec's closed list
of crash outcomes never includes "run permanently unloadable". The plan's crash injections were
placed at method boundaries, so the intra-method window was resolved silently to a narrower
guarantee than the design states.

**Why existing tests and gates do not catch it:** they do not miss the state — they *encode* the
wrong resolution of it, so no amount of green suite can surface the divergence from the design.
`test_persistent_session_recovery.py` is additionally excluded from the merge gate (see H5).

**Smallest safe remediation:** commit the private authority row in its own fenced transaction
**before** the journal file append — exactly what the `fence_connection is None` branch already
does. An orphaned pre-commit is already proven inert by the suite's own
`test_no_fence_selection_precommit_is_not_activated_by_heartbeat` (`:3244`) and
`test_no_fence_winner_precommit_is_not_activated_by_sibling_event` (`:3311`): activation sequencing
makes an authority without its event inactive. This removes the brick without weakening the
anti-forgery direction, since event-without-authority can then only mean tampering.

**Required regression test:** for a fenced run, fault-inject a process kill after `_append_locked`
returns but before the fence commit, at each of the two store methods; assert `load_run` succeeds,
that the selection case yields ordinary zero-effect claim recovery with no provider call and no CAS,
and that the completion case recovers and applies the obligation exactly once without provider replay.

---

### H4 — HIGH — Post-provider failures on the cross-run session path are recorded as exactly zero provider attempts with `known_no_effect: True`

**Affected tasks:** 12, 13.

**Exact production location at `8a1fe704`:**
- `plugins/workflow/executors/ai.py:1245-1258` — the handler wraps the **entire** first `launch_agent(request)` call and maps `OSError | PermissionError | ValueError | sqlite3.DatabaseError` to `_recovery_unavailable()` whenever `session_source == "cross_run_registry"`.
- `plugins/workflow/executors/ai.py:182-194` — `_recovery_unavailable()` returns `{"provider_attempts": 0, "provider_attempts_exact": True, "known_no_effect": True, "archon_terminal_failure": True}`.
- `plugins/workflow/executors/ai.py:1572-1574` — a **completed** provider run with an empty `result.session_id` also returns `_recovery_unavailable()`.
- Post-provider `ValueError` source: `agent/plugin_agent.py:1600` — `PluginAgentRunResult.from_wire(result)` runs after the worker and provider have fully executed and raises `ValueError` on a missing/unknown field or structured-evidence/audit disagreement (`agent/plugin_agent.py:643-717`).

**Violated invariant:** Non-negotiable invariant 9 — "nonzero provider work cannot enter the recovery
path"; design §7 scopes `persistent_session_recovery_unavailable` to *pre-provider* session
verification failures, and §8 states "after provider launch, existing uncertainty rules apply".

**Realistic trigger and step-by-step production path:**
1. A v3 node with a matching cross-run registry record runs in `context_mode="shared"` — note this is
   every cross-run persistent-session execution, not only a recovery.
2. The worker completes a full provider conversation, possibly with outward effects (a sent email, a
   filed ticket), and the result frame then fails `from_wire` — a wire-shape defect or a
   parent/worker version skew during an update.
3. `ai.py:1245` catches the `ValueError` and returns `_recovery_unavailable()`. The node is durably
   journaled as a terminal failure with **exact zero provider attempts** and **`known_no_effect: True`**.
4. The alternate trigger at `ai.py:1572-1574`: the run *completes successfully* but the host agent
   reports no session id, so the successful result is discarded and the same fabricated metadata is
   journaled.

Contrast the non-session path: the identical exception maps to `validation`/`network_error` with
conservative full-grant accounting and no zero-effect claim (`ai.py:1289-1305`). Only the
cross-run-session arm fabricates certainty.

**Concrete wrong result and consequence:** durably false zero-effect evidence after real provider
work. `archon_terminal_failure` forces `FailureClass.FATAL`, so there is no in-run replay — but the
recorded `known_no_effect: True` suppresses the unknown-outcome taxonomy for a node that may have
performed outward actions (with `outward_action`, `classify_failure` would otherwise have yielded
`UNKNOWN_OUTCOME`), and it tells operators and tooling the run is safe to resubmit. That is a
credible path to **duplicate outward provider execution**, plus an exact-zero charge into the sealed
ledger for attempts that actually happened. In the empty-session-id variant it additionally discards
a fully successful winning result.

**Evidence:** code trace above; the handler's `try` block is confirmed to open at `ai.py:1221`
(`result = launch_agent(request)`), so it spans the entire provider dispatch rather than a preflight
region. The narrow, correctly-scoped sibling handler for `PluginAgentSessionMissingError` immediately
above it (`ai.py:1222-1244`) shows the intended shape.

**Why existing tests and gates do not catch it:** every `recovery_unavailable` test injects its
failure *before* spawn — `test_real_session_database_failure_is_recovery_unavailable_before_spawn`
(`:777`), `test_registry_read_failure_is_recovery_unavailable_before_provider` (`:1396`),
`test_cross_run_session_probe_failure_is_not_treated_as_confirmed_absence` (`:674`) — and the
exception test (`:1861`) raises `OSError` before the fresh worker runs. No test raises `ValueError`
from the runner after provider dispatch on a cross-run-session node, and none drives a completed
result with an empty session id.

**Smallest safe remediation:** narrow `ai.py:1245` to the preflight boundary — wrap the preflight
`SessionDB` open/read failures in `agent/plugin_agent.py:1556-1566` in a dedicated typed operational
error and map only that (plus `sqlite3.DatabaseError` from the registry `get`) to
`_recovery_unavailable()`, letting every other `OSError`/`ValueError` fall through to the existing
conservative handlers. At `ai.py:1572-1574`, return a failure carrying conservative provider
accounting and no `known_no_effect` claim.

**Required regression test:** (a) a stub runner that records a provider dispatch and then raises
`ValueError` under a cross-run registry session — assert the error code is not
`persistent_session_recovery_unavailable`, that `known_no_effect` is not `True`, and that the charge
is conservative; (b) a completed result with `session_id=""` — assert no zero-attempt or
known-no-effect claim and that the winning result is not silently discarded.

---

### H5 — HIGH — The sealed provider-attempt transport seams live in five unledgered upstream-owned files, and the checker mode that would catch this is never run

**Affected task:** 16 (regression, integration, and customization convergence).

**Exact production location at `8a1fe704`:**
- `run_agent.py:5050-5057` — `before_transport=lambda: reserve_provider_transport_attempt(self)`.
- `agent/anthropic_adapter.py:2888, 2906-2908, 2922-2924` — the hook at both stream and create launches.
- `agent/chat_completion_helpers.py` — nine `reserve_provider_transport_attempt(agent)` sites.
- `agent/codex_runtime.py:696, 1254`.
- `agent/provider_attempts.py` — the new delegating seam.
- Ledger: `docs/upstream-customizations/workflow-orchestration.yaml` contains **no entry** for any of
  these files. `grep -rn` across `docs/upstream-customizations/*.yaml` matches
  `agent/chat_completion_helpers.py` only in `browser-profiles.yaml:109`, for the unrelated
  `cleanup_task_resources` hook.

**Violated invariant:** Non-negotiable invariant 15 — "every changed upstream-owned invariant is
represented accurately in the customization ledger and merge gate"; `docs/upstream-customizations/README.md`
requires each upstream-owned changed file to be enumerated with owned symbols, tests, and merge guidance.

**Realistic trigger and step-by-step production path:** an upstream merge (`main → base`) rewrites any
of these heavily-churned files — `interruptible_streaming_api_call` and
`_dispatch_nonstreaming_api_request` are refactored upstream regularly. Git auto-merges, or a
maintainer resolves take-theirs. The reserve call sites vanish with **no conflict, no
decision-required overlap row, and no build error**.

**Concrete wrong result and consequence:** for sealed workflow AI nodes, the worker disables the
legacy wrapper counting and relies exclusively on the transport-launch callback. With the call sites
silently reverted, the sealed grant is never charged and never exhausts: workflow AI-node provider
authority becomes **unbounded** across recovery and fallback cycles — exactly the Task 9 defect this
seam was added to close — while audit `provider_attempts` reads 0. Delivered Phase 3 behavior is
removed by a green merge.

**Evidence:** the checker's own diff-coverage algorithm, replicated faithfully over the delivery
range (same path prefixes, colocated-test and additive exemptions, `_EPHEMERAL_COVERAGE_PATHS`),
**fails** with missing paths `agent/anthropic_adapter.py, agent/chat_completion_helpers.py,
agent/codex_runtime.py, agent/provider_attempts.py, run_agent.py`. No gate ever runs that mode:
`scripts/test_workflow_merge_gate.sh:384` invokes the checker with no `--diff` (structure only),
`scripts/test_workflow_upstream_merge.sh:74` uses `--upstream-diff`, which per the README first
intersects upstream-changed paths **with ledger-declared files** — so unledgered files produce no
rows — and the plan's Task 16 command also omits `--diff`. Direct execution of the real checker in
the review worktree aborts earlier on pinned Node parser dependencies, which is why the algorithm was
replicated rather than invoked.

Supporting contradiction in the same area: `docs/upstream-customizations/workflow-orchestration.yaml:4001`
declares an owned invariant "Phase 3 Bash security base merge-gate suite selection" and lists
`tests/plugins/workflow/test_phase3_bash_lexer_security.py` in the entry's tests, while
`tests/scripts/test_workflow_merge_gate.py:79-87` now asserts the gate must **not** select that file
or `test_persistent_session_recovery.py` — changed by `e56425981`, the second-to-last commit in the
range, without reconciling the ledger. `test_persistent_session_recovery.py` (the 4,699-line primary
Task 13 suite) appears in no ledger entry at all, so nothing executes it at merge time.

**Why existing tests and gates do not catch it:** the base gate's only relevant suite,
`tests/agent/test_plugin_agent.py`, monkeypatches `run_agent.AIAgent._interruptible_api_call` with a
fake that **calls the reservation callback itself** (`tests/agent/test_plugin_agent.py:101-103`), so
it passes with every production reserve site deleted. The one production-path test
(`tests/agent/test_bedrock_interrupt_post_worker.py:106`, covering only the Bedrock family) is in
neither the merge gate nor any ledger entry's tests, so the ledger invariant runner
(`scripts/run_workflow_ledger_invariants.py:1740-1747`) never runs it either.

**Smallest safe remediation:** add a `workflow-orchestration.yaml` entry (change class
`agent-core-generic`, owned symbols `reserve_provider_transport_attempt` plus each call-site
enclosing function) covering all five files, listing
`tests/agent/test_bedrock_interrupt_post_worker.py` plus a new production-path reservation test per
transport family; add that test file to the base gate list; and reconcile the two ledger/gate
contradictions above in the same commit.

**Required regression test:** with a fake client and the real
`_dispatch_nonstreaming_api_request`/`create_anthropic_message`, assert the callback fires exactly
once per transport launch and that grant exhaustion raises before the client call; plus a
`tests/scripts/test_workflow_merge_gate.py` assertion that every ledger gate-selection claim matches
the gate's actual selection.

---

### H6 — HIGH — Inline Bash substitution has no aggregate bound; the rendered `argv` element exceeds the Linux per-argument limit and fails as untyped `executor_crash`

**Affected task:** 11.

**Exact production location at `8a1fe704`:**
- `plugins/workflow/bash_rendering.py:1742` — `if len(encoded) <= BASH_INLINE_MAX_BYTES or encoded in spill_by_value: continue` excludes inline values from every accumulator.
- `plugins/workflow/bash_rendering.py:1748-1756` — `spill_total_bytes` / `BASH_SPILL_MAX_TOTAL_BYTES` count spilled bytes only.
- `plugins/workflow/executors/bash.py:128` — `argv = ["/bin/sh", "-c", rendered_command.command]`.
- `plugins/workflow/executors/bash.py:242-245` — any non-`ValueError` spawn failure is re-raised.

**Violated invariant:** Non-negotiable invariant 8 and design §6 "Bounds" — all four documented
bounds govern spills, leaving the inline regime unbounded, which is the regime the spill machinery
exists to avoid.

**Realistic trigger and step-by-step production path:** a Bash node references a moderately large
node output several times. Each individual value is ≤ 32,768 bytes, so nothing spills, and every
occurrence is inlined in full. The concatenation becomes one `argv` element.

**Concrete wrong result and consequence:** Linux caps a single `argv` string at
`MAX_ARG_STRLEN = PAGE_SIZE * 32 = 131,072` bytes, so four maximum-size inline references already
exceed it and `execve` returns `E2BIG`. `ManagedProcessTree.spawn` raises `OSError`, which is not a
`BashRenderingError`, so the executor re-raises and the scheduler maps it to the untyped
`executor_crash` — not the documented `bash_substitution_limit`, and not a terminal Archon failure
with bounded evidence. The ceiling scales only with the authored template
(`MAX_WORKFLOW_DOCUMENT_BYTES` = 2 MiB at ~12 bytes per reference), so the worst case is a
multi-gigabyte Python string built in the scheduler process before any bound is consulted.

**Evidence — measured by this reviewer at `8a1fe704`:** template `true $n1.output ×8`, each value
exactly `BASH_INLINE_MAX_BYTES`:

```text
refs= 8   spill_count= 0   rendered_size_bytes= 262156
MAX_ARG_STRLEN(linux)=131072 exceeded: True
```

Zero spills and a single 256 KiB `argv` element.

**Why existing tests and gates do not catch it:** the only `BASH_INLINE_MAX_BYTES` reference in the
tests is a contract re-export assertion (`test_phase3_bash_substitution.py:38-39`). The bounds tests
all exercise the spill accumulators; every content test uses one reference. CI on macOS succeeds
(`ARG_MAX` there is a 1 MiB total with no per-string cap), so a Linux-only failure would not
reproduce locally.

**Smallest safe remediation:** accumulate inline bytes alongside spill bytes in the `render_v3_bash`
loop and raise `bash_substitution_limit` when the total crosses a bound safely under 128 KiB — or
spill above that aggregate rather than only per value.

**Required regression test:** render a template with N references to `BASH_INLINE_MAX_BYTES`-sized
values; assert `bash_substitution_limit` (not `executor_crash`) is raised before any spawn, and that
`rendered_size_bytes` never exceeds the declared aggregate.

---

### H7 — HIGH — Loop `until_bash` is rendered twice, re-scanning already-rendered model-controlled bytes under an empty dependency set

**Affected task:** 7 (strict substitution through every existing consumer).

**Exact production location at `8a1fe704`:**
- `plugins/workflow/executors/loop.py:228-246` — first pass renders `until_bash`, then constructs `WorkflowNode(..., value=rendered, depends_on=())`.
- `plugins/workflow/executors/bash.py:59` — `command = str(context.node.value)`.
- `plugins/workflow/executors/bash.py:40-43, 60-78` — `secure_v3` survives `replace(context, ...)`, so the already-rendered text is rendered **again** with `direct_dependencies=context.node.depends_on`, which is now `()`.

**Violated invariant:** Task 7's facade contract (`plugins/workflow/resources.py:937` — "Render only
the requested initial body; replacements are not rescanned") and design §6's explicit carve-out that
the loop `until_bash` path does not gain the v3 spill prologue in this phase.

**Realistic trigger and step-by-step production path:** a loop node whose `until_bash` references
`$LOOP_PREV_OUTPUT` — the AI iteration output, i.e. model-controlled. Pass 1 substitutes it,
`shlex.quote`-ing the model text into the command. Pass 2 re-scans that text and substitutes
anything reference-shaped that came out of the model.

**Concrete wrong result and consequence:** (a) workflow inputs (`$ARGUMENTS`, `$USER_MESSAGE`,
`$CONTEXT`, `$1..$n`) are silently injected into a shell command whose surrounding text is
model-authored — a confused-deputy mutation of an already-rendered command; (b) ordinary model prose
containing `$x.output` terminally fails the loop node with
`output_reference_not_declared_dependency`, an error the operator cannot act on and that a model can
trigger at will; (c) contrary to the design, `until_bash` does take the v3 path — including
spill-descriptor rendering — whenever a rescanned scalar exceeds 32,768 bytes.

**Evidence:** structural trace confirmed at the three cited call sites (`loop.py` hands a rendered
command to a `depends_on=()` node; `bash.py:59` re-reads it as a template and re-renders). Executed
probe against the production `resources.py` renderer with `direct_dependencies=()`:

```text
OK    "test 'model said $ARGUMENTS' = x"  ->  "test 'model said SECRET-ARGS' = x"
OK    "grep -q 'see $CONTEXT'"            ->  "grep -q 'see SECRET-CTX'"
RAISE "test '$a.output' = x"              ->  output_reference_not_declared_dependency
```

**Why existing tests and gates do not catch it:** loop coverage in `test_loop_executor.py` drives
`until_bash` with well-formed templates only; no test feeds a loop iteration output that itself
contains `$`-shaped text, and none asserts that the Bash executor does not re-render a value it
received pre-rendered. The failure is data-dependent rather than structural, so a green suite is
uninformative.

**Smallest safe remediation:** have `loop.py` hand the Bash executor an authenticated *template*
plus the loop variable context, letting `BashExecutor` render exactly once — or mark the
loop-generated node as pre-rendered so `executors/bash.py:60-78` skips substitution entirely.

**Required regression test:** a loop test whose iteration output literally contains `$ARGUMENTS` and
`$a.output`, asserting the executed `until_bash` bytes are byte-identical to the pass-1 rendering and
that the node does not fail with `output_reference_not_declared_dependency`.

---

## 4. Task 1–16 coverage matrix

| Task | Production concern | Status | Basis |
|---:|---|---|---|
| 1 | Profile-specific normalizer v3 and requested semantics | **proven** | `CURRENT_NORMALIZER_BY_PROFILE` pins legacy=2/Archon=3 with v3 rejected for non-Archon profiles at both selection (`language.py:287-314`) and snapshot parse (`:817-824`); the v2 digest document is byte-identical to the baseline for legacy; single ms→s conversion at `language.py:500-526`; bool/zero/negative/non-finite/overflow all rejected |
| 2 | Sealed effective execution semantics at admission and resume | **proven** | Built once in `prepare_run_snapshot` (`store.py:5436-5449`), sealed into `resources.json` whose SHA-256 *is* `input_manifest_digest` (`:5698`); all five admission entry points resolve limits explicitly; resume rebuilds the projection from the sealed limits and requires exact `to_dict()` equality (`execution_semantics.py:415-420`); no sibling admission path found |
| 3 | Closed static v3 output-reference grammar | **proven** | One grammar defined at `language_schema.py:67-75` and reused by node-ID admission, static scanning, condition parsing, and rendering; the scanned surface set matches the runtime template set exactly; direct-dependency enforcement re-asserted at render time |
| 4 | One typed and rendered runtime output resolver | **proven** | Single resolver (`output_resolution.py:673-728`) produces both facets; conditions consume `typed_value`, substitutions consume `rendered_text`; executors read a frozen pre-claim snapshot; no failure path yields `""`; the v2/legacy adapter branch is byte-equivalent to baseline |
| 5 | Typed v3 condition evaluation | **proven** | Same parser at admission and runtime; false → `skipped`, typed error → `failed` via a pending-only CAS with zero attempts and zero retry consumption; non-finite rejected; decimal ordering avoids binary-float artifacts; legacy dispatch is verbatim baseline code |
| 6 | Durable bounded transient-reference waits | **contradicted** | Correct for zero-attempt consumers (exact 250 ms–4 s ladder, producer-identity CAS fencing, restart-durable, no hot loop, no attempt charge), but unreachable for any consumer that has consumed an attempt — **H2** |
| 7 | Strict substitution through every existing consumer | **contradicted** | The facade is genuinely threaded through script/bash/loop/AI consumers and named scripts are correctly non-interpolated, but the loop→bash boundary re-renders an already-rendered command under an empty dependency set — **H7** |
| 8 | Sealed per-attempt timeout enforcement | **proven** | One `DeadlineBudget` per claimed attempt, mandatory at execution (`scheduler.py:3103-3107`); bash/script poll only the sealed budget; AI wall/idle/provider re-intersect with the remaining wall at every provider handoff including repair and fresh-recovery relaunch; retry backoff sits outside the attempt |
| 9 | One non-multiplying provider/workflow retry ledger | **contradicted** | Non-multiplication holds on every traced path (single grant, single charge equation, conservative charge on absent evidence, cancellation not charged), but the ledger over-charges interaction pauses — **H1** |
| 10 | Generic bounded child-descriptor inheritance | **proven** | Child reads the exact nominated descriptor only; every documented rejection fires with the right exception; `start_new_session` and process-tree termination intact; spawn failure closes no caller-owned handle and leaks no fd across 20 consecutive spawns; fail-closed on native Windows; no non-workflow caller passes `inherited_descriptors` or `pass_fds` |
| 11 | Verified large-value Bash substitution | **contradicted** | Boundary, content preservation, NUL rejection, dedup, spill limits, descriptor lifecycle, and a 15,120-case differential classifier/renderer oracle all hold — but arithmetic-imposing builtins are admitted (**C1**) and inline substitution is unbounded (**H6**) |
| 12 | Missing isolated-session classification without core widening | **contradicted** | Preflight, the atomic `LEFT JOIN` worker classification, spoof renaming, and strict frame correlation are all correct and no `plugins.workflow` import enters the agent core — but the exception mapping admits post-provider failures into the recovery classification — **H4** |
| 13 | Durable cross-run persistent-session recovery | **contradicted** | CAS taxonomy, generation fencing, claim-fenced selection, terminal gating on pending obligations, bounded backoff, and evidence privacy are implemented and heavily tested — but the atomicity claim fails across the fsync/commit window (**H3**) and the classification boundary leaks (**H4**) |
| 14 | Bounded API and Desktop evidence projections | **proven** | Normalizer v3 accepted additively at one model site; evidence projections are closed allowlists asserted against injection of private fields; `_redact_private_session_authority` strips obligations, authorities, session IDs, and fingerprints on every read path; Desktop changes are additive types and generic rendering with no parser or execution authority |
| 15 | Generated contracts, operator docs, and installed flows | **proven** | The contract is generated wholly from in-code authorities (no data-file packaging gap); the wheel flow is exercised from a clean temporary `HERMES_HOME`; schema startup is read-only before recovery with dedicated regressions; every published number matches runtime; Phase 4–6 fields are explicitly deferred in both prose and the machine contract |
| 16 | Regression, integration, and customization convergence | **contradicted** | Legacy preservation, Phase 4–6 non-activation, and narrow-waist/prompt-cache preservation are all established, and generic-seam changes are no-op-unless-callback — but ledger coverage is contradicted — **H5** |

Sub-verdicts for the cross-cutting invariants: **legacy preservation — proven**; **Phase 4–6
non-activation — proven** (`loop.command`, `loop_group`, `include`, `signal_completes` appear nowhere
in production code; Archon unknown-field admission is strict; `maxBudgetUsd`/`sandbox` remain
blocking); **narrow waist / prompt caching — proven** (no core model tool, no live system-prompt
mutation, no historical-message rewrite, no synthetic user turn, no global toolset swap);
**ledger coverage — contradicted** (H5).

## 5. Verification ledger

| # | Command / action | Commit | Result | Source |
|---:|---|---|---|---|
| 1 | `git cat-file -e` on all four commits; `git merge-base --is-ancestor` for each adjacent pair | all | all exist; ancestry confirmed | execution |
| 2 | `git show -s --format='%H%n%T%n%P%n%s' 8a1fe704…` | `8a1fe704` | tree `94f4fd45…`, parent `e5642598…`, subject `fix(workflow): keep schema startup read-only` | execution |
| 3 | `git log --oneline cffc23ce..8a1fe704 \| wc -l` | range | 132 commits — matches prompt | execution |
| 4 | `git diff --stat cffc23ce..8a1fe704` | range | 221 files, +61,199 / −967 — matches prompt | execution |
| 5 | `git diff --check cffc23ce..8a1fe704` | range | no whitespace defects | execution |
| 6 | `git diff --name-status` classification into production / test / SDD evidence | range | 45 production, 118 SDD, remainder tests | execution |
| 7 | `scripts/run_tests.sh` over the prompt's retained 8-file Phase 3 allowlist | `8a1fe704` | **8 files, 1,559 passed, 0 failed** (28.5 s) | execution |
| 8 | Repro A — v3 approval `on_reject` pause→reject→resume through the real scheduler/store | `8a1fe704` | pause charges `retry_consumed=1`; resume yields `retry_budget_exhausted` and a reconciliation pause — **H1 confirmed** | execution |
| 9 | Repro B — v3 consumer, real failed attempt, retry wake, then durable-wait deferral | `8a1fe704` | both `defer_output_resolution` and `transition_v3_reference_node` raise `RuntimeError` — **H2 confirmed** | execution |
| 10 | Arithmetic-builtin classifier/renderer probe against real `/bin/sh`, benign `touch` marker | `8a1fe704` | 5/5 forms admitted and **executed** on the inline path; the two design-rejected controls correctly rejected — **C1 confirmed** | execution |
| 11 | Inline aggregate measurement — 8 × `BASH_INLINE_MAX_BYTES` references | `8a1fe704` | `spill_count=0`, `rendered_size_bytes=262,156` (> Linux 131,072) — **H6 confirmed** | execution |
| 12 | Differential classifier/renderer oracle — 15,120 executed cases against real `/bin/sh` | `8a1fe704` | zero divergences, zero payload executions outside the C1 class | execution |
| 13 | `ManagedProcessTree` descriptor-inheritance matrix (rejections, fd-leak, spawn failure, process group) | `8a1fe704` | all clauses hold; no fd leak across 20 spawns | execution |
| 14 | Ledger coverage grep across `docs/upstream-customizations/*.yaml` for the five transport-seam files | `8a1fe704` | no entry; only an unrelated `browser-profiles.yaml` match — **H5 confirmed** | execution |
| 15 | Faithful replication of the checker's `validate_diff_coverage` algorithm over the range | `8a1fe704` | fails with the five missing paths | execution (replicated algorithm) |
| 16 | Gate/harness inspection: `scripts/test_workflow_merge_gate.sh`, `tests/scripts/test_workflow_merge_gate.py` | `8a1fe704` | lexer-security and persistent-session-recovery suites prohibited from gate selection while the ledger still claims the former | inspection |
| 17 | Full reads of the umbrella, Phase 2, and Phase 3 designs; the approved plan; the progress ledger and Task 16 report at `9aa8d832` | — | completion claims recorded as leads, not proof | inspection |

Worktree state after all work: clean (no tracked file modified; the only untracked artifacts are an
ignored durations cache and a venv symlink inside the disposable review worktree).

## 6. Unverified platform and dependency paths

- **Linux `E2BIG` threshold (H6).** The 262,156-byte single-argument rendering was measured on macOS;
  the `MAX_ARG_STRLEN = 131,072` limit it exceeds is the documented Linux kernel constant and was not
  executed on Linux. The missing aggregate bound and the untyped `executor_crash` mapping are both
  confirmed by code and measurement independently of the platform.
- **Spill-path execution for C1.** The inline path was confirmed by execution. The specialist trace
  reported the same execution on the >32,768-byte spill path; this reviewer's harness instead raised
  `bash_spill_integrity` in that configuration, an unresolved environmental difference. C1 does not
  depend on the spill claim, and the spill claim is not asserted here.
- **Native Windows.** Descriptor inheritance is fail-closed by inspection of
  `tools/managed_process.py:875-877` and `bash_rendering.py:1757-1761`; not executed on Windows.
- **Desktop suites.** Desktop typecheck, vitest, ESLint, and Prettier were not run; Desktop findings
  are inspection-only, and none rose to CRITICAL/HIGH.
- **The real `check_upstream_customizations.py` in `--diff` mode.** Direct invocation aborts on pinned
  Node parser dependencies in this environment, so the coverage failure was established by replicating
  the checker's algorithm rather than by running it. The absence of ledger entries (the underlying
  fact) was confirmed directly by grep.
- **Crash-window reproduction for H3.** Establishing it deterministically requires killing the process
  between an fsync and a commit; the finding rests on an interleaving proof over the shipped ordering
  plus the repository's own tests, which construct the exact durable state and assert the permanent
  failure.
- **Live merge behavior for H5.** No merge was run, no ref advanced, and no brand branch propagated,
  per the prompt's constraints.
