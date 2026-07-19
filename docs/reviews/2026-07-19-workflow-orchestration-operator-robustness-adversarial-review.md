# Operator-Robustness Batch Adversarial Review

**Date:** 2026-07-19
**Branch:** `feat/workflow-production-remediation` @ `b0f81eb48` (code commits
`f293f68d1` fixes, `7684c7733` tests; docs `b0f81eb48`)
**Scope:** the nine operator-robustness fixes (NF-L2, NF-L13/NF2-L5, adoption
notice, NF-L10, NF-L9, NF-L8, NF-L6, NF-L7, NF-L11) and four invariant test
additions (NF-L1, NF-L3, NF2-L1, NF2-L6), per the approved design/plan
(`679599db1`/`6672e6796`).
**Method:** four independent adversarial reviewers (notifications/pruning;
store/lease/admission; API/CLI/reload; gate + evidence audit), each holding
the implementation to the approved design AND the agreed engineering notes.
~620 targeted tests executed plus a full merge-gate reproduction. The two
severity-decisive new findings were re-verified line-by-line by the
coordinating reviewer.

## Verdict

**NOT READY — the thirteen batch items are genuinely closed, but the rework
introduced one new High and one new Medium in its failure branches, plus one
Medium evidence defect. All three are small, contained fixes.**

The happy paths are excellent: every fix implements the approved design (in
two places more strongly than specified), every agreed engineering note was
honored and test-pinned, the merge gate reproduces exactly (667 passed /
1 skipped; +15 reconciles test-by-test with zero gap), the v13 migration
follows the established shadow-table discipline and is proven against the
hash-pinned v2.0.9 fixture and the installed wheel, and the production delta
is scope-clean (no workflow imports in host files, no new env tokens, prune
and auth chains byte-verified additive).

The defects are all in what happens when the new code itself fails — the
exception contracts of the rewritten repair scanner.

## Batch-item closure

| Item | Verdict | Key evidence |
|---|---|---|
| NF-L9 repair guard | CLOSED | Processes the oversized first row (bounded single overrun); over-quota runs get a durable per-run repair event; test asserts *repair*, not cursor movement; wrap intact |
| NF-L8 cleanup reconciliation | CLOSED | Preview AND execute reconcile each candidate's journal first; four damage modes fail closed; crash-gap fixture survives `older_than=0`; token digest re-verification holds |
| NF-L11 pruning | CLOSED | `sending`/`retryable_failure` receipts never pruned; unexpired/referenced routes structurally unprunable; both constraints negative-test-pinned; bounded single transaction |
| NF-L13/NF2-L5 queue-at-capacity | CLOSED | Third disjunct queues `queue`-policy starts with durable sequence + coordinator wake; `allow`/`forbid` byte-unchanged; `queued_capacity` bound; lane-skip coexistence parametrized |
| NF-L6 foreground lease clock | CLOSED | Schema v13; fixture proves columns + run-twice + wheel; legacy NULL → wall-clock fallback once (no mass expiry); corroboration wired at all five decision sites; both clock-step directions tested |
| Adoption notice | CLOSED | Prints once with the exact status command; JSON envelope carries `execution_mode` + `execution_handoff.transition` (machine-readable, test-pinned) |
| NF-L10 CLI envelope/OSError | CLOSED | Top-level `--json` pre-scan; all argv permutations emit one stdout envelope (verified live); OSError message replaced with a constant + exception type — *stronger* than the sanitize design |
| NF-L7 reload 409 + restore | CLOSED | All seven mutation endpoints typed-409; leaf-level conditional rollback cannot clobber the winner; real-race test |
| NF-L2 attention pagination | CLOSED | Newest-first total-order keyset, HMAC scope-bound, pinned `observed_at`, 105-tied-item traversal test; Desktop compatible (un-paginated page 1 now shows the newest — the actual bug fixed) |
| NF-L1 Windows containment tests | **PARTIALLY CLOSED** | Simulation tests real and sensitivity-pinned; native test genuinely cannot silently skip — but it executes NOWHERE in CI (see NR-3) |
| NF-L3 idempotency race test | CLOSED | Genuine barrier-released spawn-process race; moderate (probabilistic) detection power — honest |
| NF2-L1 drainer race test | CLOSED | Real cross-connection interleave forcing the worst-case window; 5/5 independent reruns |
| NF2-L6 gate self-enforcement | CLOSED | +1 gate line + self-referential assertion; durable via the full-suite job (circular within the gate itself — noted) |

## New findings

### NR-1 (High) — a torn journal tail can now cause a store-global admission outage

Chain, verified line-by-line: the rewritten repair scanner reads journals
strictly — `_read_journal_events(directory, recover_torn_tail=False,
journal_data=data)` (`notifications.py:439-442`), and torn-tail self-healing
is disabled whenever `journal_data is not None` (`store.py` recovery guard) →
a torn tail raises `JournalRecoveryError` → caught as
`NotificationReconciliationError` → `reconcile_journal` calls
`_mark_repair_required(reason, run_id=run_id)` (`notifications.py:613`) —
which, despite taking a `run_id`, writes the **store-global**
`.repair-required.json` (`store.py:1094-1123`) → `storage_health()` goes
unhealthy → **every new run admission is rejected**
(`store.py:2496-2498`, `storage_repair_required`) and cleanup is blocked,
until an operator manually runs `repair_storage()` (nothing clears the
marker automatically).

Sequencing makes it realistic: after a crash mid-append (power loss,
force-quit — the exact event torn-tail recovery was built to absorb), the
recovering coordinator's **first** sweep runs notification repair
(`_notification_repair_due_at` initialized to `0.0`;
`_repair_notifications_if_due` is the first call in `_sweep_once`,
`coordinator.py:285`) — before the scheduler's normal recovering read would
have silently healed the tail, and startup claim reconciliation reads only
`run.json`, never the journal. Whether the strict scanner or a healing
reader reaches the damaged run first is a race; when the scanner wins,
routine crash noise becomes a persistent, operator-gated outage of the
feature's primary function.

The approved design asked for fail-closed **per-run** treatment ("a durable
decision fact naming the run"); the per-run `repair_events` row satisfies
that — the global marker is blast radius beyond the design. **Fix (small):**
in `reconcile_journal`'s per-run failure branch, record the run-scoped
repair event and surface per-run attention WITHOUT flipping the global
marker — or attempt a normal recovering read before declaring the journal
unverifiable (recovery is the established, already-trusted behavior for
exactly this artifact). Reserve the global marker for store-level damage
(index/generation), as before this batch.

### NR-2 (Medium) — run-lock contention now crashes the leader sweep

`_journal_candidates` explicitly re-raises `WorkflowLockTimeout`
(`notifications.py:447-448`; the old code's blanket `OSError` catch
swallowed it since `TimeoutError ⊂ OSError`), and `reconcile_journal`'s row
loop catches only `_NotificationRepairPageFull` and
`NotificationReconciliationError` — so the timeout propagates through
`_repair_notifications_if_due` → `_sweep_once` → the unguarded
`future.result()` → the leader loop's blanket handler marks the coordinator
unhealthy (misleading code `coordinator_store_error`) and forces
re-election. Every ~300 s the scan visits up to 20 runs including active
ones whose executors hold the run lock while appending; any >50 ms hold at
the wrong moment aborts the whole sweep (also skipping that pass's gateway
drain and scheduling) and churns leadership. **Fix (small):** catch
`WorkflowLockTimeout` in the row loop and skip WITHOUT advancing the cursor
(retry next cadence) — contention is normal, not damage.

### NR-3 (Medium) — the native Windows containment test executes nowhere, and the verification doc says otherwise

The verification doc states the native Windows reparse test "is selected by
the repository's native workflow matrix." It is not:
`tests/plugins/workflow/test_evidence_api.py` appears in no CI workflow file
— the `workflow-portability` matrix (the only `windows-latest` job) selects
15 files not including it, and the full-suite job runs `ubuntu-latest` only,
where the `os.name == "nt"` test platform-skips. The carefully built
cannot-silently-skip guarantee (junction fallback + `pytest.fail` on
creation failure) is currently moot: a future regression in real junction
detection keeps every CI lane green. **Fix (two lines + one doc
correction):** add `test_evidence_api.py` to the portability matrix
selection and to the matrix meta-test's pinned list; correct the
verification doc sentence.

### Lower-severity findings (backlog additions)

- **NR-4 (Low)** — the foreground renewal heartbeat became heavyweight: from
  one lock-free UPDATE to run-lock + `run.json` rewrite + journal event +
  full-journal SHA-256 per renewal (quadratic over a long run), and each
  renewal bumps `state_version`, raising spurious CAS conflicts for
  concurrent interaction responders. Correctness intact; hot-path cost.
- **NR-5 (Low)** — attention traversal can silently terminate if a
  200-candidate window yields zero items (`next_cursor` requires a non-empty
  page); improbable but "no silent truncation" is not absolute.
- **NR-6 (Low)** — `attention_candidates` lease filtering is wall-clock only
  (not clock-corroborated), delaying "needs attention" visibility for a dead
  foreground owner after a backward clock step; the authoritative adoption
  path is unaffected.
- **NR-7 (Low)** — the five plugin-lifecycle endpoints (install/enable/
  disable/update/remove) return the typed 409 but perform no config restore
  — saved-but-unapplied until a successful retry; the design's restore
  sentence is implemented only at the two provider endpoints. Related: the
  rollback's read-modify-write is not serialized against unlocked endpoints'
  config writes (tiny clobber window).
- **NR-8 (Low)** — `prune_expired`'s only production trigger is capability
  minting: an idle gateway never prunes (self-limiting; a sweep hook would
  make retention unconditional). `retryable_failure` receipts on expired
  routes are retained forever (correct for `sending`; debatable here).
- **NR-9 (Info)** — cosmetic/coverage: top-level parse-error envelope says
  `"command": "workflow workflow"`; `--json bogus` / bare `--json`
  permutations verified manually but untested; no end-to-end promotion test
  for a capacity-queued (`blocked_by=NULL`) run; no attention byte-tamper or
  mid-traversal-arrival test; endpoint-level reload-lock fast-409 untested;
  the 4 pre-existing messaging-catalog test failures in the full local run
  are the documented OTTO channel-allowlist environment mismatch, untouched
  by this batch.

## Evidence-audit outcome

Gate reproduces exactly (667/1, installed-dist 1, Desktop 17, tsc pass; +15
reconciled with zero gap). Gate script +1 line only, no relaxations. The
five consciously deferred Lows (NF-L4, NF2-L2, NF-L5, NF-L12, NF2-L3) are
recorded accurately, plus the deliver-only-facade half of NF-L11. The
reviewer addendum was preserved untouched and uncommitted. The single
truthfulness defect is the NR-3 CI claim. TDD sensitivity claims are backed
by standing tests (the reparse-attribute and identity-swap tests would fail
if the detectors were weakened).

## Required actions before merge

1. **Fix NR-1** — per-run containment of reconciliation failures (no global
   marker from the notification scanner), or recovering-read-first. With a
   red test: crash-torn tail → repair sweep → new admissions still accepted,
   damaged run surfaced per-run.
2. **Fix NR-2** — skip-without-advance on `WorkflowLockTimeout`, with a red
   test: contended run lock during repair → sweep completes, leadership
   stable, run retried next cadence.
3. **Fix NR-3** — add `test_evidence_api.py` to the portability matrix + its
   meta-test; correct the verification doc.
4. NR-4..NR-8 join the deferred-Lows backlog (NR-4 is the one I'd prioritize
   soon after merge — heartbeat cost grows with run length, and long runs
   are exactly the workhorse case).

NR-1 and NR-2 live in the same failure branch of the same function and are
naturally one small fix commit plus two red tests; NR-3 is two lines plus a
doc sentence. After those land and the gate is re-run, this batch meets the
no-Critical/High bar.

## Addendum — NR-1/NR-2/NR-3 fix review (reviewed @ `e3d92184a`)

The fixes (`544602aaa` containment + NR-2, `a3ca14b4b` NR-3 wiring,
`9c4855a06`/docs) were adversarially reviewed by two independent reviewers
plus targeted reproduction. **All three findings are CLOSED.**

- **NR-1 CLOSED.** All six approved sites converted to durable run-scoped
  `repair_events` transitions (no schema change; latest-transition-wins;
  invisible to `storage_health()`); the global marker file has exactly one
  writer and none of the six sites reaches it. The standing invariant test
  (`test_retention.py::test_run_read_damage_is_contained_while_unrelated_cleanup_and_admission_work`)
  is exemplary: real mid-file byte corruption, every approved surface
  including unrelated cleanup EXECUTE and the repair sweep, health asserted
  after each step, no-re-read proven with a corroboration counter
  (`damaged_reads == 1` across two subsequent admissions), and self-clearing
  proven end-to-end. Typed 409 `run_evidence_uncorroborated` on mutation;
  attention surfaces the damage; quarantine unchanged; store-scoped sites
  byte-untouched. The classification table (condition f) is complete —
  independently re-derived and matched.
- **NR-2 CLOSED.** `WorkflowLockTimeout` caught in the row loop with
  break-without-advance (earlier rows in the page still advance the cursor);
  the only timeout source in the call graph is the 0.05 s journal lock;
  same-sweep gateway drain and scheduling asserted under a genuinely held
  lock; next-cadence retry proven; bounded per-run warning at 3 and every
  10 consecutive timeouts.
- **NR-3 CLOSED.** Exactly +1 line in the ci.yml matrix and +1 in the pinned
  meta-test tuple; platform arithmetic reconciles (7+1 skip POSIX / 8+0
  Windows, measured); the verification record now states coverage
  truthfully, including the honest disclaimer that the symlink tests have
  not yet executed on a remote Windows runner.

Gate reproduced at HEAD: 667/1, installed-dist 1, Desktop 17, tsc 0 — exact.
No dilution (the 5 changed assertions in `test_fault_injection.py` moved
global→run-scoped expectations while retaining all fail-closed coverage);
host files byte-untouched; no new env tokens.

### Remaining findings from this pass (both Medium, both small)

- **NR2-F1 (Medium) — classification dispute, seventh site.**
  `node_effect_classification` (`store.py:4880`,
  `legacy_effect_policy_uncorroborated`) still flips the GLOBAL marker for a
  single legacy run's corrupt/mismatched `policy.yaml` — reachable from the
  scheduler during advance. The design table classifies it "Store" with a
  documented rationale (replay-safety policy unreconstructable); this review
  contests that: the damage is per-run and the consequence (all admissions
  blocked until manual repair) is the exact NR-1 outage class. Fix (convert
  to run-scoped, one site, the pattern now exists) or record explicit
  maintainer acceptance of the rationale. Related Lows: the global
  `_sync_loaded_integrity` site reuses the run-scoped reason string
  `run_evidence_uncorroborated` (forensically ambiguous — rename one);
  `repair_storage()` no-ops on run-scoped state (self-clearing covers it,
  but the documented remediation command should either clear it or say so).
- **NR2-F2 (Medium) — third recurrence of the green-gate membership gap.**
  `test_notifications.py` (the ONLY test of NR-2's cursor-retention/warn/
  retry semantics) and `test_desktop_api.py` (the NR-1 attention-surface and
  typed-409 regressions) are enforced by NO green gate — not the local merge
  gate, not the portability matrix; only the ubuntu-only repo-wide suite.
  A regression flipping break-without-advance to advance-past would keep
  every named gate green while silently skipping a contended run's repair
  forever. Fix: add both files to the ci.yml matrix + pinned meta-test tuple
  (the exact NR-3 pattern). This class has now bitten three times (NF-M5,
  NR-3, this) — after this addition, consider a structural rule in the
  meta-test: every `tests/plugins/workflow/test_*.py` file must appear in
  the merge gate or the matrix, so membership is opt-out rather than
  opt-in.

### Disposition

No Critical or High remains anywhere on the branch. With NR2-F1
(fix-or-accept) and NR2-F2 (two lines + meta-test) resolved, this branch —
including the full remediation, the follow-up fixes, the operator-robustness
batch, and these containment fixes — meets the no-Critical/High merge bar
with all review threads closed.

## Final addendum — NR2-F1 and NR2-F2 closure (reviewed @ `0198d649b`)

Reviewed directly (commits `6d213e421` legacy-policy containment,
`7f56a439b` structural gate membership, plus docs). **Both Mediums CLOSED.**

- **NR2-F1 CLOSED.** `legacy_effect_policy_uncorroborated` joined
  `_RUN_SCOPED_REPAIR_REASONS`, and the reason set was generalized into the
  single source driving every SQL/attention/status surface (removing the
  string duplication). The site records the run-scoped transition and still
  **raises** — the damaged run fails closed before any claim — with
  `repair_verified` self-clear on successful corroboration. Background-path
  trace verified: the raise lands on the scheduler's submission-pool thread
  (unobserved future), so it cannot crash the sweep or churn leadership; the
  run stays operator-visible via both the repair-reason attention clause and
  the runnable-stall detector. The test
  (`test_schema_migrations.py::test_legacy_policy_damage_is_run_scoped_visible_and_self_clearing`)
  drives the REAL v2.0.9 legacy fixture with a digest-mismatched
  `policy.yaml` and asserts: raise, healthy `storage_health()`, no marker
  file, run-scoped reason, attention visibility, unrelated admission
  succeeds, self-clear after byte restore. `repair_storage()`'s unresolved
  set retains the reason for pre-fix global markers (correct backward
  compat).
- **NR2-F2 CLOSED.** Both files added to the 3-OS matrix (+2 in ci.yml) and
  the pinned meta-test tuple — and the structural rule landed:
  `test_every_workflow_test_is_selected_or_explicitly_opted_out` inventories
  `tests/plugins/workflow/test_*.py` and requires each file to be in the
  gate, the matrix, or the explicit `WORKFLOW_GATE_OPTOUTS` dict (40 files,
  individually listed). Membership is now opt-out; a new test file that
  lands in no green gate fails the merge gate itself.

Gate reproduced at `0198d649b`: **668 passed / 1 skipped** (+1 = the new
structural meta-test, which runs in the gate), installed-dist 1, Desktop 17,
tsc 0 — matching claims exactly.

Residual notes (Low/observation, non-blocking):
- The pool-thread `advance` exception is unobserved and unlogged, and the
  wake outcome records "submitted" for a run whose advance then raised —
  diagnostics gap only (the repair reason + stall detector carry the truth).
- The 40 opt-outs share one blanket rationale and include heavyweight
  regression suites (`test_crash_recovery`, `test_approval_races`,
  `test_operator_e2e`, `test_performance_bounds`) whose only runner is the
  ubuntu sliced suite — currently red for unrelated baseline reasons, so
  their regressions could hide in an already-red check. This is now
  DOCUMENTED rather than silent, which is the rule's purpose; recommend
  revisiting the opt-out list once the mainline baseline is green (promote
  at least `crash_recovery` and `approval_races` to the matrix — both are
  platform-relevant).

**FINAL DISPOSITION: no open Critical, High, or Medium anywhere on
`feat/workflow-production-remediation`. Every review thread across the
remediation saga is closed. The branch meets the merge bar; remaining work
is the recorded Lows backlog (post-merge) and the maintainer's merge
decision on PR #3.**
