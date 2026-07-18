# Workflow Orchestration and Operator Experience Design

**Status:** Approved design for implementation planning

**Date:** 2026-07-18

**Scope:** Generic portable-workflow operation, workflow skills, Desktop workflow visibility, notifications, evidence inspection, retention, and the bundled workflow showcase

**Design relationship:** This document amends `docs/design/portable-workflow-orchestration.md`. It does not replace the portable runtime, trust, scheduling, RunStore, or upstream-merge contracts established there.

## Summary

Portable workflows must feel flexible when requested in conversation and deterministic once admitted for execution. A user may describe the same intent in many ways, but the workflow skill must resolve that intent into one safe, canonical lifecycle without syntax guessing, duplicate runs, hidden failures, invented progress, or unnecessary prompting.

Workflow visibility is equally foundational. Every meaningful transition, attempt, interaction, diagnostic, and output must leave durable, inspectable evidence. Desktop must promote actionable exceptions, identify how each run was triggered, explain what happened, and offer only recovery actions that the authoritative RunStore says are valid. Terminal runs leave the main board after a bounded visibility window without silently deleting their evidence.

The design adds four cooperating improvements:

1. A reusable workflow-skill orchestration contract for natural-language invocation and lifecycle management.
2. Correct, idempotent continuation after a human decision, including decisions made outside the chat that initiated the run.
3. A first-class Desktop operator experience covering notifications, trigger identity, evidence, recovery, archive, history, and cleanup.
4. Showcase-specific guidance built on the generic contracts rather than bespoke orchestration behavior.

No permanent model-facing core tool is added. Capability remains at the edges through the workflow plugin, CLI, skills, RunStore APIs, and Desktop workflow surface.

## Foundational Product Principles

### 1. Flexible conversation, deterministic execution

Users must be free to ask for a workflow in natural language without memorizing an identifier, exact flag order, or recovery command. The skill supplies the operational intuition:

- infer the requested workflow and operation from available evidence;
- ask only for genuinely missing input, consent, or a human decision;
- use canonical workflow identifiers and documented command shapes;
- perform one state-changing operation at a time;
- create or recover one run identity and keep using it;
- interpret exit status and structured output before taking another action;
- manage bounded polling, retries, status inspection, and handoff with minimal user nudging;
- never approve, reject, reconcile, or invent consent for the user;
- never claim completion without terminal RunStore evidence.

Conversational flexibility ends at the execution boundary. Once a run exists, the run ID, state version, pending interaction, event cursor, and `next_actions` are authoritative.

### 2. Evidence and visibility are first-class behavior

A run is not operationally complete merely because work happened. The user must be able to determine:

- what triggered it;
- what definition and inputs it used;
- what ran, in what order, and for how long;
- what succeeded, failed, skipped, paused, retried, or was cancelled;
- what human decisions were made;
- what outputs and artifacts were produced;
- whether outward actions are known, failed, or uncertain;
- why the runtime recommends each available next action.

The workflow board is a read model over durable evidence, not a substitute lifecycle authority. Absence of visible evidence must never be treated as success.

## Goals

1. Make natural-language workflow operation reliable across Desktop chat, classic CLI/TUI chat, gateway chat, and branded CLI installations.
2. Prevent duplicate or contradictory operations caused by parallel tool calls, guessed syntax, masked exit codes, or discarded run identity.
3. Make human approval automatically and idempotently continue ready work when continuation is safe.
4. Notify users when a run requires action or reaches a configured terminal outcome, regardless of whether it began on demand, in chat, in the background, through cron, CLI, Desktop, or API.
5. Make trigger origin visually distinct from run status.
6. Provide one Desktop evidence inspector for every workflow type.
7. Make failure and recovery understandable without requiring raw JSON, while keeping sanitized technical detail available on demand.
8. Keep terminal runs on the main board for seven days by default, allow immediate archive without evidence deletion, and keep explicit cleanup separate and destructive.
9. Preserve fail-closed package authentication, prompt caching, message alternation, narrow-core architecture, and upstream mergeability.
10. Use the Laptop Diagnostic showcase as an end-to-end consumer of the generic behavior, not as a special runtime path.

## Non-Goals

- Adding a permanent model-facing workflow tool.
- Importing or embedding the Archon runtime or the legacy Pi/OTTO workflow runtime.
- Making the presentation board an execution authority or enabling arbitrary card drag/drop to mutate workflow state.
- Treating node counts as elapsed-time percentages or completion estimates.
- Streaming unbounded raw process output into Desktop.
- Guaranteeing that arbitrary workflow output contains no secrets. Artifact access remains privileged and must be presented accordingly.
- Editing an admitted run's immutable workflow snapshot in place.
- Automatically deciding an uncertain outward-action outcome.
- Automatically deleting evidence merely because a card ages out of the main board.
- Building a general notification framework when existing Desktop and gateway delivery seams can carry workflow notifications.

## Production UAT Findings Driving This Design

The first installed chat-driven Laptop Diagnostic run demonstrated that the runtime could execute offline work and pause at a genuine approval gate, but exposed several orchestration failures:

- the agent guessed `laptop-diag` instead of using `laptop-diagnostic`;
- it repeated preflight/list operations and used `|| true`, destroying reliable exit-status interpretation;
- it launched multiple run attempts in parallel, creating a second queued run behind the first;
- it tried unsupported `showcase approve` and `--input` syntax instead of reading the transition-specific skill procedure;
- after the user approved in Desktop, an ordinary approval command returned `already_decided` with `run_status: running`;
- the agent incorrectly described that result as applying and resuming the approval;
- the run remained at 10/11 with no current node until the user cancelled it.

These are not isolated prompt-quality issues. They expose missing contracts at three boundaries:

1. skill-driven command orchestration;
2. idempotent decision-to-scheduler continuation;
3. operator visibility into a run that claims `running` while making no progress.

## Approaches Considered

### A. Skill text hardening only

Improve `workflow-showcase` instructions and leave runtime/Desktop behavior unchanged.

This reduces syntax mistakes but cannot make cross-surface approval continuation reliable, cannot provide evidence inspection, and cannot give cron/background users durable notifications. Rejected as insufficient.

### B. New monolithic workflow orchestration tool

Expose a permanent structured model tool that starts, monitors, approves, retries, and reports workflows.

This could reduce shell mistakes, but permanently expands the core model schema, conflicts with the edge-capability design, and makes every conversation pay for workflow surface. Rejected.

### C. Shared skill contract plus idempotent runtime continuation and native operator UX

Keep natural language at the skill edge, execute documented branded CLI operations sequentially, strengthen the workflow plugin's transition semantics, and expose the existing durable evidence through Desktop.

Selected. It addresses the whole failure class while preserving the narrow core and existing runtime authority.

## Architecture Overview

```text
Natural-language request / slash command / cron / CLI / API / Desktop
                              |
                              v
                 workflow skill or typed edge API
                              |
             canonical command + stable idempotency key
                              |
                              v
                  workflow plugin admission/runtime
                              |
                RunStore projection + event journal
                    /          |           \
                   /           |            \
          notifications   Desktop board   evidence inspector
                              |
                    typed RunStore next_actions
```

The system has one lifecycle authority: RunStore. Skills, notifications, CLI, Desktop, and showcase reporting interpret that authority; they do not synthesize competing state.

## Generic Workflow-Skill Orchestration Contract

### Skill structure

The bundled generic workflow skill owns reusable orchestration policy. Individual workflow skills and `workflow-showcase` route to it or adopt its contract rather than duplicating lifecycle rules.

The skill uses a router pattern:

- `run`: discovery, preflight, required input, confirmation, one admitted run;
- `inspect`: status, events, node attempts, artifacts, and report;
- `act`: approve/reject/input/retry/resume/reconcile/cancel/abandon;
- `retain`: archive/history/cleanup explanations;
- `author`: route to workflow-builder rather than improvising an invalid package.

Essential invariants stay in the top-level `SKILL.md`; detailed procedures live in linked workflow files; result and safety contracts live in references.

### Product CLI resolution

Resolve the executable exactly once from the active product descriptor. Branded installations use their brand slug (`loop24`, `otto`); neutral installations use `hermes`. Never fall back from a branded installation to another product's executable merely because help text prints generic internal branding.

### Identifier resolution

1. Use an exact identifier supplied by the user when it exists.
2. Use a skill-known canonical identifier when the user requests a bundled scenario.
3. If the user uses a display name or ambiguous phrase, perform one bounded discovery/list operation and select only an unambiguous match.
4. On ambiguity, ask one concise question listing the matching choices.
5. Do not shorten or invent identifiers.

### Single-flight command discipline

- Never issue parallel state-changing workflow commands.
- Never issue multiple speculative variants of the same command.
- Never pipe `yes` or other synthetic consent into workflow commands.
- Never append `|| true` or otherwise mask a workflow command's exit status.
- `--help` is read-only but is used only when the linked skill contract does not already specify the syntax.
- A failed command is interpreted before any fallback is attempted.
- A fallback must be justified by the returned error, not by guesswork.

### Run identity and idempotency

- Generate one idempotency key for one user-requested start operation.
- Reuse it for safe retries caused by transport uncertainty.
- Persist the resulting `run_id` in the conversational response and subsequent procedure.
- Do not invoke `run` again to obtain status.
- A deliberate second run requires explicit user intent and a new idempotency key.
- If admission returns an existing run, continue operating that run.
- If admission returns `queued`, report its blocker and do not start another copy.

### Structured result interpretation

For every command:

1. Require the real process exit code.
2. Parse the stable JSON result when `--json` is supported.
3. Treat JSON action/outcome/status fields as distinct facts.
4. Preserve and reuse `run_id`, `state_version`, `interaction_id`, event cursor, and valid `next_actions`.
5. Treat malformed JSON, schema mismatch, and contradictory status as typed failures requiring inspection.

Examples:

- `outcome: already_decided` does not mean the current caller applied the decision.
- `status: running` does not prove a worker or runnable node exists.
- `completed_nodes: 10` of `11` is graph progress, not 91% elapsed time.
- `current_nodes: []` with a nonterminal status may be valid briefly but becomes a stall signal when the state version and semantic progress do not advance.

### Human-action boundary

The skill may explain a pending interaction and report how the user can act. It must never:

- approve or reject for the user;
- infer consent from unrelated prose;
- pipe confirmation into a process;
- reconcile an uncertain outward action;
- describe a decision as newly applied when RunStore says `already_decided`.

When the user says an action was completed elsewhere, the skill refreshes status first. It does not replay the human action unless status still exposes that exact action and the user explicitly asks the chat agent to perform it.

### Polling and progress

- Poll one run sequentially.
- Stop at terminal state, a pending interaction, a typed recovery state, or a bounded no-progress threshold.
- Use event/state-version advancement and semantic-progress timestamps, not arbitrary sleep loops, to determine change.
- Do not call status multiple times in parallel.
- Summarize changed fields rather than printing the entire projection repeatedly.
- If a run is nonterminal with no current/runnable node and no state-version progress, report it as stalled and inspect events before suggesting recovery.

### Retry and recovery

- Automatic runtime retry follows the immutable workflow retry policy and combined attempt budget.
- Skill-level retry occurs only when `next_actions` includes retry and the failure classification makes retry meaningful.
- Fixing an external prerequisite, such as credentials or an installed runtime, may be followed by retrying the same failed node.
- Fixing the workflow definition requires a new run because the admitted definition is immutable.
- Unknown outward outcomes require reconciliation, never blind retry.
- Interrupted runs use resume/recovery semantics rather than starting duplicates.

### Completion claims

The skill may claim success only when:

- RunStore reports a successful terminal status;
- expected artifacts exist and verify when the workflow contract requires them;
- no pending interaction remains;
- cleanup claims are backed by cleanup evidence where applicable.

## Decision and Automatic Continuation Contract

### User-visible rule

Approving a workflow means “record my decision and continue all newly ready work.” The user must not need to discover a second resume operation after an ordinary approval.

### Runtime rule

Every successful decision surface—Desktop, CLI with continuation, chat skill, or another authorized API—uses one idempotent continuation operation:

1. Compare-and-set the human decision against run state, interaction ID, and state version.
2. If newly applied, invoke the scheduler for that run.
3. If already applied, inspect current state; if the run is nonterminal and has ready work but no active scheduler ownership, invoke the scheduler idempotently.
4. If already progressing or terminal, return current status without duplicate execution.
5. Return the post-continuation run projection, not the intermediate “decision recorded” projection.

The scheduler and node claims remain the duplicate-execution guard. Continuation may be requested multiple times safely; node execution may not be duplicated.

### Desktop rule

Desktop approval automatically continues. There is no separate Resume button for a successfully approved gate. Resume remains available for genuinely interrupted/recoverable runs.

### Stall classification

A nonterminal run with all of the following is not presented as merely healthy:

- no current node claim;
- no pending interaction;
- at least one ready/pending node whose dependencies are satisfied;
- unchanged state version/semantic progress beyond a bounded observation window.

It becomes an actionable recovery condition with evidence and a safe reconcile/resume action determined by runtime state.

## Trigger Identity

Run status and trigger origin are separate dimensions. The immutable trigger source is captured at admission and rendered on every card and notification.

Canonical sources already supported by admission are:

- `chat`
- `desktop`
- `cli`
- `api`
- `cron`

Desktop presentation groups them without losing the canonical value:

| Canonical trigger | Icon concept | User label |
| --- | --- | --- |
| `desktop` | pointer/play | On demand |
| `chat` | bot/message | Agent/chat |
| `cron` | clock | Scheduled |
| `cli` | terminal | Command line |
| `api` | brackets/webhook | API |

Where known, the run stores a bounded return-route descriptor separate from the input snapshot: originating profile, session/conversation, platform, thread, cron job ID, or API delivery identity. Raw credentials and secret transport metadata are excluded.

## Notification Design

### Notification-worthy transitions

Notify by default when a run enters:

- approval or bounded-input wait;
- reconciliation required;
- failed;
- interrupted;
- detected stalled recovery state;
- successful terminal completion for cron/background runs.

Successful foreground completion is configurable. Routine node progress remains board/event evidence and does not generate OS/chat noise.

### Deduplication and durability

Notification identity is `(run_id, state_version, notification_kind, destination)`. A durable delivery record or cursor prevents reconnects, polling, or gateway restarts from repeatedly alerting the user. Failed delivery remains retryable and does not erase the underlying attention item.

### Destination policy

| Trigger | Primary destination | Durable fallback |
| --- | --- | --- |
| Desktop/on demand | Desktop native/in-app notification | Workflow notification center |
| Chat/agent | Originating authorized conversation/thread | Workflow notification center |
| Cron | Configured job owner/home channel | Workflow notification center |
| CLI | Command output while attached | Workflow notification center |
| API | Explicit configured callback when present | Workflow notification center |

Notification preferences are behavioral configuration in `config.yaml`, never a new user-facing `.env` setting. Credentials for external transports continue to use the existing secret configuration paths.

### Notification content

A notification contains only bounded sanitized facts:

- workflow display name;
- run ID abbreviation;
- trigger label;
- state/attention kind;
- affected node when known;
- concise sanitized error or request;
- graph progress;
- deep link/action to open the run.

It does not contain raw prompts, tool arguments, credentials, unrestricted stdout/stderr, or artifact bodies.

## Desktop Workflow Board

### Portfolio columns

The existing columns remain a presentation over exact states:

- Queued
- Active
- Needs Attention
- Completed
- Failed / Stopped

Cards are not draggable between workflow states.

### Attention surface

Actionable items are pinned above the board in a “Needs your attention” inbox. They remain visible across board filters until resolved or explicitly dismissed for the current UI session. Dismissal never changes run state.

The inbox covers approvals, input, reconciliation, interrupted recovery, and classified stalls. Failed terminal runs remain visually prominent in Failed / Stopped but are not mislabeled as pending approval.

### Card content

Each card displays:

- workflow name;
- trigger icon and label;
- exact status and health;
- graph progress;
- current, waiting, or failed node;
- concise attention/failure summary;
- updated time;
- retry/wait indicator when relevant;
- schedule label/next occurrence for cron when available;
- link to originating conversation for chat/agent triggers when authorized.

### Refresh behavior

Existing bounded pagination, visibility-aware polling, long-poll event cursors, profile-scoped caches, and stale-write rejection remain. Notifications and attention transitions are never inferred from cosmetic card movement.

## Evidence Inspector

Selecting a run opens one generic inspector with the following views.

### Overview

Answers, in plain language:

- What happened?
- Where is the run now?
- Which node needs attention?
- What has already succeeded?
- Did any outward action occur or remain uncertain?
- What actions are safe next?

It includes immutable workflow/trigger identity, elapsed timing where factual, progress, health, last semantic progress, last error, retry timing, and pending interaction.

### Graph

Uses the existing normalized bounded topology and exact node states. Selecting a node filters Timeline, Attempts, and Artifacts. Graph rendering remains strict and bounded with the portable text fallback.

### Timeline

Renders the sanitized monotonic RunStore event journal as human-readable events:

- admission/queue/start;
- node claim/start/completion/failure/skip/retry;
- artifact creation;
- approval/input request and decision;
- cancellation/interruption/reconciliation;
- cleanup evidence;
- terminal outcome.

Unknown future event types remain inspectable in a collapsed sanitized-detail row instead of disappearing.

### Node attempts

For each node and attempt:

- state;
- start/end/duration when known;
- attempt ID;
- typed error code and sanitized message;
- retry classification and next retry;
- bounded metadata appropriate for operator display;
- references to captured output and artifacts.

### Outputs and artifacts

RunStore already retains bounded stdout/stderr or structured output for deterministic nodes and artifact references for workflow outputs. Desktop adds authenticated read APIs and previews without creating a second logging store.

Read rules:

- authorize the profile/operator scope and run identity;
- accept only an artifact reference present in the authoritative run projection;
- resolve beneath the run directory with strict containment and symlink policy;
- revalidate size and SHA-256 before serving;
- enforce bounded preview size;
- serve explicit media types;
- render text, JSON, and Markdown as inert data;
- never execute artifact HTML, JavaScript, links, commands, or embedded resources;
- offer download for unsupported/binary types without inline execution;
- warn that workflow-produced output may contain sensitive information.

Raw stdout/stderr is not included in notifications and is loaded only on user demand.

### Raw details

An optional advanced view exposes the sanitized status projection and event payloads for troubleshooting and support. Internal recovery projection snapshots and forbidden fields remain excluded.

## Recovery Actions

Desktop renders actions exclusively from RunStore `next_actions` plus a presentation map describing confirmation and help text. It does not infer action availability from column names.

### Running

- Cancel
- Inspect live evidence
- Open originating conversation when authorized

### Paused for approval

- Review referenced evidence/artifacts
- Approve and automatically continue
- Reject with bounded reason and apply declared rework/cancellation behavior
- Cancel

### Paused for input

- Provide the requested bounded input
- Cancel
- Inspect prior loop iterations/output

### Waiting retry

- Inspect classified failure and retry time
- Cancel
- Retry-now only if runtime policy explicitly exposes it

### Failed

- Retry failed node when the immutable run remains retryable
- Repair an external prerequisite, then retry
- Abandon/archive
- Start a new run after definition changes

### Interrupted or stalled recovery

- Resume/recover after runtime checks
- Cancel
- Abandon
- Inspect ownership and last progress evidence

### Reconciliation required

- Confirm succeeded
- Confirm failed
- Confirm not performed
- Cancel only when the runtime says cancellation is safe

The UI explains that an uncertain outward action must be investigated before selection.

### Succeeded, cancelled, or abandoned

- Re-run as a new run with a new idempotency key
- Archive/Clear from main board
- Inspect/download retained evidence
- Permanently clean up with explicit confirmation when eligible

## Retention, Archive, History, and Cleanup

### Main-board visibility

Terminal runs remain on the main board for seven days by default. This is a display window, not an evidence-deletion timer.

### Archive/Clear

Archive is a reversible visibility mutation for terminal runs:

- removes the card from the main board immediately;
- records who/what archived it and when;
- leaves RunStore events, attempts, interactions, artifacts, and cleanup evidence intact;
- makes the run available in History;
- supports restore to History/main-board visibility while evidence exists.

Archive is not available for nonterminal runs because hiding active responsibility is unsafe. A user must cancel/abandon or resolve the run first.

### History

History lists archived runs and terminal runs older than the main-board window. It supports bounded filters for status, workflow, trigger, and date, plus direct access to the same evidence inspector.

### Cleanup

Cleanup permanently removes eligible terminal run evidence after confirmation. It remains separate from Archive and provides a dry-run/impact summary including run count, files, and bytes.

Cleanup eligibility is made consistent for terminal states, including interrupted runs only after they have been explicitly resolved to an eligible terminal disposition. An unresolved interrupted/reconciliation state is not silently deleted.

The existing seven-day CLI cleanup default remains a selection threshold for explicit cleanup; it does not become automatic deletion.

## Generic Versus Showcase-Specific Responsibilities

### Generic improvements for every workflow

- conversational workflow routing and canonical identifier resolution;
- sequential command discipline and exit-code preservation;
- stable idempotency/run identity;
- human-action boundaries;
- bounded polling and stall detection;
- automatic idempotent continuation after decisions;
- trigger icons and return routes;
- notification policy and deduplication;
- attention inbox;
- evidence inspector, artifact reads, and raw diagnostic view;
- RunStore-authoritative recovery actions;
- seven-day board visibility, Archive, History, and explicit Cleanup;
- behavior and integration tests for the full lifecycle.

### Laptop Diagnostic showcase-specific improvements

- canonical `laptop-diagnostic` identifier and exact `--symptom` mapping;
- preflight proof that the package is offline, provider-free, and fictional;
- one run per user request with a stable idempotency key;
- stop and report at its real approval node;
- direct presentation of `diagnostic-report.json`, `diagnostic-report.md`, and final `remediation-plan.md`;
- explicit reminder that no remediation runs;
- final report claims backed by RunStore events and verified artifact bytes;
- cleanup/reset restricted to immutable showcase ownership metadata.

The showcase skill must not implement its own scheduler, approval semantics, notification mechanism, evidence store, or retention policy.

## Security and Privacy

- Package digest verification and fail-closed authentication remain unchanged.
- Evidence APIs use the same authenticated profile bridge as workflow status/actions.
- Operator scope is enforced on status, events, artifacts, archive, restore, and cleanup.
- Diagnostics continue to pass through durable redaction before journaling.
- Notification content is a stricter subset than evidence content.
- Artifact references must be projection-owned and reverified; callers cannot supply arbitrary filesystem paths.
- Markdown is rendered without executable HTML or active embedded content.
- Raw process output is opt-in to view and never assumed secret-free.
- Archive cannot hide active/nonterminal responsibility.
- Cleanup is destructive, confirmed, scoped, and race-safe with readers.
- Human decisions remain compare-and-set operations with interaction identity.
- Unknown external outcomes never become success through retry or UI convenience.

## Accessibility and Interaction

- Trigger identity is conveyed by icon and text, never icon alone.
- Status is conveyed by text and shape/icon, never color alone.
- Attention and recovery controls are keyboard accessible.
- Background refresh preserves focus and expanded evidence state.
- Dialogs describe the effect and irreversibility of destructive actions.
- Timeline entries use semantic labels and expandable technical detail.
- Artifact previews support keyboard scrolling and text selection.
- The board remains usable at laptop width; inspector detail may use tabs or a responsive drawer/page.

## API and Persistence Amendments

The implementation plan will refine exact names, but the behavioral API requires:

- paginated run listing with main/history/archive filters;
- an archive mutation with expected state version;
- a restore mutation with expected state version;
- sanitized paginated event reads;
- verified artifact content reads by authoritative artifact identity;
- decision mutations that return the post-continuation projection;
- notification/attention reads with durable dedup identity;
- cleanup preview and confirmed execution;
- trigger/return-route presentation fields that exclude credentials.

Archive metadata belongs with workflow run persistence, not Desktop local storage, so profiles and authorized surfaces agree. It must not alter definition/input/policy digests or graph execution state.

## Testing Strategy

### Skill behavioral tests

Tests exercise realistic model-facing skill procedures and assert relationships, not frozen wording:

- branded executable resolution;
- natural-language display-name to canonical workflow resolution;
- one preflight and one run operation;
- no parallel state-changing commands;
- no `|| true`, synthetic `yes`, or unsupported syntax probing;
- stable idempotency key/run ID across retries;
- correct handling of queued admission;
- external approval is refreshed rather than replayed;
- `already_decided` is not reported as newly applied;
- bounded sequential polling;
- no success claim before terminal evidence.

### Runtime tests

- Desktop approval advances newly ready nodes automatically.
- Duplicate continuation after an already-applied decision is safe and advances an orphaned ready run.
- Concurrent continuation requests execute each node once.
- Approval/cancel and continuation/cancel races preserve existing hardline winners.
- A 10/11 nonterminal run with no current node is classified and recoverable rather than indefinitely healthy.
- Reject/rework, input, reconciliation, retry, and resume use the same post-transition continuation contract where applicable.

### Persistence and API tests

- archive hides only eligible terminal runs and preserves evidence;
- restore returns archived evidence to visible history/main selection;
- main-board seven-day filtering does not delete runs;
- History pagination/filtering is stable;
- cleanup dry-run and execution agree;
- unauthorized/cross-profile reads and mutations fail closed;
- artifact containment, symlink, digest, media-type, and size checks;
- cleanup/read and archive/action races;
- interrupted/reconciliation evidence cannot be silently cleaned.

### Desktop tests

- trigger source icon and accessible label for all canonical trigger types;
- attention items remain visible across filters;
- exact status-to-column mapping;
- inspector Overview/Graph/Timeline/Attempts/Artifacts rendering;
- unknown events remain visible in collapsed detail;
- only `next_actions`-authorized buttons render;
- approval mutation returns and displays continued progress;
- archive versus cleanup confirmation semantics;
- stale mutation conflict rollback and reload;
- hidden-window polling suspension and cursor recovery;
- notification deduplication and deep linking.

### End-to-end UAT

1. Start Laptop Diagnostic from natural-language Desktop chat.
2. Confirm one admitted run with trigger `chat` and a chat/agent source badge.
3. Confirm offline fictional analysis and visible timeline/artifacts.
4. Confirm Desktop notification and attention inbox at approval.
5. Approve manually in Desktop.
6. Confirm automatic continuation without a second approval/resume command.
7. Confirm successful 11/11 terminal outcome and final artifact.
8. Confirm completion notification policy and evidence inspector.
9. Archive the run and find it in History with evidence intact.
10. Preview cleanup, execute cleanup explicitly, and confirm evidence removal.
11. Repeat notification/source/evidence coverage for a cron-triggered fixture and a background chat/agent fixture.

Native Windows coverage is required for installed branded CLI/skill execution and deterministic-process artifacts. Desktop behavioral coverage runs in its established test environment, with installed Windows UAT verifying the packaged integration.

## Documentation and Customization Ledger

Generic operational documentation must explain:

- conversational invocation and skill behavior;
- notification routing and preferences;
- trigger-source meanings;
- attention states and recovery actions;
- evidence and artifact sensitivity;
- archive/history/cleanup distinctions;
- automatic approval continuation;
- CLI equivalents for every Desktop action.

Any change to upstream-owned Desktop, gateway, or lifecycle behavior must update `docs/upstream-customizations/workflow-orchestration.yaml` with owned symbols, rationale, tests, merge guidance, and removal condition. Branded user examples use `loop24`; generic source and internal documentation may use `hermes` where appropriate.

## Rollout Boundaries

Implementation is split into independently verifiable boundaries:

1. Runtime continuation regression and generic post-decision contract.
2. Generic workflow-skill orchestration contract and showcase adoption.
3. Trigger presentation and evidence read APIs.
4. Desktop evidence inspector and state-authorized recovery actions.
5. Archive, History, seven-day board filtering, and cleanup consistency.
6. Durable notifications and return-route delivery.
7. Cross-platform, packaging, brand, and installed UAT gates.

Each boundary receives a failing regression first, focused verification, a separate commit, and ledger/documentation updates where behavior crosses upstream-owned files.

## Acceptance Criteria

- Users can request workflows naturally without exact command syntax.
- The skill resolves one canonical flow and does not spray speculative commands.
- One user request creates at most one intended run.
- Human actions remain human-only.
- Desktop approval automatically continues safe ready work.
- A non-progressing nonterminal run is visible and actionable, not silently “healthy.”
- Every run card shows an accessible trigger-source identity.
- Actionable transitions generate one durable notification to an appropriate destination.
- Desktop exposes sanitized timeline, attempts, errors, stdout/stderr references, and verified artifacts on demand.
- Recovery buttons are derived from authoritative `next_actions`.
- Terminal cards age out of the main board after seven days without evidence deletion.
- Archive is reversible and distinct from destructive Cleanup.
- History retains inspectable evidence until explicit Cleanup.
- Showcase behavior uses the generic contracts and completes end to end after manual approval.
- Prompt caching, message alternation, package authentication, narrow-core architecture, and upstream mergeability remain intact.
