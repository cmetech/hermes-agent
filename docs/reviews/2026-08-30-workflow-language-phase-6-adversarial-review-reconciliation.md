# Workflow Language Phase 6 adversarial-review reconciliation

**Candidate reviewed:** `d850707a25d0eb161d3bedd2db935d01f3573255`

**Merge base:** `1001a6705563a2f2a001b4ad8a608a2d12a6ad33`

**Independent lanes:** Claude Opus and Codex GPT-5.6, each in a separate clean
detached worktree with no inherited implementation context or access to the
other lane's report.

**Initial combined verdict:** `BLOCK` pending controller validation and
remediation.

## Source reports

- `2026-08-30-workflow-language-phase-6-adversarial-review-claude.md`
- `2026-08-30-workflow-language-phase-6-adversarial-review-codex.md`

Both lanes verified the same immutable scope and clean detached state. Claude
reported five Important functional defects; Codex reported six. Three defect
classes converged independently, and five were unique to one lane.

## Consolidated findings

| ID | Source finding(s) | Initial disposition | Consolidated defect |
|---|---|---|---|
| AR-01 | Codex `WL6-FC-001` | **Confirmed Important** | Group-level `until_bash` is not admitted/rendered through the promised v6 current/previous/outer scoped-reference contract. |
| AR-02 | Codex `WL6-FC-002` | **Confirmed Important** | Malformed `$LOOP_PREV` references can be admitted and partially substituted as a valid prefix plus trailing text. |
| AR-03 | Codex `WL6-FC-003` | **Confirmed Important** | A body Script under a group with an outer dependency receives a runtime dependency set that disagrees with authenticated predecessor evidence and fails before launch. |
| AR-04 | Claude `P6-01`; Codex `WL6-FC-004` | **Confirmed Important; independently converged** | `resume_run`/`retry_run` reset only the outer group, leaving controller/body state unclaimable and the run permanently `running`. |
| AR-05 | Claude `P6-02`; Codex `WL6-FC-005` | **Confirmed Important; independently converged** | Predicate recovery reuses the fixed `decision/attempt` directory and raises `FileExistsError` after a post-execution/pre-CAS crash. |
| AR-06 | Claude `P6-03` | **Confirmed Important** | Resume/terminal live-executor guards inspect only top-level nodes and can miss a live or uncertain body child; abandon can drop its retained worker claim. |
| AR-07 | Claude `P6-04`; Codex `WL6-FC-006`; prior final re-review finding 2 | **Confirmed Important; independently converged** | `artifacts:false` treats same-node crash/failed-attempt residue as a later attempt's baseline, allowing success while the forbidden artifact survives. |
| AR-08 | Claude `P6-05` | **Confirmed Important** | A top-level `artifacts:false` node snapshots the shared run artifact tree and can fail on an unrelated concurrent sibling/body publication. |

## Reconciliation rules

1. A finding is confirmed only after the controller traces the final production
   path and reproduces the wrong result with benign synthetic state or a closed
   deterministic state-transition proof.
2. Overlap between lanes increases confidence but does not replace validation.
3. Unique findings receive the same proof standard as converged findings.
4. Remediation fixes the shared root cause with the smallest existing mechanism,
   not one symptom per call site.
5. Every confirmed finding gets a RED regression before production changes,
   then focused GREEN, historical compatibility, and a fresh scoped re-review.

## Controller validation ledger

All eight findings were validated against the exact candidate before any code
changed.

| ID | Controller evidence | Result |
|---|---|---|
| AR-01 | Compiled a group predicate reading `$sink.output`; admission returned `output_reference_not_declared_dependency`. A `$LOOP_PREV.sink.output` predicate reached its executor with profile `hermes-legacy`, normalizer `2`, and no dependencies. | Confirmed. |
| AR-02 | Compiled `$LOOP_PREV.producer.outputx`, then rendered it with authenticated previous output `done`. | Admitted and rendered `donex`; confirmed partial-prefix substitution. |
| AR-03 | Ran a real scoped Script under a group depending on outer node `outer`. | Failed before launch with `validation: predecessor outputs are not the direct dependencies`. |
| AR-04 | Replayed failed and lease-expired nested runs through public `resume_run`, then advanced three times. | Run stayed `running`; parent was `ready`, controller/body stale, no executor calls; second admission queued. |
| AR-05 | Executed a real group predicate, faulted after executor return and before decision recording, expired the predicate lease, and advanced four times. | Every pass raised `FileExistsError` on the fixed predicate attempt directory; run/controller remained running. |
| AR-06 | Built a restarted run with one live body process and retained claim. Tested resume and abandon on separate fresh scenarios. | Resume proceeded instead of refusing; abandon terminalized and deleted the retained claim. |
| AR-07 | Executed the same idempotent `artifacts:false` Bash command twice against one publication root. | Attempt 1 failed `artifact_limit`; attempt 2 succeeded with the forbidden file still present. |
| AR-08 | Ran an `artifacts:false` Bash node while a concurrent thread created only a loop-group child publication directory. | Node failed `artifact_limit`; the no-concurrency control succeeded. |

Validation used the shared project Python with the feature worktree first on
`PYTHONPATH`, synthetic temporary `HERMES_HOME`, no network, and no live
connector/provider calls. The durable-state probes exited normally except the
combined guard probe's intentionally invalid second action; resume and abandon
were then rerun independently and both reproduced.

## Remediation ledger

Planned as three sequential root-cause batches:

1. scoped language and executor coherence: AR-01 through AR-03;
2. nested recovery and predicate attempt identity: AR-04 through AR-06; and
3. attempt-owned artifact accounting: AR-07 and AR-08.

Each batch requires RED regressions, the smallest shared production fix,
focused/historical GREEN gates, an atomic commit, and a fresh scoped review.

| Batch | Findings | Commits | Verification | Scoped review |
|---|---|---|---|---|
| 1 | AR-01–AR-03 | `e5599595e7`, `6eceb1a43d` | 15 focused, 234 Phase 6, and 191 historical tests passed; Ruff/diff clean. | Approved all three findings after one fix/re-review round; no new Critical/Important findings. |
| 2 | AR-04–AR-06 | `ef809667ad`, `4c82a4392d`, `ba43ae89c3`, `226017277f`, `1c2c3b2382`, `0d69eb4c04` | 20 focused, 220 Phase 6 recovery, and 231 historical tests passed; Ruff/diff clean. | Approved all three findings after the bounded fix/re-review loop; v1-v5 preserved; no new Critical/Important findings. |
| 3 | AR-07–AR-08 | `246304d4be`, `3752834118`, `41ea2ecb8c` | 36 adversarial, 115 Phase 6, and 124 historical tests passed; Ruff/diff clean. | Approved both findings after two supported-path escape fixes; publishing paths and v1-v5 preserved; no new Critical/Important findings. |

## Final disposition

All eight initially controller-confirmed findings are remediated and
independently re-reviewed. Every scoped remediation review approved its batch
with no remaining Critical or Important finding.

The subsequent fresh whole-branch review at `5d7a59eed9` approved AR-01 through
AR-08 but found one independent Important contract defect:

| ID | Source | Controller disposition | Remediation |
|---|---|---|---|
| AR-09 | Final whole-branch review | **Confirmed Important** | The Jira manifest node authored `retry.max_attempts: 1`, which Archon v3-v6 seals as one retry and two total workflow attempts despite the binding single-read/single-workflow-attempt contract. Commit `70caca680f` adds the smallest v6-only command/prompt zero-retry boundary, seals the manifest fetch to one total attempt, refreshes the existing package digest, and preserves v1-v5 plus Bash/Script retry semantics. |

The AR-09 RED scheduler regression executed the real admitted workflow twice and
observed two executor calls before the fix. GREEN asserts
`requested_retries == 0`, `requested_total_attempts == 1`, and
`effective_total_attempts == 1`, with only one executor call after an eligible
failure. The separate scoped re-review approved the finding as addressed with
no new Critical, Important, or Minor issue.

Final exact-code verification at `70caca680f` passed 305 Phase 6 tests; the
bounded merge gate passed 4,353 Python tests with 8 platform skips, 3 installed
distribution tests, and 211 Desktop tests, sealing
`TESTED_BASE_SHA=70caca680f9d23ead1e132707f151ed46374187d`.
The canonical repository gate retained the same 145 failures across the same 36
files as the prior candidate. Its only Phase 6-overlapping file,
`test_phase6_interactions_recovery.py`, passed 27/27 standalone and 27/27 in the
bounded four-worker gate. No remaining Critical, Important, or Minor review
finding is open for this feature.
