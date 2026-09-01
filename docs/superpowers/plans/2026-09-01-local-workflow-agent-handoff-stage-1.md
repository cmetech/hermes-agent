# Local Workflow Agent Handoff Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one profile-owned Workflow delegate an assigned prompt node to a
local Hermes profile, wait durably without holding a worker, recover after
restart, accept validated output, propagate cancellation, and explain failures
through the existing Needs Attention and evidence surfaces.

**Architecture:** Add a neutral, profile-local `hermes_cli.handoff` service and
SQLite ledger. Its first concrete channel selects either profile-scoped
loopback Runs or a bounded dedicated CLI task before submission. The existing
Workflow coordinator drives bounded convergence; no second supervisor,
channel-plugin registry, Bot migration, or remote peer adapter lands in this
stage.

**Tech Stack:** Python 3.11+, stdlib `sqlite3`, `urllib`, `subprocess`, existing
Hermes profile/secret helpers, API-server Runs, Workflow RunStore/coordinator,
pytest, and the repository's `scripts/run_tests.sh`.

**Spec:**
[`docs/proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md`](../../proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md)

**Readiness evidence:**
[`docs/assessments/2026-09-01-agent-handoff-stage-1-implementation-readiness.md`](../../assessments/2026-09-01-agent-handoff-stage-1-implementation-readiness.md)

## Global Constraints

- Start from `base`; literal `main` is synchronization-only.
- Preserve prompt-prefix bytes, strict role alternation, and the existing
  `message_agent` schema and Bot Chat behavior.
- Add no permanent model-facing tool and no non-secret `HERMES_*` setting.
- Persist a handoff and submit-attempt event before external I/O.
- Never hold a SQLite transaction across HTTP or process I/O.
- Never change mechanism after a submit attempt may have occurred.
- Keep the semantic handoff generation independent of worker attempt IDs,
  claim owners, coordinator epochs, and lease generations.
- Treat timeouts, connection loss, missing receipts, and interrupted remote
  Runs as `indeterminate`; never blind-resubmit.
- Use the target profile's secret scope. Never persist bearer credentials,
  profile filesystem paths, raw headers, or unredacted provider errors.
- Stage 1 accepts assigned prompt nodes only and noninteractive destination
  policy only. Approval, steering, follow-up messages, peers, Bot returns,
  channel registration, and GitLab+ICM remain later stages.
- Keep a Workflow run `running` while a node is `waiting_handoff`; do not add a
  new run-level status.
- Disable the CLI fallback on Windows until the accepted Stage 5 destination
  lock is implemented. Do not silently run without serialization.
- Every task starts with a failing behavioral test, runs its focused suite,
  stages exact paths only, and commits atomically.

---

## Task 1: Define the consumer-neutral contract and endpoint grammar

**Files:**

- Create: `hermes_cli/handoff/__init__.py`
- Create: `hermes_cli/handoff/models.py`
- Create: `tests/hermes_cli/handoff/test_models.py`

**Contract to implement:**

```python
HANDOFF_PHASES = frozenset({
    "prepared", "submitted", "active", "needs_input", "cancelling",
    "indeterminate", "succeeded", "failed", "cancelled",
})

@dataclass(frozen=True, slots=True)
class HandoffEndpoint:
    canonical: str
    profile: str

    @classmethod
    def parse(cls, value: str) -> "HandoffEndpoint": ...

@dataclass(frozen=True, slots=True)
class HandoffSpec:
    mode: Literal["task"]
    endpoint: HandoffEndpoint
    prompt: str
    output_schema: Mapping[str, object] | None
    deadline_at: datetime | None
    attribution: Mapping[str, str]
    required_capabilities: frozenset[str]

@dataclass(frozen=True, slots=True)
class HandoffSnapshot: ...

@dataclass(frozen=True, slots=True)
class ChannelObservation: ...
```

`HandoffEndpoint.parse()` accepts exactly
`hermes://local/<normalized-profile>`. It rejects missing profiles, extra path
segments, usernames, passwords, hosts other than `local`, ports, query strings,
fragments, percent-encoded separators, control characters, and noncanonical
profile spellings. It returns the canonical URI; there is no shorthand.

`HandoffSpec` rejects blank or oversized prompts, naive deadlines, credentials
or URLs in attribution, unsupported capabilities, and unbounded output schemas.
Fingerprint input is canonical JSON containing only stable semantic fields.

- [ ] Write parser tests for the one accepted grammar and every rejection
  listed above.
- [ ] Write model tests for timezone-aware deadlines, task-only mode, bounded
  prompt/schema sizes, immutable normalization, and stable canonical
  fingerprint input.
- [ ] Run the new tests and confirm they fail because the package does not
  exist.
- [ ] Implement the dataclasses, constants, validation, and canonical JSON
  helpers in `models.py`; re-export only the consumer-facing names from
  `__init__.py`.
- [ ] Run:

```bash
scripts/run_tests.sh tests/hermes_cli/handoff/test_models.py -q
```

- [ ] Commit exact paths:

```bash
git add hermes_cli/handoff/__init__.py hermes_cli/handoff/models.py tests/hermes_cli/handoff/test_models.py
git commit -m "feat(handoff): define local task contract"
```

## Task 2: Add the durable profile-local handoff ledger

**Files:**

- Create: `hermes_cli/handoff/store.py`
- Create: `tests/hermes_cli/handoff/test_store.py`
- Modify: `hermes_cli/handoff/__init__.py`

**Storage contract:**

Create `<profile-home>/handoffs.db` with these minimum tables:

- `handoffs`: ID, `(key_scope, handoff_key)` uniqueness, specification and
  fingerprint, sealed mechanism/binding, bounded checkpoint, phase, state
  version, next advance/deadline timestamps, submit-attempt fact, durable
  cancel fact, terminal result/failure, advance lease owner/epoch/expiry, and
  created/updated timestamps;
- `handoff_events`: `(handoff_id, sequence)` primary key, event ID, phase
  before/after, stable kind, actor, safe data, and timestamp; and
- `handoff_commands`: `(handoff_id, command_id)` primary key, content
  fingerprint, kind, safe payload, delivery state, and timestamps.

Use `hermes_state.apply_wal_with_fallback`, `BEGIN IMMEDIATE` for competing
writes, foreign keys, bounded JSON, and owner-only permissions for the database,
WAL, and SHM files. UUID v4 IDs are generated only after the unique key insert
wins.

Store methods:

```python
create_or_get(key_scope, handoff_key, spec, spec_fingerprint) -> HandoffSnapshot
get(handoff_id) -> HandoffSnapshot
list(query, *, limit, before) -> tuple[HandoffSnapshot, ...]
evidence(handoff_id, *, after_sequence, limit) -> EvidencePage
bind(handoff_id, mechanism, binding, checkpoint, expected_version) -> HandoffSnapshot
claim_advance(handoff_id, owner, *, now, lease_seconds) -> AdvanceLease | None
commit_observation(lease, observation) -> HandoffSnapshot
release_advance(lease, *, next_advance_at) -> None
record_command(handoff_id, command_id, kind, payload) -> CommandRecord
```

- [ ] Write failing tests for equivalent create replay, conflicting reuse,
  concurrent creators, immutable mechanism binding, legal/illegal lifecycle
  transitions, terminal immutability, command replay/conflict, event sequence,
  pagination, expired lease takeover, stale-fence rejection, WAL reopen, and
  file permissions.
- [ ] Add failure-injection tests proving a row and its `created` event commit
  together, a submit-attempt event commits before an adapter call may begin,
  and no credential-like value survives event redaction.
- [ ] Implement schema installation and the methods above. Keep migrations at
  schema version 1; there is no preexisting handoff database to migrate.
- [ ] Make `claim_advance()` increment a durable epoch. All post-I/O writes
  compare handoff ID, owner, and epoch; a stale worker records nothing.
- [ ] Run:

```bash
scripts/run_tests.sh tests/hermes_cli/handoff/test_store.py -q
```

- [ ] Commit:

```bash
git add hermes_cli/handoff/__init__.py hermes_cli/handoff/store.py tests/hermes_cli/handoff/test_store.py
git commit -m "feat(handoff): add durable lifecycle ledger"
```

## Task 3: Implement bounded convergent service semantics

**Files:**

- Create: `hermes_cli/handoff/service.py`
- Create: `tests/hermes_cli/handoff/test_service.py`
- Modify: `hermes_cli/handoff/__init__.py`

**Service contract:**

```python
class AgentHandoffService:
    def validate_endpoint(self, endpoint, initiator) -> EndpointAssessment: ...
    def create(self, spec, initiator, *, handoff_key) -> HandoffSnapshot: ...
    def advance(self, handoff_id, *, budget_seconds: float = 2.0) -> AdvanceResult: ...
    def command(self, handoff_id, kind, *, command_id, actor) -> HandoffSnapshot: ...
    def get(self, handoff_id) -> HandoffSnapshot: ...
    def list(self, query, *, limit=50, before=None) -> tuple[HandoffSnapshot, ...]: ...
    def evidence(self, handoff_id, *, after_sequence=0, limit=100) -> EvidencePage: ...
```

For Stage 1, `command()` implements `cancel` and `reconcile`; it rejects
`message`, `respond`, and `acknowledge` with a stable unsupported-command error.
The constructor may accept a duck-typed channel object for tests, but do not add
an ABC, registry, or plugin hook yet.

`advance()` performs at most one external operation:

1. acquire a fenced lease;
2. select bind, submit, reconcile, observe, cancel delivery, or no-op from
   durable facts;
3. journal the attempt before external I/O;
4. close the transaction;
5. call the local channel within the remaining budget; and
6. fold one observation only if the lease fence is still current.

An exception is classified as definitely-not-accepted, retryable observation
failure, or indeterminate. A recorded submit attempt always routes recovery to
reconcile, never a fresh submit.

- [ ] Write failing table-driven tests for every common phase transition and
  illegal transition.
- [ ] Add crash-window tests for death after submit journaling, death after
  channel acceptance but before checkpoint persistence, repeated terminal
  observations, concurrent advances, expired leases, and cancel/complete races.
- [ ] Assert each `advance()` invokes zero or one channel method and respects a
  finite positive budget.
- [ ] Implement the service and stable public error classes.
- [ ] Run:

```bash
scripts/run_tests.sh tests/hermes_cli/handoff/test_service.py tests/hermes_cli/handoff/test_store.py -q
```

- [ ] Commit:

```bash
git add hermes_cli/handoff/__init__.py hermes_cli/handoff/service.py tests/hermes_cli/handoff/test_service.py
git commit -m "feat(handoff): add convergent service facade"
```

## Task 4: Implement profile-scoped loopback Runs

**Files:**

- Create: `hermes_cli/handoff/local.py`
- Create: `tests/hermes_cli/handoff/test_local_runs.py`
- Modify: `hermes_cli/handoff/service.py`

**Binding rules:**

`hermes://local/<profile>` prefers Runs only when the default gateway
configuration enables multiplexing, `profiles_to_serve()` contains the target,
the API-server platform is enabled, the target profile's secret scope contains
a usable `API_SERVER_KEY`, the configured listener resolves to loopback, and
`/p/<profile>/v1/capabilities` reports durable Runs idempotency. Wildcard bind
addresses connect through `127.0.0.1`; non-loopback configured hosts are not
treated as local.

Use `agent.secret_scope.build_profile_secret_scope(target_home)` for the key,
`gateway.config.load_gateway_config()` under the default profile home for
listener configuration, `hermes_cli.profiles.profiles_to_serve()` for routing,
and `hermes_cli.urllib_security.open_credentialed_url()` for bounded requests
and cross-origin credential stripping. Never import private functions from
`hermes_cli.subcommands.peer`.

**Runs protocol:**

- create or resolve an exact session titled `Handoff: <handoff_id>` through the
  profile-prefixed sessions API;
- submit `{"input": prompt, "session_id": session_id}` with
  `Idempotency-Key: handoff-<handoff_id>`;
- on an ambiguous submit, replay that exact body and key;
- poll `GET /v1/runs/<run_id>`;
- map `queued` to `submitted`, `running` to `active`,
  `waiting_for_approval` to `needs_input`, `completed` to `succeeded`, and
  `failed`/`cancelled`/`interrupted` to their definitive or indeterminate
  facade meanings; and
- deliver cancel through `POST /v1/runs/<run_id>/stop`, then remain
  `cancelling` until polling reports a terminal status.

- [ ] Write failing tests with the real API-server route table and profile
  middleware for served/unserved profiles, target-specific keys, missing or
  weak keys, nonmultiplex mode, nonloopback hosts, capability downgrade, and
  cross-origin redirects.
- [ ] Test exact-title session reuse, duplicate keyed submission, key/payload
  conflict, lost-response replay, polling after adapter restart, interrupted
  owner mapping, bounded response reads, redacted HTTP errors, and stop races.
- [ ] Implement one `LocalHermesChannel` class with `bind`, `submit`,
  `reconcile`, `observe`, and `cancel`; do not add a generic channel interface.
- [ ] Wire it as the default channel used by `AgentHandoffService`.
- [ ] Run:

```bash
scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_local_runs.py \
  tests/gateway/test_api_server_run_idempotency.py \
  tests/gateway/test_api_server_runs.py -q
```

- [ ] Commit:

```bash
git add hermes_cli/handoff/local.py hermes_cli/handoff/service.py tests/hermes_cli/handoff/test_local_runs.py
git commit -m "feat(handoff): dispatch local tasks through keyed Runs"
```

## Task 5: Add the dedicated CLI fallback and durable receipt

**Files:**

- Modify: `hermes_cli/handoff/local.py`
- Modify: `tools/bot_relay.py`
- Create: `tests/hermes_cli/handoff/test_local_cli.py`
- Modify: `tests/tools/test_bot_turn_lock.py`
- Modify: `tests/tools/test_bot_relay_windows_paths.py`

Extend `tools.bot_relay.local_delivery_command()` with a keyword-only
`title="Bot Chat"` parameter so the existing Bot callers remain byte-for-byte
equivalent while handoffs can request `Handoff: <handoff_id>`. Keep argv/query-
file transport and reuse `acquire_turn_lock()`; do not copy either helper.

The CLI mechanism starts a small `python -m hermes_cli.handoff.local` wrapper
that:

1. verifies bounded, owner-only prompt/output/receipt paths under the initiating
   profile's handoff spool;
2. acquires the target profile's existing turn lock;
3. runs the target-profile CLI with the deterministic task title and query
   file;
4. writes bounded stdout/stderr files; and
5. atomically writes a versioned receipt containing exit code and output hashes.

The initiating `advance()` stores the wrapper's `ProcessIdentity` and returns
immediately. Observation checks the receipt first, then exact process identity.
A live identity is active; a valid success receipt is succeeded; a valid error
receipt is failed; a dead/mismatched identity without a valid receipt is
indeterminate. Cancellation uses `ManagedProcessTree.terminate_existing()` and
still requires a terminal receipt or authoritative stopped observation.

The mechanism binds only when required capabilities are noninteractive,
`os.name != "nt"`, and Runs was authoritatively unavailable before a submit
attempt. It never becomes a post-admission Runs fallback.

- [ ] Write failing tests for title override compatibility, prompt metacharacter
  safety, profile lock contention, background start, receipt integrity, output
  bounds, wrapper crash, process-identity reuse, initiator restart, timeout,
  cancellation, and absence of blind replay.
- [ ] Add a Windows test that binding refuses CLI with a stable
  `local_cli_lock_unavailable` reason; retain existing Bot behavior unchanged.
- [ ] Implement the wrapper and observation logic.
- [ ] Run:

```bash
scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_local_cli.py \
  tests/tools/test_bot_turn_lock.py \
  tests/tools/test_bot_relay_windows_paths.py \
  tests/tools/test_bot_mode_dm.py -q
```

- [ ] Commit:

```bash
git add hermes_cli/handoff/local.py tools/bot_relay.py tests/hermes_cli/handoff/test_local_cli.py tests/tools/test_bot_turn_lock.py tests/tools/test_bot_relay_windows_paths.py
git commit -m "feat(handoff): add bounded local CLI fallback"
```

## Task 6: Add the operator handoff CLI

**Files:**

- Create: `hermes_cli/handoff/cli.py`
- Modify: `hermes_cli/main.py`
- Create: `tests/hermes_cli/test_handoff_cmd.py`

Register one top-level `hermes handoff` parser with:

```text
hermes handoff list [--phase PHASE] [--limit N] [--json]
hermes handoff show <handoff-id> [--json]
hermes handoff evidence <handoff-id> [--after SEQUENCE] [--limit N] [--json]
hermes handoff reconcile <handoff-id> [--command-id ID] [--json]
hermes handoff cancel <handoff-id> [--command-id ID] [--json]
hermes handoff advance <handoff-id> [--budget-seconds N] [--json]
```

All commands operate on the selected `-p/--profile` home. Text output shows
ID, safe endpoint display, mechanism, phase, age, next observation, terminal
summary, and stable failure code. JSON output excludes prompt bodies by default
and uses stable field names. Evidence is paginated and redacted. Mutations use
a caller-supplied command ID or generate and print one before returning.

- [ ] Write failing parser and command tests for profile scope, pagination,
  unknown IDs, phase filters, JSON safety, reconcile idempotency, cancel
  idempotency, invalid budgets, and nonzero exit codes.
- [ ] Implement the parser and handlers; add the parser beside
  `build_peer_parser()` in the ordinary startup graph.
- [ ] Run:

```bash
scripts/run_tests.sh tests/hermes_cli/test_handoff_cmd.py -q
```

- [ ] Commit:

```bash
git add hermes_cli/handoff/cli.py hermes_cli/main.py tests/hermes_cli/test_handoff_cmd.py
git commit -m "feat(handoff): add diagnostic operator CLI"
```

## Task 7: Add assignment authoring, validation, and trust disclosure

**Files:**

- Modify: `plugins/workflow/language_schema.py`
- Modify: `plugins/workflow/schema.py`
- Modify: `plugins/workflow/trust.py`
- Modify: `plugins/workflow/admission.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `tests/plugins/workflow/test_language_schema.py`
- Modify: `tests/plugins/workflow/test_schema.py`
- Modify: `tests/plugins/workflow/test_trust_policy.py`
- Modify: `tests/plugins/workflow/test_admission.py`

Add one optional top-level sidecar mapping:

```yaml
outward_action_nodes:
  - security-review

assignments:
  security-review:
    endpoint: hermes://local/security-reviewer
    interaction_policy: deny
    deadline: PT4H
    on_deadline: cancel_and_fail
```

Stage 1 accepts exactly `endpoint`, `interaction_policy`, `deadline`, and
`on_deadline`. The endpoint is required; the only accepted interaction policy
is `deny`; the deadline accepts a small bounded ISO-8601 day/time subset
implemented beside the generated schema validation; and the only deadline
policy is `cancel_and_fail`. Unknown keys fail closed.

An assigned node must exist, be a prompt node, be declared outward, and not be
a loop child or shared persisted producer. The endpoint must pass local grammar
validation, the target profile must exist, and the target must differ from the
workflow owner. Admission checks mechanism availability without creating or
submitting a handoff.

Assignment data participates in the sealed workflow digest, stored definition
snapshot, catalog projection, `inspect` output, and trust summary. The trust
surface displays the canonical target profile, task mode, interaction policy,
deadline, and possible local mechanisms; it never displays credentials.

- [ ] Write failing schema tests for valid assignment parsing, closed-world
  keys, bad references, unsupported node kinds, non-outward nodes, loop/shared
  nodes, self-targeting, nonlocal endpoints, bad policies, and invalid bounds.
- [ ] Write failing admission tests for absent target profiles and unavailable
  mechanisms on the current platform.
- [ ] Write failing trust, seal-digest, snapshot, catalog, and CLI inspection
  tests proving an assignment change is visible and invalidates prior trust.
- [ ] Implement the smallest schema and projection changes; reuse existing
  profile, outward-action, trust, and sealed-digest helpers. Use one anchored
  parser for the accepted deadline subset; do not add a duration dependency.
- [ ] Run:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_trust_policy.py \
  tests/plugins/workflow/test_admission.py -q
```

- [ ] Commit:

```bash
git add plugins/workflow/language_schema.py plugins/workflow/schema.py plugins/workflow/trust.py plugins/workflow/admission.py plugins/workflow/store.py plugins/workflow/cli.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_schema.py tests/plugins/workflow/test_trust_policy.py tests/plugins/workflow/test_admission.py
git commit -m "feat(workflow): validate local agent assignments"
```

## Task 8: Add the durable Workflow waiting state

**Files:**

- Modify: `plugins/workflow/models.py`
- Modify: `plugins/workflow/store.py`
- Modify: `tests/plugins/workflow/test_store.py`
- Modify: `tests/plugins/workflow/test_crash_recovery.py`

Add `waiting_handoff` as a node state only. Keep the run `running`. Store the
minimum projection on the node: exact handoff ID, semantic handoff generation,
last observed handoff version/phase, next observation time, and deadline. Do
not copy prompts, remote checkpoints, credentials, or handoff event history
into Workflow storage.

Add fenced compare-and-set operations that:

1. move a claimed prompt node to `waiting_handoff` and release its worker;
2. refresh its observed phase and next due time without changing the semantic
   generation;
3. make a terminal handoff node ready for ordinary resumption/finalization;
4. reject a mismatched handoff ID or generation; and
5. increment the generation only after a definitive, policy-authorized retry.

Update recovery and `record_stall_if_due()` so a healthy, not-overdue
`waiting_handoff` is neither claimable work nor a stalled graph. An overdue,
indeterminate, or repeatedly unhealthy wait remains eligible for the existing
attention path.

- [ ] Write failing transition-matrix tests for claim-to-wait, claim release,
  duplicate projection, terminal wake, stale worker/fence, mismatched handoff,
  and forbidden run terminalization.
- [ ] Write restart and stall tests proving a healthy wait survives reopen and
  is not labelled stalled, while an unhealthy wait is surfaced.
- [ ] Implement the node state and store operations without adding a run-level
  status or a second result table.
- [ ] Run:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_store.py \
  tests/plugins/workflow/test_crash_recovery.py -q
```

- [ ] Commit:

```bash
git add plugins/workflow/models.py plugins/workflow/store.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_crash_recovery.py
git commit -m "feat(workflow): persist agent handoff waits"
```

## Task 9: Dispatch assigned prompt nodes and resume validated output

**Files:**

- Create: `plugins/workflow/executors/handoff.py`
- Modify: `plugins/workflow/executors/ai.py`
- Modify: `plugins/workflow/executors/__init__.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/store.py`
- Create: `tests/plugins/workflow/test_handoff_executor.py`
- Modify: `tests/plugins/workflow/test_scheduler.py`

Extract only the pure prompt/result behavior already owned by
`AgentNodeExecutor`:

```python
render_agent_prompt(context) -> str
result_from_external_response(context, text, metadata) -> NodeResult
```

The second helper must reuse the existing output-format parsing, JSON-schema
validation, size limits, metadata shaping, and failure codes. Same-profile
prompt execution continues through the same helpers, proving no behavior drift.

`HandoffPromptExecutor` uses a stable semantic key derived from workflow run,
node ID, and handoff generation. On first execution it creates and advances the
handoff once, then returns an internal waiting outcome. On resumption it reads
the exact handoff ID: terminal success becomes the ordinary validated node
result; failed, cancelled, or indeterminate becomes an explicit existing
Workflow failure/reconcile result; nonterminal state returns to waiting.

Teach the scheduler to select this executor only for assigned prompt nodes.
Teach result persistence to handle the internal waiting outcome before normal
attempt/retry accounting, so dispatch does not consume retries or retain a
worker lease.

- [ ] Write characterization tests around existing prompt rendering and output
  validation before extracting the helpers.
- [ ] Write failing executor tests for stable keys, create replay, one bounded
  advance, waiting projection, structured success, invalid output, terminal
  failures, indeterminate state, and no retry consumption while waiting.
- [ ] Write scheduler tests proving unassigned prompts use `AgentNodeExecutor`
  unchanged and assigned prompts release their worker after dispatch.
- [ ] Implement the helper extraction, executor, scheduler selection, and
  waiting-result branch.
- [ ] Run:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_handoff_executor.py \
  tests/plugins/workflow/test_scheduler.py \
  tests/plugins/workflow/test_ai_executor.py -q
```

- [ ] Commit:

```bash
git add plugins/workflow/executors/handoff.py plugins/workflow/executors/ai.py plugins/workflow/executors/__init__.py plugins/workflow/scheduler.py plugins/workflow/store.py tests/plugins/workflow/test_handoff_executor.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_ai_executor.py
git commit -m "feat(workflow): execute assigned prompts as handoffs"
```

## Task 10: Advance and recover handoffs through the existing coordinator

**Files:**

- Modify: `plugins/workflow/coordinator.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `tests/plugins/workflow/test_coordinator.py`
- Modify: `tests/plugins/workflow/test_coordinator_multiprocess.py`
- Modify: `tests/plugins/workflow/test_crash_recovery.py`

Add one bounded handoff sweep to the elected coordinator cycle. It selects due
`waiting_handoff` nodes, asks `AgentHandoffService.advance()` to perform at most
one external operation per item, projects the returned snapshot with a fenced
store operation, and wakes the ordinary scheduler only for terminal work.

Use the existing coordinator election, fencing, wake event, cycle budget,
backoff, and shutdown behavior. Do not register a second background service.
One broken handoff must not stop the sweep; record its safe error, schedule a
bounded retry, and continue until the cycle budget expires.

- [ ] Write failing tests for due selection, bounded batch/cycle time, one-step
  advancement, terminal wake, nonterminal reschedule, isolated adapter failure,
  stale coordinator fences, and shutdown.
- [ ] Add multiprocess and reopen tests proving a new elected coordinator
  resumes an admitted handoff without creating a second semantic handoff.
- [ ] Implement the sweep using the existing coordinator and scheduler seams.
- [ ] Run:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_crash_recovery.py -q
```

- [ ] Commit:

```bash
git add plugins/workflow/coordinator.py plugins/workflow/scheduler.py tests/plugins/workflow/test_coordinator.py tests/plugins/workflow/test_coordinator_multiprocess.py tests/plugins/workflow/test_crash_recovery.py
git commit -m "feat(workflow): recover handoffs in coordinator sweeps"
```

## Task 11: Propagate cancellation and explain handoff failures

**Files:**

- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/notifications.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify: `tests/plugins/workflow/test_cancel_node.py`
- Modify: `tests/plugins/workflow/test_notifications.py`
- Modify: `tests/plugins/workflow/test_notification_delivery.py`

When a run cancellation reaches `waiting_handoff`, record the run's desired
status and an idempotent handoff cancel command, then leave the node and run
nonterminal while the coordinator converges the remote truth. Do not mark work
cancelled merely because delivery was attempted.

Resolve races from authoritative handoff state:

- remote `cancelled` finalizes the node cancelled;
- remote `succeeded` before cancellation wins is validated and journaled, then
  the run's desired cancellation prevents new downstream work;
- remote `failed` remains failed with cancellation context; and
- `indeterminate` remains nonterminal and actionable until an operator
  reconciles it.

Emit normalized journal events for admitted, submitted, active, cancelling,
deadline exceeded, failed, indeterminate, reconciled, and terminal handoffs.
Map only actionable states into the existing NotificationOutbox/Needs Attention
projection. Include handoff ID, safe endpoint, node, phase, age, last successful
observation, next action, stable failure code, and the exact `hermes handoff
show/evidence/reconcile` commands. Exclude prompt/output bodies and secrets.

- [ ] Write failing cancellation tests for pre-submit cancellation, in-flight
  cancellation, repeated commands, success/cancel race, failure/cancel race,
  indeterminate cancellation, restart, and run finalization.
- [ ] Write failing notification/API tests for deduplication, acknowledgement,
  redaction, terminal clearing, evidence links, and healthy-wait silence.
- [ ] Implement cancellation propagation and journal-to-outbox mappings; reuse
  the existing activity and Needs Attention storage/API.
- [ ] Run:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_cancel_node.py \
  tests/plugins/workflow/test_notifications.py \
  tests/plugins/workflow/test_notification_delivery.py -q
```

- [ ] Commit:

```bash
git add plugins/workflow/store.py plugins/workflow/scheduler.py plugins/workflow/notifications.py plugins/workflow/dashboard/plugin_api.py tests/plugins/workflow/test_cancel_node.py tests/plugins/workflow/test_notifications.py tests/plugins/workflow/test_notification_delivery.py
git commit -m "feat(workflow): surface and cancel agent handoffs"
```

## Task 12: Prove the Stage 1 vertical slice and record the fork delta

**Files:**

- Create: `tests/plugins/workflow/test_local_handoff_e2e.py`
- Modify: `tests/plugins/workflow/test_installed_distribution_e2e.py`
- Create: `docs/upstream-customizations/agent-handoff.yaml`
- Modify: `docs/proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md`
- Modify: `docs/assessments/2026-09-01-agent-handoff-stage-1-implementation-readiness.md`

Build a real-path test with two temporary profiles, real profile homes and
SQLite stores, real API-server route/multiplex middleware, real HTTP requests,
and a deterministic fake model provider at the inference boundary. Do not mock
the handoff service, Runs routes, profile secret scope, Workflow coordinator,
or either durable store.

The E2E proof must cover:

1. an assigned prompt node is admitted and disclosed in trust output;
2. loopback Runs accepts one keyed task in the destination profile;
3. the source worker is released while the run remains active;
4. coordinator restart resumes observation and validates structured output;
5. duplicate sweeps do not duplicate destination execution;
6. cancellation converges without falsely reporting remote termination;
7. indeterminate and deadline states appear in Needs Attention with usable,
   redacted evidence; and
8. unassigned workflows and Bot Mode retain their existing behavior.

Add a separate POSIX E2E case for the dedicated CLI receipt path and a Windows
assertion that this fallback is unavailable. Extend the installed-CLI smoke
test for `hermes handoff --help`.

The fork-delta ledger records ownership, user-visible configuration, database
location, upstream touchpoints, tests, rollback steps, and the deliberately
deferred Stage 2-5 work. Mark the consolidated proposal `Stage 1 implemented`
only after all gates below pass.

- [ ] Write the failing real-path E2E tests and confirm each exercises a real
  profile boundary.
- [ ] Make only integration corrections required by the E2E failures; do not
  add peer, Bot facade, generic supervisor/registry, GitLab, interactive, or
  Windows-lock features.
- [ ] Run the focused Stage 1 gate:

```bash
scripts/run_tests.sh \
  tests/hermes_cli/handoff \
  tests/hermes_cli/test_handoff_cmd.py \
  tests/hermes_cli/test_peer_cmd.py \
  tests/hermes_cli/test_plugin_background_services.py \
  tests/tools/test_bot_turn_lock.py \
  tests/tools/test_bot_relay_windows_paths.py \
  tests/tools/test_bot_mode_dm.py \
  tests/gateway/test_api_server_run_idempotency.py \
  tests/gateway/test_api_server_runs.py \
  tests/plugins/workflow/test_local_handoff_e2e.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_notifications.py -q
```

- [ ] Run the repository-prescribed broader Workflow and CLI suites affected by
  the final diff.
- [ ] Inspect the final diff and working tree:

```bash
git diff --check
git status --short
```

- [ ] Update the proposal, readiness assessment, and fork-delta ledger with the
  exact commands/results. Commit the exact documentation and E2E paths:

```bash
git add tests/plugins/workflow/test_local_handoff_e2e.py tests/plugins/workflow/test_installed_distribution_e2e.py docs/upstream-customizations/agent-handoff.yaml docs/proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md docs/assessments/2026-09-01-agent-handoff-stage-1-implementation-readiness.md
git commit -m "test(workflow): prove local agent handoffs end to end"
```

## Stage 1 completion criteria

Stage 1 is complete only when the real-path tests demonstrate durable local
profile-to-profile task delegation, restart recovery, output validation,
cancellation truth, operator evidence, and Needs Attention while preserving
unassigned Workflow and Bot behavior.

Remote peers, Bot facade migration, the shared background supervisor, channel
plugin registration, GitLab+ICM, interactive approvals/follow-ups, and the
Windows CLI lock are intentionally outside this plan. Their accepted sequence
remains Stages 2-5 in the consolidated proposal.
