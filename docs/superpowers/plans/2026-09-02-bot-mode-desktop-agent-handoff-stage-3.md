# Bot Mode and Desktop Agent Handoff Stage 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Use
> `superpowers:test-driven-development` for every behavior change and
> `superpowers:verification-before-completion` before every commit.

**Goal:** Route supported Bot Mode messages and Desktop actions through the
consumer-neutral handoff service, preserve legacy Bot Chat and relay behavior,
and make replies, attention, and optional Bot wake-up durable across initiating
process restarts.

**Architecture:** Extend the existing profile-local handoff ledger with a
closed conversation return route and unique delivery rows. Legacy friendly
local and peer targets keep their current Bot Chat CLI and peer-DM mechanisms;
new canonical URIs and configured directory aliases explicitly select a
controlled conversation over the delivered keyed Runs rail. A single
host-owned supervisor advances handoffs and publishes durable return events to
the existing gateway/TUI completion consumers. Desktop uses profile-scoped
`agent_handoff.*` RPCs and never contacts transports directly.

**Tech Stack:** Python 3.11+, stdlib `sqlite3`/`urllib`/threading, existing
Hermes Runs and session APIs, pytest through `scripts/run_tests.sh`, Electron
React/nanostores, TypeScript, and Vitest.

**Spec:**
[`docs/proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md`](../../proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md)

**Readiness evidence:**
[`docs/assessments/2026-09-02-agent-handoff-stage-3-implementation-readiness.md`](../../assessments/2026-09-02-agent-handoff-stage-3-implementation-readiness.md)

## Global constraints

- Work only on `base`; literal `main` remains synchronization-only.
- Before Task 1, verify `git branch --show-current`, `git rev-parse HEAD`, and
  `git rev-parse origin/base`. Investigate any descendant of the accepted plan
  commit before editing.
- Follow RED-GREEN-REFACTOR in every task. Add the named failing tests, run the
  exact RED command and inspect the expected failure, implement only the task,
  rerun GREEN, refactor, and rerun GREEN.
- Run every Python test through `scripts/run_tests.sh` with
  `HERMES_TEST_FILE_RETRIES=0`. Do not substitute direct `pytest`.
- Stage exactly the task-owned paths. Preserve every unrelated tracked and
  untracked file. Make one atomic commit per task with the listed subject.
- Keep `message_agent` injected only into a managed canonical Bot Chat. Do not
  add a core model tool or mutate the schema again after the one Stage 3 epoch
  bump.
- Keep trusted actor, attribution, return route, hop count, profile scope, and
  delivery identity host-derived. Never accept them from the model or renderer.
- Never persist credentials, authorization headers, peer URLs, raw remote
  errors, unrestricted prompts/results, or unbounded transcript data in public
  evidence or delivery rows.
- Never change mechanisms after submission may have occurred. An ambiguous
  Runs response retries only with the same key; an ambiguous CLI/DM response
  becomes `indeterminate` and is not blindly resent.
- Preserve prompt caching, message-role alternation, destination profile
  isolation, credential scope, Workflow fencing, and Stage 2 cancellation and
  status truth.
- Preserve legacy bare-peer DM and Desktop relay behavior. Do not replace peer
  DM globally, retire relay, add GitLab+ICM/A2A, add the Stage 4 generic channel
  registry, or add Windows locking in this stage.
- Do not add a non-secret `HERMES_*` setting. The sole wake policy lives under
  `bot_mode` in `config.yaml`; use a fixed maximum automatic hop of one.
- Keep CLI-only installations daemon-free. Background advancement belongs only
  to the existing web/gateway background-service host.

## Baseline gate

- [ ] Run the exact focused Python baseline:

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

- [ ] Force the installed-wheel smoke through its integration marker:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  -m integration \
  -k extracted_wheel_registers_workflow_cli_from_a_clean_home -q
```

- [ ] Run the focused Desktop baseline:

```bash
(cd apps/desktop && npm run test:ui -- \
  src/plugins/hermes-bots/bot-row.test.tsx \
  src/plugins/hermes-bots/roster-actions.test.ts \
  src/plugins/hermes-bots/relay.test.ts \
  src/plugins/hermes-bots/plugin.mentions.test.ts)
```

Planning baseline on `c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d`:

```text
Python focused: 421 passed, 0 failed, 1 Windows-only skip
Installed wheel: 1 passed, 0 failed
Desktop focused: 4 files passed, 51 tests passed, 0 failed
```

If execution starts from a descendant, rerun all three commands and record the
new counts before editing.

---

## Task 1: Add the closed conversation return-route contract

**Owns:**

- `hermes_cli/handoff/models.py`
- `hermes_cli/handoff/store.py`
- `hermes_cli/handoff/__init__.py`
- `tests/hermes_cli/handoff/test_models.py`
- `tests/hermes_cli/handoff/test_store.py`

**Produces:** `mode="conversation"` and optional bounded, immutable Bot-session
or operator-inbox return routes without changing any existing task row or
fingerprint.

### RED

- [ ] Add tests proving only `task` and `conversation` are accepted;
  conversation rejects `output_schema`; a Bot route accepts only initiating
  profile, durable session ID/key, tool-call ID, closed delivery policy, and
  bounded incoming hop; an operator route accepts only profile and a
  host-generated inbox ID; unsafe/unknown/credential-shaped values fail closed;
  and task mode cannot carry a return route.
- [ ] Prove a v1 task `spec_json` loads unchanged and absent `return_route`
  leaves its exact serialized bytes, fingerprint input, and fingerprint
  unchanged.
- [ ] Run RED:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_models.py \
  tests/hermes_cli/handoff/test_store.py -q
```

### GREEN

- [ ] Extend the existing `HandoffSpec`; do not create a Bot-specific spec.
- [ ] Normalize the route with one closed validator and conditionally omit an
  absent route from fingerprinting and store serialization.
- [ ] Keep current prompt/result limits and credential-shaped rejection.
- [ ] Run GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_models.py \
  tests/hermes_cli/handoff/test_store.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/plugins/workflow/test_handoff_executor.py -q
```

### REFACTOR and commit

- [ ] Reuse current frozen mappings and canonical JSON. Do not add a route
  registry or versioned model hierarchy.
- [ ] Stage and commit exactly:

```bash
git add hermes_cli/handoff/models.py \
  hermes_cli/handoff/store.py \
  hermes_cli/handoff/__init__.py \
  tests/hermes_cli/handoff/test_models.py \
  tests/hermes_cli/handoff/test_store.py
git diff --cached --check
git commit -m "feat(handoff): define conversation return contract"
```

---

## Task 2: Add durable return deliveries and Needs Attention truth

**Owns:**

- `hermes_cli/handoff/store.py`
- `hermes_cli/handoff/service.py`
- `hermes_cli/handoff/cli.py`
- `hermes_cli/handoff/projection.py` (new)
- `tests/hermes_cli/handoff/test_store.py`
- `tests/hermes_cli/handoff/test_service.py`
- `tests/hermes_cli/handoff/test_cli.py` (new)

**Produces:** A real v1-to-v2 migration, unique Bot deliveries, durable
attention/acknowledgement, and one safe projection shared by CLI and RPC.

### RED

- [ ] Create a real v1 DB in tests and prove opening it preserves every row,
  creates `handoff_deliveries`, sets version 2 atomically, is idempotent, and
  rejects unsupported future versions.
- [ ] Prove `needs_input`, `indeterminate`, and terminal observations create one
  delivery transactionally when a Bot route exists; task rows create none;
  observation replay cannot duplicate; and result text is not copied.
- [ ] Prove delivery claim/release/complete is leased, fenced, retry-bounded,
  and restart-recoverable; stale owners cannot commit.
- [ ] Prove `acknowledge` clears attention without deleting evidence or changing
  transport truth, and public projections remain bounded/redacted.
- [ ] Run RED:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_store.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/handoff/test_cli.py -q
```

### GREEN

- [ ] Add one `handoff_deliveries` table and due index to `handoffs.db`. Store
  only delivery/event identity, closed route, method/state, lease/attempt/retry,
  acknowledgement, stable failure, and timestamps.
- [ ] Insert the delivery beside its source event in the same transaction.
- [ ] Add bounded store operations for attention, claim, completion, retry,
  failure, and acknowledgement.
- [ ] Add local-only `acknowledge` to the service command contract.
- [ ] Extract CLI summary/evidence shaping to `projection.py` without changing
  existing CLI output.
- [ ] Run GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_models.py \
  tests/hermes_cli/handoff/test_store.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/handoff/test_cli.py -q
```

### REFACTOR and commit

- [ ] Keep one transaction owner and one DB. Do not add a repository, event
  bus, or generic inbox framework.
- [ ] Stage and commit exactly:

```bash
git add hermes_cli/handoff/store.py \
  hermes_cli/handoff/service.py \
  hermes_cli/handoff/cli.py \
  hermes_cli/handoff/projection.py \
  tests/hermes_cli/handoff/test_store.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/handoff/test_cli.py
git diff --cached --check
git commit -m "feat(handoff): persist return delivery attention"
```

---

## Task 3: Resolve configured agents without weakening endpoint validation

**Owns:**

- `hermes_cli/config_defaults.py`
- `hermes_cli/handoff/directory.py` (new)
- `hermes_cli/handoff/__init__.py`
- `tests/hermes_cli/handoff/test_directory.py` (new)
- `tests/hermes_cli/handoff/test_models.py`

**Produces:** A concrete profile-scoped `handoff.agents` reader and deterministic
Bot resolution result; no generic channel or discovery registry.

### RED

- [ ] Prove valid config exposes a friendly name, canonical default endpoint,
  and endpoint list. Prove the exact resolution order: explicit URI,
  configured alias, legacy peer/profile, local roster, bare peer, relay.
- [ ] Prove ambiguity returns canonical choices and unsafe/noncanonical/raw URL,
  credential, duplicate, invalid-name, and unknown registry references fail.
- [ ] Prove only explicit URI/directory results request controlled-conversation
  capabilities; legacy results retain compatibility intent; a bare peer never
  gets a guessed profile.
- [ ] Run RED:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_directory.py \
  tests/hermes_cli/handoff/test_models.py -q
```

### GREEN

- [ ] Implement one immutable directory parser returning presentation facts and
  an existing `HandoffEndpoint`. Resolve local/peer existence through current
  authorities and load initiating-profile config per operation.
- [ ] Preserve resolution intent for the caller's capability declaration.
- [ ] Run GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_directory.py \
  tests/hermes_cli/handoff/test_models.py \
  tests/hermes_cli/test_peers.py -q
```

### REFACTOR and commit

- [ ] Do not add channel registration, network discovery, or caching.
- [ ] Stage and commit exactly:

```bash
git add hermes_cli/config_defaults.py \
  hermes_cli/handoff/directory.py \
  hermes_cli/handoff/__init__.py \
  tests/hermes_cli/handoff/test_directory.py \
  tests/hermes_cli/handoff/test_models.py
git diff --cached --check
git commit -m "feat(handoff): resolve configured bot destinations"
```

---

## Task 4: Bind conversation mode to existing Bot and Runs mechanisms

**Owns:**

- `hermes_cli/handoff/runs.py`
- `hermes_cli/handoff/local.py`
- `hermes_cli/handoff/peer.py`
- `hermes_cli/handoff/service.py`
- `hermes_cli/peers.py`
- `hermes_cli/subcommands/peer.py`
- `tests/hermes_cli/handoff/test_runs_client.py`
- `tests/hermes_cli/handoff/test_local_cli.py`
- `tests/hermes_cli/handoff/test_local_runs.py`
- `tests/hermes_cli/handoff/test_peer.py`
- `tests/hermes_cli/handoff/test_service.py`
- `tests/hermes_cli/test_peer_cmd.py`
- `tests/hermes_cli/test_peers.py`

**Produces:** Immutable conversation bindings that preserve legacy mechanisms
and use controlled Runs only for new canonical/directory intent:

| Intent | Initial mechanism |
|---|---|
| legacy friendly local | `local_bot_cli` |
| legacy explicit peer/profile | `peer_dm` |
| explicit canonical/directory local | local Runs in canonical `Bot Chat` |
| explicit canonical/directory peer | peer Runs in canonical `Bot Chat` |

### RED

- [ ] Prove canonical Bot Chat lookup/create is bounded, validates session IDs,
  uses the profile-specific authenticated base, and supplies that session to a
  stable keyed Run.
- [ ] Prove legacy local conversation reuses the existing query file,
  attribution, destination lock, `--create-if-missing`, receipt, orphan
  recovery, and exact `Bot Chat`; task mode remains unchanged.
- [ ] Prove controlled local conversation requires Runs capabilities and never
  falls back to CLI after an attempt; an ambiguous CLI receipt becomes
  `indeterminate` without resend.
- [ ] Prove legacy peer conversation uses a shared bounded peer-DM seam without
  calling `cmd_peer()`, while controlled conversation uses `peer_runs` with the
  same key and saved Run/session IDs.
- [ ] Preserve lost-response, duplicate-key, conflicting-payload,
  unsafe-redirect, ambient-proxy, credential-rotation, registry-retarget, and
  profile-isolation behavior. Peer DM must never replace an ambiguous Runs
  attempt.
- [ ] Prove service control eligibility comes from the sealed capabilities for
  local or peer Runs; compatibility CLI/DM rejects unsupported commands.
- [ ] Run RED:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_runs_client.py \
  tests/hermes_cli/handoff/test_local_cli.py \
  tests/hermes_cli/handoff/test_local_runs.py \
  tests/hermes_cli/handoff/test_peer.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/test_peer_cmd.py \
  tests/hermes_cli/test_peers.py -q
```

### GREEN

- [ ] Add canonical Bot Chat session resolution to the existing Runs client,
  using current `/api/sessions` routes and its no-proxy redirect-safe opener.
- [ ] Choose local mechanism from `spec.mode` and declared capabilities. Reuse
  the current CLI spool and `local_delivery_command()` and parameterize only
  the session title/source.
- [ ] Extract only bounded peer-DM request/session behavior into
  `hermes_cli/peers.py`; keep CLI arguments/output and legacy env fallback in
  `subcommands/peer.py`.
- [ ] Choose peer DM for legacy conversation and current Runs for controlled
  conversation/task mode. Seal honest capabilities in the binding.
- [ ] Generalize service command checks from literal `peer_runs` to sealed
  capabilities while preserving exact approval and active-state checks.
- [ ] Run GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_runs_client.py \
  tests/hermes_cli/handoff/test_local_cli.py \
  tests/hermes_cli/handoff/test_local_runs.py \
  tests/hermes_cli/handoff/test_peer.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/test_peer_cmd.py \
  tests/hermes_cli/test_peers.py \
  tests/plugins/workflow/test_handoff_executor.py \
  tests/plugins/workflow/test_remote_handoff_e2e.py -q
```

### REFACTOR and commit

- [ ] Keep one built-in local/peer dispatcher and the existing Runs reservation
  authority. Do not add a peer manager, HTTP stack, or mechanism factory.
- [ ] Stage and commit exactly:

```bash
git add hermes_cli/handoff/runs.py \
  hermes_cli/handoff/local.py \
  hermes_cli/handoff/peer.py \
  hermes_cli/handoff/service.py \
  hermes_cli/peers.py \
  hermes_cli/subcommands/peer.py \
  tests/hermes_cli/handoff/test_runs_client.py \
  tests/hermes_cli/handoff/test_local_cli.py \
  tests/hermes_cli/handoff/test_local_runs.py \
  tests/hermes_cli/handoff/test_peer.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/test_peer_cmd.py \
  tests/hermes_cli/test_peers.py
git diff --cached --check
git commit -m "feat(handoff): bind durable bot conversations"
```

---

## Task 5: Route `message_agent` through the shared facade

**Owns:**

- `tools/bot_mode_dm.py`
- `tools/bot_mode_probe.py`
- `agent/tool_executor.py`
- `tests/tools/test_bot_mode_dm.py`
- `tests/tools/test_bot_mode_probe.py`
- `tests/agent/test_tool_executor_middleware.py`

**Produces:** Optional `handoff_id`, one protocol epoch bump, stable keyed
facade creation/advance, authorized follow-up, and unchanged bare-peer/relay
compatibility.

### RED

- [ ] Re-prove `message_agent` is absent from ordinary CLI, Workflow, cron,
  subagents, unmanaged/noncanonical chats, and group-room members.
- [ ] Prove the schema adds only optional `handoff_id` and canonical target
  documentation; it exposes no actor, route, hop, URL, credential, transport,
  timeout, polling, or Workflow field.
- [ ] Prove the executor passes the provider `tool_call_id` through middleware.
- [ ] Prove key scope is initiating profile plus durable session; attribution,
  return route, delivery policy, and hop are host-derived.
- [ ] Prove explicit URI/directory targets create controlled conversation
  specs; legacy local and peer/profile targets create compatibility specs;
  `advance()` runs once; the bounded ack contains destination, phase, and ID.
- [ ] Prove equivalent tool retries return the same handoff and conflicting key
  reuse fails without another submission.
- [ ] Prove `handoff_id` verifies route owner/session/target before idempotent
  `message` or exact `respond`; terminal, stale, multiple-question,
  mismatched-target, and foreign-session calls fail closed.
- [ ] Prove hop-one return context cannot begin another automatic wake chain.
- [ ] Prove bare peer and relay still use their current compatibility paths and
  errors never expose peer URL/credential data.
- [ ] Prove protocol epoch changes exactly from 2 to 3 and remains stable.
- [ ] Run RED:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/tools/test_bot_mode_dm.py \
  tests/tools/test_bot_mode_probe.py \
  tests/tools/test_bot_relay.py \
  tests/agent/test_tool_executor_middleware.py -q
```

### GREEN

- [ ] Pass `tool_call_id` to `message_agent_tool()`, add optional `handoff_id`,
  and bump the probe protocol value once.
- [ ] Keep the current double gate. Resolve the target, build the host-owned
  route/spec, create idempotently, advance once, and return without polling.
- [ ] For a follow-up, load the row, verify its Bot route against the current
  profile/session and target, then use tool-call ID as the command ID.
- [ ] Leave `_try_relay_delivery()` and bare-peer direct DM intact.
- [ ] Run GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/tools/test_bot_mode_dm.py \
  tests/tools/test_bot_mode_probe.py \
  tests/tools/test_bot_relay.py \
  tests/agent/test_tool_executor_middleware.py \
  tests/hermes_cli/handoff/test_service.py -q
```

### REFACTOR and commit

- [ ] Keep the existing tool and module. Do not register a new model tool, Bot
  controller, or polling loop.
- [ ] Stage and commit exactly:

```bash
git add tools/bot_mode_dm.py \
  tools/bot_mode_probe.py \
  agent/tool_executor.py \
  tests/tools/test_bot_mode_dm.py \
  tests/tools/test_bot_mode_probe.py \
  tests/agent/test_tool_executor_middleware.py
git diff --cached --check
git commit -m "feat(bot): route messages through agent handoffs"
```

---

## Task 6: Run one fair multi-profile handoff supervisor in long-lived hosts

**Owns:**

- `hermes_cli/handoff/supervisor.py` (new)
- `hermes_cli/plugins.py`
- `hermes_cli/config_defaults.py`
- `tests/hermes_cli/handoff/test_supervisor.py` (new)
- `tests/hermes_cli/test_plugin_background_services.py`
- `tests/gateway/test_plugin_background_services.py`
- `tests/hermes_cli/test_web_server_plugin_services.py`

**Produces:** One core web/gateway background service that fairly advances
profile-local handoffs and publishes due durable return events; no CLI daemon.

### RED

- [ ] Prove web/gateway hosts contain one `core:agent_handoff` registration,
  CLI creates none, plugin reload cannot remove/duplicate it, and existing host
  shutdown stops it.
- [ ] Prove only valid intended profile homes are scanned, each gets its own
  store/service, credentials resolve lazily in that profile, and health exposes
  no secrets or route/result text.
- [ ] Prove one tick advances a bounded batch and one operation per handoff;
  cursor rotation prevents a busy profile/handoff from starving later work.
- [ ] Prove due deliveries are claimed before bounded `handoff_return` events
  are published; publish failure releases/reschedules; restart reclaims expired
  handoff/delivery leases and republishes pending rows.
- [ ] Prove shutdown is cooperative and health is cached O(1).
- [ ] Run RED:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_supervisor.py \
  tests/hermes_cli/test_plugin_background_services.py \
  tests/gateway/test_plugin_background_services.py \
  tests/hermes_cli/test_web_server_plugin_services.py -q
```

### GREEN

- [ ] Implement the existing `BackgroundService` protocol with a stop-event
  wait, bounded ticks, and cached `BackgroundServiceHealth`.
- [ ] Include one concrete core registration when `PluginManager` builds the
  existing host generation, outside plugin unload-owned state.
- [ ] Derive profile homes through the current gateway/profile configuration;
  never switch ambient `HERMES_HOME`.
- [ ] Publish routing/identity metadata only after the durable claim exists.
- [ ] Add `bot_mode.handoff_return_wake` to config defaults and keep the maximum
  automatic hop as a fixed value of one.
- [ ] Run GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_supervisor.py \
  tests/hermes_cli/test_plugin_background_services.py \
  tests/gateway/test_plugin_background_services.py \
  tests/hermes_cli/test_web_server_plugin_services.py \
  tests/hermes_cli/handoff/test_store.py \
  tests/hermes_cli/handoff/test_service.py -q
```

### REFACTOR and commit

- [ ] Reuse `BackgroundServiceHost`; do not add another daemon, timer framework,
  process supervisor, or plugin hook.
- [ ] Stage and commit exactly:

```bash
git add hermes_cli/handoff/supervisor.py \
  hermes_cli/plugins.py \
  hermes_cli/config_defaults.py \
  tests/hermes_cli/handoff/test_supervisor.py \
  tests/hermes_cli/test_plugin_background_services.py \
  tests/gateway/test_plugin_background_services.py \
  tests/hermes_cli/test_web_server_plugin_services.py
git diff --cached --check
git commit -m "feat(handoff): supervise durable bot returns"
```

---

## Task 7: Deliver Bot returns exactly once through existing session rails

**Owns:**

- `tools/async_delegation.py`
- `tools/process_registry.py`
- `gateway/run.py`
- `tui_gateway/server.py`
- `run_agent.py`
- `agent/conversation_loop.py`
- `agent/turn_context.py`
- `tests/gateway/test_completion_delivery.py`
- `tests/test_tui_gateway_queue_on_busy.py`
- `tests/agent/test_synthetic_turn_display_kind.py`
- `tests/agent/test_turn_context.py`
- `tests/run_agent/test_run_agent.py`
- `tests/test_hermes_state.py`

**Produces:** Bounded `handoff_return` attention/wake delivery with transcript
deduplication and hop propagation across gateway/TUI/CLI consumers.

### RED

- [ ] Prove central claim/complete/release helpers delegate a `handoff_return`
  to its profile-local handoff store while existing events stay unchanged.
- [ ] Prove success/failure/cancelled/needs-input/indeterminate formatting is
  bounded and redacted; private result is fetched only by an authorized claim.
- [ ] Prove gateway/TUI positively verify profile/session ownership, requeue a
  foreign event, and leave attention without model execution when wake is
  disabled or unavailable.
- [ ] Prove enabled wake persists `display_kind="handoff_return"`, metadata,
  and delivery ID as platform message ID before acknowledging.
- [ ] Prove replay after transcript persistence but before ack detects that ID
  and does not rerun; fast path plus supervisor also runs once.
- [ ] Prove compression resolves to the live continuation, user boundaries fail
  closed with attention retained, and hop context is installed only for the
  internal turn and cleared in `finally`.
- [ ] Add a narrow optional `persist_user_message_id` through conversation
  setup and prove it does not change API content, display metadata, ordinary
  calls, prompt caching, or role alternation.
- [ ] Run RED:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/gateway/test_completion_delivery.py \
  tests/test_tui_gateway_queue_on_busy.py \
  tests/agent/test_synthetic_turn_display_kind.py \
  tests/agent/test_turn_context.py \
  tests/run_agent/test_run_agent.py \
  tests/test_hermes_state.py -q
```

### GREEN

- [ ] Extend the existing event claim switch and completion queue; do not add a
  second queue or ledger.
- [ ] Reuse gateway/TUI drains and ownership predicates with one bounded
  handoff-return formatter.
- [ ] Thread `persist_user_message_id` beside current display fields and check
  `SessionDB.has_platform_message_id()` before replaying a return.
- [ ] Set agent-private return context around the internal turn and clear it in
  `finally`; the model schema never exposes it.
- [ ] Complete delivery only after durable transcript acceptance or proven
  prior acceptance; release every transient failure.
- [ ] Run GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/gateway/test_completion_delivery.py \
  tests/test_tui_gateway_queue_on_busy.py \
  tests/agent/test_synthetic_turn_display_kind.py \
  tests/agent/test_turn_context.py \
  tests/run_agent/test_run_agent.py \
  tests/test_hermes_state.py \
  tests/tools/test_bot_mode_dm.py -q
```

### REFACTOR and commit

- [ ] Keep current queue and synthetic-turn rails. Do not add a notification
  bus, mutate the system prompt, or inject adjacent user roles.
- [ ] Stage and commit exactly:

```bash
git add tools/async_delegation.py \
  tools/process_registry.py \
  gateway/run.py \
  tui_gateway/server.py \
  run_agent.py \
  agent/conversation_loop.py \
  agent/turn_context.py \
  tests/gateway/test_completion_delivery.py \
  tests/test_tui_gateway_queue_on_busy.py \
  tests/agent/test_synthetic_turn_display_kind.py \
  tests/agent/test_turn_context.py \
  tests/run_agent/test_run_agent.py \
  tests/test_hermes_state.py
git diff --cached --check
git commit -m "feat(handoff): deliver durable bot returns"
```

---

## Task 8: Expose profile-scoped transport-neutral Desktop operations

**Owns:**

- `tui_gateway/methods_agent_handoff.py` (new)
- `tui_gateway/server.py`
- `tests/tui_gateway/test_agent_handoff_methods.py` (new)
- `tests/tui_gateway/test_protocol.py`

**Produces:** `agent_handoff.create/get/list/evidence/command/directory` RPCs.
Existing session-transfer `handoff.request/state/fail` remains untouched.

### RED

- [ ] Prove both RPC namespaces coexist and new handlers register once.
- [ ] Prove every operation uses the authenticated selected profile home and
  closes its store.
- [ ] Prove `create` accepts only a directory alias/canonical endpoint and
  bounded message; `directory/list/get/evidence` return the shared projection;
  and `command` accepts only respond/message/cancel/reconcile/acknowledge while
  deriving actor=`operator` server-side.
- [ ] Prove renderer actor, key scope, route, hop, profile path, URL,
  credential, authorization, peer token, or raw transport ID is rejected.
- [ ] Prove ownership/capability mismatch fails with stable codes and duplicate
  create/command replays while conflicting reuse fails.
- [ ] Prove mutating/network handlers run through `_LONG_HANDLERS`.
- [ ] Run RED:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/tui_gateway/test_agent_handoff_methods.py \
  tests/tui_gateway/test_protocol.py -q
```

### GREEN

- [ ] Implement one `HandlerRegistry` module. Construct the profile-local
  service/directory inside each bounded call and close its store in `finally`.
- [ ] Reuse the shared projection and current profile/error envelopes.
- [ ] Register the module at the end-of-server seam and add only network or
  mutating operations to the current long-handler set.
- [ ] Keep polling; add no streaming subscription.
- [ ] Run GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/tui_gateway/test_agent_handoff_methods.py \
  tests/tui_gateway/test_protocol.py \
  tests/tui_gateway/test_bot_relay_methods.py \
  tests/hermes_cli/handoff/test_cli.py -q
```

### REFACTOR and commit

- [ ] Keep six concrete methods. Do not add a CRUD controller or rename the
  live `handoff.*` session-transfer methods.
- [ ] Stage and commit exactly:

```bash
git add tui_gateway/methods_agent_handoff.py \
  tui_gateway/server.py \
  tests/tui_gateway/test_agent_handoff_methods.py \
  tests/tui_gateway/test_protocol.py
git diff --cached --check
git commit -m "feat(desktop): expose agent handoff operations"
```

---

## Task 9: Add the durable handoff inbox to the Desktop Bot experience

**Owns:**

- `apps/desktop/src/plugins/hermes-bots/handoffs.tsx` (new)
- `apps/desktop/src/plugins/hermes-bots/handoffs.test.tsx` (new)
- `apps/desktop/src/plugins/hermes-bots/roster-pane.tsx`
- `apps/desktop/src/plugins/hermes-bots/plugin.tsx`
- `apps/desktop/src/plugins/hermes-bots/plugin-panes.test.tsx`

**Produces:** A combined Handoffs inbox/inspector with Needs Attention and safe
actions. Existing transient `$botAttention`, Bot Chat, and relay stay intact.

### RED

- [ ] Prove the selected profile polls `agent_handoff.directory/list`; loading,
  empty, unavailable, and stale responses do not break Bot Chat.
- [ ] Prove rows show destination, mechanism/channel, phase, elapsed time, and
  Needs Attention without raw credential/error/result fields.
- [ ] Prove opening a row fetches normalized evidence and bounded result;
  creation calls only `agent_handoff.create`; authorized actions call only the
  closed `agent_handoff.command` payload.
- [ ] Prove approval UI uses server-advertised choices, acknowledgement clears
  on the next poll, profile switching drops stale responses, and renderer code
  never contacts peer/GitLab/relay for shared handoffs.
- [ ] Re-prove current Bot row, mention, canonical chat, and relay behavior.
- [ ] Run RED:

```bash
(cd apps/desktop && npm run test:ui -- \
  src/plugins/hermes-bots/handoffs.test.tsx \
  src/plugins/hermes-bots/plugin-panes.test.tsx)
```

### GREEN

- [ ] Add one small feature that owns its request state and renders within the
  existing Bot roster experience. Poll list/get; add no socket or second store.
- [ ] Use current accessible controls, focus behavior, and
  `host.requestProfile()`. Keep failures non-destructive to chat.
- [ ] Leave `$botAttention` and `relay.ts` unchanged.
- [ ] Run GREEN and typecheck:

```bash
(cd apps/desktop && npm run test:ui -- \
  src/plugins/hermes-bots/handoffs.test.tsx \
  src/plugins/hermes-bots/plugin-panes.test.tsx \
  src/plugins/hermes-bots/bot-row.test.tsx \
  src/plugins/hermes-bots/plugin.mentions.test.ts \
  src/plugins/hermes-bots/relay.test.ts \
  src/plugins/hermes-bots/roster-actions.test.ts)

(cd apps/desktop && npm run typecheck)
```

### REFACTOR and commit

- [ ] Keep this an inspector beside Bot Chat, not a second transcript or
  transport console. Add no speculative per-bot badge.
- [ ] Stage and commit exactly:

```bash
git add apps/desktop/src/plugins/hermes-bots/handoffs.tsx \
  apps/desktop/src/plugins/hermes-bots/handoffs.test.tsx \
  apps/desktop/src/plugins/hermes-bots/roster-pane.tsx \
  apps/desktop/src/plugins/hermes-bots/plugin.tsx \
  apps/desktop/src/plugins/hermes-bots/plugin-panes.test.tsx
git diff --cached --check
git commit -m "feat(desktop): show durable agent handoffs"
```

---

## Task 10: Prove authenticated Bot handoffs and restart recovery end to end

**Owns:**

- `tests/hermes_cli/handoff/test_bot_conversation_e2e.py` (new)
- `tests/hermes_cli/handoff/test_bot_return_recovery_e2e.py` (new)
- `tests/hermes_cli/handoff/test_peer_e2e.py`
- `tests/gateway/test_completion_delivery.py`
- `tests/tui_gateway/test_agent_handoff_methods.py`
- `tests/tools/test_bot_mode_dm.py`

**Produces:** Real-boundary proof of the complete Stage 3 path. Only final
inference is doubled; sockets, auth, redirects, profiles, Runs, SQLite, session
persistence, supervisor claims, and restart remain real.

### RED

- [ ] Add tests covering two real local profiles and canonical Bot Chats;
  trusted attribution; local compatibility CLI return; and controlled local
  Runs.
- [ ] Against two authenticated temporary gateways, cover exact destination
  profile/credential/session, lost response with same key, duplicate key,
  conflicting payload, unsafe redirect, proxy bypass, and unrelated-profile
  denial.
- [ ] Cover approval/response, correlated follow-up/steer, stop, interrupted,
  deadline, and cancellation-vs-completion races.
- [ ] Cover destination restart by Run ID; initiating gateway restart before
  observation and before delivery claim; crash after transcript persistence
  before acknowledgement; and TUI/Desktop reconnect inspection/replay.
- [ ] Cover fast path plus supervisor exactly once; disabled/failed/hop-limited
  wake retaining attention; renderer forgery rejection; and unchanged bare
  peer/relay behavior.
- [ ] Run RED and ensure failures name Stage 3 behavior rather than fixtures:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_bot_conversation_e2e.py \
  tests/hermes_cli/handoff/test_bot_return_recovery_e2e.py \
  tests/hermes_cli/handoff/test_peer_e2e.py \
  tests/gateway/test_completion_delivery.py \
  tests/tui_gateway/test_agent_handoff_methods.py \
  tests/tools/test_bot_mode_dm.py -q
```

### GREEN

- [ ] Make only fixture/integration corrections here. Return any production gap
  to its owning task with a focused failing regression test and separate commit.
- [ ] Run GREEN, then repeat the two highest-risk races seven times:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_bot_conversation_e2e.py \
  tests/hermes_cli/handoff/test_bot_return_recovery_e2e.py \
  tests/hermes_cli/handoff/test_peer_e2e.py \
  tests/gateway/test_completion_delivery.py \
  tests/tui_gateway/test_agent_handoff_methods.py \
  tests/tools/test_bot_mode_dm.py -q

for stage3_run in 1 2 3 4 5 6 7; do
  HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
    tests/hermes_cli/handoff/test_bot_return_recovery_e2e.py \
    -k 'cancellation_race or transcript_ack_crash' -q || exit 1
done
```

### REFACTOR and commit

- [ ] Remove redundant mocks and reuse existing real-boundary fixtures where
  they already exist.
- [ ] Stage and commit exactly:

```bash
git add tests/hermes_cli/handoff/test_bot_conversation_e2e.py \
  tests/hermes_cli/handoff/test_bot_return_recovery_e2e.py \
  tests/hermes_cli/handoff/test_peer_e2e.py \
  tests/gateway/test_completion_delivery.py \
  tests/tui_gateway/test_agent_handoff_methods.py \
  tests/tools/test_bot_mode_dm.py
git diff --cached --check
git commit -m "test(handoff): prove durable bot return path"
```

---

## Task 11: Run installed-distribution, affected-files, and adversarial gates

**Owns:**

- `tests/plugins/workflow/test_installed_distribution_e2e.py`
- `docs/upstream-customizations/agent-handoff.yaml`
- `docs/reviews/2026-09-02-agent-handoff-stage-3-adversarial-review.md` (new)

**Produces:** Installed-wheel proof, complete affected verification, an updated
customization ledger, and a closed adversarial review before completion.

### RED

- [ ] Add an installed-wheel test proving, without source-tree imports, that
  handoff v2/supervisor modules import, `handoff` CLI registers, both
  `agent_handoff.*` and existing `handoff.*` RPC namespaces register, and a
  clean home can create/list/acknowledge a conversation handoff.
- [ ] Run RED:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  -m integration \
  -k extracted_wheel_registers_agent_handoff_stage_3 -q
```

### GREEN and complete verification

- [ ] Fix packaging only if this test proves a packaging gap; return production
  fixes to their owning task and commit separately.
- [ ] Run installed and complete affected Python gates:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  -m integration \
  -k 'extracted_wheel_registers_workflow_cli_from_a_clean_home or extracted_wheel_registers_agent_handoff_stage_3' -q

HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff \
  tests/hermes_cli/test_peer_cmd.py \
  tests/hermes_cli/test_peers.py \
  tests/hermes_cli/test_plugin_background_services.py \
  tests/hermes_cli/test_web_server_plugin_services.py \
  tests/tools/test_bot_mode_dm.py \
  tests/tools/test_bot_mode_probe.py \
  tests/tools/test_bot_relay.py \
  tests/agent/test_tool_executor_middleware.py \
  tests/agent/test_synthetic_turn_display_kind.py \
  tests/agent/test_turn_context.py \
  tests/run_agent/test_run_agent.py \
  tests/test_hermes_state.py \
  tests/gateway/test_completion_delivery.py \
  tests/gateway/test_plugin_background_services.py \
  tests/tui_gateway/test_agent_handoff_methods.py \
  tests/tui_gateway/test_bot_relay_methods.py \
  tests/tui_gateway/test_protocol.py \
  tests/test_tui_gateway_queue_on_busy.py \
  tests/plugins/workflow/test_handoff_executor.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_remote_handoff_e2e.py -q
```

- [ ] Run complete Desktop Bot tests and typecheck:

```bash
(cd apps/desktop && npm run test:ui -- src/plugins/hermes-bots)
(cd apps/desktop && npm run typecheck)
```

- [ ] Use `superpowers:requesting-code-review` for an adversarial audit of Bot
  containment/cache stability; actor/session/profile/credential/redirect trust;
  migration/event/delivery atomicity and fencing; ambiguity/mechanism
  immutability; queue/transcript/ack crash windows; compression ownership;
  wake policy/hop loops/role alternation; gateway/Desktop restart and
  cancellation races; relay/Workflow regressions; and Stage 4-5 scope creep.
- [ ] Record every finding, disposition, fix commit, and rerun in the review
  document. No HIGH finding may remain. Every code fix requires a focused RED
  regression and its own atomic commit.
- [ ] Update the customization manifest with exact Stage 3 files, commits,
  verification counts, review status, and known platform gaps.

### Final task commit

- [ ] Stage and commit exactly:

```bash
git add tests/plugins/workflow/test_installed_distribution_e2e.py \
  docs/upstream-customizations/agent-handoff.yaml \
  docs/reviews/2026-09-02-agent-handoff-stage-3-adversarial-review.md
git diff --cached --check
git commit -m "docs(handoff): record stage 3 verification"
```

## Completion checklist

- [ ] `message_agent` remains confined to managed canonical Bot Chat.
- [ ] Protocol epoch changes once to 3 and stays stable.
- [ ] Legacy local/peer and relay behavior remains compatible; canonical URI
  and directory resolution is deterministic.
- [ ] Every facade send is durable before I/O and keyed by provider operation
  identity; controlled Runs always reuse the same key and Bot Chat session.
- [ ] Ambiguity never creates an unkeyed or cross-mechanism replacement.
- [ ] Return, attention, and acknowledgement survive Bot/gateway/Desktop
  restart and cannot run the same return twice across transcript/ack crashes.
- [ ] Wake is policy-controlled, structural, and one-hop; attention remains if
  wake is disabled, unavailable, or exhausted.
- [ ] Desktop uses only profile-scoped `agent_handoff.*` and cannot forge actor
  or transport identity.
- [ ] Existing session-transfer `handoff.*`, peer DM, relay, Workflow, and
  Stage 1/2 behavior remain green.
- [ ] Installed-wheel, affected Python, Desktop/typecheck, repeated races, and
  adversarial gates pass with exact results recorded.
- [ ] Only task-owned paths were committed; unrelated files are untouched.
- [ ] Checkout is `base`; literal `main` was never used.
- [ ] Stop for maintainer review before pushing, building branded releases, or
  beginning Stage 4.
