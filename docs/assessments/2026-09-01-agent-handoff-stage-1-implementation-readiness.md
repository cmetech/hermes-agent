# Agent Handoff Stage 1 Implementation Readiness Assessment

**Date:** 2026-09-01

**Verdict:** Stage 1 implemented and verified as a bounded local-Workflow
vertical slice; Stages 2–5 remain deferred.

**Design authority:**
[`2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md`](../proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md)

## Scope of this assessment

This assessment validates Stage 1 against the live `base` tree after the
v2026.8.31 upstream merge. It covers the merged Runs and peer contracts, local
profile execution, Bot Mode, the plugin host, and the Workflow scheduler/store.
It does not authorize the Stage 2 peer adapter, Stage 3 Bot migration, or Stage
4 GitLab+ICM plugin.

## Evidence-backed verdict

The required primitives exist and compose without changing the core agent loop:

| Required capability | Live seam | Assessment |
|---|---|---|
| Profile-aware local task dispatch | `/p/<profile>/v1/runs` in `gateway/platforms/api_server.py` | Ready; the middleware enters the selected profile's home and secret scope. |
| Authenticated profile isolation | `_expected_api_key()` in `gateway/platforms/api_server.py` | Ready; named profiles fail closed and use their own `API_SERVER_KEY`. |
| Idempotent asynchronous admission | `gateway/platforms/api_server_runs.py` plus `api_server_run_idempotency.py` | Ready; keyed admission is durable and scoped by profile and credential identity. |
| Restart-visible terminal truth | `_durable_run_status()` in `api_server_runs.py` | Ready; terminal state survives, while an orphaned nonterminal run becomes `interrupted`. |
| Bounded local fallback | query-file CLI transport in `tools/bot_relay.py` | Ready on POSIX when run through a receipt-writing wrapper; Windows fallback must remain disabled until its cross-process lock is implemented. |
| Durable workflow continuation | `WorkflowCoordinatorService` and `RunScheduler` | Ready; extend the existing elected/fenced coordinator rather than adding another supervisor. |
| Workflow wait projection | `RunStore` node state machine | Feasible; add a node-level `waiting_handoff` state while keeping the run status `running`. |
| Workflow attention | journal-derived `NotificationOutbox` | Ready; add handoff-specific transition events and map only actionable failures/uncertainty into the existing Needs Attention query. |
| Operator diagnosis | top-level CLI parser plus profile-local storage | Ready; add `hermes handoff list/show/evidence/reconcile` against the initiating profile's store. |
| Shared future Bot use | gated `message_agent` in `tools/bot_mode_dm.py` | Compatible; Stage 1 must not change its schema, routing, Bot Chat transcript, or relay behavior. |
| Future external channel | plugin registration and background-service host | Compatible; the channel-registration seam should be deferred until the GitLab+ICM plugin is its second real implementation. |

No blocking upstream gap remains.

## Confirmed live behavior

### Runs and peer infrastructure

- API routes have native and `/p/{profile}` mirrors, and the request middleware
  rejects unserved profiles before entering a profile runtime scope
  (`gateway/platforms/api_server.py:2198-2309`, `8022-8034`).
- A named profile resolves its own API key and never borrows the listener
  owner's credential (`gateway/platforms/api_server.py:1993-2065`).
- Runs admission fingerprints the request and reserves its idempotency key
  before execution (`gateway/platforms/api_server_runs.py:404-618`).
- The idempotency namespace includes the routed profile and an opaque hash of
  its credential, without persisting the credential
  (`gateway/platforms/api_server_runs.py:264-298`).
- Polling can hydrate keyed status after restart. If the recorded owner no
  longer exists, a nonterminal run becomes `interrupted` rather than remaining
  falsely active (`gateway/platforms/api_server_runs.py:339-401`).
- `hermes peer run/status/stop` proves the external client contract, but its
  helpers are private and CLI-shaped. Stage 1 should use the underlying HTTP
  contract and redirect-safe URL helper, not call `cmd_peer()` or copy the Runs
  reservation store (`hermes_cli/subcommands/peer.py`).

### Local profile execution

- The proven local command is argv-based and sends the prompt through a query
  file, so prompt text is never shell-interpolated
  (`tools/bot_relay.py:561-576`).
- The existing destination-profile lock is cross-process and bounded on POSIX
  (`tools/bot_relay.py:592-676`). It is the correct serialization point for
  the CLI fallback because different initiating profiles can target the same
  destination.
- That lock currently degrades to a no-op without `fcntl`. Therefore Stage 1
  must prefer Runs everywhere and fail local CLI binding on Windows. The
  accepted Stage 5 work remains responsible for adding and validating a
  Windows lock.
- Workflow task sessions should use the deterministic title
  `Handoff: <handoff_id>`. Stage 1 need not hide them: the current CLI does not
  make titled sessions hidden, and adding hidden-session mutation is not
  required to prove the handoff lifecycle.

### Workflow integration

- The companion sidecar is closed over a generated field inventory
  (`plugins/workflow/language_schema.py:1977-2005`) and validates graph
  references separately (`plugins/workflow/schema.py:2426-2556`). Adding
  `assignments` therefore requires schema, validation, trust-summary, and
  snapshot propagation; changing only the YAML parser would be incomplete.
- The scheduler already separates worker claims from durable run state. A
  handoff dispatch can release its claim by recording `waiting_handoff`, then
  be resumed by the normal scheduler when the handoff is due or terminal.
- The run should remain `running`. Only the node needs the new state. This
  avoids expanding every run-status API and keeps the activity board's existing
  active-run semantics.
- `record_stall_if_due()` currently treats a running graph with no
  ready/claimed/running nodes as stalled
  (`plugins/workflow/store.py:10855-10920`). It must explicitly recognize a
  healthy `waiting_handoff` node and let handoff deadlines/observation health
  own the alert.
- The coordinator already performs elected, fenced, bounded sweeps and submits
  the existing scheduler (`plugins/workflow/coordinator.py:296-472`). It is the
  correct Stage 1 driver. A second generic handoff background service would
  duplicate leadership and wake logic before Bot Mode needs it.
- Prompt rendering and structured output validation already live in
  `AgentNodeExecutor` (`plugins/workflow/executors/ai.py:948-978`,
  `510-573`, `2346-2403`). The implementation should extract only the small
  pure helpers needed by both same-profile and handed-off prompt completion;
  it should not reimplement templating or output validation.
- Cancellation currently terminalizes every node without an active outward
  process after setting `desired_status=cancelled`
  (`plugins/workflow/store.py:18599-18644`). A `waiting_handoff` must instead
  record and forward the durable cancel request, then remain nonterminal until
  the handoff reports terminal truth.
- Needs Attention is already a durable projection of journal events, not UI
  state (`plugins/workflow/notifications.py:285-318`, `945-1003`,
  `1532-1573`). New UI storage is unnecessary.

### Bot Mode and plugin host

- `message_agent` is session-gated and injected deterministically into a
  managed canonical Bot Chat (`tools/bot_mode_dm.py:129-169`). Stage 1 can add
  the neutral handoff package without changing the model-visible Bot tool.
- The current Bot paths retain behavior until Stage 3: local Bot Chat, peer DM,
  and Desktop relay are not silently reinterpreted as workflow tasks.
- `BackgroundServiceHost` already owns thread lifecycle, health probing,
  bounded shutdown, reload isolation, and safe-mode behavior
  (`hermes_cli/plugin_services.py`). The later shared supervisor can register
  there when Bot Mode becomes a second consumer.
- `PluginContext` already has attributed registration patterns. A handoff
  channel registry is feasible, but landing it in Stage 1 would create an
  interface with one implementation. It remains deferred to the external
  GitLab+ICM proof.

## Stage 1 architecture adjustment

The accepted architecture remains unchanged, but its first slice should use
the smallest real ownership model:

```text
Workflow coordinator/scheduler
        |
        v
AgentHandoffService ---- profile-local handoffs.db
        |
        +---- local Runs (preferred)
        |
        +---- dedicated CLI task (POSIX, noninteractive fallback)
```

For Stage 1:

- `AgentHandoffService` and its store stay consumer-neutral under
  `hermes_cli/handoff/`.
- The service directly owns the one built-in Hermes local implementation. A
  formal plugin protocol/registry waits for Stage 4.
- The existing Workflow coordinator calls bounded `advance()` steps. A generic
  handoff supervisor waits for Stage 3, when Bot Mode needs progress outside a
  workflow run.
- The store needs handoffs, normalized events, idempotent commands, and an
  advance lease/fence. It does not need a separate return-delivery table yet:
  Workflow finalization is an idempotent projection keyed by the node's exact
  `handoff_ref`. The delivery table lands with late Bot returns, its first real
  independent consumer.
- Stage 1 supports task mode only, assigned prompt nodes only, and
  noninteractive destination policy. Approval/steer/follow-up support remains
  Stage 2 work even though local Runs can carry those operations.

This adjustment removes speculative infrastructure without weakening Stage 1
durability or the accepted future facade boundary.

## Preconditions and fail-closed behavior

Local Runs binds only when all of these are true:

1. the target profile exists and is served by the configured multiplexed local
   gateway;
2. the API server is enabled and reachable over a loopback address;
3. the target profile has a usable profile-scoped `API_SERVER_KEY`; and
4. `/v1/capabilities` advertises durable Runs idempotency.

If all four are not true, a noninteractive assignment may bind to the CLI
fallback on POSIX. A capability requiring interaction, a Windows host without
the destination lock, or a policy that forbids the CLI mechanism fails
admission before any submit attempt.

After a submit attempt is journaled, the mechanism cannot change. Ambiguous
Runs submission replays the same payload and idempotency key to reconcile.
Ambiguous CLI execution inspects the deterministic session/receipt and remains
`indeterminate` when completion cannot be proved; it never starts a second
task blindly.

## Risks and controls

| Risk | Required Stage 1 control |
|---|---|
| Lost Runs response after admission | Persist the submit-attempt fact first; replay the same keyed request to recover the remote run ID. |
| Concurrent foreground/background advancement | Lease each handoff with a monotonically increasing fence; never hold SQLite across process or HTTP I/O. |
| Duplicate workflow finalization | Store the exact handoff ID and semantic generation on the node; terminal projection is compare-and-set and idempotent. |
| Worker retry creates duplicate work | Keep semantic `handoff_generation` separate from worker attempt IDs and lease epochs; increment only after a definitive retry decision. |
| Generic stall detector mislabels a healthy wait | Exempt healthy `waiting_handoff`; emit attention only for deadline, uncertainty, adapter health, or terminal failure. |
| Cancellation lies about remote work | Keep the workflow nonterminal while the service is `cancelling`; accept authoritative completion/cancellation races. |
| Secret leakage in evidence | Persist endpoint names, hashes, status codes, and bounded redacted text; never persist bearer keys or raw credential headers. |
| Session pollution | Use one deterministic task session per handoff; expose it in evidence. Defer hiding until the product requires it. |
| Windows unsafe fallback | Disable CLI fallback on Windows until the Stage 5 lock is implemented; Runs remains available. |

## Pre-implementation verification performed

The following live suites passed on 2026-09-01:

```text
tests/gateway/test_api_server_run_idempotency.py
tests/gateway/test_api_server_runs.py
tests/hermes_cli/test_peer_cmd.py
tests/tools/test_bot_turn_lock.py
tests/tools/test_bot_mode_dm.py
tests/hermes_cli/test_plugin_background_services.py
tests/plugins/workflow/test_coordinator.py
tests/plugins/workflow/test_schema.py
tests/plugins/workflow/test_language_schema.py
```

Result: **944 passed, 0 failed, 1 platform skip**. The skip is Windows-only and
runs in the Windows CI lane.

## Post-implementation verification (2026-09-02)

Stage 1 now crosses the real local boundary with two temporary profiles and
their real homes, SQLite state, API-server multiplex middleware, profile-secret
scope, Runs routing, and HTTP. The only substituted boundary is deterministic
provider inference. The proof does not mock the handoff service, Runs,
coordinator, or durable stores.

The real-path suite verifies assignment admission/trust, an idempotency-keyed
destination Run, a released Workflow worker while its run stays active,
a destination held active across elected-coordinator replacement, one result
observed and structurally validated by the successor, duplicate suppression,
a separate active destination stopped through truthful cancellation, a
naturally expired deadline advanced by the real scheduler/coordinator path,
interrupted/deadline Needs Attention and redacted evidence, and unchanged
ordinary prompt and Bot Mode contracts. A separate POSIX subprocess verifies
a real CLI task receipt and dedicated `Handoff: <handoff_id>` session. On the
Darwin verification host, the Linux and Windows cases were skipped; this
assessment does not claim they ran locally. The Windows case runs on its host
lane and requires the Stage 1 fallback-unavailable result.

The exact Stage 1 focused gate passed:

```text
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
  tests/plugins/workflow/test_notifications.py \
  --file-retries 0 -q

1269 passed, 0 failed, 4 host-specific skips
```

Additional fresh verification:

```text
scripts/run_tests.sh tests/plugins/workflow/test_local_handoff_e2e.py \
  --file-retries 0 -q
6 passed, 0 failed, 2 host-specific skips

scripts/run_tests.sh \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  --file-retries 0 -m integration \
  -k extracted_wheel_registers_workflow_cli_from_a_clean_home -q
1 passed, 0 failed, 5 deselected

scripts/run_tests.sh tests/plugins/workflow -q
131 files; 5916 passed, 0 failed, 5 host-specific skips
```

The integration-marked installed-wheel smoke was selected explicitly; the
default non-integration selection does not count as that proof. The broad run
exited zero after five first-attempt flakes passed on automatic retry,
including a Darwin parallel SQLite/native bus crash and unrelated
timing-sensitive Workflow tests. The corrected local and installed cases
passed independently with file retries disabled. No production integration
correction was required by the end-to-end proof. Compatibility-only test
fixtures were updated to project the intentional redacted `assignments: {}`
field and to give pre-Stage-1 coordinator scheduler stubs the neutral
`advance_due_handoffs()` result.

## Implementation gate

Stage 1 is complete. Real two-profile tests prove keyed Runs held across
restart, active cancellation convergence, scheduler-driven deadline handling,
POSIX CLI fallback, output validation, evidence inspection, and Needs Attention
without changing Bot Mode behavior or adding a model tool.
