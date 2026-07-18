# Workflow Orchestration Operator Experience Design

**Date:** 2026-07-18
**Status:** Re-review conditions incorporated; ready for implementation approval
**Scope:** Shared workflow operating contract, machine API, evidence, Desktop board/inspector, archive/cleanup, and notifications

## Summary

Hermes workflows will be conversational at intent discovery and deterministic
at execution. Skills translate user intent into exact workflow CLI operations;
the plugin's RunStore and elected coordinator remain authoritative. Desktop,
Gateway, chat, cron, background agents, API clients, and CLI are projections or
admission surfaces, never competing schedulers.

This design depends on
`2026-07-18-plugin-background-services-workflow-coordination-design.md`. REST
mutations persist one bounded state change plus a durable wake and return
promptly. The workflow coordinator performs continuation outside requests.
When no healthy coordinator exists, Hermes preserves readable evidence,
refuses new background admission, and offers explicit foreground execution only
where the command supports it.

No permanent workflow model tool is added. No system prompt, historical
message, or model toolset changes mid-conversation.

## Goals

- prevent duplicate starts from one semantic intent;
- continue every runnable transition under a durable execution owner;
- make coordinator loss, stalls, retries, human gates, uncertain effects, and
  terminal outcomes visible across surfaces;
- expose enough sanitized evidence to explain what ran and what to do next;
- give machines stable JSON, exit codes, action vocabulary, and idempotency;
- give Desktop a native board and inspector without rebuilding chat or owning
  workflow state;
- separate active work, attention, terminal visibility, archive, history,
  retention, and destructive cleanup;
- keep notifications durable even when the Workflows page is closed;
- preserve prompt caching and strict role alternation.

## Non-goals

- a new workflow model tool or workflow-specific import in base host files;
- running workflow tails inside HTTP, Electron, chat, or cron requests;
- treating a client-provided operator header as authorization;
- automatic replay of an outward effect with an uncertain predecessor;
- silent evidence deletion through retention or index reconciliation;
- a second Desktop chat surface or a visual workflow authoring editor;
- generic plugin scheduling, notification, leader election, or auto-restart;
- merging candidate commits `a9ccb7e91` or `43edb4d4b` as-is.

## Authority and cross-surface topology

```text
user / agent / cron / API
        |
        v
exact CLI or authenticated REST admission/mutation
        |
        v
RunStore transaction: state + event + projection + durable wake/outbox
        |
        v
workflow-plugin elected coordinator
        |
        +--> fenced execution / retry / recovery / queued promotion
        +--> durable health, evidence, and notification state
        |
        v
CLI / Desktop / Gateway / chat projections
```

RunStore is the lifecycle and evidence authority. SQLite indexes are repairable
projections and never independent deletion authority. The coordinator lease is
the background-execution authority. The outbox is notification authority.
Desktop caches, Gateway delivery receipts, and OS notifications are projections.

The complete service, coordinator, continuation, unavailable, lease-expiry,
notification, and archive/cleanup state machines are normative in the focused
coordinator design.

## Stable machine CLI contract

### Command discovery

`hermes workflow preflight --json` is the entry contract for skills and agents.
It returns runtime/schema versions, coordinator availability, supported
commands/subcommands, exact identifier kinds, valid action names, exit-code
schema, profile/scope, and capability warnings. It never teaches a flag the
runtime does not accept.

Showcase discovery remains explicit:

```text
hermes workflow showcase list|describe|preflight|run ...
```

General lifecycle commands always take a durable run ID:

```text
hermes workflow status|events|approve|reject|provide-input|resume|retry|reconcile|cancel|archive|restore|cleanup ...
```

A showcase ID is never accepted as a substitute for a run ID after admission.

### JSON envelopes and exits

With `--json`, stdout contains exactly one JSON envelope on both success and
failure. Human diagnostics and tracebacks never corrupt stdout. The envelope is
versioned:

```json
{
  "schema_version": 1,
  "ok": false,
  "command": "workflow approve",
  "result": null,
  "error": {
    "code": "version_conflict",
    "message": "Run state changed; refresh before retrying.",
    "retryable": true,
    "details": {"run_id": "...", "current_version": 14}
  },
  "warnings": [],
  "next_actions": []
}
```

Exit categories are stable and documented:

- `0`: successful command, including idempotent existing-result reuse;
- `2`: invocation/validation error;
- `3`: not found;
- `4`: authorization/trust failure;
- `5`: state/CAS conflict;
- `6`: coordinator unavailable or runtime not ready;
- `7`: blocking doctor/integrity finding;
- `8`: execution/recovery action failed;
- `70`: unexpected internal error represented by a sanitized JSON envelope.

`doctor --json` exits nonzero for any finding that blocks the requested mode.
`events --tail N` returns the newest N matching events in chronological display
order. CAS conflicts are expected typed results, not tracebacks.

This is a deliberate migration: current approve/reject code uses exit `3` for
“decision not applied.” Under schema version 1, idempotent same-decision reuse
is `0`, a stale/different decision is conflict `5`, and `3` means not found.
Every shipped skill, example, and test that interpreted the old `3` migrates in
the same phase. Machines dispatch first on the JSON envelope's
`schema_version` and `error.code`; the process exit is the coarse transport
category. Not-found errors include at most ten authorized, deterministically
ordered `error.details.candidates` containing only identifier, kind, and safe
display label, for both workflow and showcase namespaces.

### Idempotency

Every start has `source`, `source_instance`, `intent_key`, and `start_digest`.
The idempotency key is deterministic from the stable source identity plus the
semantic operation, not a random retry value.

- JSON, non-interactive, and `--no-wait` starts require an explicit stable key
  or a source adapter capable of deriving one deterministically.
- an interactive human foreground start may request a generated key, which is
  returned and printed before execution; retries must reuse it.
- same key plus same digest returns the existing run;
- same key plus different digest is a conflict;
- an intentional second run requires an explicit new key.

### Background and foreground

`--no-wait` means durable background admission and requires a fresh healthy
coordinator inside the admission check. It never means “create work and hope a
host appears.” On failure no run directory is created.

`--foreground` is an explicit execution mode for commands that support local
execution and is allowed only when admission proves no fresh coordinator
leader. Admission atomically records a renewable foreground-owner lease; a
leader that appears later observes but does not dispatch that run. A foreground
request racing a live leader returns a typed ownership conflict, and an expired
foreground lease transfers only through fenced recovery/reconciliation. It does
not silently activate because a coordinator is absent.

Interaction commands default to mutation plus durable wake; a foreground
continuation option, where supported, is explicit and cannot be used from REST.

## Authoritative action contract

One workflow-owned transition table supplies:

- validation and handler dispatch;
- JSON `next_actions`;
- REST action metadata;
- Desktop enabled actions and confirmation requirements;
- skill interpretation tests.

Every action carries a state version. Interaction actions also require the
current interaction ID. Null or earlier interaction identity cannot satisfy a
new gate.

| Authoritative condition | Valid actions |
|---|---|
| paused for approval | approve, reject, cancel |
| paused for input | provide-input, cancel |
| waiting retry | cancel; retry-now only if policy explicitly permits |
| failed retryable node | retry-node, archive |
| interrupted and proven replay-safe | resume, cancel, archive |
| reconciliation required | record-success, record-failure, authorize-replay, cancel |
| running healthy | cancel |
| running stalled | inspect, cancel; resume only after ownership is resolved |
| succeeded/cancelled/failed terminal | archive, inspect-cleanup |
| archived terminal | restore, inspect-cleanup |

Actions not in the server response are not rendered as enabled UI controls.
Action dismissal or notification dismissal never changes the workflow.

## Generic workflow-operating skill contract

All workflow-operating skills share one reusable contract and behavioral test
harness. Showcase-specific narrative extends it without redefining lifecycle
commands.

An operating skill must:

1. resolve the branded product CLI once and retain that exact executable;
2. run read-only `preflight --json` and inspect exact supported contracts;
3. distinguish workflow/showcase definitions from durable run IDs;
4. derive and retain one stable idempotency key per semantic start;
5. execute one mutating command at a time;
6. interpret the authoritative JSON envelope and exit code before continuing;
7. stop at approval/input/reconciliation gates and report the run ID;
8. poll only while semantic progress occurs or a valid durable wake/next poll
   time exists;
9. classify `coordinator_unavailable`, no-progress, stall, conflict, and
   uncertain-effect results explicitly;
10. report observed evidence and hashes rather than promise success.

It must never:

- probe several mutating syntax variants, sequentially or in parallel;
- use `|| true`, `yes |`, or another shell construct that masks authority or
  failure for a machine decision;
- fabricate flags from Markdown examples;
- approve/reject with a showcase ID;
- start a duplicate run merely because polling or delivery timed out;
- poll forever when no owner or semantic progress exists.

Behavioral tests invoke the real parser/command seam with a temporary profile
and assert constructed argv, envelope interpretation, mutations, run counts,
human-gate stopping, and coordinator-unavailable behavior. Phrase-presence
tests alone do not satisfy this contract.

## Trigger provenance

Provenance is store-recorded and immutable after admission, but its assurance
must match the boundary that supplied it:

- `source`: `desktop`, `chat`, `background_agent`, `cron`, `cli`, or `api`;
- `assurance`: `verified_adapter`, `system_schedule`, `local_admin_claim`, or
  `legacy_unknown`;
- `source_instance`: verified adapter/session/job/client identity when the
  assurance supports one, otherwise a labeled local claim;
- `actor_id`: verified principal for authenticated boundaries, scheduler
  identity for `system_schedule`, or the local-admin process identity;
- `claimed_actor`: optional unverified actor label supplied by a local-admin CLI
  caller; it never grants authorization;
- `intent_key`: source-scoped stable semantic request identity;
- `return_route`: verified delivery descriptor only for authenticated/system
  adapters; a local claimed route is labeled unverified, cannot authorize
  delivery, and is stored separately from workflow input/start digest;
- `admitted_at`, profile, and package digest.

Desktop REST/API and direct Gateway adapters supply `verified_adapter`
provenance through typed authenticated APIs. A direct internal scheduler
adapter supplies `system_schedule`; a cron-driven skill that shells out to the
CLI remains `local_admin_claim`. Any chat or background-agent skill that spawns
the CLI through the terminal is likewise inside the CLI local-admin boundary:
it may record `source=chat` or `background_agent`, but only with
`assurance=local_admin_claim`; claimed platform actors and routes remain
visibly unverified and cannot authorize notification delivery. A future per-
session capability could strengthen that path, but is not a Phase 6
requirement. The store validates canonical combinations. UI
origin icons and assurance labels are derived from this durable record; clients
do not invent verified origin. Legacy runs display `unknown`, never `cli` by
assumption.

## Evidence model

The operator must be able to answer: what started this, what ran, what is
running now, what changed, what was emitted, why progress stopped, whether the
owner is healthy, whether an outward effect is uncertain, and what action is
safe.

### Durable evidence

- immutable admission inputs, package/start digest, provenance, and state
  versions;
- ordered framed journal events and interaction decisions;
- graph/current/previous node and attempt/retry histories;
- stdout/stderr streams with byte counts and truncation markers;
- structured node outputs and schema errors;
- artifact metadata, size, content hash, media type, and storage state;
- worker/coordinator owner epochs, PID/process-start identity, leases, and
  heartbeats;
- last meaningful semantic progress;
- cancellation, interruption, recovery, reconciliation, archive, cleanup, and
  notification decisions.

### Integrity and retention

Journal append/recovery must tolerate a torn final frame without discarding
earlier complete evidence. SQLite projections carry generation/checksum facts
and are rebuilt only from corroborated authority. Missing, empty, corrupt, or
replaced indexes trigger repair-required health and block cleanup.

Raw local evidence is sensitive. Retention never silently deletes it. Cleanup
is the sole destructive path and remains explicit.

### Query and sanitization

Evidence APIs are typed, cursor-based, size-limited, and explicit about
truncation. A single plugin-owned sanitizer serves API and notification output.
Secrets, credential-like values, unsafe paths, and untrusted control sequences
are redacted or escaped at projection time. Raw artifact download requires a
verified high-trust operator scope and remains auditable.

## Authorization

The web server derives the principal, profile, and maximum scope from the real
mounted authentication middleware. `X-Hermes-Operator-Scope` may only narrow
that verified scope; it cannot grant access. Desktop requests use the same
authenticated boundary as other plugin APIs.

CLI is a separate local-admin trust boundary. Its power and profile selection
are explicit. Gateway/chat actions bind to the authenticated platform identity
and stored return route. Cross-profile access is denied by default.

Tests mount the real plugin routes through FastAPI lifespan and middleware;
direct handler-only tests are insufficient for authorization claims.

## Desktop board and inspector

Desktop remains a projection over the authenticated workflow API. The renderer
uses nanostores/TanStack Query for UI cache, never lifecycle authority. Workflow
failures remain isolated from chat.

### Board surfaces

- **Active:** running, queued, paused, waiting retry, and interrupted work;
- **Needs attention:** approval/input, failed, stalled, coordinator unavailable,
  reconciliation required, storage degraded, and notification delivery issues;
- **Completed/Stopped:** recent terminal cards under a visibility policy;
- **History:** terminal records no longer on the active board;
- **Archive:** reversibly hidden terminal records.

Cards show durable origin, workflow/run identity, state and health, current and
previous node, queue/retry/gate reason, last meaningful progress, coordinator
heartbeat status, and attention count. Origin icons use server provenance.

### Inspector

The inspector provides:

- Overview: provenance, digest, status/health, owner/coordinator, last progress,
  failure/stall cause, and authoritative valid actions;
- Graph: current, completed, failed, skipped, and pending nodes;
- Timeline: sanitized cursor-paged events and interactions;
- Attempts: retry history, claim/lease/process identity, start/stop/recovery;
- Logs: bounded stdout/stderr with truncation and copy/export controls;
- Outputs: typed structured values and schema diagnostics;
- Artifacts: metadata, hashes, verification, and authorized retrieval;
- Notifications/cleanup: delivery history, archive state, and cleanup previews.

Mutating requests contain run version and interaction ID where applicable.
They return after the transaction and durable wake, well within Desktop's API
timeout; they never call `RunScheduler.advance`. UI displays `wake_recorded`,
`coordinator_unavailable`, or conflict truthfully and reconciles from server
state.

Reads use bounded summaries and cursors. One selected-run refresh path replaces
independent one-second full-journal scans. Hidden pages stop cosmetic polling;
durable attention/outbox state prevents transition loss.

The plugin API reuses a bounded, profile-keyed workflow store registry instead
of constructing and reconciling `RunStore` per request. It retains at most eight
profiles. Entries are acquired by reference-counted lease and only zero-
reference entries may be evicted. The event wait route is asynchronous, admits
at most 16 waiters per process, returns typed `event_wait_capacity`/429 with
retry guidance when full, and performs short store reads through a plugin-owned
blocking-I/O limiter of four workers rather than occupying FastAPI's shared chat
threadpool while sleeping. Its 30-second public wait cap remains unchanged.

These bounds live under `plugins.entries.workflow.api` as
`max_cached_profiles: 8`, `max_event_waiters: 16`, and `store_io_workers: 4`;
validation prevents zero/unbounded values.

Keyboard operation, focus preservation, status announcements, non-color health
cues, reduced motion, and laptop-width layout are release requirements.

## Archive, history, retention, and cleanup

Archive is reversible metadata allowed only for terminal runs. Restore returns
the run to History, not execution. A terminal-card visibility policy controls
board clutter without altering evidence. The initial default keeps terminal
cards on the board for seven days; `plugins.entries.workflow.retention` may
tune that visibility window. Aging changes only the board projection and never
invokes cleanup. The cutoff is computed in UTC from durable `updated_at` using
an injected clock. Restoring an archived run returns it to History even if its
terminal timestamp would otherwise fall inside the board window.

Bare cleanup is preview-only. Preview includes candidate IDs, evidence types,
bytes, index integrity, open readers/claims, notification dependencies, and
blocked reasons. Execution requires an explicit `--execute` path or a
confirmation token bound to the exact preview/version. A changed preview,
uncertain authority, live executor, pending reconciliation, or active evidence
reader fails closed.

Deletion first moves corroborated content to a recoverable quarantine and
records cleanup history. Final deletion follows the policy/grace contract.
Automatic retention never invokes destructive cleanup.

## Notifications

Workflow transitions create transactional outbox rows for approval/input,
failure, stall, completion, cancellation, and reconciliation required. A unique
transition/version/destination key prevents duplicates. Destination policy may
record an external completion delivery as suppressed, but the durable
transition and in-product history still exist.

The workflow plugin owns outbox leasing, retries, deduplication, receipts,
dead-letter state, and unresolved-attention semantics. Gateway and Desktop are
destination projections:

- Gateway sends only through the authenticated stored return route and records
  the transport result.
- Desktop's delivery read leases a row to its stable Electron client instance;
  only Electron's acknowledgement after projection marks it delivered. A crash
  before acknowledgement lets the lease expire back to pending.
- if a projection is closed, unresolved outbox/attention remains durable and
  visible on return.

Flapping failure/stall/retry transitions retain individual durable history but
coalesce external delivery per run/kind/destination over a 60-second window.
Only one summary is deliverable; later facts update its count/latest version
and supersede or suppress older undelivered deliveries. Human gates,
reconciliation, cancellation, and completion are never collapsed together.

Dismissal acknowledges presentation only. It never approves, cancels,
archives, or otherwise mutates the run.

A CLI-only installation has no coordinator and no notification delivery owner.
Background admission is refused, foreground outbox facts are query-only, and
cron/background execution requires a running Gateway or web/Desktop/headless-
serve host.

## Failure policy

- generic service construction/start failure does not prevent Hermes host/chat
  startup and is visible as unhealthy;
- no healthy workflow coordinator means background admission is refused;
- existing evidence and safe mutations remain available;
- coordinator loss after admission produces durable unavailable/stall health;
- storage uncertainty blocks deletion and capacity decisions that depend on
  the uncertain projection;
- lease uncertainty blocks outward-effect replay;
- notification failure remains a durable delivery/attention fact and never
  rolls back the workflow transition;
- one plugin/API/board failure never takes down Desktop chat or Gateway adapters.

## Acceptance criteria

- all six trigger sources persist truthful provenance, assurance, and stable
  identity without promoting local-admin claims to authenticated facts;
- retries of the same semantic start produce one run;
- every runnable interaction/retry/recovery path records a wake and continues
  under a durable coordinator;
- Desktop mutations return promptly without executing graph nodes;
- queued work always promotes after retry wait/terminal release and promotes
  after pause/interruption only when the serialization/effect policy releases
  the lane;
- JSON success/failure envelopes and exit codes are stable across every command;
- doctor, events tail, CAS conflict, and `next_actions` behavior match the
  documented contract;
- no cleanup occurs without a matching explicit preview/confirmation;
- missing/corrupt indexes preserve evidence and block deletion;
- uncertain outward attempts require reconciliation;
- skills construct only supported commands, stop at gates, prevent duplicates,
  and handle unavailable/stalled states in behavioral tests;
- Desktop exposes authoritative evidence and only valid actions;
- notifications survive closed UI, host restart, delivery retry, renderer crash
  before acknowledgement, transition flapping, and duplicate processing;
- archive/restore are reversible and retention does not delete evidence;
- Linux, macOS, and native Windows gates cover lifecycle, SQLite, filesystem,
  process identity, recovery, and restart paths;
- prompt cache prefixes and strict message alternation remain invariant.
