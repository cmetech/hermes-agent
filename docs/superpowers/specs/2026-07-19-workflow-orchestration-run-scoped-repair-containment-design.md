# Workflow Orchestration Run-Scoped Repair Containment Design

**Date:** 2026-07-19
**Status:** Approved
**Branch:** `feat/workflow-production-remediation`

## Purpose

This follow-up closes the three failure-branch regressions found in the operator-robustness adversarial review:

- **NR-1 (High):** run-scoped read damage must not create a store-global admission outage.
- **NR-2 (Medium):** transient run-lock contention during notification repair must not abort the coordinator sweep.
- **NR-3 (Medium):** the native evidence-containment tests must execute in the three-OS release-gate matrix.

The work is deliberately contained to the workflow plugin, its release-gate selection, failure-injection tests, and corrected verification evidence. It adds no core tool, host import, user-facing environment variable, or synchronous workflow execution path.

## Architectural constraints

- Journals, RunStore, the notification outbox, and repair events remain authoritative. Operator surfaces are projections.
- Notification repair keeps strict descriptor-contained journal reads. It does not switch to a recovering read merely to avoid a failure branch.
- Global repair state is reserved for store-level integrity failures and durability failures whose scope cannot be confined to one run.
- A run with uncorroborated evidence remains fail-closed for cleanup and notification reconciliation.
- Damage to one run must not block admission or cleanup of independently corroborated runs.
- A contended run lock does not authorize skipping that run or advancing the repair cursor beyond it.
- Gateway delivery and workflow scheduling remain live in a sweep that encounters transient notification-repair contention.

## Alternatives rejected

### Recover torn tails in the scanner

The notification scanner intentionally reads through the descriptor-contained strict path established by C-02. Making this caller recover or rewrite the journal would mix mutation into a broad background scan and weaken the evidence-containment posture. Normal run readers may retain their existing recovery behavior; the scanner remains strict and contains its failure.

### Catch only the scanner call site

The defect is a scope rule, not a single exception. Two operator read paths added or exercised by the robustness batch also mark the entire store when one run cannot be read. Fixing only `notifications.py` would leave the Desktop board poll and attention inbox able to reproduce the same store-wide admission outage on genuine mid-file corruption.

### Swallow lock timeouts and advance the page

Advancing beyond a contended first row would create a permanent or repeated notification-repair blind spot. The cursor stays before the contended run, and the current cadence ends cleanly so a later sweep retries the same evidence.

## Contract 1: run-scoped readers never set global repair state

The following three call sites are governed by one auditable rule:

1. notification journal repair in `plugins/workflow/notifications.py`;
2. the runs-list/status projection path in `plugins/workflow/store.py`;
3. `RunStore.attention_candidates` in `plugins/workflow/store.py`.

When one published run raises `JournalRecoveryError`, `NotificationReconciliationError`, `OSError`, malformed-data errors, or the equivalent run-local read failure at these sites, the caller must:

- append a run-scoped `repair_events` transition using the stable reason `notification_reconciliation_unverified` or `run_evidence_uncorroborated`;
- degrade only that run's returned or projected result;
- leave `.repair-required.json` unchanged;
- leave `storage_health()` healthy unless an independent store-level failure already exists;
- permit unrelated admission and unrelated cleanup to proceed.

The remaining `_mark_repair_required` call sites are intentionally unchanged. They cover authority/index/generation integrity, admission publication and reconciliation, projection/journal write durability, claim reconciliation, or other state whose safe scope is the store rather than a read projection.

## Contract 2: run-scoped damage is visible and self-clearing

Removing the global marker must not make evidence damage invisible.

Run-scoped repair state uses the existing append-only `repair_events` table without a schema change:

- a latest transition with `outcome='repair_required'` is active;
- a later successful strict scanner read resolves `notification_reconciliation_unverified`, while a later successful status read resolves `run_evidence_uncorroborated`; one reader cannot clear the other reader's reason;
- resolution appends `outcome='repair_verified'` for the same run and reason;
- only the latest transition for a `(run_id, reason_code)` pair determines whether that reason is active;
- duplicate polling while the same reason remains active does not need to append unbounded duplicate transitions.

`attention_candidates` includes runs with an active run-scoped repair reason even if their indexed status would not otherwise be an attention state. An active notification-reconciliation reason is overlaid on an otherwise readable run until the strict scanner verifies it; a normal recovering status read cannot prematurely hide the scanner's warning. The returned degraded projection carries:

- `health: "storage_degraded"`;
- `blocking_reason` equal to the active reason code;
- the same reason in `warnings`;
- the indexed run ID, workflow, status, and update time, marked non-authoritative.

The dashboard attention mapper therefore emits a stalled/degraded attention item whose `cause` names `notification_reconciliation_unverified` or `run_evidence_uncorroborated`. The reason is visible without exposing evidence contents or filesystem paths.

A successful later corroboration resolves only that run-scoped reason. It does not clear, rewrite, or acknowledge any unrelated global repair marker.

## Contract 3: cleanup containment is bidirectional

The damaged run remains ineligible for cleanup. Its preview includes `notification_reconciliation_unverified` and no confirmation token authorizes deletion.

At the same time:

- the absence of a global repair marker means a separately corroborated terminal run may receive an eligible cleanup preview;
- a separately valid new run may be admitted;
- no cleanup execution can use a token whose candidate set or authority binding changed while the damaged run was being assessed.

The failure-injection regression test must assert all three outcomes together: damaged-run preservation, unrelated-run cleanup eligibility, and unrelated-run admission.

## Contract 4: lock contention stops one repair cadence, not the coordinator sweep

`WorkflowLockTimeout` from notification journal repair is a transient per-run condition, distinct from corrupt evidence.

On timeout:

- `reconcile_journal` returns normally for the current cadence;
- the contended row is not appended to `processed_rows`;
- the repair cursor does not advance beyond that row;
- no repair-required event or global marker is written solely for contention;
- candidates durably collected before the timeout may still be recorded;
- the next repair cadence retries the contended run.

The coordinator continues the same `_sweep_once` after repair returns. If a Gateway delivery port is present, the outbox drain still runs; queued or otherwise actionable workflow work is still submitted under the existing owner/epoch fence.

### Contention diagnostics

Repeated contention on the same first run must become diagnosable without adding durable schema state.

- The RunStore instance retains a lock-guarded, bounded, process-local streak consisting of the last contended run ID and its consecutive timeout count.
- A successful repair read of that run, or contention on a different run, resets or re-keys the streak.
- The third consecutive timeout for the same run emits a warning containing the run ID, count, and statement that the cursor was retained.
- Later warnings are rate-limited to bounded milestones rather than logging every cadence.
- No journal contents, route capability, confirmation token, or filesystem path is logged.

The process-local counter is diagnostic only. Correctness depends solely on the durable cursor remaining before the row, so a restart cannot weaken repair safety.

## Contract 5: native evidence tests are part of the release matrix

`tests/plugins/workflow/test_evidence_api.py` is added to the workflow portability selection in `.github/workflows/ci.yml` and to the exact pinned list in `tests/scripts/test_workflow_merge_gate.py`.

The selection grows by exactly this file; it is not replaced by a wildcard and no existing test is removed. At the approved HEAD, the file has one platform marker: the native Windows reparse-point test skips on POSIX, while its descriptor/fallback containment tests are platform-neutral. There are no `mkfifo` or `O_NOFOLLOW` skip-marked tests in this file. Every supported matrix OS executes the applicable containment path, and the verification record must use the file's actual markers rather than assuming inverse skips that do not exist.

The final verification record reports each platform's collected/pass/skip arithmetic and reconciles the inverse platform skips. It must not claim matrix coverage based only on a local POSIX run.

## TDD strategy

The production fixes are implemented only after focused failure-injection tests fail for the intended reason.

### NR-1 red cycle

Tests inject a strict torn tail through the notification scanner and genuine mid-file damage through the status-list and attention readers, then assert:

- an active run-scoped repair event with the exact reason exists;
- no global repair marker exists and `storage_health()` stays healthy;
- the damaged run appears in the attention response with the exact degraded reason;
- its cleanup remains blocked;
- another terminal run remains cleanup-eligible;
- a new unrelated run is admitted successfully.

The tests also prove a later successful read or scan appends `repair_verified` and removes the run from repair-only attention.

### NR-2 red cycle

A real workflow lock is held by an independent execution context while notification repair reaches that run. The focused test asserts that:

- reconciliation returns without advancing the cursor;
- three consecutive timeouts produce the bounded diagnostic;
- after release, the run is retried and its missing notification is repaired.

A coordinator-level integration test performs one fenced sweep with the real held lock, an eligible Gateway outbox row, and actionable scheduled work. It asserts both Gateway delivery and scheduler submission occur in that same sweep.

### NR-3 red cycle

The merge-gate meta-test first fails because the native evidence file is absent from the exact CI selection. The workflow and pinned list are then updated together. Applicable native evidence tests are run locally, and the final three-OS result is recorded from the matrix rather than inferred.

## Verification and packaging

Planned commits remain independently reviewable:

1. this design specification;
2. a detailed executable TDD plan after written-spec approval;
3. `fix(workflow): contain run-scoped repair failures` for NR-1 and NR-2 production code and failure-injection tests;
4. `test(workflow): enforce native evidence containment` for NR-3 CI/meta-test coverage;
5. corrected final verification evidence in a documentation-only commit if needed.

Before each implementation commit: run focused red/green commands, relevant real SQLite and real coordinator integration tests, `git diff --check`, and an exact staged-file audit. Before completion: run the strengthened workflow merge gate, installed-distribution integration test, platform-relevant evidence tests, and fresh status/diff checks.

Reviewer-authored reports already present in the worktree remain untouched and unstaged. Nothing is merged, tagged, released, or pushed by this work.

## Acceptance criteria

- None of the three governed run-scoped read/scan sites writes the global repair marker for one run's damage.
- Active run-scoped damage is visible in the attention surface with its exact stable reason.
- Later successful corroboration clears only the matching active run-scoped warning through an append-only verified transition.
- Damaged-run cleanup remains blocked while unrelated cleanup and admission remain available.
- A held run lock neither advances the repair cursor nor aborts the coordinator sweep.
- Repeated same-run timeouts emit a bounded diagnostic.
- Gateway drain and scheduler submission still occur in the same contended sweep.
- `test_evidence_api.py` is pinned in both CI and the merge-gate meta-test, with honest cross-platform skip arithmetic.
- The full strengthened gate is green, and no unrelated or reviewer-owned file is included in task commits.
