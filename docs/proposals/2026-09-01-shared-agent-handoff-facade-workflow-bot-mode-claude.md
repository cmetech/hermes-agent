# Shared Agent Handoff Facade for Workflows and Bot Mode (Claude)

**Status:** Independent architecture and feasibility report; no implementation authorized.
**Date:** 2026-09-01
**Author:** Claude (principal-architect review), working independently of the two existing `2026-09-01-workflow-agent-to-agent-integration-*.md` proposals, which were not read.
**Baseline reviewed:** fork branch `base` @ `89f2cb6ea9` (merge-base with upstream = v0.20.6 / 2026.8.27, commit `5fc308a707`); upstream `NousResearch/hermes-agent` tag `v2026.8.31` (= v0.21.0), shallow-cloned and diffed file-by-file.
**Evidence tags used throughout:** **VERIFIED** (read in code or official docs, cited), **INFERRED** (derived from adjacent code), **RECOMMENDATION** (design choice), **UNRESOLVED** (decision the team must still make). Fork citations are `path:line` on `base`; upstream citations are prefixed `UP:`; GitLab citations are docs URLs.

---

## 1. Executive recommendation

1. **Build one small, host-owned Agent Handoff service in core (`hermes_cli/handoff/`), owned by neither the workflow plugin nor Bot Mode.** It consists of a durable SQLite store per initiating profile, a channel registry, a convergent facade, and one background supervisor registered through the existing `BackgroundService` host seam (`hermes_cli/plugin_services.py:105-112`, hosts `web`/`gateway`). Bot Mode keeps working when workflows are disabled; workflows never import Bot Chat, Desktop, or relay code; the Desktop never implements transport.

2. **Adopt the "durable convergent handoff resource" interface, with two corrections to the working hypothesis.** (a) Split admission from progression: `create(spec, initiator, handoff_key)` is idempotent by key and returns the durable `handoff_id`; `advance(handoff_id, budget)` is the convergent step that the supervisor, a foreground workflow scheduler, a CLI command, or a one-shot Bot Chat runner can all call safely. Folding creation into `advance(spec, …)` would make every caller re-supply the spec and would blur "already terminal" from "never admitted". (b) Replace the `unknown` state with an explicit non-terminal `indeterminate` state that carries a reconciliation obligation, and model cancellation as a `cancelling` phase plus a durable `cancel_requested_at` fact rather than a free-standing `cancel_requested` state. This mirrors the fenced `indeterminate`/`stopping` machinery upstream just shipped for hosted rooms (`UP:gateway/hosted_room_driver.py:42-51,1110-1190`) and the workflow engine's own outward-effect `reconcile` interaction (`plugins/workflow/store.py:17787-17790`).

3. **The Hermes channel is a core adapter with explicitly selected, checkpointed mechanisms.** Local profile-to-profile handoffs run on the transport Bot Mode already proves in production — `hermes -p <profile> chat -c "<title>" --create-if-missing -Q --query-file …` under the per-profile turn lock (`tools/bot_relay.py:561-576,632-676`) — using the canonical `Bot Chat` for conversational messages and a dedicated hidden titled session for task handoffs. Loopback `/p/<profile>/v1/runs` is a second, opt-in local mechanism (it needs the multiplexed `api_server` and a per-profile key, `gateway/platforms/api_server.py:1997-2058`). Remote handoffs use the peer registry (`bot_peers` + `HERMES_PEER_<NAME>_KEY`, `hermes_cli/subcommands/peer.py:20-22,46-76`) and the Runs API — with `Idempotency-Key` once upstream v0.21.0 is merged (`UP:gateway/platforms/api_server_runs.py:452-593`), and the synchronous `/api/sessions/{id}/chat` turn as the degraded mechanism until then. The Desktop relay and the A2A plugin remain separate mechanisms, not facade channels, until the peer/runs path proves parity.

4. **`message_agent` stays the single agent-facing send path and becomes a thin facade client.** Same name, same Bot-Chat-only injection and execution gates (`tools/bot_mode_dm.py:129-169,241-268`), same attribution prefix, same friendly targets; `target` additionally accepts an endpoint URI, and an optional `handoff_id` carries follow-ups and answers. Fast local replies still arrive through the proven background-completion notification; replies that arrive hours or days later are delivered by the supervisor as a new Bot Chat turn through a Bot-Mode-registered return route — never through the 900-second waiter (`tools/bot_relay.py:64,485-535`).

5. **Workflows assign work in the companion sidecar, not in a new node type.** A prompt node stays Archon-portable; the `.hermes.yaml` companion gains an `assignments` block naming the endpoint, interaction policy, and deadline for that node. The engine gains one `waiting` node state that releases the worker, a handoff reference in `run.json`, wake-on-terminal via the coordinator's existing durable wake rows, `output_format` validation reuse, and cancellation propagation through the facade's `cancel` command. Handoff nodes are `outward` by classification, so ambiguity surfaces as the existing `reconcile` interaction.

6. **GitLab+ICM is a bundled channel plugin registered through one new `PluginContext.register_handoff_channel(scheme, factory)` method.** Canonical objects: the **issue** (labels + open/closed) owns lifecycle; the **branch** `handoffs/<id>` owns request, context, inputs, and result files by content hash; **issue notes** with a machine header own messages, questions, replies, and claims; an **MR** is optional for repository changes. The initiator's local store remains the audit record. Feasibility is confirmed against official GitLab docs, including the absence of any REST `Idempotency-Key` (reconcile by deterministic title marker instead) and the absence of `updated_after` on notes (poll per changed issue).

7. **Honest guarantee:** at-least-once submission with keyed deduplication where the channel supports it; effectively-once *admission* per handoff generation; **never exactly-once external execution**. Post-admission fallback across channels is forbidden; pre-admission fallback is safe only before the first submit attempt is journaled and only across endpoints the initiator explicitly listed.

8. **Sequence:** merge upstream v2026.8.31 first (runs idempotency, `peer run/status/stop`, session attach); ship the core facade with the local Hermes mechanism in shadow mode; switch `message_agent`; add the workflow `waiting` state and sidecar assignments; add the Desktop Bot-page API; add the GitLab+ICM plugin against a sandbox project; enable remote Runs with idempotency; retire the waiter only after parity is measured.

---

## 2. Verified current-state architecture

### 2.1 Baseline facts

- **VERIFIED** The fork's `base` merged upstream v0.20.6 (`git merge-base base main` = `5fc308a707 chore: release v0.20.6`); local `main` is 223 commits ahead of that merge-base (upstream work not yet merged into `base`); `base` is 1514 fork commits ahead. Upstream `v2026.8.31` is `__version__ = "0.21.0"`, `__release_date__ = "2026.8.31"`.
- **VERIFIED** `tools/bot_relay.py`, `tools/bot_mode_probe.py`, `tools/bot_failure_reasons.py`, `tui_gateway/methods_bot_relay.py`, `hermes_cli/subcommands/peer.py` are byte-identical between `base` and the merge-base; `tools/bot_mode_dm.py` differs by 8 fork-only deletions (`git diff main base -- tools/bot_mode_dm.py`: the win32 slash rewrite and the `workdir=`/`_host_local=True` kwargs). The fork carries **no OTTO customization of the Bot Mode subsystem**; every delta is "base is behind upstream".
- **VERIFIED** The `/v1/runs` section of `gateway/platforms/api_server.py` equals upstream v0.20.6 except three fork hunks for the tool-choice contract (`_parse_api_tool_operation`, lines 7861-7865, 8102-8106, 8112).

### 2.2 Bot Mode send path (`message_agent`)

- **VERIFIED** Injection: `agent/turn_context.py:783-787` calls `ensure_message_agent_tool(agent)` every turn; the gate is `_session_title(agent) == BOT_CHAT_TITLE ("Bot Chat")` and `is_bot_mode_managed(home)` (any profile carrying `ui_meta['hermes-bots']` in `profile.yaml`, `tools/bot_mode_probe.py:66-82,98-113`). The schema is appended to `agent.tools` once and is byte-stable per session — prompt-cache safe by construction (`tools/bot_mode_dm.py:129-169`).
- **VERIFIED** Dispatch: `agent/tool_executor.py:2116-2140` special-cases `function_name == "message_agent"` and threads the calling `agent` so the tool can re-gate on the session title (`tools/bot_mode_dm.py:253-268`).
- **VERIFIED** Transports: local teammate → `hermes -p <resolved> chat --in ~ -c "Bot Chat" --create-if-missing -Q --query-file <tmp>` (`bot_mode_dm.py:331-350`); peer → `hermes peer dm <peer>[/<profile>] < <tmp>` (`:294-317`); cross-connection → relay envelope + waiter (`:353-367,369-426`). All three run through `terminal_tool(background=True, notify_on_complete=True)` (`:645-700`), so the reply is a background-process completion notification on the sender's next turn.
- **VERIFIED** Delivery hardening already in place: per-profile `fcntl.flock` turn lock with a bounded wait (`bot_mode.turn_wait_seconds`, default 120; `tools/bot_relay.py:590-676`; a no-op on Windows, `:642-645`), one policy-gated retry that never mints a fresh session (`tools/bot_failure_reasons.py:86-113`; `bot_mode_dm.py:552-596`), typed failure reasons (`bot_failure_reasons.py:27-61`), plaintext temp-file hygiene (`bot_mode_dm.py:428-520`), and a bounded one-shot linger so a recipient's own `message_agent` reply is not killed on exit (`cli.py:1431-1456`; `tools/process_registry.py:1569-1620`; `terminal.oneshot_completion_wait_seconds`, default 600).
- **VERIFIED** The reply path is in-memory and short-lived: completion events live in `process_registry.completion_queue`; the relay waiter gives up after `REPLY_WAIT_SECONDS = 900` (`bot_relay.py:64`); envelopes expire at drain after `bot_mode.envelope_ttl_seconds` (900, `hermes_cli/config_defaults.py:2848-2864`). Only `delegate_task(background=true)` completions are durable (`tools/async_delegation.py:142-187`, table `async_delegations` in `state.db`, replay cap 48 h, `:392-447`) — `message_agent` does not use that rail.
- **VERIFIED** Attribution is a server-side text prefix: `Message from 🤖 <handle> (@<handle>): ` (`bot_mode_dm.py:292`). Inside a 1:1 Bot Chat transcript there is no structural author field; the desktop roster detects bot-authored previews by regex (`apps/desktop/src/plugins/hermes-bots/plugin.js:8777`).
- **VERIFIED** Relay (`tools/bot_relay.py`, `tui_gateway/methods_bot_relay.py`): four JSON-RPC methods (`bot_relay.roster.sync`, `outbox.drain`, `deliver`, `reply`); the Desktop is the courier and holds every socket; `bot_relay.deliver` on the target gateway spawns the same `local_delivery_command` under the turn lock with a 600 s timeout (`methods_bot_relay.py:75-178`). Files: `<root>/bot_relay/{roster.json,outbox,claimed,replies,locks}`.
- **VERIFIED** Peer DMs (`hermes_cli/subcommands/peer.py`): registry `config.yaml → bot_peers` (per-profile config), key in `.env` as `HERMES_PEER_<NAME>_KEY`; `dm` finds/creates the remote canonical Bot Chat via `GET/POST /api/sessions` and runs one synchronous turn via `POST /api/sessions/{id}/chat` (`:112-152,265-271`), `DM_TIMEOUT_S = 600`. Known fork defect fixed upstream (#93935): `_load_peers()` is profile-scoped while the roster probe reads the machine root, so a secondary-profile bot's peer DM sees an empty registry (`UP:tools/bot_mode_dm.py:306-321`).

### 2.3 Runs API (`/v1/runs`)

- **VERIFIED (fork)** Routes at `gateway/platforms/api_server.py:2368-2373` (POST create; GET status; GET events SSE; POST approval/steer/stop), mirrored under `/p/{profile}` (`:8668-8673`). State is in-memory dicts (`:1651-1669`); `_RUN_STATUS_TTL = 3600`, `_RUN_STREAM_TTL = 300` (`:7732-7733`); terminal statuses expire to 404 (`:8537-8576,8285-8296`); nothing survives a restart; no idempotency key on runs (`Idempotency-Key` is honored only by chat-completions/responses through an in-memory 300 s cache, `:1440-1485,5427-5449`); no list endpoint; SSE has no cursor/replay and the queue is dropped on disconnect (`:8344-8346`); approvals are FIFO with a 300 s default timeout (`tools/approval.py:3431-3448,4548-4601`); steer only while `running` (`:8447-8455`); stop is cooperative (`:8502-8529`); a run supplied a `session_id` does **not** load that session's transcript (`:7880-7920` vs `_conversation_history_for_session` used only by session routes); concurrency is a per-listener cap (429, `hermes_cli/config_defaults.py:3240-3246`).
- **VERIFIED (upstream v0.21.0)** Runs extracted to `UP:gateway/platforms/api_server_runs.py` (1474 lines) with a durable `RunIdempotencyStore` (`UP:gateway/platforms/api_server_run_idempotency.py`: SQLite `$HERMES_HOME/runs_idempotency.db`, `BEGIN IMMEDIATE`, PK `(scope, idempotency_key)`, fingerprint compare, `RETENTION_SECONDS = 24h`, prune only when terminal), `Idempotency-Key` on `POST /v1/runs` (replay returns 202 + `Idempotency-Replayed: true` and bypasses the concurrency cap; conflict → 409 `idempotency_key_conflict`), a new terminal `interrupted` state when the owning process died, per-scope run ownership (404 for other tenants), `request_id`-targeted approvals, `session_id` transcript loading (`UP:api_server_runs.py:598-601`), `/v1/capabilities.features.runs_idempotency`, and RoomLink grant auth. Docs: `UP:website/docs/user-guide/features/api-server.md:447-453`.
- **VERIFIED (upstream v0.21.0)** `hermes peer run --idempotency-key`, `peer status`, `peer stop` (`UP:hermes_cli/subcommands/peer.py:244-445`): `run` probes `/v1/capabilities`, ensures the remote canonical Bot Chat, and POSTs `{"input", "session_id"}` with `Idempotency-Key`; requests route through `hermes_cli.urllib_security.open_credentialed_url` (already present in the fork, unused by `peer.py`).

### 2.4 Multi-profile gateway

- **VERIFIED** `/p/<profile>/` prefix middleware (`api_server.py:2205-2313`) fails closed to 404 for unknown/unserved profiles and enters `gateway.run._profile_runtime_scope` (`gateway/run.py:2216-2249`), which sets the ContextVar HERMES_HOME override and the secret scope; served set from `profiles_to_serve(multiplex=True, profile_allowlist=…)` (`hermes_cli/profiles.py:1056-1128`); secondary profiles must not bind ports (`gateway/config.py:448-472`; `gateway/run.py:15815-15851`).
- **VERIFIED** Auth is `Authorization: Bearer <API_SERVER_KEY>`; a named profile must have its **own** `API_SERVER_KEY` (≥16 chars) in its `.env`, else 401 — the listener key is rejected (`api_server.py:1997-2058`). ContextVars do not cross executor threads, so the run path re-enters the scope explicitly (`:7502-7528,7983-8064`).
- **VERIFIED** Under `/p/<profile>/`, config, model, SOUL, credentials, toolsets, memory, and the SessionDB (`<profile home>/state.db`) resolve to the destination profile (`:2486-2508,2975-2979,3208-3236`; `agent/system_prompt.py:288-311,386`). **Working directory is not per-profile** (no cwd handling in `api_server.py`; terminal falls back to the gateway process cwd, `tools/terminal_tool.py:1619-1636`), unlike the CLI twin `--in ~`.

### 2.5 Desktop Bot Mode

- **VERIFIED** `apps/desktop/src/plugins/hermes-bots/plugin.js` (16k lines) registers a `Bots` pane, a `Cronjobs` pane, mention completion/middleware, and the relay loops (roster every 60 s, drain every 30 s or on `bot_relay.outbox.pending`). A bot is a profile; the canonical chat is found by `session.list {profile, title:"Bot Chat", include_hidden}` and created with `session.create {profile, title, hidden:true}` with adopt-on-conflict (`plugin.js:5745-5890`). It calls 33 gateway RPC methods; no `runs`/`groups` calls. The attention badge is display-only and keyed on the typed failure reasons (`:194-283`). Group rounds run in the renderer with turn/epoch/hold bookkeeping (`:8173-8340`).
- **VERIFIED** Topology: the desktop spawns one `hermes serve` backend per profile it chats through (pool, LRU cap 3, `apps/desktop/electron/main.ts:1450-1463`); in "app-global remote mode" one remote backend serves every profile via `?profile=` (`:10895-10915`; `tui_gateway/server.py:2044-2070,2154-2160`).
- **VERIFIED (upstream)** v0.21.0 splits the plugin into ~50 TS modules, adds 18 `groups.*` RPCs and gateway-hosted rooms (`UP:tui_gateway/methods_groups.py`, `UP:gateway/hosted_rooms.py`, `hosted_room_driver.py`, `hosted_room_peer*.py`, `UP:gateway/platforms/api_server_room_{dispatch,grants}.py`), with the doctrine "Cross-gateway links are direct gateway-to-gateway connections — Desktop is a viewer, not a relay" (`UP:website/docs/user-guide/bot-mode.md:155-163`) while the DM relay remains Desktop-couriered (`:126`).

### 2.6 Workflow subsystem (fork-only)

- **VERIFIED** `RunStore` is profile-scoped: `$HERMES_HOME/workflows/admission.sqlite3` + per-run `run.json` and `events.jsonl` (`plugins/workflow/store.py:3161-3213`); attempts live inside `run.json` (`:11241-11262`); coordinator is a plugin background service on `web`/`gateway` with a single-leader fenced lease (`coordinator.py:644-775`; `coordinator_store.py:606-704`), sweeps ≤2 s, durable wake rows (`coordinator_store.py:231-252`); node claims carry leases and heartbeats (`store.py:12679,17338-17434`); a prompt node runs in a fresh `plugin_agent_worker` subprocess per attempt (`agent/plugin_agent.py:1598-1700`; `plugin_agent_worker.py:2178-2216`) while a scheduler thread blocks for the turn (max 4 parallel nodes).
- **VERIFIED** Node states: `pending, ready, claimed, running, waiting_retry, waiting_resolution, paused, interrupted, succeeded, failed, cancelled, skipped`; run statuses `queued, running, waiting_retry, recovery_pending, paused, interrupted, succeeded, failed, cancelled, abandoned` (`sanitize.py:48-73`). Interaction types `approval, workflow_approval, loop_input, loop_signal_confirmation, capability, reconcile` (`sanitize.py:85-92`). `effect_classification ∈ {replay_safe, outward}`; a crash in an outward attempt yields `paused` + `reconcile` instead of retry (`store.py:17560-17640,17787-17790`). Stall detection: `runnable_progress_stalled` after 60 s without a runnable node, `semantic_progress_stalled` after 300 s (`store.py:10855-10950`).
- **VERIFIED** Needs Attention is a projection, not a flag: run-derived items (pending interactions, `failed`, stalled health) merged with outbox notifications of kinds `{approval_required, input_required, failure, stalled, reconciliation_required}` (`dashboard/plugin_api.py:2298-2417,2453-2617`; `notifications.py:40-48,1550-1605`). Operator actions: `approve, reject, provide-input, resume, retry, reconcile, cancel, abandon, archive, restore` with `expected_version` CAS and `interaction_id` (`actions.py:10-92`; `plugin_api.py:2840-2960`).
- **VERIFIED** Sidecar (`<stem>.hermes.yaml`) fields: `language_compatibility, delivery_defaults, required_services, retention, tags, outward_action_nodes, outward_action_policy, execution_environment, overlap_policy, pause_lane_policy, concurrency_key, limits, resource_limits, required_secrets, scheduling` (`language_schema.py:1977-2004`); it cannot set trust or topology (`schema.py:2413-2425`). `outward_action_policy` is accepted but has no runtime consumer (**code/doc gap**).
- **VERIFIED** There is **no existing seam** for delegating a node to another Hermes profile or peer: `delegate_task` is denied on every workflow node (`executors/ai.py:996-998,1515-1517`), the only delegation is the in-worker `workflow_agent` child, and `_observe_attempt` reasons only about local PIDs (`store.py:17784-17829`).

### 2.7 Plugin surface, A2A, Kanban, GitLab

- **VERIFIED** `PluginContext` (`hermes_cli/plugins.py:1534-3812`) exposes ~45 registration points including `register_background_service(name, factory, *, hosts)` (`:1799`), `register_tool` (`:1914`; shadowing without `override=True` is a logged no-op, `:1958-1964`), `register_cli_command` (`:2276`; hard collision error), `register_platform` (`:3124`), `register_hook` (`:3459`), and per-category provider registrations; provider categories use per-module `register_provider/get_provider(scope)` (e.g. `providers/__init__.py:59-64,287` with origin precedence); there is no generic category registry. Background services: `BackgroundService.run(stop_event)/health()`; hosts `web` (`hermes_cli/web_server.py:549`) and `gateway` (`gateway/run.py:13479-13488`); safe mode starts none. Plugin runtime configuration/secrets: `ctx.configuration().setting()/secret()` (`hermes_cli/plugins.py:1787-1795`; `hermes_cli/plugin_configuration.py:730-745`), host-owned keyring storage.
- **VERIFIED** `GatewayPluginDeliveryPort` mints opaque return-route capabilities from a verified adapter source and fences each send with `(capability, idempotency_key)` receipts in `gateway-plugin-delivery.sqlite3` (`gateway/plugin_delivery.py:34-345`); the web host has no delivery port (`hermes_cli/plugins.py:4697`). The workflow outbox uses it for chat-originated runs (`plugins/workflow/notifications.py:837-865`).
- **VERIFIED** A2A (`plugins/platforms/a2a/`): upstream code, protocol v1.0 JSON-RPC, stdlib HTTP server, in-memory `TaskStore` (500 terminal records), no client idempotency key, SSE streaming, HMAC push, per-profile forwarding by spawning `hermes chat -q … -Q --source a2a` with `HERMES_HOME` set (`adapter.py:849-894`), inbound framing `PRIVACY_PREFIX` + injection defang (`security.py:180-226`), prefix-based SSRF check for callbacks only; opt-in platform; upstream v0.21.0 gates the client tools behind `check_fn` (#95681).
- **VERIFIED** Kanban: workers are `hermes -p <profile> --cli chat -q "work kanban task <id>"` subprocesses (`hermes_cli/kanban_db.py:10931-11148`); claims are CAS row updates under `BEGIN IMMEDIATE` (`:4799-4919`), `host:pid` claim locks, 15-minute leases, 60-second auto-heartbeats, PID/heartbeat/TTL reclaim (`:5129-5294,9074-9370`), a two-failure breaker (`:9374-9550`), per-run `task_runs` rows; explicitly single-host.
- **VERIFIED** Ericsson GitLab plugin (`plugins/ericsson-gitlab/`): PAT via plugin-configuration secret (`auth.py:82-83`), `PRIVATE-TOKEN` header, httpx with `trust_env=False`/no redirects (`_common/transport.py:138-168`), GET-only bounded retries with `Retry-After` ≤5 s (`_common/client.py:31,89-96,134`), 2 MiB response cap, 13 write tools (branch, commits with `actions[]`, MR create/update/note/approve/merge, CI actions) gated by `dry_run`/`confirm`, argument-digest approval rule keys, and `PluginToolAdmission`; **no Issues API and no Labels API**; `_common/` is edited only in the `ericsson-capabilities` source repo and vendored (`_common/__init__.py:3-9`); workflow core references connectors only by service vocabulary (`capabilities/ericsson.json`, `requires:`).

---

## 3. Requirements and invariants

**Functional (from the brief, restated as testable statements):**

- R1 A workflow node can assign work to another Hermes agent and wait for a correlated result without holding a worker.
- R2 Bot Mode can send a message or task to a teammate through the same service, with local delivery primary and remote delivery supported.
- R3 A non-real-time GitLab+ICM channel must work where no direct peer connections or messaging gateways are allowed.
- R4 Channels coexist; the endpoint URI selects the channel; one logical agent may have several endpoints; credentials never appear in URIs or workflow definitions.
- R5 One logical handoff uses exactly one endpoint after admission.

**Ownership invariants (verified as the current code's own boundaries and kept):**

- I1 Initiating profile owns: workflow definition and run, trust/policy (`plugins/workflow/trust.py`), attempts/retries, artifacts and result validation (`output_resolution.py`, `executors/base.py:228-265`), Activity Board and Needs Attention projections.
- I2 Destination profile owns: system prompt and identity (`agent/system_prompt.py:288-311,386`), model and provider credentials (`gateway/run.py:2216-2249`, secret scope), memory, tools and permissions, filesystem and environment, approvals (`tools/approval.py:268-300`, `single_query_mode`), transcript (`<profile>/state.db`) and cost.
- I3 Profiles are independent islands by design (AGENTS.md "Intentional design, not a gap"); the facade must not couple them beyond the message boundary.

**Hermes core invariants (AGENTS.md):**

- I4 Prompt caching is sacred: the injected `message_agent` schema must remain byte-stable for a session's life; a schema change is a one-time, loud, capability-epoch break (`tools/bot_mode_probe.py:328-411`, `protocol_version` salt), never per-turn drift.
- I5 Narrow waist: no new core model tool; extend `message_agent`; workflow gets no model tool (`plugins/workflow/plugin.yaml: provides_tools: []`).
- I6 Surface capability is a property of the session, never of the process env.
- I7 Non-secret settings go in `config.yaml`; secrets in `.env` or host-owned secret storage.
- I8 Plugins do not modify core files; widen the generic plugin surface instead.

**Durability invariants (derived):**

- D1 Intent is persisted before any external side effect; ambiguous external outcomes are reconciled, never blindly repeated (`store.py:17787-17790`; `UP:hosted_room_driver.py:1110-1190`).
- D2 Every state-changing step is fenced by a claim lease so multiple processes can observe one store (`coordinator_store.py:606-779`; `hermes_cli/kanban_db.py:4799-4919`).
- D3 Polling is the correctness path; SSE/webhooks are wakeups only (`gateway/plugin_delivery.py`, `notifications.py` receipts; GitLab has no reliable ordering guarantees for webhooks).

---

## 4. Alternatives considered

Three materially different interface shapes were evaluated. Pseudocode is Python-flavored; consumers are shown once each.

### 4.1 Remote-job / run facade

```python
class RemoteJobs:
    def start(self, endpoint: str, prompt: str, *, session: str | None, idempotency_key: str) -> JobHandle
    def status(self, handle: JobHandle) -> JobStatus            # queued|running|waiting_for_approval|completed|failed|cancelled
    def steer(self, handle: JobHandle, text: str) -> None
    def approve(self, handle: JobHandle, request_id: str, choice: str) -> None
    def stop(self, handle: JobHandle) -> None
    def wait(self, handle: JobHandle, timeout: float) -> JobStatus
```

Workflow usage: an executor calls `start()` then blocks in `wait()` (or polls `status()` from the scheduler loop). Bot usage: `message_agent` calls `start()` and spawns the existing background waiter that polls `status()`.

- Hides: HTTP/CLI transport, auth, SSE reconnect, approval request ids.
- Bad at: anything without a job-shaped counterpart. A Bot Chat DM is a *turn in a conversation*, not a job; a GitLab issue that a human-in-the-loop agent picks up next week is not a job either. It also invites the caller to hold a thread in `wait()` — exactly what the workflow engine must avoid (R1).
- Failure/recovery: the handle is the only durable fact; lost-response recovery depends entirely on the remote's idempotency (absent in the fork's Runs, present upstream), and the caller must persist the handle itself — every consumer re-implements durability.
- Verdict: correct as the **internal shape of the Hermes Runs mechanism** (it maps 1:1 to `/v1/runs` and to the subagent control plane `delegate_task(action=list|steer|stop)`, `tools/delegate_tool.py:403-582`), wrong as the shared facade.

### 4.2 Mailbox / message facade

```python
class Mailbox:
    def send(self, to: str, message: Message, *, reply_to: str | None, idempotency_key: str) -> MessageId
    def receive(self, cursor: Cursor, *, limit: int) -> tuple[list[Message], Cursor]
    def ack(self, message_id: MessageId) -> None
```

Workflow usage: the node sends a request message and later scans inbound messages for one whose `reply_to` matches; the scheduler needs its own correlation table and its own notion of "done". Bot usage: natural — `message_agent` sends, the Bot Chat receives.

- Hides: transport, envelope framing, at-least-once delivery.
- Bad at: correlation, terminal outcomes, cancellation, approvals, deadlines — all of which become conventions on top of message bodies, re-invented per consumer. The current relay is exactly this shape (`outbox/`, `replies/`) and its limits are visible: a 900 s waiter, TTL-expired envelopes, no durable correlation across restarts.
- Failure/recovery: at-least-once with consumer-side dedup; recovery after a lost send is "send again", which is unsafe for outward work.
- Verdict: correct as the **internal shape of conversational delivery** (Bot Chat turns, GitLab notes), wrong as the shared facade.

### 4.3 Durable convergent handoff resource (recommended)

```python
class AgentHandoffService:
    def validate(self, endpoint: str, *, initiator: InitiatorContext) -> BoundEndpoint
    def create(self, spec: HandoffSpec, initiator: InitiatorContext, *, handoff_key: str) -> HandoffSnapshot   # idempotent
    def advance(self, handoff_id: str, *, budget: AdvanceBudget) -> AdvanceResult   # one bounded step; convergent
    def command(self, handoff_id: str, command: HandoffCommand, *, command_id: str, actor: Actor) -> HandoffSnapshot
    def get(self, handoff_id: str) -> HandoffSnapshot
    def list(self, query: HandoffQuery) -> HandoffPage
    def evidence(self, handoff_id: str, *, after: int, limit: int) -> EvidencePage
```

Workflow usage:

```python
# executor (attempt thread) — returns immediately
bound = handoffs.validate(assignment.endpoint, initiator=ctx.initiator())
snap = handoffs.create(HandoffSpec.from_node(node, bound, deadline=...), ctx.initiator(),
                       handoff_key=f"{run_id}:{node_id}:{attempt_generation}")
store.mark_node_waiting(run_id, node_id, handoff_id=snap.handoff_id)      # releases the worker
# coordinator sweep — on wake or timer
for ref in store.waiting_handoffs(run_id):
    res = handoffs.advance(ref.handoff_id, budget=AdvanceBudget(wall=2.0))
    if res.snapshot.terminal: store.finalize_waiting_node(...)             # validate output, succeed/fail
    elif res.snapshot.phase == "needs_input": store.pause_node(..., pending_interaction={"type": "handoff_input", ...})
```

Bot usage:

```python
snap = handoffs.create(HandoffSpec(message=body, mode="conversation"), InitiatorContext.bot(profile, session_id),
                       handoff_key=tool_call_id)
handoffs.advance(snap.handoff_id, budget=AdvanceBudget(wall=1.0))         # submit now if possible
return json.dumps({"status": "sent", "handoff_id": snap.handoff_id, ...})  # reply arrives later via return route
```

- Hides: idempotent admission, channel binding, submit/observe/reconcile loops, claim leases, checkpoints, deadlines, evidence, return-route delivery.
- Bad at: streaming tokens (deliberately out of scope; the Desktop can open the destination's own transcript instead), and sub-second latency (every step is a durable write; acceptable for handoffs, not for tool calls).
- Failure/recovery: intent is durable before submission; ambiguous submission lands in `indeterminate` with a reconcile obligation; restart resumes from the stored adapter checkpoint; commands are idempotent by `command_id`.
- Verdict: **recommended.** The other two shapes become adapter internals: 4.1 for Hermes Runs, 4.2 for Bot Chat turns and GitLab notes.

### 4.4 Ownership alternatives rejected

- **Facade inside the workflow plugin.** Rejected: Bot Mode must work with workflows disabled (`plugins.enabled` opt-in, `hermes_cli/plugins.py:4926-4946`), and core must never import a plugin (AGENTS.md, plugin rule).
- **Facade inside Bot Mode (`tools/bot_mode_dm.py`).** Rejected: workflows would depend on the Bot-Chat gate, the desktop relay, and a file that upstream churns (19 changed lines in v0.21.0 alone).
- **A Desktop-driven service.** Rejected: the Desktop is a viewer/courier that is only present while a window is open; upstream is explicitly moving cross-gateway work off the Desktop (`UP:bot-mode.md:155-163`).
- **Kanban as the handoff bus.** Rejected for the same reasons the workflow design rejected mirroring nodes into Kanban (`docs/design/portable-workflow-orchestration.md`, "Mirror every workflow node into a physical Kanban task — rejected"): a second scheduler with its own claim/retry semantics, single-host by design, and no remote or GitLab counterpart.

---

## 5. Recommended shared architecture

```
                    initiators (thin clients)                       channels (adapters)
   ┌──────────────────────────────┐                        ┌─────────────────────────────────────┐
   │ workflow executor+coordinator│──┐                     │ hermes://  (core)                    │
   │  (plugins/workflow)          │  │                     │   mechanisms: cli-oneshot | runs     │
   ├──────────────────────────────┤  │   AgentHandoffService│   targets: local profile | peer      │
   │ message_agent (tools/        │──┼──►  hermes_cli/handoff/ ──►├─────────────────────────────────────┤
   │  bot_mode_dm.py)             │  │   ├ store.py (SQLite)  │ gitlab+icm://  (bundled plugin)      │
   ├──────────────────────────────┤  │   ├ service.py (facade)│   issue+branch+notes, polling        │
   │ Desktop Bot page → REST/RPC  │──┘   ├ channels.py (reg.) ├─────────────────────────────────────┤
   │  (hermes_cli/web_routers/    │      ├ supervisor.py      │ future explicit channels             │
   │   handoffs.py)               │      └ return_routes.py   └─────────────────────────────────────┘
   └──────────────────────────────┘             │
                                                ▼
                                   return routes (registered by initiator kinds)
                                   workflow: coordinator wake row   bot: Bot Chat turn   operator: none
```

**Placement decisions (answering Q1):**

| Component | Lives in | Why |
|---|---|---|
| Facade, contracts, store, supervisor, channel registry | new core package `hermes_cli/handoff/` (`contracts.py`, `store.py`, `service.py`, `channels.py`, `supervisor.py`, `return_routes.py`, `cli.py`) | Same tier as the existing host-owned generic seams (`hermes_cli/plugin_services.py`, `plugin_invocation.py`, `plugin_configuration.py`, `kanban_db.py`). Importable by `tools/bot_mode_dm.py` and by `plugins/workflow` without either importing the other. Ledgered as UNION seams in `docs/upstream-customizations/`. |
| Built-in `hermes://` channel | `hermes_cli/handoff/hermes_channel/` (core) | It needs only core facilities: profile resolution (`hermes_cli/profiles.py`), the peer registry (`hermes_cli/subcommands/peer.py`), the proven local delivery command (`tools/bot_relay.local_delivery_command`), turn locks, and `urllib_security`. |
| `gitlab+icm://` channel | bundled plugin `plugins/handoff-gitlab-icm/` registered via `ctx.register_handoff_channel("gitlab+icm", factory)` | GitLab types never enter core or workflow code; the plugin is fork-only content (Bucket 3). Reuses the vendored `_common/` transport policy by vendoring, not by importing `plugins.ericsson_gitlab` (that package must stay import-free from core and may be disabled). |
| Durable store | `$HERMES_HOME/handoffs.sqlite3` of the **initiating** profile, WAL, `BEGIN IMMEDIATE`, 0600 | Same per-profile isolation as `workflows/admission.sqlite3`, `runs_idempotency.db`, `state.db`. Multiple processes (gateway, `hermes serve`, CLI) share it through claim leases. |
| Observation supervisor | one `BackgroundService` (`run(stop_event)/health()`) registered by core for hosts `{web, gateway}` through the existing `BackgroundServiceHost`; leader-elected per store like the workflow coordinator | Reuses the lifecycle the workflow design already paid for (`docs/superpowers/specs/2026-07-18-plugin-background-services-…`). Core registration needs a reserved owner key (`plugin="hermes-core"`) — a small internal widening of `PluginManager._register_background_service`, not a plugin-facing change. |
| Return-route deliverers | registry in `hermes_cli/handoff/return_routes.py`; `bot_chat` deliverer registered by `tools/bot_mode_dm.py` at import; `workflow` deliverer registered by the workflow plugin | The facade never knows what a Bot Chat or a workflow run is; it calls `deliver(route, event)` on a registered kind. |
| Desktop API | `hermes_cli/web_routers/handoffs.py` (core FastAPI router, session-token auth) + an events frame | Same seam the fork already uses for route extraction (`web_routers/{skills,tools}.py`) and the Desktop's `ctx.rest` door (`apps/desktop/src/plugins/kanban/api.ts:1-10`). |

**How the forbidden couplings are prevented:**

- Workflow → Bot Mode: the workflow plugin imports only `hermes_cli.handoff`; the `bot_chat` return route is a string kind resolved at runtime.
- Bot Mode → workflows: `tools/bot_mode_dm.py` imports only `hermes_cli.handoff`; no workflow symbol exists in the facade.
- Desktop → transport: the Desktop calls `POST /api/handoffs` and `POST /api/handoffs/{id}/commands`; it never opens sockets on behalf of a channel (the legacy relay remains Bot-Mode-only and is listed for retirement).
- GitLab types → core: the channel protocol exchanges only `HandoffSpec`, `Checkpoint (opaque JSON)`, `Observation`, `ExternalRef (kind, id, safe_url)`; the plugin owns every GitLab object.
- Channel plugins → global state: adapters receive a `HandoffSpec` and their own checkpoint and return observations; they cannot read the store, other handoffs, workflow state, or Bot state. Registration collisions are rejected (core schemes reserved; first registration wins with a logged error, mirroring bundled-first memory-provider precedence).
- Narrow waist and caching: no new model tool; `message_agent`'s schema changes once (epoch bump); the facade adds no prompt content; the supervisor runs no model.

## 6. Facade and adapter contracts

### 6.1 Facade (consumer-facing)

```python
# hermes_cli/handoff/service.py — RECOMMENDATION
class AgentHandoffService:
    def validate(self, endpoint: str, *, initiator: InitiatorContext) -> BoundEndpoint:
        """Parse + policy-check the URI, resolve the channel, resolve the target identity.
        No external submission. May perform a bounded read-only probe when the channel
        declares `probe_on_bind` (e.g. GET /user on GitLab, GET /v1/capabilities on a peer)."""

    def create(self, spec: HandoffSpec, initiator: InitiatorContext, *, handoff_key: str) -> HandoffSnapshot:
        """Idempotent admission. handoff_id = derive(initiator.scope, handoff_key).
        Persists spec + bound endpoint + `prepared` in one transaction. A second call with the same
        key and an identical spec fingerprint returns the existing snapshot; a different fingerprint
        raises HandoffKeyConflict (mirrors RunIdempotencyStore 'conflict')."""

    def advance(self, handoff_id: str, *, budget: AdvanceBudget) -> AdvanceResult:
        """One bounded, convergent step under a claim lease:
        prepared      -> submit (or reconcile-then-submit)        -> submitted | indeterminate
        submitted     -> observe                                 -> active | needs_input | terminal
        active        -> observe; deliver queued commands
        needs_input   -> deliver queued responses; observe
        cancelling    -> deliver cancel; observe                 -> cancelled | (succeeded|failed if it beat us)
        indeterminate -> reconcile                               -> submitted | prepared(resubmit ok) | failed
        terminal      -> no-op (returns the same durable outcome)
        Returns (snapshot, next_observe_at, work_done). Never raises for channel errors; they become
        evidence + backoff. Raises only for store corruption or an unknown id."""

    def command(self, handoff_id: str, command: HandoffCommand, *, command_id: str, actor: Actor) -> HandoffSnapshot:
        """Durably enqueue `message` | `respond(interaction_id, answer)` | `cancel(reason)`.
        Idempotent by (handoff_id, command_id). Delivery happens inside `advance`."""

    def get(self, handoff_id: str) -> HandoffSnapshot: ...
    def list(self, query: HandoffQuery) -> HandoffPage: ...            # by initiator kind/scope, phase, attention flag, updated_after; keyset
    def evidence(self, handoff_id: str, *, after: int = 0, limit: int = 200) -> EvidencePage: ...
```

`AdvanceBudget(wall_seconds, max_channel_calls)` bounds one step so the supervisor's sweep, a foreground workflow scheduler, or a one-shot CLI runner can call it safely (the workflow coordinator already budgets sweeps at 2 s, `plugins/workflow/coordinator.py:313`).

**Why `create` is separate from `advance` (assessment of the hypothesis).** The hypothesis merged "persist intent and create-or-find the external handoff" into `advance(spec, context)`. Two consumers need the id *before* any external call: the workflow must record `handoff_ref` in `run.json` inside the same claim that releases the worker, and `message_agent` must return the id to the model in its acknowledgement. Keeping the spec out of `advance` also makes the supervisor stateless with respect to consumers and prevents a late `advance(spec)` from re-admitting a handoff that is already terminal. The convergent behavior the hypothesis asks for is preserved, one method later.

### 6.2 Channel adapter (plugin-facing)

```python
# hermes_cli/handoff/channels.py — RECOMMENDATION
class HandoffChannel(Protocol):
    scheme: str                                   # "hermes", "gitlab+icm"
    def capabilities(self) -> ChannelCapabilities  # interactions: bool, cancel: bool, message: bool,
                                                   # latency_class: "seconds"|"minutes"|"days",
                                                   # keyed_submit: bool (dedup by external_key), probe_on_bind: bool
    def bind(self, endpoint: EndpointRef, initiator: InitiatorContext) -> BoundEndpoint
    def submit(self, bound: BoundEndpoint, spec: HandoffSpec, external_key: str) -> SubmitOutcome
        # Accepted(external_ref, checkpoint) | Rejected(reason_class, detail) | Indeterminate(probe_hint)
    def reconcile(self, bound: BoundEndpoint, external_key: str, checkpoint: Checkpoint | None) -> Reconciliation
        # Found(external_ref, checkpoint) | NotFound | Ambiguous(candidates)
    def observe(self, bound: BoundEndpoint, checkpoint: Checkpoint, budget: AdvanceBudget) -> Observation
        # phase, interactions[], messages[], result | None, evidence_links[], checkpoint', next_observe_at, destination_identity
    def deliver(self, bound: BoundEndpoint, checkpoint: Checkpoint, command: HandoffCommand) -> CommandOutcome
        # Delivered(receipt) | Rejected(reason) | Indeterminate
```

Adapters are pure with respect to the store: every durable fact they need round-trips through the opaque `Checkpoint` (JSON ≤ 64 KiB, versioned by the adapter). `SubmitOutcome.Indeterminate` is a first-class result, not an exception, because both transports can genuinely lose the acceptance (`UP:api_server_runs.py:556-558` "A lost-acceptance replay must resolve…"; GitLab `POST /issues` has no idempotency key).

### 6.3 Return routes (initiator-facing)

```python
class ReturnRouteDeliverer(Protocol):
    kind: str                                          # "bot_chat", "workflow", "operator"
    def deliver(self, route: ReturnRoute, event: HandoffEvent, *, idempotency_key: str) -> DeliveryReceipt
```

`DeliveryReceipt` reuses `hermes_cli/plugin_invocation.DeliveryReceipt` (`delivered | retryable_failure | permanent_failure | outcome_uncertain | unauthorized`). The facade records the receipt so a reply is delivered at most once per `(handoff_id, event_seq)`.

### 6.4 Plugin extension point (Q13, smallest)

One additive method on `PluginContext`:

```python
def register_handoff_channel(self, scheme: str, factory: Callable[[HandoffChannelContext], HandoffChannel]) -> None
```

`HandoffChannelContext` carries `configuration()` (the plugin's own validated settings/secrets, `hermes_cli/plugins.py:1787-1795`), the initiating profile name, and a bounded logger — nothing else. Core schemes (`hermes`) are reserved; a plugin registering a reserved or already-taken scheme is rejected with a logged error (never silently shadowing, consistent with `register_tool`'s no-override rule at `hermes_cli/plugins.py:1958-1964`).

---

## 7. Shared state and lifecycle model

### 7.1 Phases (assessment of the candidate states)

| Candidate | Recommendation | Rationale |
|---|---|---|
| `prepared` | keep | intent durable, nothing external yet |
| `submitted` | keep | acceptance receipt persisted |
| `active` | keep | destination is working |
| `needs_input` | keep | an interaction is open (approval, clarification, question) |
| `cancel_requested` | **replace with `cancelling` + fact** | cancellation is an intent overlaid on any non-terminal phase; a phase named `cancelling` records "cancel delivered/being delivered", while `cancel_requested_at`/`command_id` are durable facts. This preserves the pre-cancel phase for audit and matches Runs `stopping` and the driver's `cancel_generation` (`UP:hosted_room_driver.py:42-51`). |
| `succeeded`, `failed`, `cancelled` | keep (terminal) | `failed` carries `failure_class` (§14) |
| `unknown` | **replace with `indeterminate`** | non-terminal, carries a reconciliation obligation and a bounded reconcile budget; "unknown" invited callers to treat it as a status rather than a task |
| — | add `expired` as a `failed` sub-class, not a phase | deadline handling is `cancelling` → `failed(deadline_exceeded)` |

Terminal set `{succeeded, failed, cancelled}`; attention set `{needs_input, indeterminate, failed}` plus `active` or `cancelling` past their deadline (the same list §14.4 projects).

### 7.2 Transitions (single writer per handoff, under claim lease)

```
prepared ──submit ok──────────────► submitted ──observe──► active ──► succeeded | failed
   │                                   ▲    │                 │
   │ submit indeterminate              │    └──► needs_input ─┘ (respond → active)
   ▼                                   │
indeterminate ──reconcile found────────┘
   │ reconcile not found (no side effect proven) ─► prepared (resubmit permitted, generation+1)
   │ reconcile ambiguous / budget exhausted ──────► failed(abandoned_indeterminate) [operator: abandon]
cancel command on any non-terminal ──► cancelling ──► cancelled | succeeded | failed(destination finished first)
deadline passed on submitted|active|needs_input ──► cancelling(reason=deadline) ──► failed(deadline_exceeded) | cancelled
```

Generations: `execution_generation` increments only on `indeterminate → prepared`; the external key carries the generation suffix (`<handoff_id>.g<N>`) so a re-submission can never be confused with a duplicate of the first (the driver uses the same device, `UP:hosted_room_driver.py:1372`).

### 7.3 Store schema (SQLite, per initiating profile)

```
handoffs(handoff_id PK, handoff_key, key_scope, spec_fingerprint, spec_json, initiator_json, endpoint_uri,
         bound_json, channel_scheme, channel_version, mechanism, phase, generation, external_ref_json,
         checkpoint_json, result_json, failure_class, failure_detail, deadline_at, cancel_requested_at,
         attention INTEGER, created_at, updated_at, terminal_at, next_observe_at, observe_backoff_seconds,
         claim_owner, claim_epoch, claim_expires_at, return_route_json, return_delivery_state)
UNIQUE(key_scope, handoff_key)
handoff_commands(handoff_id, command_id, kind, payload_json, actor_json, state pending|delivered|failed|superseded,
                 created_at, delivered_at, receipt_json, PRIMARY KEY(handoff_id, command_id))
handoff_events(handoff_id, seq, at, kind, actor_json, phase_before, phase_after, external_ref_json,
               payload_json_redacted, payload_digest, PRIMARY KEY(handoff_id, seq))
handoff_deliveries(handoff_id, event_seq, route_kind, state, receipt_json, PRIMARY KEY(handoff_id, event_seq, route_kind))
supervisor_lease(singleton: owner_id, epoch, boot_id, heartbeat_at, expires_at)        # same shape as coordinator_lease
```

Claim leases follow the fork's proven pattern: `BEGIN IMMEDIATE`, owner id `host:pid:start_time`, monotonic+boot-id clock (`plugins/workflow/lease_clock.py:16-63`), fenced updates `WHERE claim_owner=? AND claim_epoch=?`.

### 7.4 Identity derivation

- `handoff_id = "hf_" + base32(sha256(install_id ‖ key_scope ‖ handoff_key))[:26]` where `install_id` is the initiating install's stable id (upstream uses `home_install_id`; the fork can derive it from the root home's `brand.json`/a new `install_id` file — **UNRESOLVED**: choose the source of `install_id`; recommendation: a random id persisted once at `$HERMES_ROOT/install_id`).
- `key_scope` = `workflow:<profile>` | `bot:<profile>` | `operator:<profile>`; `handoff_key` = `run_id:node_id:attempt_generation` (workflow), the LLM `tool_call_id` (bot; falls back to `session_id:turn_seq`), a client UUID (operator).
- `external_key = handoff_id + ".g" + generation` is what the channel uses for keyed submission (`Idempotency-Key` on Runs; `[hf:<id>.gN]` title marker and branch name on GitLab).

### 7.5 Consumer-neutral data model (minimum contracts, Q3)

Ownership column: **S** = shared service (stored and interpreted by the facade), **W** = workflow-only (lives in `run.json`/RunStore, referenced by id), **B** = Bot-only (lives in the Bot Chat / desktop meta, referenced by id). The facade never stores W or B fields beyond an opaque `initiator.ref`.

| Contract | Fields | Owner |
|---|---|---|
| `HandoffSpec` | `mode: task\|conversation`, `prompt` (task) or `message` (conversation), `summary` (≤80 chars), `output_schema` (canonical JSON schema + fingerprint, task only), `input_artifacts[] {name, sha256, size, media_type, ref}`, `deadline` (absolute UTC), `interaction_policy: pause\|deny\|auto_cancel`, `hop_count`, `fallback_endpoints[]` (pre-admission only), `labels{}` (free-form, bounded) | S |
| `InitiatorContext` | `kind: workflow\|bot\|operator\|operator_on_behalf_of`, `profile`, `install_id`, `key_scope`, `principal` (operator) / `session_id` + `tool_call_id` (bot) / `run_id` + `node_id` + `attempt_generation` (workflow), `return_route {kind, ref}`, `assurance: verified_adapter\|local_admin_claim` (reuses `hermes_cli/plugin_invocation.PluginInvocationContext` vocabulary) | S (identity) ; W/B (`ref` payloads) |
| Stable operation identity | `handoff_id`, `handoff_key`, `key_scope`, `generation`, `external_key`, `spec_fingerprint` | S |
| `EndpointRef` / `BoundEndpoint` | `uri`, `scheme`, `authority`, `path`; bound adds `channel_version`, `mechanism`, `destination_identity {profile, install_id}` \| `{peer, origin, profile}` \| `{townhall, project_id, bot_user_id, inbox}`, `capabilities` (from the channel), `bound_at` | S |
| `Checkpoint` | opaque JSON ≤64 KiB, `version`, channel-defined (Hermes: `session_id`, `run_id`, `last_event_seq`, `subprocess identity`; GitLab: `issue_iid`, `branch`, `head_sha`, `last_note_id`, `last_issue_updated_at`) | S (stores) ; channel (interprets) |
| `HandoffSnapshot` | `handoff_id`, `phase`, `generation`, `endpoint`, `mechanism`, `destination_identity`, `external_ref {kind, id, safe_url?}`, `open_interaction?`, `result?`, `failure {class, detail_tail}?`, `deadline_at`, `cancel_requested_at?`, `attention: bool`, `state_version`, `created_at/updated_at/terminal_at`, `next_observe_at`, `links[]` | S |
| `Interaction` | `interaction_id` (channel-stable), `kind: approval\|question\|clarify`, `prompt_redacted`, `prompt_digest`, `choices[]?`, `requested_at`, `expires_at?`, `answered_by?` | S ; W maps it to `pending_interaction{type:"handoff_input"}` ; B renders it in the reply text and the drawer |
| `ResultEnvelope` | `status: succeeded\|failed`, `output_text` (≤256 KiB inline) or `output_ref`, `output_json?` (validated by W against `output_schema`), `artifacts[] {name, sha256, size, media_type, ref}`, `usage {input_tokens, output_tokens, cache_read, cache_write, cost_usd?}`, `effects[] {kind, ref, reversible}`, `destination_transcript_ref {profile, session_id}` \| `{issue_iid, commit_sha}`, `result_digest`, `provenance: channel\|transcript` | S (envelope) ; W (validation outcome, artifact publication) ; B (rendering) |
| `HandoffCommand` | `command_id`, `kind: message\|respond\|cancel`, `payload {text}` \| `{interaction_id, answer}` \| `{reason}`, `actor`, `expected_version?` | S |
| Audit evidence | `handoff_events` row (§14.1) | S ; projected by W (`evidence` kind `handoffs`) and B (`/api/handoffs/{id}/events`) |

---

## 8. Workflow integration

### 8.1 Authoring surface: sidecar assignment, not a new node type (Q4)

**RECOMMENDATION.** Keep the portable workflow Archon-valid (`NODE_TYPES` are fixed, `plugins/workflow/language_schema.py:1485-1496`; unknown node types fail `archon-2026-07` validation) and put routing in the companion:

```yaml
# review.hermes.yaml
language_compatibility: archon-2026-07
outward_action_nodes: [security-review]          # required for assigned nodes (enforced at admission)
assignments:
  security-review:
    endpoint: hermes://local/security-reviewer   # or hermes://spark/security-reviewer, gitlab+icm://corp-townhall/security-reviewer
    interaction_policy: pause                    # pause | deny | auto_cancel   (what to do on needs_input)
    deadline: PT4H                               # ISO-8601 duration from submission
    input_artifacts: [$research.output]          # references resolved by output_resolution
    on_deadline: fail                            # fail | cancel_and_fail
```

Why not a dedicated `agent:` node: (a) the sidecar already carries Hermes-specific execution policy for exactly this class of concern (`outward_action_nodes`, `execution_environment`, `required_services`; `language_schema.py:1977-2004`), keeping Ericsson/OTTO routing out of the portable file (design goal 7, `docs/design/portable-workflow-orchestration.md`); (b) the prompt node already declares everything the destination needs (`prompt`, `output_format`, `output_type`), and reusing `output_format` gives result validation for free (`executors/base.py:228-265`); (c) a workflow can then be run unassigned (local prompt node) or assigned (handoff) without editing the graph — the "same workflow, different endpoint" requirement. A dedicated node type is justified only if a future assignment needs fields with no prompt-node equivalent (e.g., fan-out to N agents); defer.

Admission rules: an assigned node must be a `prompt` node, must be listed in `outward_action_nodes` (so crash semantics are outward), may not set `context: shared` or `persist_session` (the destination owns its session), and its endpoint must pass `validate()` at trust/preflight time so the risk summary (`trust.py:298-311`) can show "assigns work to `<endpoint>`" before the operator trusts the package. Credentials are never in the sidecar; `required_secrets` may name the peer key or the town-hall secret by **name only** (`schema.py:2465`).

### 8.2 Execution lifecycle (worker released while waiting)

1. **Claim + submit.** The `AgentNodeExecutor` sees an assignment and, instead of building a `PluginAgentRunRequest`, renders the prompt with the same `_prompt` path (`executors/ai.py:948-978`), builds `HandoffSpec(mode="task", prompt, output_schema=admitted schema, artifacts=input manifest, deadline)`, calls `create()` with `handoff_key=f"{run_id}:{node_id}:{claim.owner_epoch}"`, then `advance()` once with a 2 s budget. It returns a new `NodeExecutionResult("waiting", metadata={"handoff_ref": {...}})`.
2. **Release.** `complete_node(status="waiting")` writes `node.state = "waiting"`, `node.handoff_ref`, drops the `worker_claims` row (capacity freed), and appends `node_handoff_submitted` (or `node_handoff_indeterminate`). The run stays `running` with node health `waiting` (health vocabulary already has `waiting`, `sanitize.py:74-84`). **Engine change:** `waiting` is a new node state; the stall detector must exempt runs whose only unfinished nodes are `waiting` (`store.py:10855-10950`, `runnable_progress_stalled` would otherwise fire after 60 s) and instead use the handoff deadline.
3. **Observe.** The supervisor advances the handoff; on every phase change it calls the `workflow` return route, which writes a durable coordinator wake (`coordinator_store.record_coordinator_wake`, `:231-252`, reason `handoff_changed`). The coordinator's sweep re-claims the node (existing `claim_node`) and reads `handoffs.get(handoff_id)`. In foreground CLI runs (no host), the scheduler loop itself calls `advance()` for its own waiting nodes each pass — polling is the correctness path.
4. **Interactions.** `needs_input` → per `interaction_policy`: `pause` → node `paused` with `pending_interaction{type: "handoff_input", interaction_id, handoff_id, prompt_digest}` (a new interaction type; projected into Needs Attention like `loop_input`); the operator's `provide-input` action becomes `command(respond)`; `deny` → `command(respond, denied)`; `auto_cancel` → `command(cancel)`.
5. **Finalize.** Terminal `succeeded` → the coordinator re-claims, validates `result.output` against the admitted schema through the existing `parse_validate_canonicalize` path, writes the output descriptor (`output_resolution.py:565-580`) with `provenance = handoff`, records `usage/cost` from the result envelope in `attempt.metadata`, and completes the node `succeeded`; schema failure → node `failed(error_code="handoff_output_invalid")` with retry per the node's `retry` policy (a retry creates a **new attempt generation → new handoff**, never a resubmission of the old one). `failed` → node `failed` with `error_code = "handoff_" + failure_class`; `cancelled` → node `cancelled`.
6. **Restart.** Nothing is in memory: `waiting` nodes are re-discovered from `run.json`; the facade resumes from the checkpoint; `_observe_attempt` is extended with one branch: if `node.handoff_ref` exists, liveness is the handoff's phase, not a PID (`store.py:17784-17829`).
7. **Cancellation.** `cancel_run` (`store.py:18202-18470`) gains one step: for every `waiting` node, `command(cancel, command_id=f"{run_id}:{node_id}:cancel:{desired_status_version}")`; the node stays `waiting` (health `cancelling`) until the handoff reaches a terminal phase, then lands `cancelled`; an `indeterminate` handoff at cancel time follows the existing outward rule and pauses with `reconcile` (`:18420-18470`).
8. **Needs Attention.** Run-derived items add `handoff_input` (pending interaction) and `handoff_indeterminate` (health); the outbox gets `input_required` / `reconciliation_required` / `failure` notifications through the existing `notification_kind` mapping (`notifications.py:285-320`) by emitting the journal events `node_handoff_input_required`, `node_handoff_reconciliation_required`, and the existing `run_failed`.

### 8.3 Reconcile semantics for assigned nodes

An assigned node is `effect_classification="outward"` by construction, so the existing store rule applies verbatim: a crash or lease expiry during submission is `outcome_uncertain` and pauses with `reconcile`; the operator's `reconcile` action now has a real backend — `advance()` on an `indeterminate` handoff, whose `reconcile` finds the external object by key. Only when the channel proves `NotFound` may the run resume by resubmitting (generation+1).

---

## 9. Bot Mode and `message_agent` integration

### 9.1 What is preserved (all VERIFIED as current behavior)

- Tool name `message_agent`; injection only into a canonical Bot Chat on a Bot-Mode-managed install (`tools/bot_mode_dm.py:129-169`); execution re-gate on the session title (`:253-268`); attribution prefix `Message from 🤖 <handle> (@<handle>): ` (`:292`); friendly targets (`researcher`, `hermes`, `<peer>/<agent>`, `<handle>@<connection>`); the "compose it yourself, never paste the user's words" protocol text (`tools/bot_mode_probe.py:257-283`); the capability-epoch cache-break discipline (`:328-411`).
- One agent-facing send path: `message_agent` remains the only tool; the Desktop composer middleware keeps delegating to it (`plugin.js:16062-16120`).

### 9.2 What changes

**Schema (one-time epoch break).** `target` description gains "or an endpoint URI (`hermes://…`, `gitlab+icm://…`)"; a new optional `handoff_id` parameter: "reply to, or answer a question from, an existing handoff". `protocol_version` bumps to 3 (`bot_mode_probe.py:385`) so every eternal Bot Chat prompt refreshes once. No other schema change; `message` stays ≤ `MESSAGE_MAX_CHARS`.

**Resolution order for `target`.** (1) URI → `validate()`; (2) friendly name → `handoff.agents.<name>.default` endpoint in the root `config.yaml` (non-secret; see §17); (3) legacy roster resolution exactly as today (local profile → `hermes://local/<profile>`, `<peer>/<agent>` → `hermes://<peer>/<agent>`, relay roster → legacy relay path). Ambiguity is an error listing the exact forms, as today.

**Send.** `create(HandoffSpec(mode="conversation", message=prefix+body), InitiatorContext.bot(profile, session_id), handoff_key=tool_call_id)` then one bounded `advance()`. The tool returns the existing `{"status": "sent", "to": label, "process_id"?, "handoff_id"}` acknowledgement — the model's instructions ("do NOT wait or poll") are unchanged.

**Follow-up / response.** `message_agent(target=<same>, message=…, handoff_id=hf_…)` → `command(message)` or, when the handoff is `needs_input`, `command(respond)`. The tool refuses a `handoff_id` the calling profile does not own (store lookup by `key_scope`).

**Reply delivery — how a reply hours or days later reaches the initiating Bot Chat (Q5).** Two cooperating paths, both durable:

1. *Fast path (unchanged UX).* The tool still spawns the existing background runner (`terminal_tool(background=True, notify_on_complete=True)`, `bot_mode_dm.py:645-700`), but the runner now executes `hermes -p <initiating profile> handoff wait <handoff_id> --timeout <=900` (the store is per initiating profile) instead of a raw transport. It prints `Reply from @x: …` on completion exactly as today so the sender wakes through the proven completion-notification rail (`tools/process_registry.py:1738-1800`), and on timeout prints "still pending; the reply will be delivered to this chat when it arrives" and exits 0. This keeps CLI-only Bot Chats and installs without a host working.
2. *Durable path.* When the handoff reaches a terminal phase (or `needs_input`) after the fast path gave up, the supervisor calls the `bot_chat` return route registered by `tools/bot_mode_dm.py`: it delivers `Reply from 🤖 <dest> (@<dest>) [handoff hf_…]: …` as a **new turn** into the initiator's canonical Bot Chat using the same `local_delivery_command(profile, file)` under `acquire_turn_lock` (`tools/bot_relay.py:561-576,632-676`) — role-alternation-safe because it is a fresh user-role turn, never a splice (the same reason `async_delegation` uses the completion rail, `tools/async_delegation.py:12-26`). `handoff_deliveries` guarantees at-most-once per event across the fast path and the supervisor: the fast path claims the delivery row before printing; the supervisor skips claimed rows.

The 900 s relay waiter and the 900 s envelope TTL then govern only the legacy relay path.

### 9.3 Relay and peers

- The Desktop relay stays a Bot-Mode-only transport for cross-connection teammates and is **not** modeled as a facade channel: it exists only while a Desktop that knows both connections is open, which cannot satisfy R1/D1. It is retired only after `hermes://<peer>` over Runs with idempotency covers the same targets (upstream's own direction, `UP:bot-mode.md:155-163`).
- `hermes peer dm` remains the CLI twin; the facade's remote mechanism calls the same endpoints (`/api/sessions` + `/chat`, or `/v1/runs`) directly through `urllib_security.open_credentialed_url` rather than shelling out, and fixes the profile-scoped registry defect by always reading `bot_peers` from the machine root (as `_peers()` already does, `bot_mode_probe.py:171-190`) — matching upstream #93935.

### 9.4 Privacy and attribution protections

The prefix is still applied server-side inside the facade's Hermes channel (never by the model or the Desktop); the facade records `authored_by = {kind: "bot", profile, session_id, tool_call_id}` only when the call arrives from the tool executor; the REST/RPC surface can only mint `operator` or `operator_on_behalf_of` actors (§10). Message bodies are stored redacted in evidence (§14); a `content_digest` links the evidence row to the exact bytes delivered.

## 10. Desktop Bot experience

### 10.1 Transport-neutral gateway API (Q6)

A core FastAPI router `hermes_cli/web_routers/handoffs.py`, mounted under `/api/handoffs` with the dashboard session token (`X-Hermes-Session-Token`, `hermes_cli/web_server.py:726`) — the same door the Desktop already uses for every profile's backend (`ctx.rest` / `host.requestProfile`), so it works on local, SSH, remote-URL, and cloud connections without new sockets. All bodies and responses are the consumer-neutral contracts of §7; no channel type leaks.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/handoffs/agents` | agents known to this profile: `{name, display, endpoints:[{uri, channel, default, latency_class, capabilities, health}]}` from `handoff.agents` config + local profiles + `bot_peers` + registered channel inboxes |
| POST | `/api/handoffs` | submit: `{endpoint | agent, spec, handoff_key, actor}` → snapshot (202). `actor` may only be `operator` or `operator_on_behalf_of`; the server ignores any client-supplied `bot` actor |
| GET | `/api/handoffs?scope=…&phase=…&attention=1&updated_after=…` | list (keyset) |
| GET | `/api/handoffs/{id}` | snapshot |
| GET | `/api/handoffs/{id}/events?after=&wait_seconds=` | evidence timeline, long-poll (same shape as `/api/plugins/workflow/runs/{id}/events`) |
| POST | `/api/handoffs/{id}/commands` | `{command_id, kind: message|respond|cancel, payload}` → snapshot; 409 on stale `expected_version` |
| GET | `/api/handoffs/{id}/links` | safe external references `[{kind, label, url}]` built server-side from allowlisted origins (peer URL origin, town-hall origin); never raw adapter data |

Wakeups: one events frame `handoff.changed {handoff_id, phase, attention}` on the existing per-connection events socket (the pattern the kanban plugin rides, `apps/desktop/src/plugins/kanban/completion-notify.ts:1-30`); polling remains the correctness path.

### 10.2 Bot page surfaces

- **Roster row / Bots home:** an endpoint chip per bot (`local`, `peer:spark`, `gitlab:corp-townhall`) with the default marked; the existing attention glyph gains handoff-derived reasons (`needs_input`, `indeterminate`, `failed`) sourced from `GET /api/handoffs?attention=1` rather than the display-only `$botAttention` atom (`plugin.js:194-283`), so attention survives reloads and other Desktops.
- **Chat header "Send task via…":** a picker over the bot's endpoints that calls `POST /api/handoffs` with `actor = operator_on_behalf_of` when the user is inside another bot's chat, or `operator` from the Bots home.
- **Handoff drawer:** phase, channel, endpoint, deadline, timeline, open interaction with a reply box (→ `respond`), cancel (→ `cancel`), and safe links (Hermes run/session, GitLab issue/branch/MR).

### 10.3 Actor model (Q6, false attribution)

| Origin | Actor recorded | Rendered as |
|---|---|---|
| `message_agent` tool call inside an agent turn | `{kind:"bot", profile, session_id, tool_call_id}` — minted only by the tool executor path | "🤖 researcher" |
| Bot page action by the user | `{kind:"operator", principal:<dashboard principal>}` | "You" |
| Bot page action from inside bot X's chat, sent to bot Y | `{kind:"operator_on_behalf_of", principal, on_behalf_of: X}` | "You (via @X)" — the delivered message prefix reads `Message from 👤 operator via @X:` |

The Hermes channel applies the prefix from the actor kind; a REST client cannot obtain the `🤖` prefix. The desktop renders `authored_by.kind` structurally instead of regex-matching the prefix (`plugin.js:8777`).

---

## 11. Local and remote Hermes channel design

### 11.1 Endpoint grammar

```
hermes://local/<profile>              # a profile on this install
hermes://<peer>[/<profile>]           # a registered peer (bot_peers); bare peer = its default profile
hermes://<name>                       # shorthand: resolved as local profile, else peer; ambiguity is a validation error
gitlab+icm://<townhall>/<agent-inbox> # a configured town hall (project + credentials by name) and an inbox
```

No host:port, userinfo, query, or fragment is ever accepted in a `hermes://` URI; the network location comes from the peer registry. This is the SSRF boundary (§15).

### 11.2 Local mechanisms (Q7) — assessment

| Candidate | Verdict | Evidence |
|---|---|---|
| **`cli-oneshot`**: `hermes -p <profile> chat --in ~ -c "<title>" --create-if-missing -Q --query-file <tmp>` under the per-profile turn lock | **primary for both conversations and tasks** | Proven transport used by `message_agent`, the relay, Kanban workers, and A2A forwarding (`tools/bot_relay.py:561-576`; `tui_gateway/methods_bot_relay.py:123-131`; `hermes_cli/kanban_db.py:10931-11148`; `plugins/platforms/a2a/adapter.py:849-894`). Runs entirely under the destination's `HERMES_HOME` (identity, credentials, tools, memory, transcript, `--in ~` cwd). Limits: approvals are decided by `approvals.single_query_mode` (default `deny`, `tools/approval.py:3484-3496`), so interactions are not surfaceable; one turn per process; wall timeout per turn (600 s today). |
| Multiplexed loopback `/p/<profile>/v1/runs` | **opt-in second mechanism (`mechanism: runs`)** | Surfaces approvals (`approval.request` SSE + `POST …/approval`), steer, stop; identical to the remote path. Requires `api_server` enabled with `multiplex_profiles` and, because every named profile has its own `API_SERVER_KEY`, one local peer entry per destination profile (`hermes peer add local-<profile> --url http://127.0.0.1:8642 --key <that profile's key>`; the facade then targets `/p/<profile>/`), and has no per-profile cwd (`api_server.py`: no cwd handling). Restart durability only after the v0.21.0 merge. |
| Existing Bot Chat subprocess | same as `cli-oneshot` with title `Bot Chat` | Conversation mode. Task mode uses a dedicated hidden session titled `Handoff: <handoff_id>` (`--create-if-missing` mints it; `hermes_cli/main.py:2100-2180`) so long tasks do not pollute the canonical chat and the transcript is isolated and linkable. |
| Direct in-process `AIAgent` | **rejected** | Would run the destination's identity inside the initiator's process with the initiator's secret scope, process-global tool registry, and env; violates I2 and the multiplex isolation rules (`agent/secret_scope.py:33-38`, `gateway/run.py:2092-2103`). |
| Kanban-style worker | **subsumed** | It *is* `cli-oneshot` plus a board; the board is the wrong coordination primitive here (§4.4). |
| `PluginAgentRunner` (workflow's node worker) | **rejected for cross-profile** | Runs under the initiator's home with initiator-dictated tool policy (`agent/plugin_agent.py:517-575`: no `profile` field); correct for same-profile nodes, wrong for a handoff whose destination must own tools and approvals. |
| Desktop backend per-profile `hermes serve` RPC | **not a backend-side mechanism** | Ports/tokens are owned by the Electron main process (`apps/desktop/electron/main.ts:1450-1463`); a gateway-side facade cannot reach them, and the Desktop must not implement transport. |

Mechanism selection happens once in `bind()` (recorded in `bound_json.mechanism`) and never changes after the first submit attempt is journaled.

### 11.3 Which mechanism for which consumer

- Bot Mode conversation (`mode=conversation`): `cli-oneshot` into `Bot Chat` — exactly today's transport, now durable.
- Workflow task (`mode=task`): `cli-oneshot` into `Handoff: <id>` by default; `runs` when the sidecar or config selects it and the destination's interactions must be surfaced (`interaction_policy: pause` with a channel that lacks `interactions` is an admission warning: interactions will be auto-denied by the destination's `single_query_mode`).
- Long-running local tasks (hours): `cli-oneshot` is bounded by one turn's wall time; document that multi-hour local work needs either the `runs` mechanism or a GitLab+ICM handoff; do not extend the one-shot timeout indefinitely (the one-shot linger design is deliberately bounded, `hermes_cli/config_defaults.py:405-415`).

### 11.4 Remote mechanisms (Q7)

| Mechanism | Assessment |
|---|---|
| **Runs** (`POST /p/<profile>/v1/runs` + `Idempotency-Key`, GET status, SSE events, approval/steer/stop) | **primary remote mechanism** once v0.21.0 is merged: durable replay 24 h, `interrupted` on remote restart, `session_id` transcript loading, ownership scoping. Until then: no idempotency, 1 h terminal retention, memory-only — usable but the facade must treat every lost response as `indeterminate` and reconcile by re-reading the Bot Chat transcript for the handoff marker (weak). |
| Peer DM (`/api/sessions/{id}/chat`, synchronous ≤600 s) | **degraded mechanism** for peers that do not advertise `runs_idempotency` (probe `/v1/capabilities`, as `UP:peer.py:229-242` does). Blocks one HTTP connection for the turn; a lost response is `indeterminate`. |
| Desktop relay | separate; Bot-Mode-only; retire after Runs parity (§9.3). |
| A2A plugin (`a2a_call`, JSON-RPC v1.0) | separate. It is a *platform* for interoperating with non-Hermes agents; in-memory task store, no idempotency key, per-message task ids (`protocol.py:577-770`; `adapter.py:706`). A future `a2a://` channel is possible behind the same registry but is not needed by the two named consumers. |
| Upstream hosted-room peer transport (`PeerRunsHTTPClient`, RoomLink grants) | not in the fork; after the merge it is a strong template for scoped grants and fenced dispatch (`UP:gateway/hosted_room_peer.py:526-660`), but it is room-specific and should be reused as a pattern, not depended on. |

**Which existing mechanisms become adapter internals:** `local_delivery_command` + `acquire_turn_lock`, the peer registry and key resolution, the session find-or-create logic of `peer.py:112-152`, and the Runs client. **Remain separate:** relay, A2A, kanban. **Retire only after parity:** the relay waiter (`waiter_command`), the raw `hermes peer dm` shellout inside `message_agent`.

---

## 12. GitLab+ICM town hall protocol

### 12.1 Repository and folder conventions (ICM-inspired, VERIFIED against the ICM repo)

ICM prescribes hyphenated, zero-padded stage folders, one routing `CONTEXT.md` per folder (routing tables, not content; <80 lines), a stage contract with `## Inputs | ## Process | ## Checkpoints | ## Audit | ## Outputs` tables, one-way references, canonical sources, `.gitkeep` for empty folders, and hand-off through `output/` folders (`_core/CONVENTIONS.md`, `_core/templates/stage-context-template.md`). ICM has **no** native versioning, run ids, request files, decision logs, or multi-agent messaging — the paper lists real-time multi-agent collaboration as out of scope (arXiv 2603.16021 §5.2). The town hall therefore borrows ICM's *reading discipline* and adds its own handoff layer explicitly.

```
/CLAUDE.md                          # Layer 0: what this town hall is; "Routing" → /CONTEXT.md; "What to Load" table
/CONTEXT.md                         # Layer 1: Task Routing table: inbox name → /agents/<inbox>/CONTEXT.md
/_protocol/PROTOCOL.md              # this protocol, version 1 (§12.2)
/_protocol/templates/{request,result,question,reply,claim}.md
/agents/<inbox>/CONTEXT.md          # Layer 2 contract for that inbox: Inputs (labels it watches), Process, Outputs
/agents/<inbox>/references/         # Layer 3 (standards, checklists) — the destination's own material
/handoffs/<handoff-id>/             # exists only on branch handoffs/<handoff-id> (never on the default branch)
    CONTEXT.md                      # Layer 2 stage contract for THIS handoff (generated from the spec)
    request.md                      # front matter + the prompt (canonical request)
    context/*.md                    # initiator-supplied context files (each ≤ 512 KiB)
    inputs/manifest.json            # [{path, sha256, size, media_type}] for context/ and attachments
    output/result.md                # destination-authored result (front matter + body)
    output/manifest.json            # [{path, sha256, size, media_type, kind}] incl. result.md
    output/.gitkeep
```

`request.md` front matter (YAML): `protocol: gitlab-icm/1`, `handoff_id`, `generation`, `initiator: {install_id, profile, kind}`, `inbox`, `mode: task|conversation`, `deadline`, `output_schema_sha256`, `prompt_sha256`, `created_at`. The body is the rendered prompt. `output/result.md` front matter: `protocol`, `handoff_id`, `generation`, `status: succeeded|failed`, `failure_class?`, `claimed_by: {bot_user_id, agent}`, `result_sha256`, `usage?`.

### 12.2 Canonical object per concern (Q8 — "one authoritative home")

| Concern | Canonical GitLab object | Never duplicated in |
|---|---|---|
| Lifecycle phase | **issue labels** `hf-state-<phase>` + issue `state` (`closed` ⇔ terminal) | files, comments (comments may *announce* a change; the label is authoritative) |
| Request, context, inputs, result bytes | **branch `handoffs/<id>`** files, addressed by commit SHA | issue description (holds only a short summary + links) |
| Messages, questions, replies, claims | **issue notes** with a machine header | files |
| Repository changes as a deliverable | **merge request** from `handoffs/<id>` | — |
| Initiator's view (attempts, deadlines, evidence, delivery) | **local `handoffs.sqlite3`** | GitLab (reconstructed only during reconcile) |

Labels: `hf` (marker), `hf-inbox-<inbox>`, `hf-init-<install_id_short>`, `hf-state-{open,claimed,active,needs-input,cancel-requested,done,failed,cancelled}`. **VERIFIED** scoped labels (`key::value` exclusivity) are Premium/Ultimate (docs.gitlab.com/user/project/labels/#scoped-labels), so the protocol must not rely on exclusivity: the adapter always issues `add_labels` + `remove_labels` in one `PUT` (docs.gitlab.com/api/issues/, update parameters) and treats multiple `hf-state-*` labels as `indeterminate`-until-reconciled. Where scoped labels are available, `hf-state::<phase>` may be used for UX; the adapter reads either. Label → facade phase mapping: `open → submitted`, `claimed | active → active`, `needs-input → needs_input`, `cancel-requested → cancelling`, `done → succeeded`, `failed → failed`, `cancelled → cancelled`; a closed issue with no terminal label is `indeterminate`.

### 12.3 Protocol versioning

`_protocol/PROTOCOL.md` carries `version: 1`. Every file front matter and every machine note header states `protocol: gitlab-icm/<major>`. Unknown major → the adapter refuses to act on the object and records `channel_misconfigured`; minor additions are optional fields only. The channel plugin's `channel_version` is recorded on every handoff row.

### 12.4 Discovery, inbox, and identity

- Town halls are configured by **name** in the initiator's `config.yaml` under `handoff.townhalls.<name>: {origin, project, inbox_labels_prefix?}`; the token is a plugin-configuration secret keyed by town hall name (`ctx.configuration().secret("token:<name>")`), never in the URI (R4, I7).
- Identity: on `bind`, `GET /user` pins `{id, username, bot}` (docs.gitlab.com/api/users/) and `GET /projects/:id/members/all` confirms the role is at least Developer for write inboxes; the pinned bot user id is stored in `bound_json.destination_identity` and re-verified on every observe (drift → `failed(destination_identity_drift)` after one reconcile).
- Service accounts / project access tokens are the intended principals (bot users `project_{id}_bot_*`, docs.gitlab.com/user/project/settings/project_access_tokens/; service accounts are Free-tier, docs.gitlab.com/user/profile/service_accounts/). Scopes: `api` (writes) or `read_api`+`write_repository` split by role.
- Destination inbox: a Hermes profile runs `hermes handoff serve gitlab+icm://<townhall>/<inbox>` (a background service on gateway/web, or a cron job in CLI-only installs) that polls `GET /projects/:id/issues?labels=hf,hf-inbox-<inbox>,hf-state-open&state=opened&order_by=updated_at&sort=asc&per_page=100` and claims work (§12.6).

### 12.5 Deterministic ids, markers, branch-per-handoff

- Issue title: `[hf:<handoff_id>.g<generation>] <summary ≤ 80 chars>`; the marker is the dedup key (`search=<marker>&in=title&state=all`, docs.gitlab.com/api/issues/ list parameters).
- Branch: `handoffs/<handoff_id>` created from the town hall's base ref via `POST /projects/:id/repository/branches {branch, ref}`; "already exists" is treated as reuse (**INFERRED**: 400 per community reports; the docs do not state the status code — the adapter treats any 4xx on create followed by a successful `GET /repository/branches/:name` as reuse).
- Files are written with one `POST /projects/:id/repository/commits {branch, commit_message, actions[]}` (create/update/delete, ≤100 actions, docs.gitlab.com/api/commits/). Optimistic concurrency is **file-level**: `actions[].last_commit_id` is honored for update/move/delete and fails 400 when the file changed (`Files::BaseService#file_has_changed?`, source-verified); there is no branch-level `expected_sha` on the Commits API, so the adapter reads the branch head before each commit and records the resulting commit SHA in the checkpoint.

### 12.6 Claiming and optimistic concurrency

GitLab has no compare-and-set on issue updates, so claiming is a two-step, order-resolved protocol:

1. Claimant posts a **claim note** `<!-- hf v=1 kind=claim id=<uuid> bot=<user_id> gen=<N> -->` (`POST /projects/:id/issues/:iid/notes`, `internal: true` where the project allows).
2. Claimant lists notes `order_by=created_at&sort=asc` (docs.gitlab.com/api/notes/) and wins iff its claim note is the earliest `kind=claim` for that `gen` by note `id`; losers post nothing further and back off. The winner then `PUT`s `assignee_ids=[self]`, `add_labels=hf-state-claimed`, `remove_labels=hf-state-open`.
3. A claim expires if no `kind=heartbeat` note (every 10 minutes while active) or state change occurs within `claim_ttl` (default 30 min). Expiry is observed by the initiator, which surfaces it as `stalled` attention. **RECOMMENDATION:** only the initiator may reclaim — by `cancel` on the current generation and a fresh submission under `gen+1` — never a competing claimant; that keeps duplicate-execution risk with the party that owns the workflow and avoids two bots racing on one issue.

### 12.7 Questions and replies

Notes with headers: `kind=question` (destination → initiator; sets `hf-state-needs-input`), `kind=answer` (initiator, references `question_id`; restores `hf-state-active`), `kind=message` (free-form either way), `kind=status` (progress), `kind=heartbeat`, `kind=cancel-request` (initiator; label `hf-state-cancel-requested`), `kind=cancelled` (destination ack), `kind=done` (destination; carries `commit_sha`, `result_path`, `manifest_sha256`; label `hf-state-done`; issue closed by the destination or by the initiator after verification), `kind=failed`. Headers are the only machine-parsed content; bodies are untrusted text (§15).

### 12.8 Completion and result verification

On `kind=done`, the initiator fetches `output/manifest.json` and each listed file at the recorded `commit_sha` (`GET /projects/:id/repository/files/:path/raw?ref=<sha>`, docs.gitlab.com/api/repository_files/), verifies every `sha256` and `size`, verifies the commit author email equals the pinned bot user's noreply email (docs: `project_{id}_bot_*@noreply.<host>`), verifies `result.md` front matter `handoff_id`/`generation`, then — for task mode — validates the JSON block against the admitted output schema. Any mismatch → `failed(result_tampered | output_invalid)` with the evidence recorded locally; the issue gets `hf-state-failed` and a `kind=verification-failed` note so the town hall reflects the outcome.

### 12.9 Optional merge requests

When the result includes changes intended for a target branch, the destination opens an MR `handoffs/<id> → <target>` (`POST /projects/:id/merge_requests {source_branch, target_branch, title, description, labels, remove_source_branch}`) and records `mr_iid` in the `kind=done` header. The initiator never merges automatically; merging is an operator decision (`PUT …/merge` with `sha` for optimistic safety; approvals are Free-tier for approve/unapprove, rules are Premium). Workflow policies that need review create a downstream `approval` node.

### 12.10 Cancellation

`command(cancel)` → `kind=cancel-request` note + `add_labels=hf-state-cancel-requested`. The destination acknowledges with `kind=cancelled` (label `hf-state-cancelled`, close issue) or, if it already finished, `kind=done` wins. The initiator's phase is `cancelling` until either arrives or the deadline passes (then `failed(deadline_exceeded)`); side effects the destination already took are reported as `cancelled_after_effects` (§13.5).

### 12.11 Polling, cursors, backoff, rate limits

- Initiator cursor: `updated_after=<last_seen_updated_at − 5 min>` over `GET /projects/:id/issues?labels=hf,hf-init-<me>&state=all&order_by=updated_at&sort=asc&per_page=100` (keyset pagination is available for project issues since 18.3, docs.gitlab.com/api/rest/); dedup by `(iid, updated_at)`. For each changed issue, list notes with `order_by=created_at&sort=asc` from the last seen note `id` (notes have **no** `updated_after`/`created_after` filter — VERIFIED absent from docs.gitlab.com/api/notes/). Labels are read from the issue itself; `resource_label_events` (docs.gitlab.com/api/resource_label_events/) are consulted only during reconcile to learn who changed state and when.
- Cadence: `next_observe_at` is adaptive: 30 s while `submitted`/`needs_input`, 2 min while `active` with recent heartbeats, exponential to 15 min when idle, immediate on a webhook wakeup.
- Rate limits: honor `RateLimit-Remaining`/`RateLimit-Reset`/`Retry-After` (docs.gitlab.com/administration/settings/user_and_ip_rate_limits/); budget note creation (GitLab.com 60/min/user, issue creation 200/min). The per-town-hall client keeps one token bucket shared by every handoff in the profile.
- Webhooks (optional wakeup only): `POST /projects/:id/hooks {url, token, issues_events, note_events, push_events, push_events_branch_filter: "handoffs/*"}`; verify `X-Gitlab-Token` or `webhook-signature`; duplicates and reordering are expected (docs.gitlab.com/user/project/integrations/webhooks/), so a webhook only sets `next_observe_at = now`.

### 12.12 Retention and archival

Issues stay closed forever (they are the town hall's memory); branches `handoffs/<id>` are deleted by a housekeeping pass after `retention_days` (default 30) once the initiator has archived the result locally and no open MR references them; results that must persist are merged into `/archive/<yyyy>/<handoff_id>/` on the default branch via an MR when the sidecar sets `archive: true`. Attachments >1 MiB go through `POST /projects/:id/uploads` (docs.gitlab.com/api/project_markdown_uploads/) only when the town hall permits it; otherwise they are rejected at admission (§15 oversized artifacts).

### 12.13 Reconciliation after ambiguous GitLab outcomes

| Ambiguous call | Reconcile |
|---|---|
| `POST /issues` timed out | search `[hf:<id>.g<N>]` in title, `state=all`, `created_after = submit_started − 1h`; 0 → resubmit; 1 → adopt; >1 → adopt lowest `iid`, label others `hf-duplicate`, close them with a `kind=duplicate` note |
| branch create timed out | `GET /repository/branches/handoffs/<id>`; present → adopt |
| commit timed out | `GET /repository/tree?path=handoffs/<id>&ref=handoffs/<id>` and compare file blobs' `content_sha256` (Files API GET) with the intended manifest; match → adopt the head commit; mismatch → rewrite with `last_commit_id` |
| note create timed out | list notes since last cursor and match `<!-- hf … id=<uuid> -->`; present → adopt |
| label update timed out | re-read the issue; if both old and new `hf-state-*` labels are present → issue one corrective `PUT`; the local phase is authoritative for the initiator's own transitions |

---

## 13. Durability, idempotency, and reconciliation

### 13.1 Where the supervisor runs (Q9)

- `hermes serve` (web host) and `hermes gateway` (gateway host) both start plugin background services (`hermes_cli/web_server.py:549`; `gateway/run.py:13482`); the core supervisor registers for both. Exactly one process per store leads (SQLite `BEGIN IMMEDIATE` election with epoch fencing, copied from `plugins/workflow/coordinator_store.py:606-704`); the web host defers to a fresh gateway lease the way the workflow coordinator does (`coordinator.py:185-198`). A standby still serves reads and accepts commands (they are durable rows).
- Desktop navigation or restart does not matter: the store and supervisor live in the backend. If the Desktop's pooled per-profile backend is evicted (LRU, `apps/desktop/electron/main.ts:1461-1463`), the gateway (autostarted with the desktop in this fork, `hermes_cli/config_defaults.py:3089`) or the next backend resumes from the store.
- Multiple processes observing one store: fenced claim leases per handoff; a stale epoch cannot write (`UPDATE … WHERE claim_owner=? AND claim_epoch=?`).
- CLI-only environments: no host → `hermes handoff advance [--all|<id>] [--wait <s>]` drives `advance()` inline; the CLI's idle loop can call it on the same cadence it already drains process notifications (`cli.py:20386,20596`); a `hermes cron` job (the fork's own Footprint-Ladder rung 2) provides an unattended ticker; a foreground workflow run drives its own waiting nodes. This is why `advance()` must be convergent and cheap.
- Deadlines and stall detection: `deadline_at` per handoff; `stalled` attention when `next_observe_at` is overdue by > 3 intervals or the destination's last heartbeat/status is older than `stall_after` (channel-specific default: 10 min Hermes, 60 min GitLab).

### 13.2 Honest guarantees (Q10)

- **Admission:** effectively-once per `(key_scope, handoff_key)`; a duplicate `create` returns the same id; conflicting specs are rejected.
- **External submission:** at-least-once. Duplicates are prevented only where the channel supports keyed submission: Runs with `Idempotency-Key` (upstream v0.21.0, 24 h retention; **not** in the fork today), GitLab by deterministic title marker + reconcile (search-then-adopt, not atomic). For `cli-oneshot` and the fork's Runs, a lost acceptance is `indeterminate` and requires reconcile evidence (a `Handoff: <id>` session exists in the destination's `state.db`; a Bot Chat transcript contains the marker) before resubmission.
- **Execution:** never exactly-once. A destination may execute after the initiator lost the acceptance; the protocol makes that visible (`indeterminate` → reconcile) rather than pretending otherwise.
- **Commands:** idempotent by `command_id`; delivery is at-least-once with channel-side dedup where possible (Runs steer has none; GitLab notes carry the `id` header).
- **Reply delivery to the initiator:** at-most-once per event via `handoff_deliveries`; a crash between the external send and the receipt write can lose one delivery (mirrors `GatewayPluginDeliveryPort` "never replaying an uncertain attempt", `gateway/plugin_delivery.py:298-345`) — it is re-surfaced as attention, not re-sent.

### 13.3 Failure matrix

| Scenario | Behavior |
|---|---|
| Lost submission response | Runs w/ key: replay returns the original `run_id` (`Idempotency-Replayed`); Runs w/o key (fork today) / cli-oneshot: `indeterminate` → reconcile (search destination session by title / marker) → adopt or resubmit gen+1; GitLab: §12.13 |
| Crash after external acceptance, before receipt persistence | same as lost response; the durable `submit_attempted` event written *before* the call proves an attempt happened |
| Duplicate process claims | fenced lease; the loser's writes fail; the winner re-observes |
| Repeated commands | `command_id` PK; second call returns the existing row |
| Remote gateway restart | fork Runs: status 404 after restart → `indeterminate`; upstream: `interrupted` (durable) → `failed(destination_interrupted)` with retry per node policy |
| Terminal result retention expiry (fork: 1 h; upstream keyed: 24 h) | observe finds 404 after the handoff was `active` → `indeterminate`; reconcile reads the destination session transcript (`/api/sessions/{id}` on the peer) for the marker + final assistant message; if found → `succeeded` with `result.provenance=transcript`; else `failed(result_expired)` |
| GitLab create/update ambiguity | §12.13 |
| Cancellation after external side effects | `cancelled_after_effects` recorded from the destination's ack payload (Runs: `pending_steer`/`output` present on `cancelled`; GitLab: `kind=cancelled` with `effects: [...]`); the workflow treats it as `outward` and pauses with `reconcile` when effects are non-empty |

### 13.4 Pre-admission fallback (narrow safe case)

Fallback across endpoints is safe **only** when all of the following hold: (a) no `submit_attempted` event exists for the current generation; (b) the alternate endpoint was listed by the initiator at `create` time (`spec.fallback_endpoints`), never chosen by the facade; (c) `bind()` of the primary failed deterministically (invalid, unreachable at bind probe, misconfigured) rather than timing out during submit; (d) the switch is journaled as `endpoint_switched` with both URIs. After the first submit attempt, the only path is reconcile → resubmit on the same endpoint, or operator `abandon` and a new handoff.

### 13.5 Effects reporting

Every terminal envelope carries `effects: [{kind, ref, reversible: bool}]` when the destination can report them (Runs: tool-call summary from the run's events; GitLab: the `kind=done|cancelled` header). The workflow surfaces non-empty effects on a non-`succeeded` outcome as `reconcile`; Bot Mode shows them in the reply text.

---

## 14. Audit, observability, and Needs Attention

### 14.1 Normalized evidence timeline

`handoff_events` kinds (closed vocabulary): `created, bound, endpoint_switched, submit_attempted, submitted, submit_indeterminate, reconciled, claimed, phase_changed, message_sent, message_received, interaction_requested, interaction_answered, heartbeat, result_received, result_verified, result_rejected, cancel_requested, cancel_confirmed, deadline_exceeded, delivered_to_initiator, delivery_failed, failed`.

Required fields per event: `seq, at, kind, actor {kind, profile?, principal?, on_behalf_of?, tool_call_id?}, channel {scheme, version, mechanism}, endpoint_uri, external_ref {kind, id, safe_url?}, phase_before, phase_after, payload_digest (sha256 of the unredacted payload), payload_redacted (bounded, §14.2), destination_identity {profile|bot_user_id, install_id|origin}`.

Both consumers project the same rows: the workflow `evidence` reader gains a `handoffs` kind (`plugins/workflow/evidence.py:25-36`) that reads the facade's `evidence()` for the node's `handoff_ref`; the Bot page reads `/api/handoffs/{id}/events`.

### 14.2 Redaction boundaries and digests

- Stored redacted: prompt and message bodies (first 512 chars + digest), results (digest + size; full bytes live in the destination transcript / branch file / workflow artifact), URIs stripped of any query. Reuse the workflow sanitizer's key regex and truncation (`plugins/workflow/sanitize.py:21-23,131-150`) rather than inventing another.
- Never stored: tokens, peer keys, GitLab tokens, `Authorization` headers, raw external error bodies (classified to `failure_class` + a 500-char tail, as the relay already does, `tui_gateway/methods_bot_relay.py:138-160`).
- External safe references: server-built URLs from allowlisted origins only (peer URL origin, town-hall origin); Hermes references are `{profile, session_id, run_id?}` rendered as `hermes://` deep links by the Desktop (CLAUDE.md keeps both `hermes://` and `otto://` registered).
- Adapter/version/configuration identity: `channel_version`, `mechanism`, `bound_json` digest, `spec_fingerprint`.

### 14.3 Failure classification

`failure_class ∈ {endpoint_invalid, channel_misconfigured, auth, unreachable, rejected_by_destination, submit_indeterminate, abandoned_indeterminate, destination_failed:<reason>, destination_interrupted, output_invalid, result_tampered, result_expired, deadline_exceeded, cancelled_after_effects, destination_identity_drift}` where `<reason>` reuses the Bot Mode enum (`tools/bot_failure_reasons.py:27-61`: `provider_auth_or_access, provider_quota_limit, provider_rate_limit, provider_server_error, context_overflow, missing_config, model_unavailable, runtime_offline, delivery_timeout, target_busy, unknown`). `AUTO_RETRYABLE` (`:66-69`) decides whether a workflow `retry` is permitted without operator action.

### 14.4 Attention triggers and drill-down

- **Needs Attention (workflow):** `needs_input` (pending interaction), `indeterminate` (reconcile), `failed` (any class), `active` past deadline (stalled), `cancelling` past deadline. Cleared by `respond`, `reconcile`, `retry/abandon`, or terminal transition — never by dismissing.
- **Bot attention (Desktop):** the same four conditions filtered to `key_scope=bot:<profile>`; badge reasons map to the existing hint table (`plugin.js:207-212`) plus `needs_input`/`indeterminate`.
- **Activity board drill-down:** the workflow run inspector shows the handoff row under the node (phase, endpoint, deadline, last event) and links to the timeline; GitLab issue/branch/MR links and Hermes run/session links come from `/api/handoffs/{id}/links`.

### 14.5 What a user can answer from the timeline

Who → whom (`actor`, `endpoint_uri`, `destination_identity`); initiator kind (`actor.kind` + `key_scope`); channel/endpoint/mechanism; admission confirmed (`submitted` with `external_ref`); who executed (`claimed` with bot user / profile + session id); messages and interactions (`message_*`, `interaction_*` with digests); output (`result_received/verified` with digest, size, artifact refs); why it stopped (`failed.failure_class` + tail); whether retry/reply/cancel/reconcile is safe (`phase`, `generation`, `submit_attempted` presence, `effects`).

## 15. Security and trust boundaries

| Threat | Control (RECOMMENDATION unless noted) |
|---|---|
| Endpoint URI injection / SSRF | `hermes://` carries no network location: hosts come only from `bot_peers` (registered by an operator); `gitlab+icm://` names a configured town hall. Any URI with userinfo, port, query, fragment, or an unregistered authority fails `validate()`. Remote calls go through `hermes_cli/urllib_security.open_credentialed_url` (fork-present, `hermes_cli/urllib_security.py:19-45`: cross-origin redirects drop credentialed headers) and, for peers, require `https` outside loopback as upstream's `validate_room_link_url` does (`UP:gateway/hosted_room_peer.py:500-525`). The A2A plugin's prefix-only SSRF check (`plugins/platforms/a2a/security.py:307-343`, hostnames unresolved) is *not* the model to copy. |
| Credential leakage | Peer keys stay `HERMES_PEER_<NAME>_KEY` in the initiator's `.env` (VERIFIED existing); town-hall tokens are plugin-configuration secrets resolved per invocation (`hermes_cli/plugins.py:1787-1795`) and never retained across calls; the store never persists request bodies with headers; evidence redaction §14.2; `bound_json` stores names, never values. |
| Cross-profile authorization | A profile may initiate only to endpoints allowed by `handoff.allow` (default: every local profile + registered peers + configured town halls; deny-list supported). The destination decides what it will *do*: local `cli-oneshot` runs with the destination's own `single_query_mode` (deny by default, `tools/approval.py:3484-3496`), Runs require the destination profile's own `API_SERVER_KEY` (`api_server.py:1997-2058`), GitLab requires membership. Nothing in the facade elevates the initiator into the destination's secret scope (`gateway/run.py:2216-2249` is entered only by the destination's own process). |
| Destination identity drift | `destination_identity` pinned at bind (profile + install id; peer origin + advertised profile; GitLab bot user id + project id) and compared on every observe; drift → reconcile once, then `failed(destination_identity_drift)`. |
| Plugin registration collisions | reserved core schemes; first registration wins; duplicates logged and rejected; `channel_version` recorded per handoff so a plugin upgrade cannot silently reinterpret old checkpoints (adapter checkpoints are versioned). |
| Malicious or compromised GitLab comments | Only `<!-- hf … -->` headers are parsed (strict grammar, bounded, from the pinned bot user or the initiator for state-changing kinds); all bodies are untrusted text wrapped in an explicit framing when handed to any model (reuse the A2A `PRIVACY_PREFIX` style, `security.py:206-226`, and its injection defang) and never interpreted as slash commands; state changes from unexpected authors are ignored and recorded as `message_received(untrusted_author)`. |
| Prompt injection from peer agents | Results and messages are delivered to the initiator as *data* with provenance framing (`Reply from 🤖 … [handoff …]`), size-capped (`MESSAGE_MAX_CHARS` for conversation replies, 256 KiB for task results); workflow results are additionally schema-validated before use and never executed as instructions; the Bot Chat protocol text already tells agents that such messages are from a teammate, not the user (`bot_mode_probe.py:257-283`). |
| Result tampering | manifest digests + commit author verification (GitLab); run ownership scoping + transcript cross-check (Runs); `result_verified` evidence with digest; mismatch → `failed(result_tampered)`. |
| Oversized artifacts | admission caps: message 16 000 chars, prompt 128 KiB (matches `UP:hosted_room_driver.py:36` `MAX_PROMPT_BYTES`), context file 512 KiB, ≤20 input artifacts, result 256 KiB inline (larger by reference with size in manifest); GitLab notes limited to 1 000 000 chars by GitLab (docs.gitlab.com/administration/instance_limits/) but the adapter caps consumption at 64 KiB per note. |
| Replay and duplicate execution | §13; generation-suffixed external keys; `command_id` PK; never resubmit without `NotFound` proof. |
| False author attribution | actor minted server-side by origin (§10.3); REST cannot produce `bot`; the delivered prefix derives from the actor kind. |
| Agent communication loops | `hop_count` in `HandoffSpec` (incremented when a handoff is created inside a turn that itself was a handoff delivery; max 3, configurable); per-profile rate limit on `create` (default 30/hour, mirrors A2A's `max_pingpong_turns`, `plugins/platforms/a2a/protocol.py:74-84`); the existing "never ping-pong acknowledgements" protocol text. |
| Automatic channel fallback | forbidden after admission (§13.4); the facade has no fallback logic beyond the pre-admission rule; workflows must list fallbacks explicitly. |
| Remote tool side effects outside source visibility | `effect_classification=outward` for every handoff; `effects[]` reporting; reconcile-on-ambiguity; the trust preflight lists assigned endpoints in the risk summary before the operator trusts a package. |

Profile isolation and session-scoped capability rules are preserved: the facade adds no tool to any session; `message_agent`'s availability remains a property of the session (title + managed install), never of the process env (AGENTS.md "Surface capability is a property of the SESSION").

---

## 16. Plugin and upstream-integration strategy

### 16.1 Upstream-owned files that must change (smallest set)

| File | Change | Merge class |
|---|---|---|
| `tools/bot_mode_dm.py` | `message_agent_tool` delegates to `hermes_cli.handoff` (target resolution, `handoff_id`, return-route registration); schema text + `handoff_id` param | Bucket 1 UNION; upstream changed 19 lines in v0.21.0 — the delegation must be re-applied by hand |
| `tools/bot_mode_probe.py` | `protocol_version` 2 → 3; one sentence in the protocol section about URIs/`handoff_id` | Bucket 1 UNION (one constant, one string) |
| `hermes_cli/plugins.py` | `PluginContext.register_handoff_channel` + a reserved core owner for `_register_background_service`; the core supervisor's registration call lives in `PluginManager` (invoked from `start_background_services`), so neither host needs an edit | Bucket 1 UNION (additive methods; already a ledgered seam) |
| `hermes_cli/web_server.py` | mount `web_routers/handoffs.py` | Bucket 1 UNION (one include) |
| `gateway/run.py` | nothing beyond the existing `start_background_services("gateway")` | none |

Everything else is new files (Bucket 3): `hermes_cli/handoff/**`, `hermes_cli/web_routers/handoffs.py`, `plugins/handoff-gitlab-icm/**`, workflow-plugin changes (fork-only), Desktop plugin changes (fork-only files or the `hermes-bots` plugin — **note** upstream v0.21.0 rewrote `plugin.js` into ~50 TS modules, so Bot-page UI work should wait for that merge to avoid a throwaway port).

### 16.2 Merge-risk assessment

- Upstream v0.21.0 moved `/v1/runs` into `api_server_runs.py`; the fork's three tool-choice hunks sit inside the moved block (`api_server.py:7861-8112`) — an INFERRED conflict at merge time, independent of this proposal but a prerequisite for the Runs client.
- `hermes_cli/subcommands/peer.py` changed by 255 lines upstream; the facade must not fork it — it should call the same endpoints and, after the merge, reuse `_peer_run_durability`/`_ensure_bot_chat` as helpers.
- The hosted-room work (`groups.*`, `hosted_room_*`) does not touch the seams above; the facade's `hermes://` peer mechanism and upstream's `PeerRunsHTTPClient` can coexist.
- Ledger entries: add `agent-handoff-facade`, `bot-mode-message-agent-facade-delegation`, and `plugin-handoff-channel-registration` to `docs/upstream-customizations/` with owned symbols, invariant tests, and removal conditions (the fork's standing rule; no upstream PRs).

### 16.3 Why not scatter channel logic

The alternative — teaching the Desktop plugin, the peer CLI, the relay, the workflow executor, and the agent loop each about GitLab or Runs — reproduces the relay's shape five times and makes every upstream churn in those files a merge risk. One seam (`message_agent` → facade) and one registry (`register_handoff_channel`) confine the blast radius.

---

## 17. Compatibility and migration

- **Config (non-secret, `config.yaml`):**
  ```yaml
  handoff:
    enabled: true
    agents:                       # friendly names → endpoints
      security-reviewer:
        default: gitlab+icm://corp-townhall/security-reviewer
        endpoints: [hermes://local/security-reviewer]
    townhalls:
      corp-townhall: {origin: https://gitlab.example.com, project: group/agents-townhall}
    allow: ["hermes://local/*", "hermes://spark/*", "gitlab+icm://corp-townhall/*"]
    local_mechanism: cli-oneshot  # or runs
    supervisor: {observe_interval_seconds: 30, max_concurrent_advances: 8}
  bot_mode:
    message_agent_via_handoff: false   # stage flag; default flips to true at Stage 3
  ```
  Secrets: `HERMES_PEER_<NAME>_KEY` (existing) and the town-hall token in the plugin's configuration secret store; none in `config.yaml`.
- **Backward compatibility:** with `message_agent_via_handoff: false`, `message_agent` behaves exactly as today. With it on, friendly targets, peer targets, and relay targets resolve as before; only the reply path changes from waiter-only to waiter+supervisor. Existing Bot Chat prompts refresh once (epoch bump). Older peers without `runs_idempotency` are served by the synchronous mechanism.
- **Workflow packages:** unassigned workflows are unchanged; `assignments` is a new optional sidecar key (unknown keys already fail schema, `schema.py:2426-2432`, so the schema and `sidecar_field_names()` must be extended together); the new `waiting` node state must be added to `_PUBLIC_NODE_STATES` and the Desktop adapter columns (`apps/desktop/src/app/workflows/adapter.ts:22-45`) or it will render as `failed` (sanitizer fallback).
- **Migration risks:** (1) the epoch bump refreshes every eternal Bot Chat prompt once — expected cost; (2) the stall detector change must land with the `waiting` state or assigned nodes will be reported stalled after 60 s; (3) the Desktop's `$botAttention` becomes derived from the store — a reload no longer clears real attention; (4) Windows: the turn lock is a no-op (`tools/bot_relay.py:642-645`), so the facade must serialize local deliveries per profile itself (a store-level per-profile mutex row) rather than rely on `flock`.
- **Fallbacks:** if the supervisor cannot start (safe mode, or no host), everything still works through `hermes handoff advance` and the fast-path runner; if a channel plugin is disabled, its handoffs read `channel_unavailable` and remain resumable after re-enable.

---

## 18. Test and validation strategy

| Layer | Tests (new unless noted) |
|---|---|
| Unit | state-machine transition table (every phase × every event, including illegal ones); endpoint parser and policy (`hermes://` rejects host/port/userinfo/query; ambiguity); id derivation determinism; redaction; `command_id` idempotency; pre-admission fallback rule |
| Store | two-process claim/lease fencing with real subprocesses (template: `tests/plugins/workflow/test_coordinator_multiprocess.py`), lease expiry with boot-id/monotonic skew (template: `test_coordinator.py::…wall_clock_steps`), WAL contention |
| Restart/recovery | kill during `submit` → `indeterminate` → reconcile adopt vs resubmit; kill between send and receipt on the return route → attention, no duplicate delivery; supervisor takeover epoch |
| Local profile e2e | temp `HERMES_ROOT` with two profiles and a fake provider; real `hermes -p` subprocess delivery into `Bot Chat` and into `Handoff: <id>`; turn-lock serialization; `single_query_mode` deny surfacing as `needs_input`-unsupported |
| Remote peer | two `api_server` listeners on loopback with multiplex + per-profile keys (templates: `tests/gateway/test_multiplex_api_server_routing.py`, `test_api_server_runs.py`); lost-response replay with and without `runs_idempotency`; 404-after-TTL reconcile via transcript |
| GitLab sandbox | a fake GitLab HTTP server (template: `scripts/gitlab_skill_routing_livetest.py`'s fake-PAT harness) covering create/reconcile/claim/question/done/cancel/MR and every §12.13 ambiguity; an env-gated live test against a throwaway project |
| Workflow e2e | a showcase package with one assigned node under the deterministic runner (`plugins/workflow/entitlement.py`), covering waiting → wake → validate → succeed, output_invalid → retry as a new generation, cancel propagation, restart mid-wait, stall exemption |
| Bot Mode | extend `tests/tools/test_bot_mode_dm.py`, `test_bot_relay.py`, `test_bot_turn_lock.py`, `test_bot_retry_policy.py`: gates unchanged, prefix unchanged, `handoff_id` follow-up, late delivery as a new turn, no double delivery |
| Desktop RPC | FastAPI `TestClient` for `web_routers/handoffs.py` (actor minting, 409 on stale version, links allowlist); vitest for the Bot page (`apps/desktop/src/plugins/hermes-bots/tests/*` pattern) |
| Security | SSRF/redirect (credentialed header dropped cross-origin); oversized inputs rejected at admission; untrusted-author state notes ignored; framing applied to inbound bodies; REST cannot mint `bot` actors; hop-count loop cut-off |
| Gates | `generate <brand> --check` 8/8 untouched (no emitter file changes); `scripts/check_upstream_customizations.py` with the new ledger entries |

---

## 19. Staged implementation plan

Each stage keeps current Bot Mode behavior intact and ships behind a flag.

0. **Prerequisite:** merge upstream v2026.8.31 into `base` (runs idempotency, `peer run/status/stop`, session attach, `register_platform_handler`, Bot plugin TS split). Resolve the `/v1/runs` extraction conflict by porting the three tool-choice hunks into `api_server_runs.py`.
1. **Core facade (shadow):** `hermes_cli/handoff/` with store, contracts, `hermes://local` `cli-oneshot` mechanism, supervisor, `hermes handoff {create,advance,wait,get,list,cancel,respond}`; ledger entries; unit/store/restart tests. No consumer wired.
2. **Workflow assignments:** sidecar `assignments`, `waiting` node state, stall exemption, wake integration, output validation, cancel propagation, Needs Attention items, evidence `handoffs` kind, deterministic-runner e2e.
3. **`message_agent` on the facade:** flag `bot_mode.message_agent_via_handoff`, epoch bump, fast-path runner + `bot_chat` return route, peer mechanism (Runs w/ key when advertised, sync chat otherwise); relay path untouched.
4. **Desktop Bot page:** `web_routers/handoffs.py`, events frame, roster chips, drawer, actor model (after the TS split merge).
5. **GitLab+ICM channel plugin:** `plugins/handoff-gitlab-icm/` (vendored `_common/` transport policy), inbox server command, sandbox project, live-gated tests; town-hall bootstrap script that writes `CLAUDE.md`, `CONTEXT.md`, `_protocol/`.
6. **Loopback `runs` mechanism + remote hardening:** per-profile key registration UX, capability probe, interaction surfacing.
7. **Retirement:** measure waiter vs supervisor delivery parity; remove the raw `hermes peer dm` shellout from `message_agent`; keep the relay until upstream's hosted-room/peer path covers cross-connection DMs.

Explicit deferred scope: A2A as a channel, streaming token relay through the facade, fan-out/quorum handoffs, automatic MR merging, town-hall reclaim by competing claimants, multi-host store sharing.

---

## 20. Risks, rejected approaches, and unresolved decisions

**Risks**

- The Runs client is only as durable as the peer: until every peer runs ≥ v0.21.0 the facade must carry the weak transcript-based reconcile (§13.3).
- `cli-oneshot` cannot surface approvals; workflows that assign tool-heavy tasks locally will see denied dangerous commands unless the destination's `single_query_mode` is `approve` (a destination-side, deliberate setting) or the `runs` mechanism is configured.
- The stall detector and `waiting` state change touch the core of the workflow store; a mistake there stalls or spuriously fails unrelated runs — the deterministic-runner e2e must cover unassigned workflows too.
- GitLab labels are not exclusive on Free tier; the adapter's "multiple state labels ⇒ indeterminate" rule is essential.
- The Desktop's per-profile backend pool can evict the process that hosts the supervisor for a profile; the gateway autostart covers this in the fork, but a Desktop-only install with the gateway disabled relies on `hermes handoff advance` from the CLI idle loop.

**Rejected approaches** (with the deciding fact): facade in the workflow plugin (Bot Mode must work without workflows); facade in Bot Mode (workflow must not depend on Bot Chat/relay); Desktop-driven transport (viewer, not a service; upstream direction); Kanban as bus (single-host, second scheduler); dedicated `agent` node type now (breaks Archon validation, sidecar suffices); in-process `AIAgent` for cross-profile (isolation); automatic post-admission fallback (duplicate execution); `unknown` as a phase (hides the reconcile obligation); relay as a facade channel (not durable, Desktop-bound).

**Unresolved decisions**

1. Source of a stable `install_id` for id derivation (§7.4) — recommend a persisted random id at the Hermes root.
2. Whether the workflow's `interaction_policy: pause` should be allowed for channels without `interactions` (recommend: admission warning, not error).
3. Whether the GitLab inbox server should live in the same plugin as the channel (recommend yes; it shares the client and protocol) and which profile should run it in production.
4. Whether to expose `hermes://<name>` shorthand at all, or require `local/`/`<peer>/` prefixes (recommend: allow with ambiguity errors, to keep the brief's `hermes://spark-reviewer` style working via friendly-name config).
5. Whether the Windows-only per-profile serialization should be a store row or a named mutex (recommend store row; it also fixes the documented `flock` gap for the legacy path).

---

## 21. Final recommendation

Adopt the durable convergent handoff resource as the shared facade, implemented as a small host-owned core package (`hermes_cli/handoff/`) with a per-profile SQLite store, fenced claim leases, a convergent `advance()`, idempotent `create()`/`command()`, a core `hermes://` channel whose local mechanism is the already-proven `hermes -p … chat -c "<title>" -Q` transport (task handoffs into a dedicated hidden session, conversations into `Bot Chat`), an opt-in loopback/remote Runs mechanism that becomes durable after the v2026.8.31 merge, and a GitLab+ICM channel plugin registered through a single new `PluginContext.register_handoff_channel`. Keep `message_agent` as the only agent-facing send path and make it a thin facade client with a one-time epoch bump; keep the Desktop as a viewer that submits and observes through a transport-neutral core REST/events API; route workflow assignments through the companion sidecar with one new `waiting` node state. State the guarantee honestly — keyed at-least-once submission, effectively-once admission, never exactly-once execution — and never fall back across channels after admission.

---

## 22. Source and evidence index

**Fork (`base` @ `89f2cb6ea9`)**

- Bot Mode: `tools/bot_mode_dm.py` (schema 75-127; gate 129-169; dispatch 241-350; relay 353-426; delivery 520-757), `tools/bot_relay.py` (constants 51-78; roster 105-235; envelopes 243-440; waiter 485-535; delivery command 541-576; turn lock 590-676), `tools/bot_mode_probe.py` (gate 66-113; protocol text 242-283; epoch 328-411), `tools/bot_failure_reasons.py` (27-113), `tui_gateway/methods_bot_relay.py` (whole file), `agent/turn_context.py:783-787`, `agent/tool_executor.py:2116-2140`, `cli.py:1431-1456,13009-13030,20386,20596`, `tools/process_registry.py:1569-1620,1738-1800`, `tools/async_delegation.py:12-26,142-187,392-447`, `hermes_cli/config_defaults.py:215-217,405-415,2848-2864,3068-3071,3089`, `hermes_cli/main.py:2100-2180` (`-c <title> --create-if-missing`), `website/docs/user-guide/bot-mode.md`.
- Peers/profiles/multiplex: `hermes_cli/subcommands/peer.py` (whole), `gateway/platforms/api_server.py:64-84,1997-2074,2205-2313,2455-2508,2975-2979,3208-3236,7502-7528`, `gateway/run.py:2216-2249,15743-15851`, `gateway/config.py:448-472,994-1002`, `hermes_cli/profiles.py:1056-1128,2007-2031`, `agent/secret_scope.py:33-38,149-203`, `hermes_constants.py:19-39,151-174`, `tools/terminal_tool.py:1619-1636`, `hermes_cli/urllib_security.py:1-45`.
- Runs: `gateway/platforms/api_server.py:1440-1485,2368-2373,5427-5449,7729-8580,8668-8673`; `tools/approval.py:268-300,3431-3448,3484-3496,4446-4601`; `agent/interrupt_compat.py:25`; `run_agent.py:3553-3650`; `website/docs/user-guide/features/api-server.md`.
- Desktop: `apps/desktop/src/plugins/hermes-bots/plugin.js:143-283,1497-1852,5652-5929,6704-6962,7345-8607,8777-9010,14212-14350,15604-16120`; `apps/desktop/electron/main.ts:1450-1463,10895-10915`; `tui_gateway/server.py:2044-2160,8385-8415,10934-11070,11366-11470,12860-12930`; `apps/desktop/src/plugins/kanban/{api.ts,plugin.tsx,completion-notify.ts}`; `apps/desktop/src/plugins/README.md`.
- Workflow: `plugins/workflow/{store.py (3161-3437,10059-10950,11212-11262,12679-13299,17338-17829,18202-18470,19346-19890,20042-20360), coordinator.py (55-775), coordinator_store.py (93-252,517-827), scheduler.py (362-417,917-1079,4820-5016,5575-6016,6272-6759), executors/{base.py (51-307), ai.py (446-2300), approval.py (67-107)}, actions.py (10-110), notifications.py (27-127,203-320,656-865,1141-1605), dashboard/plugin_api.py (120-2960), evidence.py (25-317), sanitize.py (21-150,540-600), provenance.py (42-152), trust.py (298-311,571-1027), admission.py, api_admission.py (58-603), entitlement.py (33-209), provider_authority.py (186-360), runner_binding.py (421-610), sessions.py (1052-1290), language_schema.py (1485-2004), schema.py (2413-2491,2761-2766), input_contract.py (21-84), output_resolution.py (565-790), discovery.py (44-146), models.py, lease_clock.py (16-63), locks.py (69-123)}`, `agent/plugin_agent.py:517-577,744-776,1598-1700,1979-2100`, `agent/plugin_agent_worker.py:1103-1200,1440-1447,1828-1832,1926-1946,2007-2035,2178-2216,2762-2775`, `hermes_cli/plugin_services.py:1-170`, `hermes_cli/plugin_invocation.py:1-95`, `gateway/plugin_delivery.py:34-345`, `gateway/run.py:13468-13488,14557-14580`, `hermes_cli/plugins.py:1534-3812,4569-4776,4812-4955,5017-5052`, `apps/desktop/src/app/workflows/{index.tsx,adapter.ts}`, `docs/design/portable-workflow-orchestration.md`, `docs/superpowers/specs/2026-07-18-plugin-background-services-workflow-coordination-design.md`, `docs/upstream-customizations/workflow-orchestration.yaml`.
- A2A: `plugins/platforms/a2a/{DESIGN.md,README.md,plugin.yaml,__init__.py (35-135),protocol.py (35-84,367-411,491-525,577-814),adapter.py (63-69,164-336,338-949,1122-1266),security.py (78-372),tools.py (53-596)}`.
- Kanban: `hermes_cli/kanban_db.py:5-69,368-410,567-737,1626-1687,1773-1830,3133-3231,4506-4919,5098-5294,5543-5790,8475-8642,9074-9550,10112-10180,10931-11213`, `gateway/kanban_watchers.py:1274`, `tools/kanban_tools.py:326-375`, `plugins/kanban/systemd/hermes-kanban-dispatcher.service`.
- GitLab plugin: `plugins/ericsson-gitlab/{__init__.py (15-31,323-412,485-512),auth.py (57-115),client.py (31-140),operations.py (312-350,2613-2703,3022-3027,3459-3485,3663-3760,4135-4182,4308-4331,4547-4626,4788-4797,4938-5036),tools.py (39-53,759-1105),application.py,models.py,_common/{transport.py (95-263),client.py (97-228),errors.py (20-102),guardrails.py (20-50)}}`, `capabilities/workflows/jira-to-gitlab.yml` + `.hermes.yaml`, `optional-mcps/gitlab/manifest.yaml`, `docs/handoffs/2026-08-31-ericsson-gitlab-remaining-reads-implementation-handoff.md`.
- Tests referenced: `tests/tools/test_bot_{mode_dm,relay,turn_lock,retry_policy,failure_reasons}.py`, `tests/tui_gateway/test_bot_relay_methods.py`, `tests/hermes_cli/test_peer_cmd.py`, `tests/gateway/test_api_server_runs.py`, `tests/gateway/test_multiplex_api_server_routing.py`, `tests/plugins/workflow/{test_coordinator.py,test_coordinator_multiprocess.py,test_crash_recovery.py,test_idempotency_multiprocess.py,test_notifications.py}`, `scripts/gitlab_skill_routing_livetest.py`.

**Upstream v2026.8.31 (scratchpad shallow clone)**

- `hermes_cli/__init__.py` (0.21.0 / 2026.8.31); `gateway/platforms/api_server_runs.py:45-99,264-401,404-703,1017-1035,1072-1134,1137-1215,1353-1404,1425-1474`; `gateway/platforms/api_server_run_idempotency.py` (whole); `gateway/platforms/api_server.py:141-152,1559-1562,3348-3351,7504-7676`; `gateway/platforms/api_server_room_{dispatch,grants}.py`; `hermes_cli/subcommands/peer.py:10,200-445,497-540`; `tools/bot_mode_dm.py:303-321,587-609`; `tui_gateway/methods_bot_relay.py:215-227`; `tui_gateway/methods_groups.py:1-80`; `tui_gateway/hosted_room_{service,peer_http,peer_transport,driver,server_rpc}.py` (headers); `gateway/hosted_rooms.py`, `gateway/hosted_room_peer.py:35,500-660`, `gateway/hosted_room_driver.py:36-118,491-503,638-798,1110-1190`, `gateway/hosted_room_replicas.py` (header); `gateway/config.py` (`room_link_url`); `plugins/platforms/a2a/{adapter.py (445-448),tools.py (585-630)}`; `website/docs/user-guide/bot-mode.md:128-163`, `website/docs/user-guide/features/api-server.md:430-470`, `website/docs/user-guide/multi-profile-gateways.md`, `website/docs/reference/cli-commands.md:446-485`; GitHub release notes for v2026.8.31 (Bot Mode, `hermes peer`, subagent steering/stop/schema, gateway control socket, selective multiplex profile serving).

**GitLab official documentation (fetched 2026-09-01)**

- Issues API (create/list/update; `iid` admin-only; `updated_after`; `order_by=updated_at`; `add_labels`/`remove_labels`; `state_event`) — docs.gitlab.com/api/issues/
- REST API guide (pagination offset/keyset; project issues keyset since 18.3; no request `Idempotency-Key`) — docs.gitlab.com/api/rest/
- Notes API (`sort`, `order_by`; no `updated_after`; `internal`; 1 000 000-char body) — docs.gitlab.com/api/notes/ ; Discussions — docs.gitlab.com/api/discussions/
- Resource label/state events — docs.gitlab.com/api/resource_label_events/ , docs.gitlab.com/api/resource_state_events/
- Branches — docs.gitlab.com/api/branches/ ; Commits (`actions[]`, `start_branch`, `last_commit_id`) — docs.gitlab.com/api/commits/ ; Repository files (`last_commit_id`, raw GET, size/throttle limits) — docs.gitlab.com/api/repository_files/ ; Repositories (tree, keyset, compare) — docs.gitlab.com/api/repositories/ ; file-level optimistic check source: gitlab.com/gitlab-org/gitlab `app/services/files/base_service.rb`, `multi_service.rb`, `lib/api/files.rb`; open caveat gitlab.com/gitlab-org/gitlab/-/issues/438657
- Merge requests (create, merge with `sha`, 409 on mismatch) — docs.gitlab.com/api/merge_requests/ ; approvals tiers — docs.gitlab.com/api/merge_request_approvals/
- Labels API — docs.gitlab.com/api/labels/ ; scoped labels (Premium/Ultimate) — docs.gitlab.com/user/project/labels/#scoped-labels
- Events API — docs.gitlab.com/api/events/ ; project webhooks + delivery headers (`X-Gitlab-Token`, `webhook-signature`, retries/duplicates) — docs.gitlab.com/api/project_webhooks/ , docs.gitlab.com/user/project/integrations/webhooks/
- Rate limits and headers — docs.gitlab.com/user/gitlab_com/ , docs.gitlab.com/administration/settings/user_and_ip_rate_limits/ , docs.gitlab.com/rate_limits/content_creation/ , docs.gitlab.com/administration/instance_limits/ ; ETag caching scope — docs.gitlab.com/development/polling/
- Auth/identity — docs.gitlab.com/api/rest/authentication/ , docs.gitlab.com/user/project/settings/project_access_tokens/ , docs.gitlab.com/security/tokens/access_token_scopes/ , docs.gitlab.com/user/profile/service_accounts/ , docs.gitlab.com/api/service_accounts/ , docs.gitlab.com/api/users/ , docs.gitlab.com/api/project_members/
- Search API scopes/tiers — docs.gitlab.com/api/search/ ; uploads — docs.gitlab.com/api/project_markdown_uploads/

**ICM**

- github.com/RinDig/Interpretable-Context-Methodology (`README.md`, `CLAUDE.md`, `_core/CONVENTIONS.md`, `_core/placeholder-syntax.md`, `_core/templates/*.md`, `workspaces/*`) ; paper arXiv 2603.16021 (abstract names the method "Model Workspace Protocol"; body uses ICM; §3 principles, §3.4 git compatibility, §5.2 multi-agent out of scope, §5.3 auditability, §6.2 future provenance work).
