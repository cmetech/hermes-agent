# Workflow Orchestration Production Remediation Plan

> **For implementation agents:** Execute only after maintainer approval. Use
> `superpowers:executing-plans`, test-first vertical slices, exact-path staging,
> and the repository's `scripts/run_tests.sh`. Do not merge candidate commits
> `a9ccb7e91` or `43edb4d4b` wholesale.

**Goal:** Make portable workflows safe and operable across CLI, Desktop,
Gateway, cron, background agents, API, and chat, with durable continuation,
deterministic machine contracts, inspectable evidence, and bounded recovery.

**Architecture:** Base Hermes adds only a generic blocking plugin background-
service lifecycle hosted by web/Desktop and Gateway. The workflow plugin is its
first consumer and owns election, wakes, scheduling, recovery, evidence,
notifications, and all workflow state. RunStore remains authoritative. REST
mutations are bounded state+wake commits; the coordinator executes later.

**Status:** Proposed after adversarial review; implementation and release are
blocked pending maintainer approval.

**Normative designs:**

- `docs/design/portable-workflow-orchestration.md`
- `docs/superpowers/specs/2026-07-18-plugin-background-services-workflow-coordination-design.md`
- `docs/superpowers/specs/2026-07-18-workflow-orchestration-operator-experience-design.md`
- `docs/reviews/2026-07-18-workflow-orchestration-adversarial-review-reconciliation.md`

## Global invariants

- No permanent model-facing workflow tool.
- No workflow imports in `hermes_cli/plugin_services.py`,
  `hermes_cli/plugins.py`, `hermes_cli/web_server.py`, or `gateway/run.py`.
- No `AIAgent`, prompt, message, tool, provider credential, or model context in
  the generic service API.
- No non-secret `HERMES_*` configuration; user settings use `config.yaml`.
- No background admission without a fresh durable coordinator heartbeat.
- No uncertain outward-effect replay.
- No evidence deletion based on a missing/empty/corrupt/replaced index.
- No workflow execution in Desktop HTTP mutations.
- Prompt prefix bytes and strict role alternation remain unchanged.
- Every phase begins with a failing behavioral test and ends with focused real-
  path verification plus an atomic commit. Mocks supplement but never replace
  filesystem, SQLite, process, lifecycle, restart, or mounted-auth tests.

## Phase 1 — Safety-critical data and execution protections

**Purpose:** Remove destructive and duplicate-effect hazards before adding a
long-lived execution owner.

### 1.1 Fail-closed admission reconciliation

**Files:**

- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/admission.py`
- Modify: `tests/plugins/workflow/test_admission.py`
- Modify: `tests/plugins/workflow/test_fault_injection.py`
- Modify: `tests/plugins/workflow/test_store.py`

- [ ] Add failing tests for deleted, empty, replaced, corrupt, partially
  migrated, and status-inconsistent admission SQLite databases while valid run
  directories/journals exist.
- [ ] Define corroboration rules: complete journal/run metadata plus matching
  digest can rebuild a projection; uncertainty produces `repair_required` and
  preserves the directory.
- [ ] Replace orphan deletion in `RunStore._reconcile_admission` with evidence-
  preserving quarantine/repair records. Deletion is never a reconciliation
  operation.
- [ ] Make capacity/admission fail closed when active-state authority cannot be
  corroborated.
- [ ] Record repair decisions and hashes as durable evidence.

### 1.2 Cleanup defaults and deletion barrier

**Files:**

- Modify: `plugins/workflow/cli.py`
- Modify: `plugins/workflow/store.py`
- Modify: `tests/plugins/workflow/test_retention.py`
- Modify: `tests/plugins/workflow/test_cli.py`

- [ ] Reproduce that bare `workflow cleanup` currently deletes.
- [ ] Change bare cleanup to preview-only; remove the safety-inverting
  `--dry-run` store_true behavior.
- [ ] Add explicit `--execute` and confirmation-token support bound to preview
  candidates, state versions, index integrity, and expiry.
- [ ] Block execution for live claims/readers, reconciliation-required runs,
  uncertain indexes, or changed previews.
- [ ] Quarantine before final deletion and persist cleanup history.

### 1.3 Lease, process, and replay safety

**Files:**

- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/executors/base.py`
- Modify: `tests/plugins/workflow/test_crash_recovery.py`
- Modify: `tests/plugins/workflow/test_approval_races.py`
- Modify: `tests/plugins/workflow/test_shutdown_recovery.py`
- Modify: `tests/plugins/workflow/test_process_lifecycle_soak.py`

- [ ] Persist executor ID, PID, process-start identity, owner/epoch, effect
  classification, and evidence paths before outward work.
- [ ] Keep recovery identity after claim expiry; stale completions remain
  evidence even when fenced from changing state.
- [ ] Prove termination and identity match before automatic replay.
- [ ] Route unknown/mismatched/live outward-effect attempts to
  `reconciliation_required`; allow automatic interruption only for proven-safe
  cases.
- [ ] Make abandon/cancel eligibility and terminal CAS atomic; perform process
  control outside SQLite and record its result separately.

### 1.4 Journal and projection durability

**Files:**

- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/locks.py`
- Modify: `tests/plugins/workflow/test_fault_injection.py`
- Modify: `tests/plugins/workflow/test_run_queries.py`
- Modify: `tests/plugins/workflow/test_performance_bounds.py`

- [ ] Add torn-tail, partial-write, fsync interruption, stale-projection,
  directory-replace, lock-timeout, and Windows rename/locking tests.
- [ ] Introduce recoverable journal framing/checksum and preserve every complete
  frame when only the final frame is torn.
- [ ] Version/check the SQLite projection and rebuild only from corroborated
  authoritative evidence.
- [ ] Make list/capacity filters surface storage degradation rather than lie.

**Phase 1 verification:**

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_retention.py \
  tests/plugins/workflow/test_fault_injection.py \
  tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_shutdown_recovery.py \
  tests/plugins/workflow/test_process_lifecycle_soak.py \
  tests/plugins/workflow/test_run_queries.py -q
```

**Exit:** no evidence-loss path, destructive default, abandoned live executor,
or automatic uncertain-effect replay remains. Commit as one or more safety-only
commits before lifecycle work.

## Phase 2 — Generic plugin background-service lifecycle

**Purpose:** Add the smallest enforceable generic host contract, with the
workflow registration as the immediate consumer but no workflow host imports.

### 2.1 Protocol, registration, and supervisor

**Files:**

- Create: `hermes_cli/plugin_services.py`
- Modify: `hermes_cli/plugins.py`
- Create: `tests/hermes_cli/test_plugin_background_services.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

- [ ] Write conformance tests for host filtering, registration attribution,
  duplicate rejection, partial-registration rollback, factory failure, `run`
  failure/early return, health failure, and sibling isolation.
- [ ] Implement the selected `run(stop_event)` protocol, sanitized snapshots,
  one supervisor thread per service, and one aggregate shutdown deadline.
- [ ] Check safe mode at discovery and host start; invoke zero factories.
- [ ] Interlock `discover_and_load(force=True)` with active generations.
- [ ] Stop all old services before registry clear; abort reload and construct no
  replacements after any stop timeout.
- [ ] Record the shared-file rationale, consumer, tests, merge guidance, and
  removal conditions in the ledger.

### 2.2 Web/Desktop host

**Files:**

- Modify: `hermes_cli/web_server.py`
- Create: `tests/hermes_cli/test_web_server_plugin_services.py`

- [ ] Use real FastAPI lifespan tests proving `web` services start after
  discovery, failure does not block readiness/chat APIs, and stop precedes host
  resource teardown.
- [ ] Bind one service host for dashboard and headless `serve`; place sanitized
  local health in `app.state` without making it cross-process authority.
- [ ] Preserve existing cron ticker behavior; do not migrate it in this phase.

### 2.3 Gateway host

**Files:**

- Modify: `gateway/run.py`
- Create: `tests/gateway/test_plugin_background_services.py`

- [ ] Use a real thread service in Gateway lifecycle tests.
- [ ] Start applicable services after discovery/required readiness; failure is
  logged/visible but Gateway still starts.
- [ ] Stop services before plugin/resource teardown and within the existing
  bounded shutdown flow.
- [ ] Assert source and import graph contain no workflow-specific dependency.

**Phase 2 verification:**

```bash
scripts/run_tests.sh \
  tests/hermes_cli/test_plugin_background_services.py \
  tests/hermes_cli/test_web_server_plugin_services.py \
  tests/gateway/test_plugin_background_services.py -q
```

**Exit:** generic lifecycle conformance, safe mode, failure isolation, bounded
shutdown, and no-overlap reload pass with real threads in both hosts. Commit
base lifecycle separately for upstream salvage, but do not merge/release that
infrastructure without the Phase 3 workflow consumer. Phase 2 and Phase 3 form
one merge-readiness unit.

## Phase 3 — Workflow coordinator

**Purpose:** Make continuation durable and remove scheduling ownership from
requests and incidental foreground processes.

**Files:**

- Create: `plugins/workflow/coordinator.py`
- Create: `plugins/workflow/coordinator_store.py`
- Modify: `plugins/workflow/__init__.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Create: `tests/plugins/workflow/test_coordinator.py`
- Create: `tests/plugins/workflow/test_coordinator_multiprocess.py`
- Modify: `tests/plugins/workflow/test_scheduler.py`
- Modify: `tests/plugins/workflow/test_parallel_scheduler.py`
- Modify: `tests/plugins/workflow/test_retry.py`
- Modify: `tests/plugins/workflow/test_desktop_api.py`
- Modify: `tests/plugins/workflow/test_operator_e2e.py`

### 3.1 Election, heartbeat, and health

- [ ] Add failing two-process tests for one leader/one standby, leader kill,
  lease expiry, epoch fencing, SQLite lock contention, clock gap, and Windows-
  compatible takeover.
- [ ] Persist coordinator identity, PID/start token, host kind, epoch, heartbeat,
  lease, sweep cursor, and last progress in workflow-owned SQLite.
- [ ] Expose durable `healthy`, `standby`, `unavailable`, `degraded`, and stale-
  heartbeat facts through workflow status/doctor.

### 3.2 Durable wakes and complete continuation

- [ ] Add durable wake rows/generation in the same protected mutation as admit,
  approve, reject, provide-input, resume, retry, reconcile, cancel, and lane-
  releasing terminal/archive/cleanup transitions.
- [ ] Add crash injection after transaction commit but before local signal;
  restart must consume the wake.
- [ ] Make local condition notification a latency optimization only.
- [ ] Delete/reject any REST path that calls `RunScheduler.advance`; mutations
  return updated state, wake acknowledgement, and coordinator warning promptly.

### 3.3 Bounded sweep, retries, queues, and recovery

- [ ] Implement cursor/time/item-bounded sweeps with fairness between durable
  wakes and periodic recovery.
- [ ] Promote queued runs when a run pauses, waits retry, interrupts, completes,
  fails, cancels, or otherwise safely releases its execution lane.
- [ ] Requeue approved/input/resumed/retried/reconciled work fairly if another
  run owns the lane.
- [ ] Wake due retries without an in-memory sleep/timer.
- [ ] Recover stranded running/pending-final-node states and classify stalls by
  meaningful progress, lease, log, and coordinator health.
- [ ] Ensure duplicate wakes/promotions do not duplicate nodes or outward work.

### 3.4 Admission failure policy

- [ ] Reject background/`--no-wait` admission transactionally when no fresh
  healthy leader exists; create no run directory.
- [ ] Keep explicit foreground execution available for supported CLI commands.
- [ ] If the coordinator disappears after admission, preserve the run/evidence,
  report unavailable/stalled health, and recover under a later leader.

**Phase 3 verification:**

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_scheduler.py \
  tests/plugins/workflow/test_parallel_scheduler.py \
  tests/plugins/workflow/test_retry.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_operator_e2e.py -q
```

**Exit:** host/process restart, every interaction, retry wake, and queued
promotion continue without foreground ownership; HTTP never executes graph work.

## Phase 4 — CLI and API machine contracts

**Files:**

- Modify: `plugins/workflow/cli.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Create: `plugins/workflow/actions.py`
- Create: `plugins/workflow/machine_contract.py`
- Modify: `tests/plugins/workflow/test_cli.py`
- Modify: `tests/plugins/workflow/test_doctor.py`
- Modify: `tests/plugins/workflow/test_catalog_cli.py`
- Modify: `tests/plugins/workflow/test_desktop_api.py`
- Modify: `tests/plugins/workflow/test_run_queries.py`

- [ ] Freeze schema-versioned success/error envelopes and exit-code table; add
  parser tests for every command's success, validation, not-found, auth,
  conflict, unavailable, blocking-doctor, action failure, and internal error.
- [ ] Catch CAS/runtime errors as typed envelopes without tracebacks on stdout.
- [ ] Make doctor nonzero on blocking findings and mode-aware.
- [ ] Fix `events --tail` to return newest N in display order.
- [ ] Generate handler validation, REST metadata, and `next_actions` from one
  authoritative action table; test every state/action pair.
- [ ] Require current interaction ID and state version for approval/input.
- [ ] Make preflight publish exact supported syntax, identifier kinds, action
  names, coordinator state, and contract version.
- [ ] Require/derive deterministic idempotency for JSON, non-interactive, and
  background starts; remove random retry identity.
- [ ] Define prompt boundedness and prompt return for unavailable/background
  failure; never accept work without an owner.

**Verification:**

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_doctor.py \
  tests/plugins/workflow/test_catalog_cli.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_run_queries.py -q
```

**Exit:** machines never need syntax probing or stderr parsing; every advertised
action succeeds for its authoritative state or returns a documented conflict.

## Phase 5 — Reusable workflow skill contract and showcase skill

**Files:**

- Modify: `skills/productivity/workflow/SKILL.md`
- Modify: `skills/productivity/workflow-showcase/SKILL.md`
- Modify: `plugins/workflow/showcase.py`
- Create: `tests/skills/test_workflow_operator_behavior.py`
- Modify: `tests/plugins/workflow/test_showcase_catalog.py`
- Modify: `tests/plugins/workflow/test_showcase_offline_e2e.py`
- Modify: `apps/desktop/src/lib/workflow-skill-command.test.ts`

- [ ] Build a behavioral harness that captures exact argv and feeds real JSON
  results; first demonstrate current duplicate starts, wrong IDs, masked
  failures, nonexistent flags, and endless/unowned polling.
- [ ] Rewrite reusable guidance to resolve the branded CLI once, preflight,
  retain stable idempotency, mutate one command at a time, stop at human gates,
  and interpret coordinator unavailable/no-progress/stall/conflict.
- [ ] Ban failure-masking and approval piping in examples and behavior.
- [ ] Keep showcase discovery/run syntax explicit; use returned run ID for every
  general lifecycle action.
- [ ] Test retrying one semantic skill intent creates one run.
- [ ] Test the skill reports observed evidence/hashes and never promises
  completion before authoritative terminal state.

**Verification:**

```bash
scripts/run_tests.sh \
  tests/skills/test_workflow_operator_behavior.py \
  tests/plugins/workflow/test_showcase_catalog.py \
  tests/plugins/workflow/test_showcase_offline_e2e.py -q
```

```bash
cd apps/desktop && npx vitest run src/lib/workflow-skill-command.test.ts
```

**Exit:** generic and showcase guidance are separated and behaviorally aligned
with the real machine contract.

## Phase 6 — Provenance, evidence, and authorization

**Files:**

- Create: `plugins/workflow/provenance.py`
- Create: `plugins/workflow/evidence.py`
- Create: `plugins/workflow/sanitize.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `plugins/workflow/showcase.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify source adapters for chat/background/cron/API admission identified by
  code search; do not add a new core workflow path
- Create: `tests/plugins/workflow/test_provenance.py`
- Create: `tests/plugins/workflow/test_evidence_api.py`
- Modify: `tests/plugins/workflow/test_operator_scope.py`
- Modify: `tests/plugins/workflow/test_desktop_api.py`
- Modify: `tests/plugins/workflow/test_security_boundaries.py`

- [ ] Persist canonical `desktop`, `chat`, `background_agent`, `cron`, `cli`,
  and `api` source, verified actor, source instance, intent key, and authenticated
  return route; show legacy absence as `unknown`.
- [ ] Trace every production admission writer and add cross-surface tests proving
  none hardcodes `cli` incorrectly.
- [ ] Define bounded cursor APIs for timeline, interactions, attempts,
  stdout/stderr, outputs, artifacts/hashes, process/coordinator identity,
  recovery, cleanup, and notification evidence.
- [ ] Implement one sanitizer for API/notification projections with explicit
  truncation; preserve labeled sensitive raw evidence at rest.
- [ ] Derive principal/profile/maximum scope from real middleware. A header may
  narrow but never grant authority. Document local CLI admin separately.
- [ ] Mount real FastAPI auth/lifespan tests for allowed, denied, cross-profile,
  raw-artifact, and forged-header cases.

**Verification:**

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_provenance.py \
  tests/plugins/workflow/test_evidence_api.py \
  tests/plugins/workflow/test_operator_scope.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_security_boundaries.py -q
```

**Exit:** provenance is server truth, evidence supports every inspector claim,
and authorization is exercised through the actual boundary.

## Phase 7 — Desktop operator experience

**Files:**

- Modify: `apps/desktop/src/app/workflows/adapter.ts`
- Modify: `apps/desktop/src/app/workflows/store.ts`
- Modify: `apps/desktop/src/app/workflows/index.tsx`
- Modify: `apps/desktop/src/app/workflows/attention-inbox.tsx`
- Modify: `apps/desktop/src/app/workflows/run-inspector.tsx`
- Create focused components under: `apps/desktop/src/app/workflows/components/`
- Modify: `apps/desktop/src/app/workflows/adapter.test.ts`
- Modify: `apps/desktop/src/app/workflows/index.test.tsx`
- Modify: `apps/desktop/src/app/workflows/workflow-operations.e2e.test.tsx`

- [ ] Add typed API models for provenance, lifecycle/health separation,
  coordinator/lease status, evidence cursors, and authoritative actions.
- [ ] Render origin icons from server provenance; show active, attention,
  completed/stopped, history, and archive without client-invented state.
- [ ] Build Overview, Graph, Timeline, Attempts, Logs, Outputs, Artifacts, and
  recovery sections from bounded evidence APIs.
- [ ] Render only server-valid actions with version/interaction CAS and required
  confirmations: approve/reject, provide-input, retry, resume, reconcile,
  cancel, archive, restore, preview cleanup, execute cleanup.
- [ ] Show mutation+wake acknowledgement separately from continuation; surface
  coordinator unavailable and conflicts without impairing the terminal/chat.
- [ ] Replace independent one-second full reads with visible-page bounded
  summaries/cursors and selected-run refresh; pause hidden cosmetic polling.
- [ ] Add keyboard/focus, `aria-live`, non-color health, reduced motion, and
  laptop-width tests.
- [ ] Assert every mutation returns within a bounded test deadline while a
  deliberately slow workflow completes later under the coordinator.

**Verification:**

```bash
cd apps/desktop && npx vitest run \
  src/app/workflows/adapter.test.ts \
  src/app/workflows/index.test.tsx \
  src/app/workflows/workflow-operations.e2e.test.tsx
```

**Exit:** Desktop answers the incident questions, offers only safe actions, and
never becomes execution authority or a second chat surface.

## Phase 8 — Archive, history, retention, and cleanup UX

**Files:**

- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify Desktop workflow files from Phase 7
- Modify: `tests/plugins/workflow/test_retention.py`
- Modify: `tests/plugins/workflow/test_desktop_api.py`
- Modify Desktop workflow tests

- [ ] Persist reversible archive metadata/version independently of lifecycle.
- [ ] Implement restore to History, not execution.
- [ ] Define terminal board aging as visibility policy only; evidence remains.
- [ ] Expose cleanup preview/history and matching explicit execution in CLI/API.
- [ ] Test changed preview, corrupt index, live reader/claim, uncertain effect,
  pending notification dependency, partial quarantine failure, restore, and
  cleanup retry.
- [ ] Verify archived/aged runs remain queryable and notifications/actions do
  not silently mutate lifecycle.

**Verification:** focused Python retention/API tests plus Desktop workflow tests.

**Exit:** Active, attention, terminal, history, archive, restore, retention, and
destructive cleanup have distinct durable semantics.

## Phase 9 — Durable notifications

**Files:**

- Create: `plugins/workflow/notifications.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/coordinator.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Add plugin-owned Gateway delivery adapter using an existing generic outbound
  seam; if none suffices, stop for a generic design amendment rather than import
  workflow code into Gateway core
- Modify Desktop workflow notification projection and Electron-native bridge
  only through existing generic renderer/main seams
- Create: `tests/plugins/workflow/test_notifications.py`
- Create: `tests/plugins/workflow/test_notification_delivery.py`
- Modify Desktop workflow tests

- [ ] Add transactional outbox rows for approval/input, failure, stalled,
  configurable completion, cancellation, and reconciliation-required.
- [ ] Deduplicate on transition/state-version/destination and implement leases,
  retry/backoff, receipts, dead-letter, explicit retry, and unresolved attention.
- [ ] Make coordinator the outbox policy owner; destination adapters only
  project/send and record results.
- [ ] Preserve pending attention across closed Desktop, Gateway/web restart,
  delivery timeout, duplicate consumer, and offline destination.
- [ ] Ensure Desktop dismissal changes presentation only.
- [ ] Deliver chat/Gateway messages through an alternation-safe existing path;
  assert no synthetic user message or system/tool mutation.
- [ ] Fault after transport send but before receipt and prove dedup/reconciliation
  prevents uncontrolled duplicate delivery.

**Verification:** notification unit, real RunStore restart, Gateway projection,
Desktop projection, and message-alternation tests.

**Exit:** durable outbox is authoritative; every destination has a named owner
and observable failure state.

## Phase 10 — Release gates and UAT

### 10.1 Full verification

- [ ] Run all workflow tests through `scripts/run_tests.sh`.
- [ ] Run generic plugin lifecycle, web lifespan, Gateway lifecycle, auth,
  cron, background-agent, chat, and prompt-cache/alternation tests.
- [ ] Run Desktop typecheck, lint, workflow tests, and full Vitest suite.
- [ ] Run package/wheel/sdist/update/install tests from a clean temporary home.
- [ ] Run the upstream-customization ledger checker and verify every shared file
  is covered with a removal condition.
- [ ] Run `git diff --check` and review exact staged paths.

### 10.2 Cross-platform fault matrix

- [ ] Linux, macOS, and native Windows: SQLite election/locks, atomic
  replace/quarantine, process-start identity, termination limitations, journal
  tear, coordinator kill/takeover, host restart, concurrent web+Gateway hosts.
- [ ] Coordinator loss before admission, after admission, after wake commit,
  after claim, during effect, after effect/before result, and during shutdown.
- [ ] Retry due while no host, interaction while no host, queued promotion after
  every lane-release transition, and stall detection/recovery.
- [ ] Notification retry/dedup, closed UI, archive/restore, cleanup preview/
  execute, evidence inspection, and corrupted-index preservation.
- [ ] Resource soak: no unbounded thread/process/handle/file/disk growth.

### 10.3 Surface UAT

- [ ] CLI human and `--json` journeys.
- [ ] Desktop board/inspector and bounded mutations.
- [ ] Gateway-channel start, gate, notification, and recovery.
- [ ] Cron/schedule with stable identity and truthful provenance.
- [ ] Background agent and natural-language chat invocation through skills.
- [ ] Direct authenticated API admission/mutation/evidence.
- [ ] Explicit foreground operation with no long-lived host.
- [ ] Install/update/rollback rehearsal without releasing.

### 10.4 Final review and release preparation

- [ ] Re-run adversarial review against implementation and this plan.
- [ ] Confirm no unsupported promises, placeholders, workflow core imports,
  model-tool additions, non-secret env config, prompt mutation, or stale docs.
- [ ] Obtain maintainer approval for release candidate.
- [ ] Only then create release commits/tags, monitor CI to terminal success, and
  perform installed-artifact smoke/UAT. Do not release from this planning pass.

## Ordered release-blocker checklist

Release is blocked until every item is checked in this order:

1. [ ] Admission/index reconciliation cannot delete or authorize cleanup of
   valid evidence under missing, empty, corrupt, replaced, or inconsistent DB.
2. [ ] Bare cleanup is preview-only and explicit execution is confirmation-bound.
3. [ ] Lease expiry preserves executor identity and uncertain outward effects
   require reconciliation.
4. [ ] Journal/projection recovery survives torn writes and fails closed.
5. [ ] Generic service lifecycle passes real web/Gateway start, failure,
   shutdown, safe-mode, and no-overlap reload tests.
6. [ ] Workflow coordinator election/heartbeat/wake/recovery passes two-process
   restart and native Windows tests.
7. [ ] Every interaction, retry, cancellation/lane release, and queued promotion
   continues durably outside HTTP.
8. [ ] Background admission is refused without a healthy coordinator and
   explicit foreground behavior is truthful.
9. [ ] CLI/API JSON, exits, CAS, doctor, events-tail, next-actions, and
   deterministic idempotency contracts pass behavioral tests.
10. [ ] Generic/showcase skills use real supported commands, stable identity,
    one mutation at a time, human gates, and no-progress/unavailable handling.
11. [ ] All trigger sources record authenticated durable provenance.
12. [ ] Evidence queries, sanitization, retention, and real middleware
    authorization support every operator claim.
13. [ ] Desktop board/inspector offers complete evidence and only valid actions;
    mutations remain bounded while work continues asynchronously.
14. [ ] Archive/history/retention/cleanup semantics are distinct and tested.
15. [ ] Durable notification outbox, named delivery owners, dedup, retries,
    closed-surface persistence, and dismissal semantics pass.
16. [ ] Linux/macOS/native-Windows, surface UAT, restart/fault matrix, packaging,
    update/install, soak, ledger, prompt-cache, and alternation gates pass.
17. [ ] A fresh adversarial review has no Critical/High release blockers and a
    maintainer explicitly approves implementation for release.

## Self-review checklist for each phase

- [ ] Does every state transition name one durable authority and one owner?
- [ ] Can the test prove the real filesystem/SQLite/process/restart boundary?
- [ ] Is any API or flag speculative or absent from the implementation slice?
- [ ] Can a timeout produce a false success or duplicate effect?
- [ ] Can missing projection data be mistaken for deletion authority?
- [ ] Is health separate from lifecycle and derived from durable facts?
- [ ] Does a shared Hermes change remain generic and have a concrete consumer?
- [ ] Is its ledger entry/removal condition updated in the same commit?
- [ ] Are unrelated working-tree files unstaged?
