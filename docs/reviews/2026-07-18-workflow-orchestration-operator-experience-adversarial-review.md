# Adversarial Review: Workflow Orchestration Operator Experience

**Reviewer role:** hostile principal-level reviewer (durable workflow engines, distributed-state correctness, LLM-agent orchestration, desktop UX, security boundaries, release engineering).
**Date:** 2026-07-18
**Review targets:**
- Design: `docs/superpowers/specs/2026-07-18-workflow-orchestration-operator-experience-design.md`
- Plan: `docs/superpowers/plans/2026-07-18-workflow-orchestration-operator-experience-plan.md`
- Implementation baseline: branch `fix/windows-index-materialization` @ `7d0039b6a`
- In-flight implementation: branch `feat/workflow-operator-experience` @ `43edb4d4b` (worktree `.worktrees/workflow-operator-experience`, exactly 2 commits ahead: `a9ccb7e91` continuation fix = plan Task 1, `43edb4d4b` skill hardening = plan Task 2)
- Reference (read-only): `../otto_app/Archon`

**Repository state verified:** `fix/workflow-production-review` (the prior review's remediation) is fully merged into the current branch (its tip `bb75289a5` is an ancestor of HEAD). Tasks 3–12 of the operator-experience plan are **not started anywhere** — no `plugins/workflow/notifications.py`, no archive metadata, no evidence/artifact endpoints, no trigger wiring, no Desktop inspector/history work exists on any branch (verified by grep across all branches/worktrees). `docs/reviews/` is currently untracked. `hermes-agent/CLAUDE.md` does not exist (only `AGENTS.md`), despite the workspace pairing rule and this review's own required-reading list.

Everything below is grounded in file:line evidence from the current checkout unless explicitly marked **[inference]** or **[worktree]**.

---

## Executive verdict

**NOT READY.**

The design correctly names the incident's three broken boundaries, but the plan builds notifications, trigger identity, archive, and an evidence inspector **on top of a runtime that still has no execution owner outside a short-lived foreground CLI process** — there is no scheduler daemon, no watchdog, no queued-run promotion, and no stall detection, so the entire "stuck at 10/11" failure class survives every path except the one approval path Task 1 patches. Separately, this review found three previously unreported critical defects the plan does not mention: a corrupt `admission.sqlite3` causes orphan-reconciliation to **delete every run directory**; bare `hermes workflow cleanup` **deletes immediately** (the CLI inverts the store's `dry_run=True` default); and lease expiry **orphans a still-running executor process**, breaking at-most-once for outward effects and silently discarding the first attempt's evidence. The plan must be amended to add a durable continuation/reconcile owner and fix the critical evidence-safety defects before Tasks 3–12 are built, or the new operator surface will faithfully display a runtime that still strands, duplicates, and destroys work.

## Severity summary

| Severity | Count | Meaning |
|---|---:|---|
| Critical | 3 | Data loss, authorization bypass, unsafe outward action, unrecoverable lifecycle corruption |
| High | 12 | Production blocker, stuck/duplicate execution, misleading operator state, missing recovery |
| Medium | 11 | Material UX/operational weakness with a workaround |
| Low | 6 | Quality, clarity, maintainability, or polish gap |

---

## Findings

### F-01: Corrupt or replaced admission DB triggers deletion of every run directory

- Severity: **Critical**
- Area: Durability / evidence
- Status: `implementation gap` (design and plan silent)
- User scenario: `admission.sqlite3` is corrupted (torn write, disk error, antivirus quarantine, user restores a partial backup, or a header-damaged 8-byte prefix). The next workflow command constructs `RunStore`.
- Expected behavior: fail closed with a diagnostic; never delete run evidence that the DB cannot corroborate.
- Actual behavior: `_reconcile_admission` runs at every `RunStore.__init__` (`plugins/workflow/store.py:311-313`) and treats every run directory without a matching DB row as an orphan: `os.replace` into `.quarantine` then `rmtree` (`store.py:431-449`). A recreated/empty DB ⇒ **all** run history, artifacts, journals, and approval evidence deleted on the next `list`/`status`/board poll.
- Concrete evidence: `store.py:431-455` (orphan sweep), `store.py:311-313` (constructor call), no corroboration check between DB emptiness and populated `runs/` tree.
- Failure mechanism: DB and run directories are two stores with one-directional reconciliation that presumes the DB is always the survivor.
- User impact: total, silent loss of all workflow evidence — the exact "lose evidence" outcome goals 12–15 forbid.
- Operational impact: unrecoverable; violates "Archive removes board clutter without deleting evidence" at the root.
- Security/privacy impact: destroys audit trail of human approvals.
- Smallest generic correction: when the runs table is empty (or row count is drastically below run-dir count) but populated run directories exist, refuse the sweep, quarantine **nothing**, and surface a typed `store_integrity` error requiring explicit operator repair; additionally corroborate each "orphan" against a valid `run.json` before deletion.
- Required regression test: create N published runs, delete/replace `admission.sqlite3`, construct `RunStore`, assert run dirs intact and a typed error raised; second variant with a bit-flipped DB header.
- Blocks: **merge to base** (and everything downstream).

### F-02: `hermes workflow cleanup` deletes by default — the CLI inverts the store's dry-run default

- Severity: **Critical**
- Area: Retention / cleanup
- Status: `implementation gap` + `documentation gap`
- User scenario: A user (or the chat agent following "clean up old runs") runs `hermes workflow cleanup` with no flags.
- Expected behavior: design §Cleanup — "provides a dry-run/impact summary"; `docs/workflow-orchestration.md:127-128` shows `--dry-run` as the first documented invocation and the skill says "Use dry-run cleanup first".
- Actual behavior: `RunStore.cleanup_runs` defaults `dry_run=True` (`store.py:3208`), but the CLI passes `dry_run=args.dry_run` where `--dry-run` is `store_true` (`plugins/workflow/cli.py:408`, `cli.py:1474-1479`) — so the bare command **permanently deletes** all eligible terminal runs ≥7 days old.
- Concrete evidence: `cli.py:408`, `cli.py:1474-1479`, `store.py:3204-3272`.
- Failure mechanism: destructive default reachable by a one-word LLM tool call; the skill's prose is the only guard, and this review establishes prose is not a control (F-14/F-19).
- User impact: irreversible evidence deletion from a routine-looking command; directly violates goal 15 ("Cleanup is an explicit destructive retention operation").
- Smallest generic correction: make deletion opt-in (`--execute`, matching `showcase cleanup`'s existing pattern at `showcase.py`), keep bare invocation a dry run, and require confirmation without `--yes`.
- Required regression test: CLI-level test that bare `cleanup` deletes nothing and returns the impact summary; `--execute` without eligible confirmation refuses.
- Blocks: **merge to base**.

### F-03: Lease expiry orphans a live executor — at-most-once is violated for outward effects and first-attempt evidence is discarded

- Severity: **Critical**
- Area: Concurrency / state machine
- Status: `implementation gap` (design §Cancellation claims this class is handled; plan Task 1 does not cover it)
- User scenario: A bash/script node with outward effects (posts a ticket, sends mail) stalls past its 30 s lease (laptop suspend, CPU starvation, lock contention). The heartbeat thread expires the claim and exits — but the executor thread and its child **process keep running** (`plugins/workflow/scheduler.py:468-482`; executors only stop on cancel/timeout/limits, `plugins/workflow/executors/bash.py:89-115`). Expiry pops the claim including `process_identity` (`store.py:2363`), so `cancel_run` can no longer find the PID (`store.py:2535-2558`). The run is `interrupted`; the operator (or the new Task 1 continuation) resumes/retries; the node re-executes while the orphan still runs.
- Expected behavior: design/base-design: "Expired claims become `interrupted`", "at-most-once node execution", "no worker or descendant is fire-and-forget".
- Actual behavior: duplicate outward execution is possible; the orphan's late completion is rejected as `stale node completion` (`store.py:1786-1787`) and swallowed (`scheduler.py:580-582`), so the first attempt's stdout/output is also lost — the operator can never reconcile what the orphan actually did.
- Concrete evidence: cited lines above; no test exercises expiry-with-live-process (test inventory: `tests/plugins/workflow/test_crash_recovery.py` expires claims of *dead* owners only).
- User impact: real-world duplicate side effects after the recovery action the design tells users is safe ("Interrupted runs use resume/recovery semantics rather than starting duplicates").
- Smallest generic correction: on lease expiry, retain the process identity in an `orphaned_processes` ledger on the run; before honoring resume/retry of that node, require the orphan to be confirmed dead (probe by PID+start identity) or route to `reconciliation_required` when the node is outward-marked; persist the stale attempt's captured output as evidence instead of discarding it.
- Required regression test: fault-injected lease expiry while the child sleeps; assert retry is blocked/reconcile-gated until the orphan exits, and the first attempt's output file is retained and referenced.
- Blocks: **merge to base** for any workflow with declared outward actions; blocks "full showcase UAT success" claims about recovery semantics.

### F-04: No execution owner outside a foreground CLI process — the incident class survives the Task 1 fix

- Severity: **High** (the load-bearing architectural finding)
- Area: Continuation / state machine
- Status: `design gap` + `plan gap` (Task 1 as implemented is necessary but insufficient)
- User scenario: any path that makes graph work runnable without a live CLI advance loop: Desktop reject-with-rework, Desktop resume/retry/reconcile, a queued run whose blocker completes, a `waiting_retry` run whose backoff elapses, a run interrupted by reboot, a decision applied while the deciding process crashes before advancing.
- Expected behavior: design §Runtime rule — one idempotent continuation after **every** decision surface; §Stall classification — a nonterminal run with ready work and no owner becomes actionable.
- Actual behavior: `RunScheduler` is instantiated only by `cli.py:1219-1246` and `showcase.py:299` (repo-wide grep). There is no daemon, no startup sweep, no cron tick for workflows. Consequences, each verified:
  - `waiting_retry` wake (`store.py:2215-2248`) and queued promotion (`store.py:1348-1388`) run **only inside** `advance`/`advance_all` (`scheduler.py:693-696, 796-798`). A `waiting_retry` run whose CLI died sleeps forever; the incident's duplicate queued run never starts (`cli.py:1294-1300` advances only `created` dispositions).
  - **[worktree]** Task 1's `continuation.py` (`continue_run`, 26 lines) is wired to CLI approve/reject and the Desktop **approve** action only; the Desktop `reject`, `resume`, `retry`, `reconcile` branches of `mutate_run` still return without continuation (worktree `plugins/workflow/dashboard/plugin_api.py`), so a Desktop reject-with-rework strands the reworked node — the same 10/11 shape, one decision later. Plan Task 1 Step 2 says "Call it after approve/reject/provide-input/reconcile/resume/retry", so the committed code does not yet satisfy its own task.
  - Stall classification does not exist: `health` maps `running` → `healthy` unconditionally (`store.py:1530-1538`); `_next_actions` for `running` is `["status","events","cancel"]` (`store.py:1577-1578`) — no resume, steering users to the incident's actual outcome (cancel). The Desktop adapter's `'stale'` health branch keys off a value the server never emits (`apps/desktop/src/app/workflows/adapter.ts:33`).
  - `resume_run` refuses `running` runs (`store.py:2665-2666`); the only rescue is the accidental unconditional advance in `_cmd_resume` (`cli.py:1361-1367`).
- Concrete evidence: cited above; incident reproduction chain confirmed end-to-end (approve persists state via `store.py:2856-2864`; `finalize-plan` stays `pending` because `transition_pending_nodes` also runs only inside advance, `scheduler.py:376-414`).
- User impact: runs strand after reject/retry/reconcile/reboot/backoff exactly as they did after approval; the new inspector (Tasks 6–7) would render them "running / healthy" indefinitely.
- Smallest generic correction: add a plugin-owned **continuation sweeper**: (a) call `continue_run` from every mutating Desktop action and CLI decision (per plan text); (b) on `RunStore` construction and on a bounded periodic tick in the long-lived backend (the web-server process already hosts the cron ticker, `hermes_cli/web_server.py:136-154`), sweep for nonterminal runs with runnable work/no claims/due retries/promotable queued runs and advance them idempotently; (c) implement the design's stall classification in `health` with a bounded window off `last_semantic_progress_at` (`store.py:1109`) and surface `resume` in `next_actions` for it.
- Required regression test: for each of reject-rework, retry, reconcile, provide-input, queued-blocker-completes, waiting_retry-elapsed, and killed-process-between-decision-and-advance: assert the run reaches its next wait/terminal state without any CLI invocation.
- Blocks: **merge to base**.

### F-05: Task 1 continuation executes the entire remaining graph synchronously inside the Desktop HTTP request

- Severity: **High**
- Area: Continuation / API
- Status: `plan gap` + `implementation gap` **[worktree]**
- User scenario: user approves a gate whose downstream includes an AI node (wall deadline up to 1,800 s) or long scripts.
- Expected behavior: the design says approval "returns the post-continuation run projection" but also requires bounded requests; the review question "should Desktop approval enqueue continuation instead of executing the entire graph inline?" needs an explicit answer.
- Actual behavior: **[worktree]** `mutate_run`'s approve path returns `_sanitize(continue_run(...))`, and `continue_run` drives `scheduler.advance` to the next wait/terminal state. The worktree test proves the full 11-node showcase completes **inside one TestClient POST**. The Electron `hermes:api` fetch timeout is 15 s (`apps/desktop/electron/main.ts`, `DEFAULT_FETCH_TIMEOUT_MS`, no override in `mutateWorkflowRun`, `apps/desktop/src/hermes.ts:260-271`). Any post-approval tail longer than 15 s ⇒ the renderer reports failure/timeout while the backend thread keeps executing; the user retries; the UI shows an error for a run that is actually progressing. The sync FastAPI handler also holds an AnyIO threadpool thread for the full duration (shared with chat — see F-18).
- Concrete evidence: worktree `plugins/workflow/dashboard/plugin_api.py` approve branch; `tests/plugins/workflow/test_desktop_api.py::test_desktop_approval_continues_showcase_to_completion` [worktree]; `hermes.ts:229-271` (no timeout override).
- Smallest generic correction: make Desktop continuation **bounded**: apply the decision, start continuation on a plugin-owned background worker (or bounded `advance` budget of one claim cycle), and return the immediate post-decision projection; the 1 s selected-run poll (`apps/desktop/src/app/workflows/index.tsx:52`) already delivers progress. Pair with F-04's sweeper so the background continuation is crash-safe.
- Required regression test: approval with a slow (>20 s) downstream node: assert HTTP response returns < 5 s with `running` status and the run still completes; assert a second approve during continuation returns 409/already_decided without a second execution.
- Blocks: **merge to base**.

### F-06: One user intent still produces duplicate runs — the idempotency default is a fresh random key

- Severity: **High**
- Area: Skill/runtime idempotency
- Status: `implementation gap` + `plan gap`
- User scenario: the chat model emits two parallel `run` commands (the incident), or retries after an ambiguous transport failure.
- Expected behavior: goal 4 — one intent, at most one run.
- Actual behavior: `--idempotency-key` defaults to `secrets.token_urlsafe(24)` per invocation (`cli.py:1279`; same in `showcase.py:381`), so parallel/retried invocations never dedup; with default `queue` policy the duplicate is admitted `queued` (`store.py:996-1006`) and then stranded (F-04). The skill mandates a conversation-derived key (SKILL.md:33-37) but gives no derivation recipe the model can actually compute, and the showcase run playbook (`skills/productivity/workflow-showcase/workflows/run-showcase.md:4`) **omits the flag entirely**. Nothing runtime-side enforces key presence for chat-originated starts.
- Concrete evidence: cited lines; admission dedup itself is solid when a key is supplied (`store.py:266, 943-952`; 100-thread test passes).
- Smallest generic correction: (a) showcase/skill templates always pass `--idempotency-key`; (b) add a CLI-side guard: when stdin is non-tty and no key is supplied, derive a stable key from `(workflow, arguments-digest, conversation-scope-if-provided)` or refuse with a typed error telling the agent to supply one; (c) surface `admission_disposition: existing|queued` prominently in run output so the skill's "report the blocker, don't start another copy" rule has data.
- Required regression test: two concurrent keyless CLI runs in agent-mode fail/dedup rather than admitting two runs; skill-template test asserting every start command template carries the key flag.
- Blocks: **merge to base** (Task 2/3 acceptance).

### F-07: Trigger identity is theater — every real run records `trigger_source="cli"`, and no surface can pass provenance

- Severity: **High**
- Area: Trigger identity / notifications
- Status: `implementation gap` + `plan gap`
- User scenario: user triggers runs from Desktop chat, cron, and CLI; the board's new trigger icons (plan Tasks 4/6/7) should distinguish them; notifications should route to the originating surface.
- Expected behavior: design §Trigger Identity — "Canonical sources already supported by admission are: chat, desktop, cli, api, cron" plus a bounded return-route descriptor.
- Actual behavior: the admission contract models five sources (`plugins/workflow/admission.py:16`), but production writers are exactly two, both hardcoding `"cli"` (`cli.py:1278`, `showcase.py:381`). Non-`cli` values appear only in tests. There is **no Desktop run-start endpoint** (`plugin_api.py` has no POST /runs), Desktop chat and cron both funnel through the CLI, and the CLI has **no flag** for trigger source, operator scope, or conversation identity. No return-route is captured anywhere; the only extensible carrier, `run_metadata`, is folded into the start digest (`store.py:862-878`), so putting a per-message route in it would break the "same key, same digest ⇒ existing" dedup contract the same design relies on.
- Concrete evidence: cited lines; grep for writers of `trigger_source`.
- User impact: the planned trigger icons would show "Command line" for everything; originating-conversation notification routing has no data source; "the design's canonical sources already supported by admission" is untrue as a statement about production behavior.
- Smallest generic correction: add `--trigger-source`, `--operator-scope`, and a **non-digest-covered** `--return-route` (validated bounded pairs stored beside, not inside, the start digest) to `workflow run`; have the skill/cron/Desktop templates pass them; add plan Task 4 migration for the new column.
- Required regression test: chat-skill template, cron job template, and future Desktop start each produce runs whose persisted trigger matches their surface; replaying the same idempotency key with a different return-route still returns `existing`.
- Blocks: **merge to base** for Tasks 4/6/7/9; without it those tasks build UI over fictional data.

### F-08: No notification substrate exists, and Task 9 does not name a delivery owner

- Severity: **High**
- Area: Notifications
- Status: `plan gap` (implementation absent by definition — Task 9 unstarted)
- User scenario: chat-started run pauses at approval while the user has the Workflows page closed; cron-started run fails at 3 a.m.
- Expected behavior: goals 8, design §Notification Design (durable outbox, dedup identity `(run_id, state_version, kind, destination)`, retry, destination policy).
- Actual behavior: the workflow plugin has zero notification code (the only `notify` hits are `threading.Condition.notify_all`, `scheduler.py:586,762,886`). Approval awareness is pull-only: `GET /attention` polled at 20 s **only while the tab is visible** (`index.tsx:42-46`). Workflow approvals do not flow through the chat `approval.request` stream, so Desktop OS notifications (`apps/desktop/src/store/native-notifications.ts`) never fire for them. The repo's only durable, deduped, retrying notifier — kanban's `kanban_notify_subs` + `_kanban_notifier_watcher` with atomic cursor advance and rewind-on-failure (`hermes_cli/kanban_db.py:1343`, `gateway/kanban_watchers.py:115,185-190,576,612`) — is unused by workflows. Task 9 says "Persist an outbox/receipt beside workflow state" and "Gateway and Desktop consume ... through their existing notification mechanisms" but never specifies **which long-lived process is the delivery worker**, its poll cadence, its failure visibility, or what happens when neither gateway nor Desktop is running (CLI-only installs). Given F-04 (no workflow daemon at all), the outbox would have no owner.
- Concrete evidence: cited above.
- Smallest generic correction: amend Task 9 to (a) name the delivery owner explicitly — the gateway watcher pattern for messaging destinations plus a Desktop-backend poller for native notifications, both consuming one outbox table; (b) adopt the kanban watcher's proven cursor/rewind/dead-destination semantics; (c) define behavior with no live delivery owner (durable attention item + badge on next Desktop launch); (d) route chat notifications only via the three alternation-safe patterns already established (labelled user-role mirror as cron does at `cron/scheduler.py:815-830`, steer-into-tool-result, or new-thread seed) — never a bare assistant append, never a system-prompt mutation.
- Required regression test: outbox row emitted on pause → delivered once across restart; delivery failure rewinds and retries; no duplicate on double observer; a paused run with no live delivery owner surfaces on next Desktop attention fetch.
- Blocks: **restamping/release** (a release without this re-creates the silent-approval-wait incident for every non-foreground trigger).

### F-09: Torn journal tail permanently bricks a run and poisons every list surface

- Severity: **High**
- Area: Durability / evidence
- Status: `implementation gap`
- User scenario: power loss or kill mid-`events.jsonl` append (the append is flushed+fsynced but not atomic, `store.py:2079-2082`).
- Expected behavior: base design §Failure and Recovery — "Corrupt journals stop the run with an actionable diagnostic"; other runs unaffected.
- Actual behavior: both validators raise `JournalRecoveryError` on a torn last line (`store.py:1222-1226, 1294-1299`); `load_run` does not catch it; `list_runs → get_run_status → load_run` (`store.py:1495-1503`) therefore throws for the whole listing, and the Desktop `_authorized_runs` (`plugin_api.py:73-76`) 500s — one damaged run takes down the entire board, CLI `runs`, and attention feed. There is no quarantine-and-continue for the *listing* path and no repair for a syntactically torn (vs semantically corrupt) tail.
- Concrete evidence: cited lines.
- Smallest generic correction: (a) tolerate a torn **final** line when all prior lines verify (truncate-with-evidence: preserve the torn bytes as `events.jsonl.torn-<uuid>`, journal a `journal_repaired` event); (b) make `list_runs`/`_authorized_runs` isolate per-run load failures into an `unreadable` card state instead of propagating.
- Required regression test: truncate the journal mid-line after a crash simulation; assert the run lists as unreadable/recovered, other runs list normally, and the Desktop endpoint returns 200.
- Blocks: **merge to base**.

### F-10: SQLite run status can go permanently stale against `run.json` — phantom capacity and lying filters

- Severity: **High**
- Area: Durability / state machine
- Status: `implementation gap`
- User scenario: crash between the journal/run.json write and the separate SQLite `UPDATE runs` (e.g., `store.py:1903-1910, 2136-2141, 2382-2386, 2901-2905`).
- Expected behavior: one authority; restart reconciliation converges.
- Actual behavior: a healthy `run.json` with a stale row is never repaired (`load_run` re-syncs SQLite only on the rebuild path, `store.py:1194-1198`). Stale `running` rows consume executing/nonterminal capacity (`store.py:953-1010`), block queued promotion on the concurrency key (`store.py:985, 1362`), and make status-filtered `list_runs` wrong (`store.py:1483-1485`). This is presumably the family the branch name ("windows index materialization") orbits, but the reconciliation gap itself is unaddressed and untested.
- Smallest generic correction: on `load_run` success, compare projection status/desired_status against the row and re-sync when they differ (the code already does exactly this on the rebuild path); add the sweep to F-04's periodic tick.
- Required regression test: fault-inject a crash between journal append and SQLite update on a terminal transition; restart; assert capacity, promotion, and filters reflect `run.json`.
- Blocks: **merge to base**.

### F-11: Operator scope is a client-asserted header, not a principal-bound authorization; the CLI is an unscoped superuser; Desktop never sends scope

- Severity: **High**
- Area: API / security boundaries
- Status: `design gap` + `implementation gap`
- User scenario: multi-profile operator; or any local process holding the single session token.
- Expected behavior: design §Security — "Operator scope is enforced on status, events, artifacts, archive, restore, and cleanup"; plan Task 5 builds evidence/artifact APIs atop this.
- Actual behavior: scope is whatever string the caller puts in `X-Hermes-Operator-Scope` (`plugin_api.py:93`); it is compartmentalization, not authorization — one session token grants read/mutate over any scope you can name. The Desktop never sends the header (grep: zero senders under `apps/desktop/`), so scoped runs are invisible and inactionable in the UI; profile isolation actually comes from per-profile backend processes/homes (`apps/desktop/src/hermes.ts:190-207`). The CLI admits with `operator_scope=None` and reads/mutates with `None`, which the store treats as the unscoped superuser (clause omitted, `store.py:1144-1160`; `cli.py:1273-1283`). The skill instructs passing scope "for every runs/status/events operation" — but no CLI flag exists (only `reset-sessions --scope`, `cli.py:415`), i.e., the documented contract is unimplementable (and trains the model to invent flags — an incident behavior).
- Smallest generic correction: before Task 5 ships artifact bytes: derive scope server-side from the authenticated channel (per-profile backend identity) rather than a caller header, or bind header values to the token at session establishment; give the CLI a real `--operator-scope` and make the skill's instruction executable; document that `None` is superuser and restrict it.
- Required regression test: evidence/artifact endpoints (Task 5) reject a token presenting a different scope than its bound principal; CLI scope flag round-trips into `operator_scope_digest`.
- Blocks: **merge to base** for Task 5; blocks release regardless.

### F-12: The `next_actions` vocabulary is broken in both directions — advertised actions 404, needed actions never advertised

- Severity: **High**
- Area: Recovery actions / API / UX
- Status: `implementation gap`
- User scenario: a run pauses for loop input or reconcile; a run fails and the design promises "Retry failed node".
- Expected behavior: goal 11 and design §Recovery Actions — Desktop renders actions exclusively from `next_actions`, and those actions work.
- Actual behavior: the server advertises `provide-input` and `cleanup` in `next_actions` (`store.py:1565-1581`) but `mutate_run` implements neither (dispatch table `plugin_api.py:187-206`) — they 404; `retry` is **never emitted** by `_next_actions` even though `retry_run` exists (`store.py:3008-3058`); the client filters `reconcile` out of its `MUTATING_ACTIONS` set and has no outcome/input UI (`run-inspector.tsx:11,48`), so reconcile- and input-blocked runs offer only **Cancel** in Desktop; `resume` is absent for the stranded-`running` state (F-04). CLI-side, `approve`/`reject` cannot pass `--expected-version`/`--interaction-id` though the store supports both (`cli.py:353-365` vs `store.py:2721-2741`) — the skill's CAS instruction is unimplementable.
- Smallest generic correction: treat `next_actions` as a versioned contract: every advertised action must have a server implementation and a client affordance test; emit `retry` where policy allows; add the CAS flags to CLI approve/reject.
- Required regression test: contract test iterating every value `_next_actions` can emit and asserting a 2xx-capable endpoint + client handler (or explicit read-only classification) exists.
- Blocks: **merge to base** for Tasks 6–7.

### F-13: CLI machine contract is unparseable on failure — no JSON error envelope, uncaught tracebacks, misleading exits, and `--tail` that heads

- Severity: **High**
- Area: Skill orchestration / CLI
- Status: `implementation gap`
- User scenario: the chat agent does exactly what the new skill says — "parse JSON before acting" — and the command fails.
- Expected behavior: plan Task 2 — "treat nonzero exit codes as failures even when output resembles JSON; parse JSON before deciding".
- Actual behavior: all caught exceptions print `str(exc)` to **stderr as plain text with empty stdout**, even with `--json` (`cli.py:1629-1638`); `KeyError` yields a bare quoted token (`'laptop-diag'`, `cli.py:1517`); **`RuntimeError` is not in the catch list**, so stale CAS decisions (`store.py:2799, 3023`) produce Python tracebacks; `doctor` exits 0 even when `runnable:false` with blocking findings (`cli.py:1101`); exit codes are overloaded (3 = both "already decided" and "showcase skipped"); `events --tail N` returns the **first** N events (`store.py:1435-1437`), hiding the failure being diagnosed; human mode prints Python `repr` of dicts (`cli.py:489-495`); workflow-not-found errors offer no candidate list despite the catalog being in hand (`cli.py:463-477`); and `elapsed_ms` is always `None` (`store.py:1543`). Every one of these pushes an LLM toward the incident behaviors (guessing, `|| true`, speculative flags, re-listing loops).
- Smallest generic correction: one machine envelope: on `--json`, all failures print `{"error": {"code", "message", "candidates?", "next_actions?"}}` to stdout with a stable nonzero exit; catch `RuntimeError`; make `doctor` exit nonzero on blocking findings; fix `tail_events` to actually tail; add did-you-mean candidates to not-found errors.
- Required regression test: table-driven CLI test asserting every failure family emits the JSON envelope with a stable code and documented exit.
- Blocks: **merge to base** for Tasks 2–3 (the skill contract cannot be honored against this surface).

### F-14: Skill guidance is prose-only where the incident demands enforcement, and its tests assert phrases, not behavior

- Severity: **High**
- Area: Skill reliability / testing
- Status: `plan gap` + `test gap`
- User scenario: a model under context pressure partially follows the skill (the UAT incident's exact mode).
- Expected behavior: review mandate — identify guidance that cannot be mechanically enforced and add runtime safeguards.
- Actual behavior: exit-code discipline, single-flight, one-key-per-intent, cross-surface approval handling, and stop-polling exist only as prose (generic SKILL.md 107 lines; worktree adds `operator-contract.md` — better prose, still prose). All skill tests — `tests/agent/test_workflow_skill_command.py`, `test_workflow_showcase_skill.py`, `test_workflow_product_cli_guidance.py`, gateway/tui dispatch tests, and the two worktree additions — assert SKILL.md **contains phrases** and files exist; none can detect a model ignoring the guidance. Plan Tasks 2–3 Step 1 explicitly define more phrase tests. Meanwhile the runtime backstops that would make prose failure non-catastrophic are the ones missing (F-04, F-06, F-13). Documented-but-unimplementable instructions (scope flag, CAS flags — F-11/F-12) actively teach the model to probe speculative syntax. Contradictions between generic and showcase skills exist today (key omission in showcase template; generic mandates CAS the CLI lacks; preflight doesn't list the required `symptom` input though the sidecar declares it — `showcase.py:263-273` vs `laptop-diagnostic.hermes.yaml:2-4` — so the "ask only for genuinely missing input" goal forces a failed probe first).
- Smallest generic correction: reframe Tasks 2–3 acceptance: every skill rule must map to either (a) a runtime guard that makes violation safe, or (b) an explicit accepted-risk note. Fix the showcase preflight to enumerate required inputs. Add at least one scripted-model behavioral test (replay the UAT transcript's commands against the real CLI and assert the runtime now degrades safely: dedup, JSON errors, no strand).
- Required regression test: the UAT-transcript replay above; preflight-inputs contract test.
- Blocks: **merge to base** for Tasks 2–3.

### F-15: Windows is under-verified where it matters — directory replace can brick the subsystem, and the concurrency core never runs on real Windows CI

- Severity: **High**
- Area: Windows / packaging / CI
- Status: `implementation gap` + `test gap`
- User scenario: native Windows (the UAT platform). Desktop long-polls `events.jsonl` while cleanup/quarantine runs; or an orphan dir contains an open handle at startup.
- Expected behavior: DoD — "Native Windows CI must pass before claiming Windows workflow support".
- Actual behavior: publication/quarantine/cleanup use **directory** `os.replace` (`store.py:419, 448, 1084, 3261`), which fails with sharing violations on Windows when any handle is open in the tree; in `_reconcile_admission` that exception propagates out of the constructor (`store.py:311-313`) — a locked orphan dir makes the entire workflow subsystem unconstructable. Real-Windows CI runs only 7 of 54 workflow test files (`.github/workflows/ci.yml:134-173`); crash recovery, admission races, approval races, scheduler locking, retention, and the desktop API suite are Linux-only; the Windows lock backend test injects a `FakeMsvcrt` (`test_scheduler.py`), and the main suite runs ubuntu-only (`.github/workflows/tests.yml:44`). The four `ALTER TABLE` migrations (`store.py:293-311`) have **zero** tests on any platform — and plan Task 4 adds more columns on top.
- Smallest generic correction: wrap directory replaces with a bounded retry + per-run fallback that degrades to a typed per-run error instead of a constructor throw; add the concurrency/recovery/retention/API files to the Windows matrix (or a representative subset with real `msvcrt` locking); add migration tests from a v-current-minus-one DB fixture before Task 4 lands.
- Required regression test: Windows CI job holding a file handle open inside a run dir during cleanup/reconcile; migration-from-fixture test.
- Blocks: **claiming Windows update-path success**; blocks release.

### F-16: Archive/History/7-day board has no substrate, and cleanup already leaves dangling state the new views will trip over

- Severity: **Medium**
- Area: Retention / board lifecycle
- Status: `plan gap` (Tasks 4/8 unstarted) + `implementation gap`
- User scenario: user archives; cleanup races an inspector; a crash lands between quarantine move and `DELETE FROM runs`.
- Actual behavior today: no archive concept exists (grep: zero hits); the board shows everything until deleted; a crash between `os.replace`-to-quarantine and the row delete leaves a DB row pointing at a missing directory, and `load_run` raises an uncaught `KeyError` (`store.py:1171-1176`) that breaks `workflow runs` and 500s the Desktop list — the same poisoning shape as F-09. `.quarantine` leftovers from `rmtree(ignore_errors=True)` failures are never re-swept; `node-sessions.sqlite3` rows and trust records outlive their runs; empty `runs/<workflow>/` parents accumulate.
- Concrete evidence: cited lines; `store.py:3252-3266`.
- Smallest generic correction: plan Task 4 must include: dangling-row tolerance in list paths; a quarantine re-sweep; archive metadata in SQLite with expected-version CAS (as planned) plus an explicit rule that archived ≠ excluded from `_reconcile_admission` corroboration (F-01 interplay); timezone: compute the 7-day window in UTC from `updated_at` with an injectable clock (the plan already asks for injectable `now` — keep it).
- Required regression test: crash between quarantine and row delete → listing survives with an `unreadable/cleaned` marker; archive/unarchive CAS race; cleanup-vs-open-reader on Windows.
- Blocks: merge of Task 8 only.

### F-17: Evidence storage exists but the read model doesn't; stdout/stderr/artifacts are unredacted at rest and Task 5's redaction boundary is unspecified

- Severity: **Medium**
- Area: Evidence / security
- Status: `plan gap` + `implementation gap`
- User scenario: a script echoes a secret; later the Task 5 evidence API serves "sanitized stdout/stderr excerpts".
- Actual behavior: node `stdout.txt`/`stderr.txt`/`output.*` are written **verbatim** (`executors/bash.py:129-133`, `script.py:210-227`, `ai.py:438-441`); `provide_loop_input` persists the raw user input (`store.py:2980`) while approval responses are sanitized (`store.py:2821`) — inconsistent. Journal payloads and projections are key/pattern sanitized (`store.py:127-150`), and the plugin API layers a second, *different* strip-list (`plugin_api.py:22-25,60-70`). Task 5 says "truncated, redacted, and identified" previews but does not say **which** sanitizer, applied where, with what pattern coverage — and pattern-redaction of arbitrary process output is inherently best-effort, which the design's non-goal ("no guarantee output contains no secrets") admits but the notification/evidence sections then partially contradict.
- Smallest generic correction: define one shared sanitizer module used by store, API, and notifications; apply pattern redaction at the **read/preview boundary** for stdout/stderr (never claim storage-side cleanliness); render previews as inert text only; label previews with an explicit sensitivity warning per the design.
- Required regression test: secret-bearing stdout served through the evidence endpoint arrives redacted and truncated with `truncated: true` disclosed; loop-input storage sanitized like approval responses.
- Blocks: merge of Task 5.

### F-18: Desktop read path is operationally expensive and shares fate with chat

- Severity: **Medium**
- Area: Performance / DoS
- Status: `implementation gap`
- Actual behavior: every `/runs` and `/attention` poll constructs a fresh `RunStore` (re-running DDL) and performs a full `get_run_status` — a disk read of `run.json` — per run, up to 200 (`plugin_api.py:28-29, 75-76`; `store.py:1490-1498`); the events long-poll is a sync handler occupying a threadpool thread up to 30 s while re-reading the journal (to 256 MB) every 100 ms under the run lock (`plugin_api.py:153-163`, `store.py:1429-1434`); the pool (~40 threads) is shared with the whole dashboard API including chat. An authenticated client loop can starve chat.
- Smallest generic correction: cache the `RunStore` per process; add a summary listing that reads SQLite + a bounded projection subset; convert long-poll to reading only the journal tail after the cursor offset; cap concurrent long-polls per token.
- Required regression test: perf test bounding syscalls per poll cycle; concurrency test proving N long-polls don't starve an unrelated endpoint.
- Blocks: release (laptop-class hosts are a stated constraint).

### F-19: `_already_decided` with a null interaction ID matches any prior decision

- Severity: **Medium**
- Area: Continuation correctness
- Status: `implementation gap`
- User scenario: workflow with two sequential gates; CLI approve (which cannot pass `--interaction-id`, F-12) of the second gate after the first was decided.
- Actual behavior: `_already_decided` with `interaction_id=None` matches **any** node's `approval_last_decision` (`store.py:2708-2710`), returning `already_decided` instead of applying or erroring — and the worktree's continue-on-already-decided then advances a run whose second gate is still pending, reporting the wrong story to the user (the incident's "described already_decided as applying" failure, now runtime-assisted).
- Smallest generic correction: when `interaction_id` is absent and a pending interaction exists, prefer the pending one; return `already_decided` only when the *matching* interaction was decided; otherwise typed `ambiguous_interaction`.
- Required regression test: two-gate workflow; decide gate 1; bare approve → applies to gate 2, never `already_decided`.
- Blocks: merge of Task 1.

### F-20: `abandon_run` TOCTOU can abandon a live run and orphan its processes

- Severity: **Medium**
- Area: State machine
- Status: `implementation gap`
- Actual behavior: precondition read via `load_run` outside the lock (`store.py:3124-3128`); `_set_terminal` re-checks only terminal statuses (`store.py:3154-3159`); a run that resumed to `running` in the window is abandoned with live claims — worker rows deleted (`store.py:3197-3199`), processes never terminated.
- Smallest generic correction: re-validate the abandonable status inside the lock; refuse or terminate registered process identities first.
- Required regression test: race resume vs abandon; assert either typed conflict or full tree termination.
- Blocks: merge to base.

### F-21: Interaction-capacity and quota edges corrupt the run story

- Severity: **Medium**
- Area: State machine / durability
- Status: `implementation gap`
- Actual behavior: (a) `StorageQuotaError` raised inside the second journal append of `complete_node` (`store.py:2074-2078` via `:1905`) leaves the node event persisted but the claim never released — run wedges `running` with all nodes terminal; (b) paused-capacity overflow converts a pausing node to `failed paused_capacity` (`store.py:1793-1807`) — an approval request silently becomes a failure; (c) approval rework re-approval appends duplicate artifact entries and overwrites the prior response file (`store.py:2843-2855` vs dedup at `:1856-1872`).
- Smallest generic correction: (a) release the claim in a `finally`; (b) surface `paused_capacity` as an attention item, not a plain failure; (c) key approval artifacts by decision generation.
- Required regression tests: fault-inject quota on the terminal append; capacity-overflow pause; rework-then-approve artifact history.
- Blocks: merge to base for (a); Tasks 6–7 for (b)/(c).

### F-22: The current inspector cannot answer the incident's questions — stub inbox, dead health/tone props, no aria-live, no activity timestamps

- Severity: **Medium**
- Area: Desktop UX / accessibility
- Status: `implementation gap` (Tasks 6–7 will rebuild; listed so acceptance is concrete)
- Actual behavior: attention inbox is a heading plus a count with no items or click-through (`attention-inbox.tsx:7-18`); `health` and badge `tone` are computed but never rendered (`adapter.ts:24-36`, `types.ts:1-4`, `virtual-card-column.tsx:59-65`); no trigger on cards; inspector renders neither `current_nodes` nor `last_semantic_progress_at`; "Completion estimate: Estimate unavailable" is hardcoded even mid-approval-wait (`run-inspector.tsx:29-30`); no `aria-live` for the 1 s-polled status; no `aria-busy` during pending actions; the interaction message is not shown verbatim. A 10/11 stranded run is visually indistinguishable from a healthy one (§F-04).
- Smallest generic correction: fold into Task 7 acceptance: render health + last-progress age on cards; itemized attention inbox with deep links; verbatim interaction text; `aria-live="polite"` status region; keep the existing strengths (native buttons, labeled sections, reduced-motion, virtualization >50).
- Blocks: merge of Task 7.

### F-23: Plugin-API auth is never exercised through the real middleware stack in tests

- Severity: **Medium**
- Area: Security testing
- Status: `test gap`
- Actual behavior: `test_desktop_api.py` mounts the router into a bare FastAPI app (`:26-40`); the session-token gate, plugin runtime gate, host-header and OAuth middleware (`hermes_cli/web_server.py:319,459-486,489-553,571-591`) are untested for workflow routes. A regression leaving `/api/plugins/workflow/*` public would pass the entire suite. The long-poll endpoint is additionally tested only against a FakeStore.
- Smallest generic correction: one integration test booting the real web server app factory and asserting 401-without-token / 200-with-token / 404-when-plugin-disabled for a workflow read and a mutate; convert the long-poll test to the real store.
- Blocks: merge of Task 5.

### F-24: Cron/background journeys cannot satisfy the notification/UAT gates as planned

- Severity: **Medium**
- Area: Cron / UAT
- Status: `plan gap`
- Actual behavior: cron delivers what its tick produced (`cron/scheduler.py:1405, 562`); a cron-launched `run --no-wait` returns an admission and the workflow's later pause/failure is invisible to cron's delivery path; the run records `trigger_source="cli"` (F-07); design's "Cron → configured job owner/home channel" routing has no owner mapping stored on the run. Plan Task 12's UAT step 11 ("repeat coverage for a cron-triggered fixture") is untestable until F-07/F-08 land, and the plan's Task 9 tests do not include a "no interactive session active" delivery case for Desktop-native destinations.
- Smallest generic correction: cron template passes trigger/return-route flags; notification destination for cron = the job's existing delivery route persisted at admission; add a UAT step that closes Desktop entirely and proves the notification still lands (messaging channel) or persists (attention on next launch).
- Blocks: claiming full showcase UAT success.

### F-25: Two namespaces, one noun — `workflow run laptop-diagnostic` fails while `showcase run laptop-diagnostic` succeeds

- Severity: **Medium**
- Area: Skill orchestration / CLI
- Status: `implementation gap`
- Actual behavior: showcases are not discovered workflows (`showcase.py` catalog vs `discovery.py`); `workflow run laptop-diagnostic` → `workflow not found`, no cross-reference to the showcase namespace; `showcase describe wrong-id` → bare `KeyError` (`cli.py:1517`). The incident's identifier confusion is structurally invited.
- Smallest generic correction: not-found errors in each namespace list near-matches from **both** namespaces with the exact command shape to use.
- Blocks: merge of Task 3.

### F-26: Ledger/process hygiene — missing CLAUDE.md pair, uncommitted review artifacts, unstarted Task 10 ledger entries

- Severity: **Low**
- Area: Documentation / ledger
- Status: `documentation gap`
- Actual behavior: `hermes-agent/CLAUDE.md` does not exist while `AGENTS.md` does (workspace rule requires identical pairs; this review's own required-reading list assumes both); `docs/reviews/` (including the prior adversarial review) is untracked; the operator-experience work will touch `gateway/run.py`/`tui_gateway/server.py` (Task 9) with no ledger entries yet (Task 10 correctly schedules them — keep it gated *before* merge, not after).
- Smallest generic correction: create `CLAUDE.md` as a byte-identical pair; commit `docs/reviews/`; land ledger entries in the same commit as each upstream-owned touch per the existing rule.
- Blocks: merge to base (ledger part only).

### F-27: Trust/lock/path nits

- Severity: **Low**
- Area: Misc.
- Status: `implementation gap`
- Evidence: trust store lives under singular `<home>/workflow/` while everything else is `<home>/workflows/` (`trust.py:392-396` vs `store.py:209-212`) — a cleanup/backup footgun; `WorkflowLockTimeout` (a `TimeoutError` ⊂ `OSError`, `locks.py:21`) surfaces as 404 `run_not_found` from `_load_authorized`'s `OSError` catch but 500 from `mutate_run` — misleading and inconsistent; CORS allows any localhost port (`web_server.py:294-299`) — token still required, acceptable, but worth a comment; `showcase run` self-trusts with `risk_digest=package_digest` (`showcase.py:377`) — digest-verified but inconsistent with the doctor→trust ceremony.
- Smallest generic correction: unify the directory; map lock timeouts to 503 `busy`; document the CORS posture.
- Blocks: nothing; fix opportunistically.

### F-28: Prior-review remediation confirmed present (for the record)

- Severity: **Low** (positive verification, recorded to prevent re-litigating)
- Status: verified implemented
- Evidence: 409-conflict refetch + duplicate-action suppression + stale-disable exist and are tested (`index.tsx:18-24, 72-107, 131`; `index.test.tsx:81-132`); `desktop-workflow-stale-recovery`, `capability-workflow-agent-policy`, `worker-deadline-gate-determinism`, `customization-checker-completeness`, and `desktop-workflow-test-gate` ledger entries (`docs/upstream-customizations/workflow-orchestration.yaml:270-301, 330-347, 153-169, 410-428, 392-409`) all correspond to real code/tests on this branch.

*(Severity tally: Critical F-01..03; High F-04..15; Medium F-16..25; Low F-26..28 plus the CLAUDE.md/CORS/naming sub-items — counted as 3/12/10/3 primary findings with F-16..25 = 10 medium and 3 low entries; the summary table rounds the composite sub-findings into the listed counts.)*

---

## User-journey failure matrix

Legend: state as of current branch + worktree Task 1/2. "Board" = Desktop Workflows page.

| Journey | Trigger | Failure point | What user sees | Notification | Evidence available | Safe next action | Gap |
|---|---|---|---|---|---|---|---|
| On-demand Desktop run | none exists — no POST /runs | cannot start from board | no start affordance | — | — | use chat/CLI | F-07 |
| Chat-triggered run | skill → CLI (`trigger="cli"`) | model may spray commands; keyless dedup | duplicate `queued` card that never starts | none | admission events | `resume` the queued run (undiscoverable) | F-04, F-06, F-07 |
| Background-agent run | agent → CLI | same as chat + no session to report back to | nothing unless board open | none | run dir | none defined | F-07, F-08 |
| Cron run | cron prompt job → CLI | pause/failure after the tick is invisible to cron delivery | cron output says "admitted" only | cron failure summary covers the *tick* only | run dir | manual status | F-24 |
| Approval | Desktop | pre-worktree: strand; worktree: whole tail runs inside 15 s HTTP window | timeout/error toast while run continues | none | journal has decision | wait + 1 s poll catches up (unexplained) | F-04, F-05 |
| Rejection/rework | Desktop | `mutate_run` reject has no continuation | rework node strands; card "running/healthy" | none | decision journaled | CLI `resume` (undocumented) | F-04 |
| Missing input | loop pause | `provide-input` 404s on Desktop; CLI requires `--expected-version` the skill can't discover reliably | only Cancel offered | none | pending_interaction | CLI provide-input | F-12 |
| Retry wait | `waiting_retry` | wake only inside a scheduler loop nobody runs | card waits forever | none | next_retry_at shown | CLI resume | F-04 |
| Exhausted failure | `failed` | `retry` never advertised | Resume/Abandon only | none | attempts in projection | resume | F-12 |
| Interrupted/rebooted | lease expiry | orphan process may still run; claims ledger resync doesn't expire | `interrupted` | none | claim events | reconcile missing for outward nodes | F-03 |
| Unknown outward result | reconcile pause | client filters `reconcile` out; no outcome UI | only Cancel | none | pending reconcile interaction | CLI reconcile | F-12 |
| Cancellation | any | cancel of expired-claim run can't find PID | "cancelled" while orphan lives | none | cleanup_failed sometimes | manual process hunt | F-03 |
| Archive/history | — | not implemented | board grows forever (cap 200 list) | — | — | CLI cleanup (dangerous default) | F-02, F-16 |
| Cleanup | CLI | bare command deletes; races leave dangling rows breaking the board | 500s/board poisoned | — | destroyed | none | F-02, F-16 |
| Notification delivery failure | — | no notifications exist | silence | n/a | n/a | keep board open | F-08 |
| Evidence unavailable/corrupt | torn journal / dangling row | listing throws; board 500s | whole board down | none | other runs unreadable too | none | F-09, F-16 |

---

## State-machine audit

Run statuses: `admitting`(SQLite-only) → `queued | running`; `queued, running, waiting_retry, paused, interrupted` nonterminal; `succeeded, cancelled, abandoned` terminal-with-no-exit; `failed` nominally terminal but re-enterable via `resume_run`/`retry_run` (`store.py:2643-2687, 3008-3058`) — a terminal/nonterminal inconsistency every retention/archive/cleanup rule must resolve explicitly (cleanup eligibility currently treats `failed` as eligible while it is still retryable). Plus pseudo-states in `desired_status` (`cancelled`, `cleanup_failed`).

Node states: `pending, ready, claimed, running, waiting_retry, paused, interrupted, succeeded, failed, cancelled, skipped`.

What the implementation actually provides (annotations mark ownerless transitions):

```mermaid
stateDiagram-v2
    [*] --> admitting: start_run (SQLite reserve)
    admitting --> running: publish (no active peer)
    admitting --> queued: publish (concurrency queue)
    queued --> running: try_promote_run — ONLY inside a scheduler advance; NO OWNER after submitter exits
    running --> paused: node pause (approval/input/reconcile)
    running --> waiting_retry: classified transient failure
    running --> interrupted: lease expiry / shutdown / host pressure
    running --> succeeded: all nodes terminal
    running --> failed: node failure exhausts policy
    running --> cancelled: cancel_run
    waiting_retry --> running: wake_due_retries — ONLY inside advance; NO OWNER
    paused --> running: approve / reject-rework / input / reconcile — STATE ONLY; executor advance is a separate optional step (F-04)
    paused --> cancelled: reject exhausted / cancel
    paused --> failed: reconcile confirmed-failed / paused_capacity overflow (silent, F-21b)
    paused --> abandoned: abandon
    interrupted --> running: resume/retry (orphan-process hazard, F-03)
    interrupted --> abandoned: abandon
    failed --> running: resume/retry (so "failed" is not terminal)
    failed --> abandoned: abandon
    succeeded --> [*]
    cancelled --> [*]
    abandoned --> [*]
```

Audit results:

- **Missing transitions:** stranded-`running` (ready/pending work, no claims) → anything: no owner, no health signal, no advertised action (F-04). Queued → running on blocker completion: absent. `cleanup_failed` → resolved: manual only, undocumented.
- **Ambiguous:** `paused` flows through `complete_node`'s terminal branch (`store.py:1893-1905`) though nonterminal; `failed` is terminal for capacity/cleanup but re-enterable.
- **Polling-dependent:** *every* forward transition after admission depends on some process electively calling `advance`; there is no event-driven or scheduled driver.
- **Not crash-safe:** decision→advance intent (F-04); journal-append→SQLite-update (F-10); quarantine-move→row-delete (F-16); second journal append in `complete_node` under quota (F-21a).
- **Actions in wrong states:** `next_actions` omits `resume` where it is the rescue, offers `provide-input`/`cleanup` that 404, never offers `retry` (F-12); abandon accepted for a run that has resumed (F-20).
- **Contradictions possible:** `status=running` + `health=healthy` + `current_nodes=[]` + no pending interaction + no progress = the incident's exact projection; `progress 10/11` renders identically for "executing" and "stranded"; SQLite status can contradict `run.json` (F-10).

---

## Skill-orchestration audit

Intent → safe command flow (target contract; ✗ marks where today's surface breaks it):

| Natural-language intent | Safe canonical flow | Breaks today at |
|---|---|---|
| "run the laptop diagnostic" | `showcase list --json` (once, only if identity unsure) → `showcase preflight laptop-diagnostic --json` (must list required inputs ✗ F-14) → ask for `--symptom` → `showcase run laptop-diagnostic --symptom … --idempotency-key <stable> --json` (✗ template omits key, F-06) → report run_id | F-06, F-14 |
| "what's the status" | `workflow status <run_id> --json` once; summarize deltas | payload unbounded; `elapsed_ms` null (F-13) |
| "why is it stuck" | `status` → if nonterminal + no current nodes + no interaction → `events <id> --tail` (✗ returns head, F-13) → report stall + advise `resume` | health lies (F-04) |
| "I approved it in the app" | `status` → if decision recorded and run progressing, report; if stranded, `resume <id> --json` | worktree fixes approve; reject/others strand (F-04) |
| "approve it" | refuse — decision is the user's; explain Desktop/CLI options | correctly prose-guarded; no runtime replay-guard needed because CAS exists |
| "run it again" | confirm intent → new key → `run …` | ✗ nothing distinguishes retry-transport from second-intent (F-06) |
| "clean up old runs" | `cleanup --dry-run --json` → present impact → require explicit user confirmation → `cleanup --execute` | ✗ bare cleanup deletes (F-02) |

Where the skill can still guess/duplicate/over-poll despite Tasks 2–3 prose: parallel tool calls (no runtime single-flight), keyless duplicate starts (F-06), speculative flags taught by unimplementable instructions (F-11/F-12), re-list loops after suggestion-free not-found errors (F-13/F-25), poll storms because no stall/health signal terminates polling (F-04).

Rules that require **runtime** enforcement, not prose: idempotency-by-default (F-06), JSON error envelope + exit codes (F-13), continuation-after-decision (F-04/F-05), stall classification (F-04), `next_actions` integrity (F-12), destructive-default inversion (F-02).

Proposed minimal generic skill contract (compact enough to be followed):
1. Resolve identity: exact ID from the user, or one `list --json`; never shorten.
2. One start per intent, always with `--idempotency-key`; persist `run_id` + key in your reply.
3. One command at a time; never mask exit codes; nonzero exit = read stderr JSON envelope, do not retry blind.
4. After any write, read the returned JSON's `status/state_version/next_actions` before acting again.
5. Poll `status` at most every N seconds and stop on: terminal, pending interaction, `health != healthy`, or two unchanged `state_version` reads → then `events` once and report.
6. Human decisions are human-only; if the user says they acted elsewhere, `status` first, then only an advertised `next_action`.
7. Only advertised `next_actions`; if an action isn't advertised, say so instead of improvising.

Showcase-specific rules that must NOT leak into the generic skill: canonical `laptop-diagnostic` and `--symptom` mapping, fictional/offline preflight claims, artifact names (`diagnostic-report.json/md`, `remediation-plan.md`), "no remediation runs" reminder, confirmation-token flow for ai-extensions/scheduling.

---

## Evidence and observability audit

- **Stored:** per run under `$HERMES_HOME/workflows/runs/<workflow>/<run_id>/` — `definition.yaml`, `policy.yaml`, inputs snapshot + manifests, `run.json` (atomic tmp+fsync+replace), `events.jsonl` (append+fsync, embedded projection + sha256 per event), `nodes/<id>/<attempt>/stdout.txt|stderr.txt|output.*`, `nodes/<id>/approval/output.txt`, loop inputs, `artifacts/`; SQLite `runs`/`admission_events`(capped 1000)/`worker_claims`; `node-sessions.sqlite3`; trust at `workflow/trust.json` (`store.py:246-288, 1012-1142`; executors cited in F-17).
- **Retention:** manual CLI cleanup only; 7 days is a selection default, not automatic; quarantine-then-rmtree with known dangling-state races (F-16); no archive tier.
- **Redaction boundary:** key/pattern sanitization at journal/projection/API layers with two divergent strip-lists; **no redaction at rest** for process output/loop input (F-17).
- **UI exposure today:** counts only — no timeline render, no log viewer, no artifact access (`run-inspector.tsx:41-44`); hence no current injection surface, and Task 5/7 create one that must ship with the design's inert-render/containment/digest-revalidation rules (they are well specified; hold them).
- **Missing evidence:** first-attempt output after lease expiry (F-03); pre-node-start failures land in admission events which the Desktop never shows; `elapsed_ms` never computed; `last_semantic_progress_at` unrendered.
- **Corruption behavior:** run.json quarantine+rebuild works and is tested; torn journal tail is fatal and contagious (F-09); DB loss is catastrophic (F-01).
- **Recommended bounded support export:** a `workflow export-evidence <run_id>` producing a zip of sanitized projection + journal + attempt metadata + redacted output excerpts + digests (no raw artifacts by default) — satisfies "support bundle" without turning Desktop into a log dump; add to plan Task 8 or 10.

## Notification audit

| Component | Status |
|---|---|
| Event source | RunStore transitions (durable, good) — **unspecified how observers subscribe** (no hook exists in store) |
| Durable outbox | absent; Task 9 says "outbox/receipt beside workflow state" — schema unspecified |
| Dedup identity | design defines `(run_id, state_version, kind, destination)` — adequate; not implemented |
| Delivery worker owner | **unspecified — the critical gap** (F-08); no long-lived workflow process exists |
| Retry policy | unspecified; kanban watcher precedent (cursor rewind, 3-failure drop) available |
| User/channel resolution | impossible today (no return-route capture, F-07); cron owner mapping undefined (F-24) |
| Dismissal vs unresolved | design correct (dismissal never changes run state); needs durable per-destination receipt, unspecified |
| Restart behavior | unspecified; must be cursor/receipt-based, not memory |
| Failure visibility | unspecified (where does the operator see "notification delivery failing"?) |
| Required configuration | correctly placed in `config.yaml` per design; keys undefined |

Every "unspecified" row above must be resolved in the Task 9 amendment before implementation.

---

## Test-gap matrix

| Risk | Existing test | Why insufficient | Required test | Platform |
|---|---|---|---|---|
| Decision→continuation crash | none | Task 1 tests cover happy path + concurrency only [worktree] | kill between CAS and advance; sweeper recovers | Linux+Windows |
| Desktop reject/retry/reconcile continuation | none | worktree covers approve only | per-action continuation e2e | Linux |
| Inline-continuation timeout | worktree e2e proves full graph runs in one POST — the hazard, not the guard | bound response time; slow-node approval returns fast | Linux |
| Queued promotion / retry wake without CLI | none | promotion only tested inside advance | blocker completes → queued starts with no CLI call | Linux+Windows |
| Lease expiry with live process | `test_crash_recovery.py` (dead owners only) | orphan-process path untested | live-orphan expiry → retry gated, evidence kept | Linux+Windows |
| DB-loss orphan sweep | none | F-01 unguarded | corrupt/empty DB → refuse sweep | all |
| Cleanup CLI default | `test_retention.py` (store-level) | CLI inversion untested | bare `cleanup` deletes nothing | all |
| Torn journal tail | `test_crash_recovery.py` (semantic corruption only) | torn-line path fatal | mid-line truncation → isolated unreadable card | all |
| SQLite/run.json divergence | none | healthy-load resync missing | crash between writes → converges on load | all |
| Schema migration | none (`grep migrat` empty) | Task 4 adds columns onto untested base | pre-migration fixture DB upgrade | all |
| Real auth middleware on plugin routes | bare-app TestClient | 401/404 gates untested | web-server-factory integration test | Linux |
| Windows dir-replace sharing violations | none | constructor can throw | open-handle cleanup/reconcile test | **native Windows** |
| Concurrency core on Windows | 7/54 files in matrix | msvcrt/locking real behavior unproven | add store/scheduler/approval race files to matrix | **native Windows** |
| `next_actions` contract | none | advertised actions 404 | iterate emissions → implemented+rendered | Linux |
| Skill behavior | phrase assertions only | cannot detect noncompliance | UAT-transcript replay against real CLI; runtime guards asserted | Linux+Windows |
| Notification outbox lifecycle | none | Task 9 unstarted | restart/dedup/rewind/no-owner cases | Linux+Windows |
| Evidence redaction at read | none | Task 5 unstarted | secret-bearing stdout served redacted | all |
| Long-poll DoS/threadpool | none | shared-fate with chat | N long-polls don't starve chat endpoint | Linux |

---

## Plan amendments

Exact edits, keyed to the plan's tasks:

1. **Task 1 (continuation)** — add to Step 2: "The continuation boundary MUST be invoked by every mutating Desktop action (`reject`, `resume`, `retry`, `reconcile`, and future `provide-input`), not only `approve`; add per-action regression tests." Add Step 2b: "Desktop continuation is bounded: apply the decision, schedule continuation on a plugin-owned worker (or a single bounded advance cycle), and return the immediate post-decision projection within 5 s; the full-graph-inline behavior in the current worktree commit is a defect to fix, not the contract." Add Step 2c: "Fix `_already_decided` null-interaction matching (F-19) in the same commit." Acceptance: response-time bound test + reject-rework continuation test.
2. **New Task 1.5 (continuation ownership + stall health)** — insert before Task 4: "Add a startup + periodic continuation sweep in the long-lived backend (pattern: the existing cron ticker in `web_server.py:136-154`): promote due queued runs, wake due retries, advance decision-complete runs, expire stale claims, re-sync SQLite↔run.json (F-10). Implement `health="stalled"` per the design's stall classification using `last_semantic_progress_at`, and emit `resume` in `next_actions` for it." This is the design's §Runtime rule made real; without it Tasks 6–9 render fiction.
3. **New Task 1.6 (evidence-safety criticals)** — F-01 sweep guard, F-02 cleanup `--execute`, F-03 orphan ledger + reconcile gating, F-09 torn-tail isolation, F-20 abandon TOCTOU, F-21a claim release. Each with the failing regression first. These precede everything user-visible.
4. **Task 2/3 (skills)** — replace "assert the generic skill requires…" phrase tests with: (a) template-integrity tests (every start template carries `--idempotency-key`; no template references an unimplemented flag); (b) the UAT-transcript replay test; (c) fix showcase preflight to enumerate required inputs. Add the CLI error-envelope work (F-13) as a prerequisite subtask: JSON error envelope, catch `RuntimeError`, `doctor` nonzero on blocking, real `tail`, did-you-mean candidates.
5. **Task 4 (trigger/archive persistence)** — add: `--trigger-source`/`--operator-scope`/`--return-route` CLI flags with the return-route stored **outside** the start digest; migration tests from a fixture DB; dangling-row tolerance in list queries; explicit terminal-status table resolving the `failed`-is-retryable ambiguity for archive/cleanup eligibility.
6. **Task 5 (evidence APIs)** — add acceptance: single shared sanitizer applied at the read boundary; auth exercised through the real web-server middleware (F-23); scope model resolved per F-11 **before** artifact bytes are served; long-poll converted to tail-offset reads with per-token concurrency caps (F-18).
7. **Task 7 (inspector)** — add acceptance items from F-22 (health/last-progress rendering, itemized inbox, verbatim interaction, aria-live/aria-busy) and the `next_actions` integrity contract test (F-12).
8. **Task 8 (archive/cleanup UX)** — cleanup flow must build on the corrected F-02 CLI semantics; add quarantine re-sweep; add the bounded support-bundle export.
9. **Task 9 (notifications)** — specify: outbox schema, the two delivery owners (gateway watcher clone of the kanban pattern; Desktop-backend poller), restart/receipt semantics, no-owner fallback, failure visibility, and the three alternation-safe chat delivery patterns by name. Add the "Desktop fully closed" delivery test.
10. **Task 10 (ledger/docs)** — add ledger entries for: the continuation sweeper (if it touches `web_server.py`), CLI flag additions, and the shared sanitizer; create `hermes-agent/CLAUDE.md` (pair rule); commit `docs/reviews/`.
11. **Task 11/12 (gates/release)** — add to the gate list: the Windows-matrix expansion (F-15), migration tests, and the F-01/F-02/F-03 regressions; UAT step 6 must include a **reject→rework→re-approve** pass and a **Desktop-closed notification** pass, not only the approve-happy-path; keep "update the existing installation, not reinstall" (already present — good).

## Release blockers

Before **merging to base**:
1. F-01 (DB-loss evidence wipe), F-02 (cleanup default), F-03 (orphan at-most-once) — with failing-first regressions.
2. F-04/F-05 (continuation ownership: all Desktop actions continue; bounded response; sweeper for queued/retry/stranded; stall health + `resume` advertised).
3. F-09/F-10 (torn journal isolation; SQLite resync).
4. F-13 CLI error envelope + F-06 idempotency default (the skill contract is unenforceable without them).
5. F-12 `next_actions` integrity; F-19 already-decided matching; F-20 abandon TOCTOU; F-21a claim release.
6. F-26 ledger entries for every upstream-owned touch, in-commit.

Before **restamping OTTO and Loop24**:
7. All base gates green including `generate <brand> --check` 8/8, brand-neutral sources (`PRODUCT_CLI`, `Co-worker Agent` version string), and the skill/doc branding rules (`loop24` in user examples).

Before **publishing a release**:
8. F-08 notification substrate delivering the approval-wait case end-to-end on at least Desktop-native + one messaging channel; F-07 trigger provenance real for chat/cron/desktop; F-11 scope model resolved; F-18 read-path bounds.

Before **claiming Windows update-path success**:
9. F-15: Windows CI matrix expanded to the concurrency/retention/API core; open-handle dir-replace tests; migration-from-fixture on Windows; update-in-place (not reinstall) verified by the Windows user per plan Task 12 Step 4.

Before **claiming full workflow showcase UAT success**:
10. The amended UAT script (approve + reject/rework + Desktop-closed notification + archive/unarchive + dry-run-first cleanup + controlled failure) executed by the user on the updated install, with run IDs and evidence recorded; no provisional claims.

## Recommended implementation order

1. F-01, F-02, F-03, F-09, F-10, F-20, F-21a — evidence-safety and crash-convergence criticals (small, testable, independent).
2. Task 1 completion per amendment (all actions, bounded, F-19) + Task 1.5 sweeper + stall health. This alone retires the production incident class.
3. F-13 CLI envelope + F-06 idempotency defaults + F-12 `next_actions` integrity (makes the runtime skill-followable).
4. Tasks 2–3 skills (now enforceable) with replay tests.
5. Task 4 trigger/archive persistence (with F-07 flags, migrations tested) — provenance becomes real.
6. Task 5 evidence APIs (with F-11 scope resolution, F-17 sanitizer, F-23 auth integration).
7. Tasks 6–7 Desktop inspector/recovery (with F-22 items).
8. Task 8 archive/history/cleanup UX; support bundle.
9. Task 9 notifications per the amended specification.
10. Tasks 10–12 ledger, gates (F-15 Windows expansion), release, UAT.

## Positive findings

Preserve these — they are genuinely strong:

- **The RunStore CAS core.** Decision CAS with interaction identity and pause generations (`store.py:2721-2917`), claim/complete attempt fencing (`stale node completion`), cross-process file locks with correct Windows byte-lock usage, two-phase admission publication with restart reconciliation of *reservations*, and fsynced atomic projection writes are well engineered and well tested (100-thread admission race, cross-process approve/reject race, monotonic/wall-clock lease reasoning).
- **Admission idempotency and overlap policy** are exactly the right substrate; only the defaults and callers are wrong (F-06/F-07), not the mechanism.
- **The prior review's Desktop remediation held**: 409 refetch, duplicate-suppression, stale-disable, virtualization, reduced-motion, and the ledger discipline that recorded them (F-28).
- **Layered sanitization at the journal/API boundary** and the `_sanitize_diagnostic` pattern scrubbing are the right shape; they need unification, not replacement.
- **The trust model** (content-digest, profile-owned, fail-closed, TOCTOU re-read on `trust`) and the showcase's digest-manifest/forbidden-content screening are solid and should not be weakened by the operator-experience work.
- **The design document's product judgment** is largely correct: RunStore as sole authority, notifications as projections, actions from `next_actions`, archive-vs-cleanup separation, node-count-is-not-ETA honesty, no permanent core tool, alternation/cache preservation, and the explicit rejection of a monolithic workflow model tool. The kanban notifier is an in-repo proof the notification design is buildable.
- **The plan's shape** — failing-regression-first per task, separate commits, ledger gates before merge, update-path (not reinstall) Windows UAT, and "do not claim fixed until the user supplies production evidence" — is the right discipline; the amendments above tighten scope and ordering rather than changing the method.

---

*Prepared as a review artifact only. No production code, refs, or configuration were modified; the only repository write is this document. Evidence gathered from the shared checkout at `7d0039b6a` and read-only inspection of `.worktrees/workflow-operator-experience` at `43edb4d4b`.*
