# Workflow Language Phase 6 functional-correctness review — Codex

Candidate: `d850707a25d0eb161d3bedd2db935d01f3573255`
Merge base: `1001a6705563a2f2a001b4ad8a608a2d12a6ad33`
Review date: 2026-08-30

## 1. Scope verification and starting state

The review ran only in the detached checkout
`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-loop-groups-phase-6-review-codex`.
Before review, and again after all verification, it reported:

- `git status --short --branch`: `## HEAD (no branch)` with no changed paths;
- `git rev-parse HEAD`: `d850707a25d0eb161d3bedd2db935d01f3573255`;
- `git branch --show-current`: empty, confirming detached HEAD;
- `git merge-base base HEAD`: `1001a6705563a2f2a001b4ad8a608a2d12a6ad33`;
- review range: 29 commits and 113 changed paths; and
- `git diff --check 1001a6705563a2f2a001b4ad8a608a2d12a6ad33..d850707a25d0eb161d3bedd2db935d01f3573255`: exit 0 with no output.

All eleven binding sources were read completely before the implementation
assessment. Findings were derived from the final production tree, binding
specifications, unchanged callers, and the tests executed in this review. No
prior report was opened or relied on. No subagent, network service, credential,
live connector, or non-synthetic fixture was used. Production code, tests, Git
state, refs, and generated files were not modified.

## 2. Verdict

**BLOCK.** Six IMPORTANT functional defects violate locked Phase 6 invariants.
Three break scoped language/executor behavior; two prevent durable crash
continuation; one permits `artifacts:false` success with a node-attributable
artifact after reconciliation and retry.

## 3. Findings table

| ID | Severity | Invariant(s) | Primary production location | Wrong result |
|---|---|---:|---|---|
| WL6-FC-001 | IMPORTANT | 6 | `plugins/workflow/schema.py:1791-1841,1885-2003,2039-2042`; `plugins/workflow/scheduler.py:2753-2813` | Group `until_bash` cannot use current-body references and executes previous references under legacy rendering semantics. |
| WL6-FC-002 | IMPORTANT | 2, 6 | `plugins/workflow/schema.py:1879-1882,1927-1959`; `plugins/workflow/resources.py:68-71,1026-1055,1143-1163`; `plugins/workflow/bash_rendering.py:34-37,1521-1529` | A malformed `$LOOP_PREV` token is admitted and its valid prefix is silently substituted. |
| WL6-FC-003 | IMPORTANT | 6, 8 | `plugins/workflow/scheduler.py:2006-2032,5152-5172`; `plugins/workflow/executors/script.py:143-153,328-341` | Any scoped script in a group with an outer dependency fails validation before launch. |
| WL6-FC-004 | IMPORTANT | 9 | `plugins/workflow/store.py:17330-17517,18540-18638`; `plugins/workflow/scheduler.py:2445-2504,2052-2081` | Resuming a run interrupted by a replay-safe child leaves the group permanently running but unclaimable. |
| WL6-FC-005 | IMPORTANT | 9 | `plugins/workflow/scheduler.py:2758-2838`; `plugins/workflow/store.py:12187-12316`; `plugins/workflow/executors/bash.py:59-60` | A crash after predicate execution but before its decision CAS makes predicate recovery reuse a non-reusable attempt directory. |
| WL6-FC-006 | IMPORTANT | 10 | `plugins/workflow/scheduler.py:2241-2272`; `plugins/workflow/executors/base.py:147-205`; `plugins/workflow/executors/bash.py:64-74,350-362`; `plugins/workflow/executors/script.py:354-365,539-550`; `plugins/workflow/store.py:20049-20100` | Reconciliation retry can succeed while retaining a forbidden artifact written by the crashed attempt. |

## 4. Full finding proofs

### WL6-FC-001 — group `until_bash` is outside the v6 scoped-reference contract

1. **ID and severity.** `WL6-FC-001`, IMPORTANT.
2. **Exact production location.** Group predicates are exposed to the generic
   static validator at `plugins/workflow/schema.py:2039-2042`. That validator
   accepts only `node.depends_on` at `schema.py:1791-1841` and runs before the
   v6 body-scope validator at `schema.py:2298-2321` and `2918-2930`. The v6
   validator itself iterates only body children at `schema.py:1885-2003`.
   Runtime predicate construction is at
   `plugins/workflow/scheduler.py:2753-2813`; its execution context omits the
   v6 profile/version and gives the synthetic Bash node only
   `group.depends_on`. `NodeExecutionContext` therefore defaults to legacy/v2
   at `plugins/workflow/executors/base.py:96-97`.
3. **Violated invariant.** Invariant 6. The binding design also explicitly says
   at `docs/superpowers/specs/2026-08-29-workflow-language-phase-6-durable-loop-groups-design.md:443-447`
   that group `until_bash` resolves current body, approved outer, and previous
   values through the same strict renderer.
4. **Trigger and production path.** Author a valid v6 group with body node
   `sink` and `until_bash: 'test "$sink.output" = done'`. Normalization emits
   the group predicate as an interpolated surface. The v3 static validator sees
   `sink` but compares it with the group's outer `depends_on`, so admission
   raises `output_reference_not_declared_dependency`. A
   `$LOOP_PREV.sink.output` predicate takes the other broken path: the v6 body
   validator never validates the group predicate; at runtime the variable
   context has previous outputs, but the predicate `NodeExecutionContext`
   selects the legacy Bash renderer because profile/version were omitted.
5. **Wrong result and consequence.** A current-output completion predicate that
   the Phase 6 language promises is rejected before run creation. A previous
   predicate may be admitted but reaches the shell without v6 scoped
   substitution, so completion is decided from literal/unset-shell text rather
   than the authenticated previous publication. Legitimate groups either
   cannot run or terminate/continue incorrectly.
6. **Evidence/reproduction.** This is a deterministic call-path reproduction:
   `schema._interpolated_node_templates` ->
   `_validate_v3_static_output_references` compares the body ID with outer
   dependencies; runtime independently constructs a legacy/v2 context. The
   scheduler's `all_body` helper at `scheduler.py:2753-2757` proves that the
   intended body result set exists, but it is discarded when the actual Bash
   node is rebuilt at `2777-2788`.
7. **Why tests miss it.** Phase 6 predicate tests use literal commands such as
   `true`, `false`, and `exit 91`
   (`tests/plugins/workflow/test_phase6_interactions_recovery.py:43-218`). The
   current/outer/previous scope test uses a prompt child, not the group
   predicate (`test_phase6_scheduler.py:990-1045`). No test compiles and then
   executes a group predicate containing any of the three promised scopes.
8. **Smallest safe remediation.** Give group predicates one explicit v6
   admission/runtime contract. Validate them against all current body IDs,
   group outer dependencies, and previous body IDs. Construct the predicate
   context with the sealed package profile/version, strict resolver, matching
   direct dependency set, and the normal sealed execution limits. Do not route
   this surface through the legacy top-level dependency validator.
9. **Required regression.** Compile and execute group `until_bash` cases for
   current whole output/field, approved outer output/field, and
   `$LOOP_PREV` on iteration one and later iterations. Assert unknown IDs,
   undeclared outer IDs, impossible fields, and malformed paths fail admission.

### WL6-FC-002 — `$LOOP_PREV` accepts and rewrites invalid token prefixes

1. **ID and severity.** `WL6-FC-002`, IMPORTANT.
2. **Exact production location.** The admission regex at
   `plugins/workflow/schema.py:1879-1882`, runtime regex at
   `plugins/workflow/resources.py:68-71`, and Bash copy at
   `plugins/workflow/bash_rendering.py:34-37` have no closed-token boundary.
   Admission masks every prefix match at `schema.py:1927-1959`; runtime masks
   and later substitutes the same prefix at `resources.py:1026-1055,1143-1163`.
3. **Violated invariants.** Invariants 2 and 6: invalid scope syntax must fail
   admission, and the three reference scopes must have deterministic strict
   semantics on every surface.
4. **Trigger and production path.** Put
   `$LOOP_PREV.producer.outputx` in a body prompt, Bash/script body, approval, or
   rejection text. The custom regex matches only
   `$LOOP_PREV.producer.output`, masks it, and leaves `x`. The ordinary strict
   parser sees no malformed reference in the masked prefix, so admission
   succeeds. Runtime performs the same partial match and substitutes the
   authenticated previous whole output into that span.
5. **Wrong result and consequence.** If the previous output is `done`, the typo
   renders as `donex` instead of being rejected. Similar malformed path
   suffixes can become valid-prefix substitutions plus leftover text. That is
   silent wrong scoped substitution, not merely a diagnostic-quality issue.
6. **Evidence/reproduction.** The ordinary closed grammar explicitly rejects
   an alphanumeric/underscore/path continuation after its matched token at
   `plugins/workflow/language_schema.py:245-266`. The three Phase 6 regexes lack
   that check, and `SubstitutionRenderer._replace` receives a span ending before
   the suffix. The input above therefore has one exact, deterministic parse and
   render result.
7. **Why tests miss it.** Existing tests cover valid previous references,
   unknown producer IDs, missing fields, and first-iteration absence, but not a
   valid-token prefix followed by invalid continuation characters. The regex is
   duplicated, so prompt and Bash coverage do not automatically cover one
   another.
8. **Smallest safe remediation.** Parse previous references through the same
   canonical closed grammar, ideally by rewriting only the `$LOOP_PREV.` scope
   prefix and retaining ordinary-token validation. If regex remains, add the
   exact `_complete_reference_at` boundary and one canonical path grammar in a
   shared helper used by schema, resource rendering, and Bash lexing.
9. **Required regression.** Table-test prompt, Bash, script, approval, and
   rejection surfaces with valid adjacent punctuation and invalid
   `outputx`, repeated/trailing dots, bracket/slash forms, non-ASCII
   continuations, and invalid numeric path segments. Every invalid case must
   fail at admission and none may partially render.

### WL6-FC-003 — outer group dependencies make scoped scripts self-inconsistent

1. **ID and severity.** `WL6-FC-003`, IMPORTANT.
2. **Exact production location.** `_runtime_work_node` appends all outer group
   dependencies to the child node at
   `plugins/workflow/scheduler.py:2006-2032`. Context construction at
   `scheduler.py:5152-5172` nevertheless projects only the body map and passes
   only the original `work_item.node.depends_on` to `_predecessor_results`.
   The v6 Script executor requires exact set equality at
   `plugins/workflow/executors/script.py:143-153,328-341`.
3. **Violated invariants.** Invariants 6 and 8. Outer output is an admitted child
   scope, and reusing the existing Script executor must preserve a coherent
   dependency/evidence contract.
4. **Trigger and production path.** Create outer node `outer`, make the group
   depend on `outer`, and put an inline or named Script node in the body. The
   scheduler dispatches a runtime Script node whose dependencies include
   `outer`. It builds predecessor evidence from the body projection and the
   child's original body-only dependencies, so the evidence key set omits
   `outer`. Script execution sees v6 semantics and calls
   `_predecessor_json_parts`.
5. **Wrong result and consequence.** The executor returns `failed` with
   `error_code="validation"` and message `predecessor outputs are not the direct
   dependencies` before spawning the script. This affects every scoped Script
   under a group with any outer dependency, even if the script never references
   that outer output.
6. **Evidence/reproduction.** For a body script with no body dependencies and a
   group depending on `outer`, `context.node.depends_on == ("outer",)` while
   `context.predecessor_results == {}`. Lines 149-153 of `script.py` make the
   resulting failure unconditional. With body dependencies, the mismatch is
   `{body..., outer}` versus `{body...}` and is equally deterministic.
7. **Why tests miss it.** Real scoped Bash/Script tests create groups without
   outer dependencies (`test_phase6_scheduler.py:534-575`). The outer scope
   behavior test uses prompt nodes (`990-1045`). The shared-context evidence
   test with an outer group dependency also substitutes a recording prompt
   executor and intentionally asserts body-only evidence (`740-790`), so it
   never exercises Script's exact v6 predecessor-file contract.
8. **Smallest safe remediation.** Construct predecessor results over the same
   dependency identity used by the runtime Script node. Merge authenticated
   current-body and approved-outer publications with the same collision rule as
   scoped reference resolution. If Script predecessor input is intentionally
   body-only, keep that as a separate sealed field rather than mutating
   `node.depends_on`; the two sets must not disagree.
9. **Required regression.** Run real inline and named Script executors through
   `RunScheduler` in a group with one outer dependency, then with both body and
   outer dependencies. Assert execution succeeds and the predecessor JSON keys,
   values, and publication evidence exactly match the declared runtime set.

### WL6-FC-004 — replay-safe nested interruption cannot be resumed

1. **ID and severity.** `WL6-FC-004`, IMPORTANT.
2. **Exact production location.** Stale-claim expiry iterates nested children
   and marks the child `interrupted` plus the run `interrupted` at
   `plugins/workflow/store.py:17330-17517`; it does not change the enclosing
   group/controller states. `resume_run` checks and resets only top-level
   `projection["nodes"].values()/items()` at `store.py:18591-18638`. The
   controller initializes only when parent state is `ready` and no controller
   exists, then otherwise requires parent state `running`, at
   `plugins/workflow/scheduler.py:2445-2504`. Ready-work enumeration accepts only
   body children whose state is `ready` at `scheduler.py:2052-2081`.
3. **Violated invariant.** Invariant 9: a normal crash boundary loses required
   durable progress and cannot continue, despite a replay-safe, known-stopped
   child.
4. **Trigger and production path.** A replay-safe body child holds a claim when
   its scheduler host dies. After process termination is known and the lease
   expires, `expire_stale_claims` marks that child `interrupted`, retains the
   parent group/controller as `running`, and marks the run `interrupted`. The
   operator uses the existing resume action. `resume_run` sees only the
   top-level group, changes it to `ready`, leaves its existing controller and
   interrupted child untouched, and marks the run runnable.
5. **Wrong result and consequence.** The resumed run reports `running`, but the
   controller refuses to advance because the parent is not `running` and cannot
   reinitialize because a controller already exists. The interrupted child is
   never listed as ready. No claim can be acquired, no sibling/iteration can
   finish, and repeated resume/advance calls do not change state.
6. **Evidence/reproduction.** This follows a closed state transition:
   `(group=running, child=running, run=running)` -> stale expiry
   `(group=running, controller=running, child=interrupted, run=interrupted)` ->
   resume `(group=ready, controller=running, child=interrupted, run=running)`.
   The two scheduler guards cited above exclude both controller transition and
   work dispatch from that final state. The repository's own stale-child test
   proves the first transition at
   `tests/plugins/workflow/test_phase6_store.py:677-719`.
7. **Why tests miss it.** `test_child_heartbeat_and_stale_expiry_use_existing_claim_lifecycle`
   stops immediately after asserting the interrupted child and released worker
   claim. It never calls `resume_run` and then `advance_all`. Other reconciliation
   tests cover outward/uncertain children, not replay-safe resume composition.
8. **Smallest safe remediation.** Make resume scope-aware. For each active group,
   authenticate the nested recovery record, preserve succeeded/skipped siblings,
   reset only a termination-proven replay-safe interrupted child to `ready`, and
   keep both parent and controller `running`. Reject or reconcile uncertain
   nested recovery. Do not set a parent with an existing controller to `ready`.
9. **Required regression.** Run a real replay-safe body process, stop the host at
   a deterministic barrier, expire its lease after proving process termination,
   call the public resume path, and advance to group/run success. Assert completed
   siblings do not rerun, the recovered child gets one new attempt, and a second
   resume is idempotent.

### WL6-FC-005 — predicate recovery reuses an occupied attempt directory

1. **ID and severity.** `WL6-FC-005`, IMPORTANT.
2. **Exact production location.** The predicate uses the fixed filesystem path
   `publication / "attempt"` at
   `plugins/workflow/scheduler.py:2758-2813`, while `BashExecutor` unconditionally
   creates its attempt directory with `exist_ok=False` at
   `plugins/workflow/executors/bash.py:59-60`. Predicate claim recovery reuses
   the existing `attempt_id` at `plugins/workflow/store.py:12187-12245`; its
   preparation clears process metadata but neither rotates nor removes the
   attempt directory at `store.py:12247-12316`.
3. **Violated invariant.** Invariant 9: the predicate crash window loses the
   required completion result and cannot safely replay.
4. **Trigger and production path.** Let a real group `until_bash` process start,
   create its attempt directory, stop cleanly, and return to the scheduler.
   Crash the scheduler between `BashExecutor.execute` returning and
   `record_loop_group_predicate_decision` at `scheduler.py:2835-2838`. On lease
   turnover the store observes the process stopped, reclaims the same predicate
   attempt, and clears the old spawn/process fields. The controller redispatches
   the same predicate with the same fixed path.
5. **Wrong result and consequence.** The second `BashExecutor.execute` raises
   `FileExistsError` before execution. That exception escapes the direct
   predicate call, the durable decision remains pending, and every later
   recovery reuses the same occupied path. The group cannot complete or
   continue after an otherwise recoverable crash.
6. **Evidence/reproduction.** The crash cut is explicitly between synchronous
   executor return (`scheduler.py:2776-2814`) and the decision CAS
   (`2835-2838`). The first call necessarily executes line 60 of `bash.py`, so
   the directory exists. Recovery returns the original attempt ID at
   `store.py:12238-12245`, and the only cleanup in preparation is removal of
   in-memory spawn/process keys (`12293-12301`). No filesystem branch exists.
7. **Why tests miss it.** The Phase 6 turnover test replaces Bash with
   `CrashBeforePredicateSpawn` and raises before any filesystem or process is
   created (`tests/plugins/workflow/test_crash_recovery.py:3035-3127`). The
   ordinary-loop predicate crash test is a different execution path. No Phase 6
   test crashes after a real predicate executor returns but before decision
   recording.
8. **Smallest safe remediation.** Bind a durable dispatch/recovery generation to
   the predicate filesystem attempt, and allocate a fresh contained attempt
   directory for each authorized replay while retaining the original predicate
   obligation/claim correlation. Do not delete or reuse a possibly meaningful
   nonempty directory blindly.
9. **Required regression.** Execute a real Bash group predicate, fault after
   executor return and before decision CAS, expire/turn over the owner, restart,
   and assert one bounded replay reaches the correct decision with distinct
   attempt directories and no duplicate concurrent process.

### WL6-FC-006 — `artifacts:false` forgets attribution across crash/retry

1. **ID and severity.** `WL6-FC-006`, IMPORTANT.
2. **Exact production location.** Scoped publication directories are stable for
   `(group, iteration, node)` and omit the attempt ID at
   `plugins/workflow/scheduler.py:2241-2272`. Bash and Script compare only a
   before/after snapshot of the current invocation at
   `plugins/workflow/executors/bash.py:64-74,350-362` and
   `plugins/workflow/executors/script.py:354-365,539-550`. The snapshot helper
   intentionally accepts unchanged preexisting regular entries at
   `plugins/workflow/executors/base.py:147-205`. A nested safe-to-retry
   reconciliation sets the child ready without clearing or tainting that
   publication directory at `plugins/workflow/store.py:20049-20100`.
3. **Violated invariant.** Invariant 10, which explicitly covers initial
   execution, crash, retry, resume, and reconciliation.
4. **Trigger and production path.** Run an `artifacts:false` Bash or Script body
   node whose command writes `$ARTIFACTS_DIR/forbidden` only if it does not
   already exist. Crash the scheduler after the child writes the file but before
   the executor's post-snapshot check/result recording. Once process termination
   is proven, use the existing safe-to-retry reconciliation path (for an outward
   or otherwise uncertain attempt). The new attempt gets a new attempt directory
   but the same publication directory. Its command observes the existing file
   and leaves it unchanged.
5. **Wrong result and consequence.** The retry's before and after snapshots are
   identical, so the executor succeeds while `forbidden` remains in the node's
   publication tree. A file attributable solely to that node survives a
   successful `artifacts:false` attempt.
6. **Evidence/reproduction.** The interleaving is finite and requires no race
   assumption beyond the specified crash boundary. Snapshot 1 is empty; the
   first child creates `forbidden`; the host dies before snapshot 2. Reconcile
   sets the child ready. Retry snapshot 1 contains `forbidden`; the conditional
   command does not mutate it; retry snapshot 2 is byte/identity-equal. Lines
   355-362 of `bash.py` (or 541-550 of `script.py`) therefore do not return
   `artifact_limit`.
7. **Why tests miss it.** Direct executor tests cover immediate creation,
   directories, same-content rewrite, and allowing unrelated preexisting
   publications (`tests/plugins/workflow/test_phase6_execution_context.py:350-474`).
   They do not compose a crash-created same-node residue with store recovery or
   reconciliation. Treating all preexisting files as unrelated is exactly the
   missing attribution dimension.
8. **Smallest safe remediation.** Persist a node-scope artifact-free baseline or
   taint before launch and carry it across attempts, or execute into private
   attempt staging and publish only after the artifact-free check. An ambiguous
   artifact-free crash must fail closed until residue is attributed/removed by a
   corroborated recovery action; a later attempt may not redefine it as
   unrelated prior publication.
9. **Required regression.** For both Bash and Script, fault after creating a file
   and before post-snapshot/result recording, prove termination, reconcile
   safe-to-retry, and execute a second command that leaves the file untouched.
   Assert the run cannot succeed while the residue survives. Retain the existing
   test that genuinely unrelated prior group publications remain allowed.

## 5. Locked-invariant matrix

| # | Status | Evidence |
|---:|---|---|
| 1 | PASS | `plugins/workflow/language.py:35-42,205-209` keeps legacy at v2, Archon current at v6, and v1-v6 support. Snapshot-format-2 load/store paths remain at `scheduler.py:561-624` and `store.py:6354-6385`. Phase 3-5 compatibility suite: 408 passed. |
| 2 | **FAIL** | Structural bounds/nesting/resource admission is otherwise sealed in `schema.py:734-1322`, but WL6-FC-002 admits malformed previous-reference syntax. |
| 3 | PASS | `LoopGroupChildScope`, generated UUID attempt IDs, path containment in `scheduler.py:2241-2272`, store scope authentication, and Phase 6 identity/store tests passed. No authored path becomes a worker key. |
| 4 | PASS | Controllers advance synchronously without worker claims; children flow through `_ready_work_items`, existing `claim_loop_group_child`, shared `worker_claims`, and the profile/run capacity checks. Scheduler/fairness suites passed. |
| 5 | PASS | Controller iteration transition and pending-decision CAS keep one active iteration; source-index sort is at `scheduler.py:2082-2091`; sealed primary sink and hard maximum are authenticated. Competing-coordinator and store tests passed. |
| 6 | **FAIL** | WL6-FC-001, WL6-FC-002, and WL6-FC-003 demonstrate rejected, partially substituted, and executor-incoherent scoped references. |
| 7 | PASS | Previous outputs are stored in the controller and resolved only through authenticated prior-iteration artifacts; iteration-one absence and reopened previous-output tests passed. |
| 8 | **FAIL** | WL6-FC-003 gives Script a dependency set that disagrees with its authenticated predecessor evidence. Nested directory isolation itself is contained. |
| 9 | **FAIL** | WL6-FC-004 loses resumability for a stopped replay-safe child; WL6-FC-005 makes the real predicate post-execution crash cut unrecoverable. |
| 10 | **FAIL** | WL6-FC-006 permits a node-attributable artifact to survive reconciliation retry and successful completion. Direct no-follow/symlink/hardlink checks do not close cross-attempt attribution. |
| 11 | PASS | Exact tool-call contract sealing/correlation and the no-repair branch were traced through the AI executor and Jira manifest contract. Required AI/executor/Jira tests passed. |
| 12 | PASS | Scoped outward IDs, exact approval dependencies, effect classification, per-attempt authority, one-shot consumption, and reconciliation are retained. Synthetic Jira tests reject missing/mismatched write evidence. |
| 13 | PASS | Expected Jira outcomes are bounded terminal success values; ambiguous/unknown write schemas fail closed and no write node has blind retry. Jira Phase 6 tests passed. |
| 14 | PASS | Jira reducers use the authenticated predecessor file, validate object identities/evidence, preserve ordered per-ticket records, and generate JSON/Markdown. The package/vendored bytes agree and reducer tests passed. |
| 15 | PASS | Backend projection/event sanitizers expose bounded categorical parent/child summaries and omit private bodies/outputs/paths. Public projection tests passed. |
| 16 | PASS | Store lookup/mutation remains operator/profile scoped; cache/request keys include profile; two-profile backend tests passed. No process-global run board was added. |
| 17 | **UNPROVEN** | Static Desktop code captures request profiles and treats loop-group fields additively, but the four required Desktop tests could not start and typecheck could not complete because checkout dependencies were missing. |
| 18 | PASS | No core model tool, second scheduler, API version, or prompt/message mutation was introduced. Historical scheduler/crash/executor suites (408 + 267 test executions) passed. |
| 19 | PASS | Only Jira Defect Loop uses the migrated v6 group; the other seven assessed flows remain explicit documentation deferrals and contain no accidental v6 syntax. |
| 20 | **UNPROVEN** | Source schema, website/builder references, vendored Jira bytes, runtime v6 metadata, and the 48 vendor tests agree. A built wheel/sdist installed in isolation was not executed in this review, and Desktop dependency failures prevented a complete distributed-client check. |

## 6. Top adversarial reproductions and wrong observable results

1. **Group predicate scope.** A group whose body contains `sink` and whose
   `until_bash` reads `$sink.output` is rejected as though `sink` had to be an
   outer group dependency. Changing it to `$LOOP_PREV.sink.output` can admit the
   workflow but selects the legacy predicate renderer. Observable result:
   admission failure or a shell decision based on unsubstituted text.
2. **Malformed previous token.** Put
   `$LOOP_PREV.producer.outputx` in an admitted child prompt. With previous output
   `done`, the renderer produces `donex`. Observable result: silent substitution
   instead of `output_reference_syntax_invalid`.
3. **Outer dependency plus Script.** Outer node `outer` -> group
   `depends_on: [outer]` -> body Script. Observable result: Script returns
   `validation: predecessor outputs are not the direct dependencies` without
   spawning.
4. **Nested crash/resume.** Expire one known-stopped replay-safe child claim and
   call `resume_run`. Observable state becomes parent `ready`, controller
   `running`, child `interrupted`, run `running`; `advance_all` has no claimable
   item and makes no progress.
5. **Predicate post-execution crash.** Kill the scheduler after real predicate
   Bash returns but before its decision CAS. Observable result on recovery:
   `FileExistsError` at the fixed `decision/attempt` directory, repeated while
   the durable predicate remains pending.
6. **Artifact residue.** Crash an artifact-free child after it creates a file,
   reconcile safe-to-retry, and let the retry leave the file unchanged.
   Observable result: the node/run can succeed with the forbidden file still in
   its scoped publication directory.

## 7. Test-integrity assessment

The required Python suites are substantial and all executable cases passed,
but they are strongest at isolated contracts and ordinary success paths. They
do not compose the exact seams in the six findings:

- group predicate tests use literal Bash and never combine current/outer/
  previous references with predicate admission and real rendering;
- previous-reference tests omit malformed valid-prefix tokens;
- outer-scope tests substitute prompt/recording executors, while real Script
  tests omit group outer dependencies;
- stale-child tests stop at the interrupted projection and never exercise the
  public resume-to-completion path;
- predicate turnover mocks a crash before spawn, so no attempt directory
  exists; and
- artifact-free tests start each invocation with a locally classified
  preexisting tree and never compose crash residue with reconciliation retry.

The 230-test Phase 6 command, 408-test compatibility command, and 267-test
executor command all passed with zero failures/skips in their reported
summaries. The latter two Phase 6 files are intentionally repeated in the
executor command, so 905 is an execution count, not a unique-test count. The
green totals do not contradict the deterministic uncovered paths above.

Desktop evidence is incomplete for environmental reasons: Vitest failed during
configuration loading and TypeScript failed module resolution before a complete
typecheck. Those failures are not attributed to Phase 6 without changed-code
causality, but they prevent a PASS claim for the Desktop invariant.

## 8. Verification ledger

| # | Exact command | Result |
|---:|---|---|
| 1 | `git status --short --branch` | Exit 0: `## HEAD (no branch)`, no changed paths. |
| 2 | `git rev-parse HEAD` | Exit 0: `d850707a25d0eb161d3bedd2db935d01f3573255`. |
| 3 | `git merge-base base HEAD` | Exit 0: `1001a6705563a2f2a001b4ad8a608a2d12a6ad33`. |
| 4 | `git rev-list --count 1001a6705563a2f2a001b4ad8a608a2d12a6ad33..d850707a25d0eb161d3bedd2db935d01f3573255` | Exit 0: 29. |
| 5 | `git diff --name-status 1001a6705563a2f2a001b4ad8a608a2d12a6ad33..d850707a25d0eb161d3bedd2db935d01f3573255` | Exit 0: 113 changed paths inventoried. |
| 6 | `git diff --check 1001a6705563a2f2a001b4ad8a608a2d12a6ad33..d850707a25d0eb161d3bedd2db935d01f3573255` | Exit 0, no output. |
| 7 | `scripts/run_tests.sh tests/plugins/workflow/test_phase6_language.py tests/plugins/workflow/test_phase6_admission.py tests/plugins/workflow/test_phase6_execution_context.py tests/plugins/workflow/test_phase6_store.py tests/plugins/workflow/test_phase6_scheduler.py tests/plugins/workflow/test_phase6_interactions_recovery.py tests/plugins/workflow/test_phase6_public_projection.py tests/plugins/workflow/test_phase6_jira_defect_loop.py -q` | First invocation exit 1: wrapper found no checkout venv containing pytest. No tests ran. |
| 8 | `HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase6_language.py tests/plugins/workflow/test_phase6_admission.py tests/plugins/workflow/test_phase6_execution_context.py tests/plugins/workflow/test_phase6_store.py tests/plugins/workflow/test_phase6_scheduler.py tests/plugins/workflow/test_phase6_interactions_recovery.py tests/plugins/workflow/test_phase6_public_projection.py tests/plugins/workflow/test_phase6_jira_defect_loop.py -q` | Exit 0: 230 passed, 0 failed, 0 skipped reported; 100.8 s. |
| 9 | `HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase3_language.py tests/plugins/workflow/test_phase3_execution_semantics.py tests/plugins/workflow/test_phase4_language.py tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_phase5_language.py tests/plugins/workflow/test_phase5_provider_snapshot.py tests/plugins/workflow/test_phase5_execution_authority_continuity.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_cancel_node.py -q` | Exit 0: 408 passed, 0 failed, 0 skipped reported; 17.8 s. |
| 10 | `HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_loop_executor.py tests/plugins/workflow/test_phase6_execution_context.py tests/plugins/workflow/test_phase6_jira_defect_loop.py -q` | Exit 0: 267 passed, 0 failed, 0 skipped reported; 11.1 s. |
| 11 | `cd apps/desktop && npm test -- --run src/lib/workflow-public-codec.test.ts src/app/workflows/adapter.test.ts src/app/workflows/workflow-run-drawer.test.tsx src/app/workflows/index.test.tsx` | Exit 1 before test execution: Vite could not resolve `@rolldown/plugin-babel` from `vite.config.ts`. |
| 12 | `cd apps/desktop && NODE_PATH=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-loop-groups-phase-6/apps/desktop/node_modules npm test -- --run src/lib/workflow-public-codec.test.ts src/app/workflows/adapter.test.ts src/app/workflows/workflow-run-drawer.test.tsx src/app/workflows/index.test.tsx` | Exit 1 before test execution with the same unresolved `@rolldown/plugin-babel`; not claimed as equivalent success. |
| 13 | `cd apps/desktop && npm run typecheck` | Exit 2: module resolution failed for `blobatar/blob` and `blobatar/react`; typecheck did not complete. |
| 14 | `node --test scripts/__tests__/vendor-ericsson.test.mjs` | Exit 0: 48 passed, 0 failed, 0 skipped; about 2.18 s. |
| 15 | Targeted `rg`, `sed`, and `nl -ba` inspections of the changed schema/language, scheduler/store, resources/Bash renderer, Bash/Script executors, Jira reducer, public projection, Desktop adapter/codec, packaging, and their unchanged callers/tests | Read-only production-path tracing; yielded the six findings above. No repository write. |
| 16 | Final `git status --short --branch; git rev-parse HEAD; git branch --show-current; git merge-base base HEAD; git rev-list --count ...; git diff --name-only ... \| wc -l; git diff --check ...` | Exit 0: detached clean HEAD at candidate; base merge-base; 29 commits; 113 paths; no diff-check output. |

## 9. Unverified platforms and dependencies

- **Desktop Vitest:** UNPROVEN. The required four test files did not execute
  because the checkout's installed dependencies could not resolve
  `@rolldown/plugin-babel`.
- **Desktop TypeScript:** UNPROVEN. Typecheck stopped on missing
  `blobatar/blob` and `blobatar/react` modules.
- **Native Windows:** UNPROVEN. No native Windows process/filesystem run was
  available. POSIX/macOS no-follow behavior was inspected and covered by the
  Python suite; no Windows claim is inferred from it.
- **Built installed wheel/sdist:** UNPROVEN as an executed artifact. Vendored
  Ericsson workflow parity and package tests passed, and source/package bytes
  were inspected, but an isolated installed distribution was not built and run.
- **Live Jira/GitLab/providers:** intentionally not exercised. All Jira and
  connector evidence was synthetic and local, as required.

## 10. Final worktree status

The detached review checkout remains unchanged:

```text
## HEAD (no branch)
HEAD=d850707a25d0eb161d3bedd2db935d01f3573255
branch=<detached>
merge-base=1001a6705563a2f2a001b4ad8a608a2d12a6ad33
commits=29
changed-paths=113
git diff --check: clean
```

The only persistent write from this review is this authorized report in the
separate feature worktree.
