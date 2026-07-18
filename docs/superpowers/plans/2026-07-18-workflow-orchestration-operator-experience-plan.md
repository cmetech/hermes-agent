# Workflow Orchestration Operator Experience Implementation Plan

> **For Codex:** Use `superpowers:executing-plans` to implement this plan task-by-task. Apply `superpowers:test-driven-development` to every behavior change and `superpowers:verification-before-completion` before each completion claim, merge, or release.

**Goal:** Make natural-language workflow operation reliable with minimal user nudging, and make every workflow outcome inspectable, recoverable, and lifecycle-manageable from Desktop.

**Architecture:** Keep workflow capability at the edge: the workflow plugin remains the durable lifecycle authority, CLI commands remain the model-operated control plane, and skills supply orchestration judgment. Add a generic idempotent continuation service behind all approval/recovery entry points, enrich RunStore with trigger/display and archive metadata, expose bounded sanitized evidence through the existing plugin REST adapter, and extend the existing Desktop Workflows page rather than creating a second board or chat surface. Notifications are projections of durable run transitions, never a second source of truth.

**Tech stack:** Python 3.11+, SQLite/JSONL RunStore, FastAPI plugin adapter, pytest, React/TypeScript, nanostores, Vitest, Electron Desktop, Ruff, mypy, repository workflow/brand/release gates.

**Approved design:** `docs/superpowers/specs/2026-07-18-workflow-orchestration-operator-experience-design.md`

**Non-negotiable constraints:** Preserve prompt caching and message alternation; add no permanent model-facing core tool; use no legacy Pi/OTTO runtime; keep generic source/docs branded neutrally and user-facing branded commands resolved through `PRODUCT_CLI`; preserve fail-closed authorization, operator scoping, bounded redaction, upstream mergeability, and unrelated work.

---

## Task 1: Fix approval continuation as a generic runtime invariant

**Files:**
- Create: `plugins/workflow/continuation.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify: `plugins/workflow/showcase.py`
- Test: `tests/plugins/workflow/test_approval.py`
- Test: `tests/plugins/workflow/test_cli.py`
- Test: `tests/plugins/workflow/test_desktop_api.py`
- Test: `tests/plugins/workflow/test_showcase_offline_e2e.py`

**Step 1: Write failing regression tests for the production condition**

Add a real RunStore/Scheduler workflow with an approval node followed by a final script node. Prove:

- a Desktop approval applies the interaction and advances the same run to its next wait or terminal state;
- an approval already applied by Desktop followed by chat/CLI continuation is idempotent and still advances a stranded `running` run whose next node is ready;
- two concurrent continuation attempts execute the final node at most once;
- rejection follows its declared bounded path and never executes an approved-only action;
- showcase approval uses the same generic helper.

Run:

```bash
python3 -m pytest \
  tests/plugins/workflow/test_approval.py \
  tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_showcase_offline_e2e.py -q
```

Expected: FAIL because Desktop mutates RunStore without scheduling continuation and CLI only advances when `outcome == "applied"` plus `--continue`.

**Step 2: Implement one idempotent continuation boundary**

Create a small plugin-owned service that accepts an existing `RunStore`, runtime config, run ID, optional agent runner/profile, and the interaction result. It must:

- reload authoritative status after the CAS decision;
- advance only nonterminal runs that have executable graph work;
- tolerate `already_decided` when the recorded decision matches and the graph still needs continuation;
- rely on RunStore claims/leases for at-most-once node execution;
- return the final sanitized status, not invent success;
- never bypass operator scope or interaction IDs.

Call it after approve/reject/provide-input/reconcile/resume/retry where the mutation makes graph work runnable. Desktop approval must continue by default. Keep `--continue` accepted for compatibility, but make approval continuation the safe default rather than an optional correctness switch.

**Step 3: Run focused tests and inspect events**

Run the Step 1 command. Assert event sequences contain the decision, node claim/start/finish, and terminal transition exactly once.

**Step 4: Commit**

```bash
git add plugins/workflow/continuation.py plugins/workflow/cli.py \
  plugins/workflow/dashboard/plugin_api.py plugins/workflow/showcase.py \
  tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_showcase_offline_e2e.py
git commit -m "fix(workflow): continue runs after durable interactions"
```

---

## Task 2: Make generic workflow skills deterministic and lifecycle-aware

**Files:**
- Modify: `skills/productivity/workflow/SKILL.md`
- Create: `skills/productivity/workflow/workflows/start-and-monitor.md`
- Create: `skills/productivity/workflow/workflows/inspect-and-recover.md`
- Create: `skills/productivity/workflow/references/operator-contract.md`
- Test: `tests/agent/test_workflow_skill_command.py`
- Test: `tests/gateway/test_workflow_skill_dispatch.py`
- Test: `tests/tui_gateway/test_workflow_skill_dispatch.py`

**Step 1: Add failing skill-contract tests**

Assert the generic skill requires all of these reusable behaviors:

- resolve exact workflow IDs from `list --json`; never guess or abbreviate;
- one preflight/doctor, one start command, and one stable idempotency key per user intent;
- never use `|| true`, pipe `yes`, try speculative flags, or launch parallel variants;
- treat nonzero exit codes as failures even when output resembles JSON;
- parse JSON before deciding the next action;
- stop and report verbatim at genuine human input/approval;
- never approve for the user;
- after a user acts in Desktop, inspect status and invoke only an advertised `next_action`;
- bounded polling with unchanged state-version/progress detection, followed by events and a typed recovery action;
- distinguish read-only inspection from write actions and confirm only destructive/outward operations;
- keep run ID, interaction ID, state version, trigger, and idempotency identity across turns.

Expected: FAIL against the current monolithic skill.

**Step 2: Refactor the skill into a compact router plus shared procedures**

Keep `SKILL.md` concise and route start/monitor and inspect/recover work to the new files. Encode a state machine rather than a list of command examples. Make the contract generic for future workflow skills; workflow-specific skills may add inputs and interpretation but must not weaken it.

**Step 3: Verify prompt-cache and dispatch invariants**

Run:

```bash
python3 -m pytest \
  tests/agent/test_workflow_skill_command.py \
  tests/gateway/test_workflow_skill_dispatch.py \
  tests/tui_gateway/test_workflow_skill_dispatch.py -q
```

Confirm the skill still loads as one user message, does not alter the system prompt, and registers no model tool.

**Step 4: Commit**

```bash
git add skills/productivity/workflow tests/agent/test_workflow_skill_command.py \
  tests/gateway/test_workflow_skill_dispatch.py \
  tests/tui_gateway/test_workflow_skill_dispatch.py
git commit -m "feat(skills): harden workflow orchestration guidance"
```

---

## Task 3: Apply showcase-specific guidance without duplicating the generic contract

**Files:**
- Modify: `skills/productivity/workflow-showcase/SKILL.md`
- Modify: `skills/productivity/workflow-showcase/workflows/run-showcase.md`
- Modify: `skills/productivity/workflow-showcase/workflows/resume-and-report.md`
- Modify: `skills/productivity/workflow-showcase/references/showcase-contract.md`
- Test: `tests/agent/test_workflow_showcase_skill.py`
- Test: `tests/agent/test_workflow_product_cli_guidance.py`

**Step 1: Write failing UAT-derived contract tests**

Assert the showcase skill:

- delegates generic lifecycle handling to the `workflow` skill contract;
- uses exact `laptop-diagnostic`, never `laptop-diag`;
- runs preflight once and run once;
- never launches a duplicate showcase while one is paused/active;
- reports synthetic/offline evidence before execution;
- stops at approval and tells the user they may act in Desktop;
- after approval, uses ordinary workflow status/continuation and bounded polling;
- fetches the final report only after terminal success;
- explicitly reports failed/cancelled outcomes and queued duplicates rather than hiding them.

**Step 2: Update only showcase-specific content**

Keep scenario IDs, fictional evidence, expected artifacts, and report interpretation in this skill. Keep generic command discipline, interaction handling, polling, and recovery in the base workflow skill.

**Step 3: Verify**

```bash
python3 -m pytest \
  tests/agent/test_workflow_showcase_skill.py \
  tests/agent/test_workflow_product_cli_guidance.py -q
```

**Step 4: Commit**

```bash
git add skills/productivity/workflow-showcase \
  tests/agent/test_workflow_showcase_skill.py \
  tests/agent/test_workflow_product_cli_guidance.py
git commit -m "fix(skills): make showcase chat orchestration deterministic"
```

---

## Task 4: Persist trigger identity, archive state, and operator-facing health

**Files:**
- Modify: `plugins/workflow/admission.py`
- Modify: `plugins/workflow/store.py`
- Test: `tests/plugins/workflow/test_admission.py`
- Test: `tests/plugins/workflow/test_run_queries.py`
- Test: `tests/plugins/workflow/test_retention.py`
- Test: `tests/plugins/workflow/test_operator_e2e.py`

**Step 1: Add failing storage and migration tests**

Test both a new database and migration from the current schema. Add contracts for:

- normalized trigger kinds `desktop`, `chat`, `agent`, `cron`, `cli`, and `api`, plus bounded optional source label/reference;
- `archived_at` and `archived_by` metadata persisted in SQLite;
- archive/unarchive allowed only for terminal runs and scoped to the authorized operator;
- main-board query excludes archived terminal runs and terminal runs older than seven days;
- history query includes both archived and age-filtered terminal runs;
- active/queued/paused/interrupted runs never disappear due to age;
- terminal status provides `archive`, while archived status provides `unarchive`; cleanup remains distinct;
- a health reason differentiates user wait, retry wait, stalled runnable graph, terminal success, terminal failure, cancellation, and interruption.

**Step 2: Implement additive SQLite migrations and query modes**

Extend the existing `PRAGMA table_info` migration map. Do not rewrite run directories or delete evidence. Add `view="main"|"history"|"all"` and an injectable `now` for deterministic tests. Preserve the existing default for non-Desktop callers unless explicitly migrated in the same change.

**Step 3: Verify**

```bash
python3 -m pytest \
  tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_run_queries.py \
  tests/plugins/workflow/test_retention.py \
  tests/plugins/workflow/test_operator_e2e.py -q
```

**Step 4: Commit**

```bash
git add plugins/workflow/admission.py plugins/workflow/store.py \
  tests/plugins/workflow/test_admission.py tests/plugins/workflow/test_run_queries.py \
  tests/plugins/workflow/test_retention.py tests/plugins/workflow/test_operator_e2e.py
git commit -m "feat(workflow): persist operator lifecycle metadata"
```

---

## Task 5: Expose bounded evidence and lifecycle APIs

**Files:**
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify: `tests/plugins/workflow/test_desktop_api.py`
- Modify: `tests/plugins/workflow/test_security_boundaries.py`

**Step 1: Add failing REST contract tests**

Cover:

- `GET /runs?view=main|history` with bounded signed cursor pagination;
- archive/unarchive actions with expected state version;
- `GET /runs/{id}/events` retains current bounded sanitized cursor contract;
- `GET /runs/{id}/evidence` returns a summary of node attempts, sanitized stdout/stderr excerpts, declared outputs, and artifact metadata;
- `GET /runs/{id}/artifacts/{artifact_ref}` returns only a run-owned declared artifact, with size/content-type bounds and download disposition;
- path traversal, undeclared paths, symlinks escaping the run, secrets, prompt/reasoning/environment/tool arguments, and cross-scope access fail closed;
- action responses return authoritative updated status and advertised next actions.

**Step 2: Implement API projections over RunStore evidence**

Do not create a second log database. Read the existing event journal and declared artifact references through RunStore-owned validation helpers. Text previews must be truncated, redacted, and identified as stdout/stderr/output; binary artifacts are metadata/download only.

**Step 3: Verify**

```bash
python3 -m pytest \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_security_boundaries.py -q
```

**Step 4: Commit**

```bash
git add plugins/workflow/dashboard/plugin_api.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_security_boundaries.py
git commit -m "feat(workflow): expose sanitized run evidence"
```

---

## Task 6: Extend Desktop types, adapter, and state for board/history/evidence

**Files:**
- Modify: `apps/desktop/src/hermes.ts`
- Modify: `apps/desktop/src/types/hermes.ts`
- Modify: `apps/desktop/src/app/workflows/adapter.ts`
- Modify: `apps/desktop/src/app/workflows/adapter.test.ts`
- Modify: `apps/desktop/src/app/workflows/store.ts`

**Step 1: Add failing adapter tests**

Define and test typed mappings for trigger identity, health reason, archive metadata, evidence summaries, artifact references, and server-advertised actions. Reject unknown unsafe action execution while rendering unknown trigger/health values with an accessible fallback.

**Step 2: Implement the client contract**

Add API methods for main/history pages, evidence, artifact access, and archive/unarchive. Keep store atoms feature-local and keep route roots thin.

**Step 3: Verify**

```bash
cd apps/desktop
npx vitest run --environment jsdom src/hermes.test.ts src/app/workflows/adapter.test.ts
npm run typecheck
```

**Step 4: Commit**

```bash
git add apps/desktop/src/hermes.ts apps/desktop/src/types/hermes.ts \
  apps/desktop/src/app/workflows/adapter.ts \
  apps/desktop/src/app/workflows/adapter.test.ts \
  apps/desktop/src/app/workflows/store.ts
git commit -m "feat(desktop): model workflow lifecycle evidence"
```

---

## Task 7: Build the first-class Desktop inspector and recovery experience

**Files:**
- Modify: `apps/desktop/src/app/workflows/index.tsx`
- Modify: `apps/desktop/src/app/workflows/run-inspector.tsx`
- Modify: `apps/desktop/src/app/workflows/attention-inbox.tsx`
- Create: `apps/desktop/src/app/workflows/evidence-panel.tsx`
- Create: `apps/desktop/src/app/workflows/recovery-actions.tsx`
- Create: `apps/desktop/src/app/workflows/trigger-icon.tsx`
- Modify: `apps/desktop/src/app/workflows/index.test.tsx`
- Modify: `apps/desktop/src/app/workflows/workflow-operations.e2e.test.tsx`
- Modify: `apps/desktop/src/i18n/types.ts`
- Modify: `apps/desktop/src/i18n/en.ts`
- Modify: locale catalogs required by `apps/desktop/src/i18n/languages.test.ts`

**Step 1: Add failing user-journey tests**

Test these flows:

- cards show accessible origin icon/label for on-demand Desktop, chat/agent, cron, CLI, and API;
- Needs Attention remains pinned and states the required human action;
- selecting a run shows summary, current/failed node, chronological events, bounded logs/outputs, artifacts, and provenance;
- actions render from `next_actions`, not a hard-coded status guess;
- approve/reject/input/reconcile show the interaction message verbatim;
- retry identifies its node, resume explains interruption, cancel explains non-rollback, and abandon explains terminalization;
- stale CAS responses refresh and preserve the user's selection/comment without double-applying;
- successful completion surfaces report/artifacts; failure surfaces error plus prior successful evidence;
- keyboard navigation, focus restoration, live-region status, text labels, and contrast-safe non-color cues work.

**Step 2: Implement the inspector**

Use tabs or sections for Overview, Timeline, Logs & Outputs, and Artifacts. Do not dump raw JSON by default. Show exact event timestamps and node identities, with a developer-oriented sanitized JSON disclosure as a secondary action.

**Step 3: Verify**

```bash
cd apps/desktop
npx vitest run --environment jsdom \
  src/app/workflows/index.test.tsx \
  src/app/workflows/workflow-operations.e2e.test.tsx \
  src/i18n/languages.test.ts
npm run typecheck
npm run lint
```

**Step 4: Commit**

```bash
git add apps/desktop/src/app/workflows apps/desktop/src/i18n
git commit -m "feat(desktop): add workflow evidence and recovery inspector"
```

---

## Task 8: Add archive, history, and explicit cleanup UX

**Files:**
- Modify: `apps/desktop/src/app/workflows/index.tsx`
- Modify: `apps/desktop/src/app/workflows/run-inspector.tsx`
- Create: `apps/desktop/src/app/workflows/history-view.tsx`
- Modify: `apps/desktop/src/app/workflows/index.test.tsx`
- Modify: `apps/desktop/src/app/workflows/workflow-operations.e2e.test.tsx`
- Modify: `plugins/workflow/cli.py`
- Modify: `tests/plugins/workflow/test_cli.py`
- Modify: `docs/workflow-orchestration.md`

**Step 1: Add failing lifecycle tests**

Prove:

- completed and failed/stopped terminal cards remain on the main board for seven days;
- Archive removes a terminal card from the main board immediately without deleting evidence;
- History can find, filter, inspect, and unarchive it;
- Clear column is a bulk archive operation with a count/confirmation, not cleanup;
- cleanup is a separate dry-run-first destructive flow showing run/file/byte counts and retention cutoff;
- interrupted unresolved runs cannot be cleaned until abandoned or otherwise terminalized;
- cleanup never touches active, paused, queued, or unauthorized runs.

**Step 2: Implement lifecycle UX and CLI parity**

Add main/history switching and bulk archive. If the existing CLI has no archive commands, add `workflow archive RUN_ID`, `workflow unarchive RUN_ID`, and history filters while preserving existing cleanup syntax.

**Step 3: Verify**

```bash
python3 -m pytest tests/plugins/workflow/test_cli.py tests/plugins/workflow/test_retention.py -q
cd apps/desktop
npx vitest run --environment jsdom \
  src/app/workflows/index.test.tsx \
  src/app/workflows/workflow-operations.e2e.test.tsx
```

**Step 4: Commit**

```bash
git add plugins/workflow/cli.py tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_retention.py apps/desktop/src/app/workflows \
  docs/workflow-orchestration.md
git commit -m "feat(workflow): add archive and history lifecycle"
```

---

## Task 9: Project durable notifications from workflow transitions

**Files:**
- Create: `plugins/workflow/notifications.py`
- Modify: `plugins/workflow/plugin.yaml`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify: `gateway/run.py`
- Modify: `tui_gateway/server.py`
- Modify: `apps/desktop/src/hermes.ts`
- Test: `tests/plugins/workflow/test_notifications.py`
- Test: `tests/gateway/test_workflow_notifications.py`
- Test: `tests/tui_gateway/test_workflow_notifications.py`
- Test: `apps/desktop/src/hermes.test.ts`

**Step 1: Add failing delivery-contract tests**

Test durable notification records keyed by run ID + transition/state version for:

- user action required;
- retry exhausted/failure;
- stalled/needs recovery;
- succeeded with artifact/report summary;
- cancelled/abandoned.

Prove duplicate observers/restarts do not redeliver the same transition, delivery failures remain retryable, notifications contain no raw logs/secrets, and the configured channel maps to the originating authenticated surface when possible. Cron and background-agent runs must notify without holding their worker.

**Step 2: Implement notification projection and edge adapters**

Persist an outbox/receipt beside workflow state or in additive RunStore tables. Gateway and Desktop consume bounded sanitized notification payloads through their existing notification mechanisms. Do not introduce outbound telemetry or a core model tool. Add user-facing behavioral settings to `config.yaml`/plugin config, never `.env`.

**Step 3: Verify**

```bash
python3 -m pytest \
  tests/plugins/workflow/test_notifications.py \
  tests/gateway/test_workflow_notifications.py \
  tests/tui_gateway/test_workflow_notifications.py -q
cd apps/desktop
npx vitest run --environment jsdom src/hermes.test.ts
```

**Step 4: Commit**

```bash
git add plugins/workflow/notifications.py plugins/workflow/plugin.yaml \
  plugins/workflow/dashboard/plugin_api.py gateway/run.py tui_gateway/server.py \
  apps/desktop/src/hermes.ts apps/desktop/src/hermes.test.ts \
  tests/plugins/workflow/test_notifications.py \
  tests/gateway/test_workflow_notifications.py \
  tests/tui_gateway/test_workflow_notifications.py
git commit -m "feat(workflow): notify operators of durable transitions"
```

---

## Task 10: Update customization ledger, operations docs, and release gates

**Files:**
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Modify: `docs/workflow-orchestration.md`
- Modify: `docs/design/portable-workflow-orchestration.md`
- Modify: `docs/plans/2026-07-15-portable-workflow-orchestration-plan.md`
- Modify: `tests/test_packaging_metadata.py`
- Modify: `tests/plugins/workflow/test_showcase_distribution_e2e.py`
- Modify: `.github/workflows/ci.yml` only if native Windows behavior is not already covered by the workflow portability job

**Step 1: Record every upstream-owned touch before merging**

Add or extend ledger entries separately for generic RunStore/API behavior, Desktop workflow presentation, gateway/TUI notification composition, and test gates. Record owned symbols/contracts, invariant tests, merge guidance, expected commit subjects, last verified upstream, and removal conditions.

**Step 2: Document the operator model**

Document natural-language routing, trigger identity, notifications, Needs Attention, evidence sources/redaction, recovery actions, seven-day main-board policy, archive/history versus cleanup, cron/background behavior, and showcase UAT. State clearly that archive is reversible metadata while cleanup deletes retained run evidence.

**Step 3: Verify distribution and ledger coverage**

```bash
python3 scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/workflow-orchestration.yaml
python3 -m pytest \
  tests/test_packaging_metadata.py \
  tests/plugins/workflow/test_showcase_distribution_e2e.py \
  tests/scripts/test_workflow_merge_gate.py -q
```

**Step 4: Commit**

```bash
git add docs/upstream-customizations/workflow-orchestration.yaml \
  docs/workflow-orchestration.md docs/design/portable-workflow-orchestration.md \
  docs/plans/2026-07-15-portable-workflow-orchestration-plan.md \
  tests/test_packaging_metadata.py \
  tests/plugins/workflow/test_showcase_distribution_e2e.py \
  .github/workflows/ci.yml
git commit -m "docs(workflow): define observable operator lifecycle"
```

---

## Task 11: Run full verification and adversarial review

**Files:** no production edits unless a failing gate produces a separately tested fix.

**Step 1: Focused Python quality gates**

```bash
python3 -m pytest \
  tests/plugins/workflow \
  tests/agent/test_workflow_skill_command.py \
  tests/agent/test_workflow_showcase_skill.py \
  tests/agent/test_workflow_product_cli_guidance.py \
  tests/gateway/test_workflow_skill_dispatch.py \
  tests/tui_gateway/test_workflow_skill_dispatch.py \
  tests/cron/test_workflow_cron.py -q
ruff check plugins/workflow tests/plugins/workflow \
  tests/agent/test_workflow_skill_command.py \
  tests/agent/test_workflow_showcase_skill.py
mypy plugins/workflow
```

**Step 2: Desktop gates**

```bash
cd apps/desktop
npm run test:workflow-ui
npm run typecheck
npm run lint
npm run build
```

**Step 3: Repository workflow and brand gates**

```bash
scripts/test_workflow_merge_gate.sh --phase base
scripts/test_workflow_upstream_merge.sh
```

Run brand gates from the established detached release worktrees against the exact tested base SHA:

```bash
scripts/test_workflow_merge_gate.sh --phase brand --brand otto --tested-base-sha <TESTED_BASE_SHA>
scripts/test_workflow_merge_gate.sh --phase brand --brand loop24 --tested-base-sha <TESTED_BASE_SHA>
```

**Step 4: Security and code review**

Review the full diff for cross-scope access, artifact traversal, secret/log leakage, notification spoofing/replay, stale CAS races, duplicate execution, unbounded output, accessibility regressions, and prompt-cache/tool-schema changes. Resolve every high-severity finding with a failing test first.

**Step 5: Commit verification-only fixes separately**

Use narrow `fix(...)` or `test(...)` commits; do not fold unrelated gate repairs into feature commits.

---

## Task 12: Restamp branches, release, monitor CI, and complete Windows UAT

**Files:** version/release files determined by the established release scripts and current branch graph.

**Step 1: Merge/restamp using the established custom-package process**

Inspect status, worktrees, exact base/OTTO/Loop24 ancestry, and release docs. Propagate only the exact tested base commit. Preserve customization-ledger merge decisions; never use blanket `ours`/`theirs` on ledger-owned files.

**Step 2: Publish a patch release only after all gates pass**

Use the repository release script and both branded release repositories. Verify version string `Co-worker Agent`, branded executable `loop24`, release notes, checksums, installers/assets, and update metadata.

**Step 3: Monitor GitHub Actions and assets**

Wait for every required workflow to finish. Record run URLs/IDs, tested SHAs, asset names, sizes, and checksums. A queued or in-progress workflow is not a pass.

**Step 4: Windows update-path UAT**

Ask the Windows user to update the existing installation, not reinstall. Then perform natural-language Desktop chat UAT:

1. Ask chat to run the bundled Laptop Diagnostic with the fictional slow-start symptom.
2. Confirm exactly one preflight and one run were issued and capture the run ID.
3. Confirm the run appears with the correct trigger icon and pauses in Needs Attention.
4. Inspect timeline/logs/outputs/artifacts before approval.
5. The user approves manually in Desktop; the agent never approves for them.
6. Confirm automatic continuation completes 11/11 without an unexplained stuck state.
7. Confirm completion notification and final report/artifacts.
8. Archive the run, find it in History, unarchive it, then archive it again.
9. Dry-run cleanup and verify evidence remains until the explicit retention/deletion action.
10. Trigger a controlled failure and confirm failure notification, diagnosis, allowed recovery actions, and evidence.

Do not claim Windows chat/board/update UAT fixed until the user supplies this production evidence.

**Step 5: Final evidence handoff**

Report commits, exact tested SHAs, gate results, release tags/assets, remaining limitations, and Windows verification status. Keep any not-yet-user-verified claim explicitly provisional.
