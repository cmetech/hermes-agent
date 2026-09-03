# Agent Handoff Stage 3 Implementation Readiness Assessment

**Date:** 2026-09-02

**Verdict:** Ready to implement after plan approval. The live tree contains the
consumer-neutral handoff lifecycle, authenticated local/peer Runs channels,
canonical Bot Chat gate, durable session routing, completion-delivery rails,
background-service host, and Desktop RPC pattern required by the accepted
architecture. Stage 3 needs one backward-compatible handoff schema migration
and narrow extensions to those authorities; it does not need a new model tool,
peer transport, general notification framework, channel registry, or daemon.

**Design authority:**
[`2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md`](../proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md)

**Delivered foundation:**
[`2026-09-01-local-workflow-agent-handoff-stage-1.md`](../superpowers/plans/2026-09-01-local-workflow-agent-handoff-stage-1.md),
[`2026-09-02-local-workflow-agent-handoff-stage-1-adversarial-review-remediation.md`](../reviews/2026-09-02-local-workflow-agent-handoff-stage-1-adversarial-review-remediation.md),
[`2026-09-02-agent-handoff-stage-2-implementation-readiness.md`](2026-09-02-agent-handoff-stage-2-implementation-readiness.md),
and
[`2026-09-02-remote-workflow-agent-handoff-stage-2.md`](../superpowers/plans/2026-09-02-remote-workflow-agent-handoff-stage-2.md)

## Scope and starting state

This assessment validates only Stage 3: Bot Mode and Desktop consumption of the
shared handoff service, durable Bot return delivery, optional bounded wake-up,
and recovery after initiating-process restart. It does not reopen Stages 1 or
2 and does not authorize Bot Mode redesign, a new model-visible tool, Workflow
changes unrelated to shared return delivery, GitLab+ICM, generic channel
registration, peer-DM retirement, relay retirement, A2A, or any Stage 4-5
feature.

The checkout was verified before investigation:

```text
branch: base
HEAD: c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d
origin/base: c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d
Stage 1 and Stage 2: merged
```

Only unrelated untracked user files were present. They were not modified,
staged, deleted, or treated as design authority.

## Planning baseline

The focused Python baseline collected and ran real tests with retries disabled:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_models.py \
  tests/hermes_cli/handoff/test_store.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/handoff/test_local_runs.py \
  tests/hermes_cli/handoff/test_peer.py \
  tests/tools/test_bot_mode_dm.py \
  tests/tools/test_bot_mode_probe.py \
  tests/tools/test_bot_relay.py \
  tests/hermes_cli/test_peer_cmd.py \
  tests/gateway/test_completion_delivery.py \
  tests/tui_gateway/test_bot_relay_methods.py \
  tests/test_tui_gateway_queue_on_busy.py \
  tests/plugins/workflow/test_handoff_executor.py -q
```

```text
421 passed, 0 failed, 1 Windows-only skip
```

The installed-wheel smoke was forced through its integration marker:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  -m integration \
  -k extracted_wheel_registers_workflow_cli_from_a_clean_home -q
```

```text
1 passed, 0 failed
```

The focused Desktop baseline ran the current Bot roster, action, relay, and
mention behavior:

```bash
(cd apps/desktop && npm run test:ui -- \
  src/plugins/hermes-bots/bot-row.test.tsx \
  src/plugins/hermes-bots/roster-actions.test.ts \
  src/plugins/hermes-bots/relay.test.ts \
  src/plugins/hermes-bots/plugin.mentions.test.ts)
```

```text
4 files passed, 51 tests passed, 0 failed
```

The only output beyond test results was Node's experimental local-storage
warning. The previously recorded whole-Workflow load-sensitive failures and
macOS SQLite/background-thread bus error remain outside Stage 3 unless a Stage
3 change is proven to worsen them.

## Evidence-backed readiness matrix

| Stage 3 requirement | Exact live authority | Readiness |
|---|---|---|
| Consumer-neutral lifecycle | `AgentHandoffService`, `HandoffStore`, `LocalHermesChannel`, and `PeerHermesChannel` under `hermes_cli/handoff/` | Ready to extend. Creation, mechanism binding, attempts, observations, commands, leases, fencing, deadlines, and bounded evidence already have one owner. |
| Strict local and peer endpoints | `HandoffEndpoint.parse()` in `hermes_cli/handoff/models.py` | Ready. It accepts only `hermes://local/<profile>` and `hermes://peer/<peer>/<profile>` and rejects percent encoding, controls, queries, fragments, userinfo, and noncanonical shapes. |
| Canonical Bot Chat containment | `ensure_message_agent_tool()` and the execution-time gate in `tools/bot_mode_dm.py`, backed by `BOT_CHAT_TITLE` and `is_bot_mode_managed()` | Ready. The schema is injected only into a managed canonical Bot Chat and execution repeats the gate. |
| Stable Bot operation identity | Provider `tool_call_id` in `agent/tool_executor.py` | Small wiring change. The executor already receives the stable ID but does not pass it to `message_agent_tool()`. |
| Bot protocol epoch | Protocol fingerprint in `tools/bot_mode_probe.py` | Ready. The current explicit value is `2`; Stage 3 requires exactly one bump to `3` after the stable schema change. |
| Friendly-name resolution | Local roster, registered peer lookup, and Desktop relay resolution in `tools/bot_mode_dm.py`; shared peer registry in `hermes_cli/peers.py` | Ready with the resolution clarification below. Existing direct-DM and relay paths remain compatibility authorities for targets the shared endpoint directory cannot represent. |
| Durable local/peer conversation | Existing local Bot Chat CLI turn and peer DM paths, plus local/peer Runs and profile session routes | Ready with mode-aware binding extensions. Legacy friendly targets preserve their current mechanism. A new canonical URI or configured directory endpoint is the explicit opt-in to a controlled Runs conversation with durable keyed admission and follow-up. |
| Legacy local Bot path | `_start_delivery()` and the bounded `hermes -p <profile> chat ... -c "Bot Chat"` path in `tools/bot_mode_dm.py` | Ready to preserve as the accepted weaker mechanism for legacy friendly targets. It already owns attribution, query-file handling, destination locking, process tracking, and cleanup. |
| Legacy peer DM and Desktop relay | `hermes peer dm` path in `tools/bot_mode_dm.py`, `hermes_cli/subcommands/peer.py`, `tools/bot_relay.py`, `tui_gateway/methods_bot_relay.py`, and Desktop `relay.ts` | Must remain unchanged for compatibility targets. They are not durable Workflow fallbacks and are not retired in Stage 3. |
| Durable return truth | Append-only handoff events and immutable terminal result in `HandoffStore` | Schema extension required. The accepted `deliveries` concept is not present in schema version 1. |
| Session-bound delivery | `SessionDB`, `has_platform_message_id()`, gateway completion routing, and TUI completion queue ownership checks | Ready to reuse. Both gateway and TUI already prove ownership before injecting a synthetic internal turn and handle compression/restart routing. |
| Durable delivery claim rail | `claim_event_delivery()`, `complete_event_delivery()`, and `release_event_delivery()` in `tools/async_delegation.py` | Ready to extend to one new `handoff_return` event type. Today only `async_delegation` receives a durable token. |
| Delivery acceleration | `process_registry.completion_queue`, gateway completion watcher, and TUI notification poller | Ready. The in-memory queue can accelerate a durable row but must never become the source of truth. |
| Long-lived supervision | `BackgroundServiceHost` and `BackgroundServiceRegistration` in `hermes_cli/plugin_services.py`, started by web and gateway through `PluginManager` | Ready with a core registration. CLI-only installs correctly have no background host. |
| Multi-profile ownership | `profiles_to_serve()` in `hermes_cli/profiles.py` and explicit per-profile homes | Ready. The supervisor can open one service/store per served profile without mutating ambient profile state or sharing credentials. |
| Operator/public projection | `_summary()` and `_evidence_payload()` in `hermes_cli/handoff/cli.py` | Ready to extract into a small shared projection module so CLI and RPC do not fork redaction or field meanings. |
| Desktop RPC | Profile-scoped `HandlerRegistry` modules in `tui_gateway/` and `host.requestProfile()` in the Desktop | Ready with a namespace clarification. Existing `handoff.request/state/fail` already mean session transfer. |
| Desktop Bot surface | Existing roster pane and nanostore-based Bot plugin under `apps/desktop/src/plugins/hermes-bots/` | Ready for a small durable handoff inbox/inspector. Existing `$botAttention` is transient provider/blocking state and is not durable handoff truth. |
| Policy configuration | `bot_mode` defaults in `hermes_cli/config_defaults.py` and normal `config.yaml` lookup | Ready. Add one non-secret wake-policy boolean; no environment variable is needed. |

No blocking upstream gap remains.

## Required live-code clarifications

These clarify how the accepted design maps to the repository. They do not
change the architecture.

### 1. Use `agent_handoff.*` for the Desktop RPC namespace

The consolidated proposal describes operations equivalent to
`handoff.create/get/list/evidence/command`; it explicitly says those are
semantic operations, not mandatory final signatures. The live TUI gateway
already owns:

```text
handoff.request
handoff.state
handoff.fail
```

Those methods transfer a live Desktop session to a messaging platform. Reusing
that namespace would create two unrelated meanings. Stage 3 should therefore
register:

```text
agent_handoff.create
agent_handoff.get
agent_handoff.list
agent_handoff.evidence
agent_handoff.command
agent_handoff.directory
```

`directory` is the smallest read operation needed by the accepted Desktop agent
picker. The server derives the initiating profile and operator actor; the
renderer never supplies an actor, URL, credential, or transport token.

### 2. Do not guess a profile for a bare registered peer

The legacy target `peer-name` means “the peer gateway's launch profile” to
`hermes peer dm`. The registered peer entry does not identify that profile, so
it cannot be converted honestly into
`hermes://peer/<peer>/<profile>`. Assuming `default` would change behavior and
could cross a profile boundary.

Bot resolution should therefore remain:

1. explicit canonical endpoint URI;
2. configured agent-directory alias and its canonical endpoint;
3. explicit registered peer/profile target;
4. existing local roster target;
5. legacy bare-peer direct DM; then
6. existing Desktop relay target.

Only a newly explicit canonical URI or configured directory alias selects the
controlled Runs conversation. Legacy local and explicit peer/profile syntax
still enters the shared facade but preserves its current local CLI or peer-DM
mechanism. Bare peers remain on the direct-DM compatibility path until an
operator configures a canonical directory alias or supplies a canonical URI.

### 3. A conversation handoff is one durable request/reply lifecycle

Stage 3 should not turn a handoff row into an unbounded chat log. One
`HandoffSpec(mode="conversation")` admits one request and converges to one
terminal result. A new message after terminal state creates a new handoff while
reusing the same canonical Bot Chat session for conversational memory.

`handoff_id` is valid while the referenced handoff is nonterminal for a
correlated `message`/steer, or while it has exactly one pending approval for an
exact `respond`. It is rejected for terminal, mismatched-target, stale,
ambiguous, or unauthorized rows.

### 4. Stage 3 interactive response means the live exact approval protocol

The merged Runs API exposes one exact pending approval request with choices:

```text
once | session | always | deny
```

It does not expose a general natural-language question protocol. Accordingly,
`message_agent` may translate a follow-up into `respond` only when exactly one
approval is pending and the bounded message is one of those normalized choices.
Other nonterminal follow-ups use the existing correlated `message` command,
which the Runs channel maps to steer. A general question/answer protocol remains
deferred until a real channel advertises it.

### 5. Durable delivery is authoritative; wake-up is an optional projection

The handoff database owns the delivery row, lease, attempts, attention state,
and acknowledgement. `process_registry.completion_queue` is only a low-latency
signal to existing gateway/TUI consumers. If the process exits, the supervisor
requeues the due durable delivery after restart.

The returned internal event carries the delivery ID, handoff ID, origin agent,
origin session, and hop count. The gateway/TUI persists the delivery ID as the
synthetic user's platform/message ID before acknowledging delivery. Replay can
then detect the transcript record and acknowledge without running the model a
second time.

Automatic wake is controlled by `bot_mode.handoff_return_wake`. A fixed maximum
automatic hop of one is sufficient for Stage 3 and avoids a speculative tuning
setting. Disabled, unsupported, exhausted, or failed wake leaves the durable
Needs Attention item and inspector result available.

## Smallest safe contract extensions

### Handoff specification

Extend `HandoffSpec.mode` from `Literal["task"]` to
`Literal["task", "conversation"]` and add one optional, closed return route.
A Bot route contains only:

- initiating profile;
- durable canonical Bot Chat session ID;
- gateway/TUI session key when present;
- initiating model tool-call ID;
- delivery policy; and
- incoming automatic-hop count.

An operator route contains only the initiating profile and a host-generated UI
inbox identifier. It creates durable attention for Desktop-created handoffs but
never wakes a Bot or accepts a renderer-supplied actor.

No provider name, peer URL, credential, authorization material, raw transport
ID, or unrestricted metadata belongs in the route. Existing task rows have no
return route. Serialization and fingerprinting must omit an absent route so
every Stage 1/2 task specification stays byte-identical after upgrade.

For `conversation` mode, `output_schema` remains absent and the required
capability set is derived by the host. The prompt remains the bounded attributed
message already accepted by the handoff service.

### Durable deliveries and attention

Migrate `handoffs.db` from schema version 1 to version 2 by adding a single
`handoff_deliveries` table rather than another database. A delivery references
its handoff and source event sequence and stores only closed route, state, method,
lease, attempt, retry, acknowledgement, and stable failure facts. Uniqueness on
`(handoff_id, event_sequence, route_kind)` prevents observation replay from
creating duplicate returns.

Create the delivery in the same transaction that records an observation that
requires Bot attention:

- `needs_input`;
- `indeterminate`;
- `succeeded`;
- `failed`; or
- `cancelled`.

Repeated identical observation is harmless. Result text stays in the existing
private bounded terminal result; the delivery row references it rather than
copying it. Public list/get/evidence responses contain hashes, sizes, phases,
safe failure codes, and bounded redacted previews only.

Attention is an unacknowledged delivery, not an in-memory Desktop badge.
`acknowledge` is a local projection command and does not contact the destination.
It should be added to the existing closed command service without changing any
terminal transport state.

### Conversation mechanisms

Friendly local Bot targets should initially bind the accepted local Bot Chat
CLI mechanism. It reuses the existing destination lock, bounded query-file
subprocess, process receipt, and orphan recovery, but selects canonical
`Bot Chat` rather than a task session. The facade key and durable attempt
journal make a lost receipt `indeterminate`; they do not pretend the CLI has
Runs-level admission guarantees.

Existing friendly and explicit-profile peer Bot targets should initially bind
the current peer DM conversation mechanism, whose weaker turn-scoped
capabilities are recorded honestly. Extract the bounded request/session helper
needed by the channel; do not call `cmd_peer()` and do not copy the Runs
reservation logic. A bare peer stays on the legacy direct path because its
destination profile is unknowable locally.

A canonical URI or configured directory alias is new syntax and therefore the
safe explicit opt-in to a controlled conversation. For that case, resolve or
create the destination's canonical Bot Chat through the existing
profile-specific session route and submit to that session through the delivered
local/peer Runs client with the stable handoff key. That is the concrete
Stage 3 mechanism on which `handoff_id` follow-up, approval response, steer,
stop, and restart recovery are supported.

Stage 3 must not silently move a legacy friendly target merely because Runs
exists. Once any selected mechanism may have submitted, it remains immutable,
and peer DM is never used to replace an ambiguous peer Runs result.

## Message-agent integration

`message_agent` remains injected, not registered, and stays absent from every
noncanonical surface. Its schema changes only by:

- documenting canonical `hermes://local/...` and
  `hermes://peer/.../...` targets; and
- adding optional `handoff_id`.

The executor passes the provider tool-call ID already in scope. The tool derives
the key scope from the initiating profile and durable session, creates the
conversation handoff, advances once, and returns a short acknowledgement. It
does not poll during the turn.

Trusted attribution remains server-derived. A caller cannot provide sender
identity, return-route identity, hop count, peer URL, or credential. When the
turn itself was an automatic handoff return, host-only transient context carries
the incoming hop so a recursive send cannot reset the loop bound.

The protocol fingerprint increments once from `2` to `3`, causing existing
long-lived Bot Chats to refresh the stable schema one time. Subsequent turns are
byte-stable, preserving prompt caching and role alternation.

## Supervisor and restart behavior

Add one core handoff supervisor factory to the existing background-service host
generation used by web and gateway. Do not create a new scheduler thread in
each consumer and do not add a CLI daemon.

For each served profile home, one supervisor instance:

1. claims a bounded number of due handoffs fairly;
2. performs at most one bounded `advance()` operation per claimed handoff;
3. claims due return deliveries;
4. publishes a bounded `handoff_return` acceleration event; and
5. releases or reschedules work using the durable lease.

Startup scans the v2 ledger, so restart between remote completion and local
delivery, between queue publication and consumer claim, or between transcript
persistence and delivery acknowledgement converges without transport
resubmission or duplicate model execution. Destination status remains
authoritative by Run ID. Cancellation continues through the existing idempotent
command/status path and wins or loses according to the observed terminal truth.

Health reporting is the existing O(1) `BackgroundServiceHealth` snapshot with
stable codes. It must not include prompts, results, route metadata, credentials,
or remote errors.

## Desktop and operator projection

Extract the CLI's current safe summary/evidence formatting into a shared
handoff projection module, then have both CLI and `agent_handoff.*` use it. The
RPC remains profile-scoped and closes its profile-local store after each bounded
operation.

The minimum Desktop addition is a durable Handoffs section in the existing Bot
roster experience:

- poll the directory and handoff list through `host.requestProfile()`;
- display destination, mechanism/channel, phase, elapsed time, and Needs
  Attention;
- open normalized evidence and the bounded result projection;
- create a message against a selected canonical endpoint; and
- offer only authorized `respond`, `message`, `cancel`, `reconcile`, and
  `acknowledge` actions.

The Desktop must not call a peer gateway directly, mint Bot attribution, expose
raw result/error bodies, or replace the existing relay code. Polling is enough
for Stage 3; no new streaming subscription is justified.

## Real-boundary test strategy

Unit and contract tests must be supplemented by real boundaries:

- temporary profile homes and real SQLite v1-to-v2 migration;
- two real local profiles with canonical Bot Chats and the actual local Runs
  adapter;
- two authenticated temporary gateway sockets for peer conversation Runs;
- real API idempotency behavior for lost submit response, duplicate key, and
  conflicting payload;
- destination restart and authoritative Run status recovery;
- initiating gateway restart before observation and before return delivery;
- initiating TUI/Desktop disconnect before reply, followed by inspect and
  replay after reconnect;
- approval response, correlated follow-up/steer, stop, interrupted, deadline,
  and cancellation races;
- same-origin and unsafe cross-origin redirects plus ambient proxy isolation;
- destination credential and profile isolation;
- delivery crash points before queue, after queue, after transcript persistence,
  and before acknowledgement;
- fast-path plus supervisor observation producing one delivery;
- automatic return attempting another send at hop one without starting a
  second automatic wake chain;
- renderer attempts to forge actor, profile, URL, route, or credential fields;
  and
- existing peer DM and Desktop relay compatibility tests remaining green.

Inference may be replaced at the final provider boundary, but endpoint parsing,
profile scoping, authentication, redirect handling, HTTP sockets, Runs
reservation/status, SQLite stores, session persistence, supervisor claims, and
Desktop RPC codecs remain real.

## Risks and platform gaps

- The local Bot CLI fallback retains its existing POSIX-only destination lock;
  Windows parity remains Stage 5 and must not be pulled into Stage 3.
- Legacy bare-peer DM is not restart-durable through the new ledger because its
  target lacks a canonical profile. This is explicit compatibility behavior,
  not a silent claim of parity.
- General natural-language remote questions are not available in the current
  Runs protocol. Stage 3 supports exact approvals and correlated steer/follow-up
  only.
- Automatic wake can create surprising work, so it is policy-controlled,
  structurally marked, idempotent, and fixed at one automatic hop. Durable
  attention remains the fallback.
- The shared completion queue is process-local. Correctness depends on the
  handoff delivery table and restart scan, never on queue survival.
- Existing whole-Workflow timing failures and the macOS SQLite/background-thread
  shutdown bus error remain recorded baseline defects. Stage 3 should run its
  focused and real-boundary suites with retries disabled and compare any broad
  failure to the merge base before expanding scope.

## Readiness conclusion

Stage 3 is implementation-ready after approval of the accompanying plan. The
only required durable migration is `handoffs.db` v1 to v2 for return deliveries.
Controlled network submission and control stays on the delivered local/peer
Runs authorities; legacy peer DM remains an explicitly weaker compatibility
mechanism. All Bot containment stays on the existing canonical Bot Chat gate,
and all UI operations stay behind profile-scoped host RPC. No live-code finding
contradicts the accepted consolidated architecture.
