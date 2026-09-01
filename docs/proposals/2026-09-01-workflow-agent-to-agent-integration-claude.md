# Agent-to-Agent Work in the Workflow Plugin — Architecture Assessment

Assessed: local `hermes-agent` checkout, branch `base` @ `89f2cb6ea9` (tracked tree clean; only untracked docs), against upstream tag [`v2026.8.31` = v0.21.0 @ `29112bef09`](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.8.31). Date: 2026-09-01.

Legend used throughout: **[V]** verified fact (read in code/tests), **[I]** inference, **[R]** recommendation, **[U]** unknown requiring validation. Local citations are `path:line` in the checkout; upstream citations are `upstream:path:line` and resolve to `https://github.com/NousResearch/hermes-agent/blob/v2026.8.31/<path>#L<line>`.

---

## Conclusion

**Depend on the destination gateway's `/v1/runs` API directly — the exact primitive `hermes peer run/status/stop` wraps — called from the workflow coordinator with an `Idempotency-Key` derived from the run/node/attempt, an isolated session per attempt (never the canonical Bot Chat by default), and peer aliases + `HERMES_PEER_<NAME>_KEY` resolved from the initiating profile. Express the author contract as a per-node *assignment* in the Hermes companion file (`<workflow>.hermes.yaml`), leaving the portable definition untouched. Model the remote run in the ledger as one attempt whose worker is released after the receipt is persisted (a new `waiting_remote` node state polled by the scheduler), re-adopted after a coordinator restart by replaying the same key, and funnelled into the *existing* `reconcile` interaction whenever the remote outcome is ambiguous.** Normal remote waiting stays in the "Active" column; only paused/reconcile, prolonged unreachability, and failures reach "Needs attention".

Three verified facts shape everything else:

1. **The local checkout does not contain v2026.8.31.** `base` carries upstream v0.20.6 (2026-08-27) plus fork work; `hermes peer run/status/stop`, `gateway/platforms/api_server_runs.py` and the durable `RunIdempotencyStore` are absent locally. Any implementation starts with the upstream merge.
2. **Upstream `/v1/runs` is durable only when keyed, and never resumes an in-flight run across a gateway restart** — it lazily marks it `interrupted`. That is the right contract for a workflow engine (the engine owns retry policy), but it means the destination cannot be the durable authority; the workflow ledger must be.
3. **Remote tool approval over the API does not exist in practice.** `tools/approval.py` classifies `api_server` sessions as *unattended*: dangerous commands resolve instantly via `approvals.unattended_mode` (default `deny`) and never reach the `waiting_for_approval`/`POST …/approval` path the docs describe. So the destination profile *is* the approval authority, and v1 needs no cross-gateway approval interaction.

---

## 1. Executive assessment

**What upstream gives us.** Three surfaces exist, and only one is a workflow-grade primitive:

- `message_agent` (Bot Mode tool) — fire-and-forget, Bot-Chat-only, correlated by nothing more than a background process id; the reply re-enters the *sender's* Bot Chat as a completion notification. Not injectable into a workflow node's agent (which runs `platform="plugin-agent"` in a fresh session) and not correlatable. Unsuitable.
- `hermes peer dm` — one synchronous HTTP turn on the destination's canonical Bot Chat, 600 s client timeout, no run id, no cancel, no idempotency. Suitable for short notifications only.
- `/v1/runs` (+ `hermes peer run/status/stop`) — asynchronous `run_id`, durable keyed admission with replay, pollable status incl. `output`/`usage`/`error`, cooperative stop, per-run isolated session by default. The CLI wrapper *always* pins the canonical Bot Chat; the raw API does not. This is the primitive.

**What our plugin gives us.** A durable, fenced, single-profile orchestration engine with exactly the primitives a remote lifecycle needs: journal-before-effect (`record_spawn_intent`), effect classification (`replay_safe | outward`), a `reconcile` interaction for uncertain outcomes, cooperative cancellation with an "unconfirmed termination → reconcile" path, worker-releasing waits (`paused`, `waiting_retry`, `waiting_resolution`), a sealed per-node provider route pattern with drift detection, and a closed public vocabulary that the desktop decodes. It has **no** HTTP client, no notion of another profile/gateway/machine, and no node state for "waiting on remote work".

**The integration boundary.** The seam is *inside the workflow plugin*: a new executor + a `waiting_remote` node state + a receipt sub-record, talking to a stock upstream `api_server`. Nothing changes in core Hermes, no model tool is added on either side, no upstream file is modified, and the destination gateway needs zero fork-specific code (it only needs `api_server` enabled with a strong `API_SERVER_KEY`, which is what `hermes peer` already requires).

**What to defer.** Cross-gateway approval relay (unreachable upstream today), steering, SSE consumption, fan-out/collect nodes, RoomLink HMAC grants, canonical-Bot-Chat sessions, and a CLI-subprocess transport for local profiles. None is needed to ship a correct first release.

---

## 2. Verified upstream behavior (v2026.8.31)

### 2.1 Local checkout vs the tag

| Item | Local `base` @ `89f2cb6ea9` | Upstream `v2026.8.31` |
|---|---|---|
| Upstream lineage | v0.20.6 (2026.8.27) via merge `6f6f8d88b0` "Merge upstream v0.20.6 into base"; fork version label `4.2.2` (`hermes_cli/__init__.py:15-16`, commit `918ca1ec6a`) **[V]** | v0.21.0 (`upstream:hermes_cli/__init__.py`) **[V]** |
| `hermes peer` | `add/list/remove/dm` only (`hermes_cli/subcommands/peer.py`, 342 lines; diff vs upstream = 255 lines) **[V]** | adds `run/status/stop` + `_resolve_peer_target`, `_peer_run_durability`, redirect-safe `_request` (`upstream:hermes_cli/subcommands/peer.py:207-241, 296-408`) **[V]** |
| `/v1/runs` | routes present inline in `gateway/platforms/api_server.py:2368-2373`; in-memory `_run_statuses` (`:1663`); **no** `RunIdempotencyStore`, no `interrupted` detection; `Idempotency-Key` only for chat-completions/responses via `_IdempotencyCache` (`:1441, :5427, :6645`) **[V]** | extracted to `api_server_runs.py` (1474 lines) + `api_server_run_idempotency.py` (376 lines); durable SQLite reservations, owner-pid restart detection **[V]** |
| `message_agent` | present (`tools/bot_mode_dm.py`, 757 lines; 27-line diff — profile pin `#93935`, Windows path fix) **[V]** | 782 lines **[V]** |
| `tools/bot_mode_probe.py` | identical **[V]** | — |
| Tests | `tests/gateway/test_api_server_run_idempotency.py` absent **[V]** | present |

**Conclusion [V]:** the local checkout contains the *Bot Mode DM* half (dm/message_agent) but not the *durable runs* half. Release notes and local code are not the same thing here.

### 2.2 Surface semantics

| Surface | Sync/async | Correlated? | Durable? | Session | Cancel | Cite |
|---|---|---|---|---|---|---|
| `message_agent` tool | Async, fire-and-forget; returns `{"status":"sent","process_id"}` | Only by background-process id; reply arrives as a completion notification in the sender's Bot Chat | Process-local; after a gateway restart the checkpointed process is re-adopted *detached* ("Can't read output, but can report status + kill") — the reply text is lost **[V]** | Destination canonical Bot Chat (`hermes -p <bot> chat -c "Bot Chat" --create-if-missing -Q --query-file`) | None | `upstream:tools/bot_mode_dm.py:1-39, 75-126, 314-328, 362-380, 667-718`; `upstream:tools/process_registry.py:2926-2955`; `upstream:tools/terminal_tool.py:3520-3535` |
| `hermes peer dm` | Sync HTTP `POST /api/sessions/{id}/chat`, `DM_TIMEOUT_S=600` | Reply on stdout; no id | No | Destination canonical Bot Chat, found by title with `include_hidden=1`, created if missing | None | `upstream:hermes_cli/subcommands/peer.py:49, 133-181, 410-437` |
| `hermes peer run` | Async: `POST /v1/runs` → `run_id` | `run_id` + `Idempotency-Key` (default `peer-<uuid>`) | Yes when the peer advertises `runs_idempotency.durable`; CLI warns otherwise | **Always** the canonical Bot Chat (`session_id` = found/created Bot Chat) | `hermes peer stop` → `POST /v1/runs/{id}/stop` | `upstream:hermes_cli/subcommands/peer.py:229-241, 347-408, 308-340` |
| `POST /v1/runs` (raw) | Async, 202 | `run_id`; optional `Idempotency-Key` | Keyed runs only | `session_id` param; defaults to `run_id` → **isolated session per run** | `POST …/stop` (cooperative) | `upstream:gateway/platforms/api_server_runs.py:404-1015` |
| RoomLink member turn | Async `POST /v1/runs` + poll | Fixed key `room:{task_id}:{execution_generation}` + home-side receipt table | Yes (target durable store required) | Hidden `Group: <room_id>` session on target | Two-phase stop | `upstream:gateway/platforms/api_server_room_dispatch.py:151-153`; `upstream:gateway/hosted_rooms.py:1014-1062` |

### 2.3 `/v1/runs` in detail

- **Routes [V]:** `POST /v1/runs`, `GET /v1/runs/{id}`, `GET /v1/runs/{id}/events` (SSE), `POST …/approval`, `POST …/steer`, `POST …/stop` (`upstream:gateway/platforms/api_server_runs.py:83-91`); every route is also mounted under `/p/{profile}/…` (`upstream:gateway/platforms/api_server.py:7770-7772`).
- **Status vocabulary [V]:** `queued`, `running`, `waiting_for_approval`, `stopping`, `completed`, `failed`, `cancelled`, `interrupted` (`runs.py:135-140, 368-373, 706, 757, 1405`). Status dict carries `session_id`, `model`, `last_event`, `output`, `usage`, `error`, `pending_steer`, and `approval` only while waiting (`runs.py:116-153`).
- **Session [V]:** `session_id = body.session_id or stored_session_id`; `session_id = session_id or run_id` (`runs.py:546, 607`). An existing `session_id` loads that session's transcript as history (`runs.py:600-603`). `approval_session_key = run_id` regardless of session (`runs.py:613`). The run binds `platform="api_server"`, `async_delivery=False` (`api_server.py:7211-7246`).
- **Execution [V]:** `agent.run_conversation` in the default thread-pool executor (`runs.py:861`); concurrency cap `gateway.api_server.max_concurrent_runs` default 10 → 429 + `Retry-After: 1` (`api_server.py:1758-1780`; `hermes_cli/config_defaults.py:3338-3346`). Toolsets come from the destination's `platform_toolsets.api_server` (`api_server.py:3088-3095`). Transcript persists in the destination's `state.db` (`api_server.py:3134`).
- **Durability [V]:** status is in-memory (`_run_statuses`, `runs.py:77`) and is persisted to `$HERMES_HOME/runs_idempotency.db` **only** if the request carried an `Idempotency-Key` (`runs.py:146-152`; `api_server_run_idempotency.py:34-42, 56-70`). The store silently falls back to `:memory:` (`durable=False`) if the file cannot be opened (`idempotency.py:41-52`).
- **Restart [V]:** on the first status read after a restart, a non-terminal keyed run whose `owner_pid`/`owner_started` no longer match is rewritten to `interrupted` with error "The gateway restarted before this run settled." (`runs.py:339-401`). Never resumed. Unkeyed runs vanish (404 `run_not_found`, `runs.py:1017-1035, 1064-1068`) **[I]**. `POST …/stop` on a run not active in this process → 409 `run_not_active` (`runs.py:1396-1403`).
- **Retention [V]:** SSE buffers 300 s if no subscriber (`api_server.py:7507`; `runs.py:1432-1463`); in-memory terminal statuses 3600 s (`api_server.py:7508`; `runs.py:1465-1474` — note `interrupted` is not in the sweep set); durable rows pruned only when **terminal and** older than 24 h / past `retention_until` (`idempotency.py:26-27, 237-294`); a non-terminal reservation never ages out.
- **Idempotency [V]:** key 1–255 visible ASCII (`runs.py:452-462`); scope = sha256(`profile\0API_SERVER_KEY`) — per URL-profile and credential (`runs.py:264-298`); fingerprint = sha256 of canonical `{body, gateway_session_key}` (`runs.py:467-481`); same key + different fingerprint → 409 `idempotency_key_conflict` (`runs.py:569-576`); reuse → 202 `{run_id, status, replayed:true}` + `Idempotency-Replayed: true` (`runs.py:577-593`), bypassing the concurrency cap (`runs.py:559-561`); reservation is atomic in `BEGIN IMMEDIATE` (`idempotency.py:117-192`). Invalid body and 429 do not consume the key (tests `upstream:tests/gateway/test_api_server_runs.py:873, 897`).
- **Stop [V]:** sets `stopping`, calls `request_hard_interrupt`, returns immediately; settles to `cancelled` only when the executor thread returns `interrupted=True`; a run that finishes anyway settles `completed` (`runs.py:862-876, 1353-1422`; test `:692`).
- **Events [V]:** a single consumer; the queue is dropped when the stream closes (`runs.py:1129-1132`) — not replayable.
- **Capabilities [V]:** `GET /v1/capabilities.features` advertises `run_submission`, `runs_idempotency{supported,durable,retention_seconds}`, `run_status`, `run_events_sse`, `run_stop`, `run_steer`, `run_approval_response` (`api_server.py:3312-3410`; `runs.py:94-99`). No installation id is exposed here **[V]**.

### 2.4 Approval reality (the most important discrepancy)

- `_UNATTENDED_APPROVAL_PLATFORMS = {"webhook", "msgraph_webhook", "api_server"}` (`upstream:tools/approval.py:276-280`); `_is_gateway_approval_context()` returns `False` for them (`:343`).
- The dangerous-command gate's non-interactive branch resolves instantly by `approvals.unattended_mode` (default `deny`) — "Resolves instantly — never a pending approval nobody can answer" (`:3868-3887`; `:4805, :4938-4993`; execute_code `:5529-5547`). `_get_unattended_approval_mode` reads only `approvals.unattended_mode` (`:3560-3574`; default `deny` at `upstream:hermes_cli/config_defaults.py:2562`).
- All four sites that can push an `approval.request` to the run's notify callback sit under `is_gateway`/`is_ask` (`:3915-3925, :5202-5208, :5687, :5910-5912`).
- Therefore, for a bearer-authenticated `/v1/runs` run, `waiting_for_approval` and `POST /v1/runs/{id}/approval` are unreachable in the default configuration **[V by reading; U by live run]**. The only path that reaches the attended branch is the legacy process env `HERMES_EXEC_ASK=1` on the destination, and only for the terminal-command gate (`:4805` includes `is_ask`; `:3828-3830` and `:5529` do not) **[V]**. RoomLink's execution policy overrides `approvals.mode` (`:3450-3461`) but not the unattended gate **[V]**.
- Upstream's own tests only exercise resolution with a pre-seeded queue (`upstream:tests/gateway/test_api_server_runs.py:1413-1465`) **[V]**.

### 2.5 Hosted rooms / RoomLink — reusable pattern, not reusable code

**[V]** A room member turn on a peer is exactly a keyed `/v1/runs` run: body `{input, hosted_room_dispatch}`, `Authorization: HermesRoom <HMAC grant>`, fixed key `room:{task_id}:{execution_generation}` (`upstream:gateway/platforms/api_server_room_dispatch.py:65-186`), a home-side receipt binding the logical id to `run_id` (`upstream:gateway/hosted_rooms.py:1014-1062`), status by polling with backoff (`upstream:tui_gateway/hosted_room_peer_http.py:733-790`), one identical replay on ambiguous admission (`:616-621`), two-phase cancel needing an exact terminal ack (`upstream:gateway/hosted_room_driver.py:1486-1573`), `indeterminate → deferred/member_unavailable` after 60 s on the home side (`upstream:tui_gateway/hosted_room_driver.py:1338-1349`), and destination-owned execution policy sealed by digest and refused on drift (`upstream:gateway/hosted_room_execution_policy.py:117-160`; 403 `room_execution_policy_changed`). Plain `http` is refused off-loopback (`upstream:gateway/hosted_room_peer.py:491-523`).

**[V]** The driver tables have a foreign key to `hosted_rooms(room_id)` and schema validation requires it (`upstream:gateway/hosted_room_driver.py:329, 354, 369-376`); the `bot_room` toolset is a marker; there is no in-tree UI consumer at this tag. **[R]** Copy the *shape* (intent → receipt → poll → two-phase cancel → deferred-on-silence), not the modules.

### 2.6 Credentials and HTTP security

**[V]** Peer names/URLs live in `config.yaml` root key `bot_peers` (read via profile-scoped `load_config()`, `upstream:peer.py:57-70`); the key lives in `.env` as `HERMES_PEER_<NAME>_KEY`, read through `agent.secret_scope.get_secret` (fail-closed under multiplex) with `os.environ` fallback (`:53-54, :73-83`). Requests use `Authorization: Bearer` over `hermes_cli.urllib_security.open_credentialed_url`, which strips the header across cross-origin redirects (`:86-122`; test `upstream:tests/hermes_cli/test_peer_cmd.py:477`). `hermes peer add` accepts plain `http://` (`:253`). The api_server binds `127.0.0.1` by default, refuses to start without a ≥16-char key even on loopback, has no TLS, no IP allowlist, and a named profile under `/p/<profile>/` must present *that profile's own* `API_SERVER_KEY` (`upstream:gateway/platforms/api_server.py:265, 1516, 1892-1969, 7682-7720, 7836-7841`). Bot Mode reads `bot_peers` from the *root* config while `hermes peer` reads the *profile* config; `message_agent` pins `-p <self-profile>` to reconcile them (`upstream:tools/bot_mode_probe.py:171-192`; `upstream:tools/bot_mode_dm.py:306-328`).

### 2.7 Release notes vs docs vs code

| Claim | Source | Code | Verdict |
|---|---|---|---|
| "Replies land in each agent's canonical Bot Chat, so conversations between agents are durable and inspectable, not fire-and-forget" | release notes (Highlights) | tool schema and docs say `message_agent` "is FIRE-AND-FORGET"; the reply text rides a background process whose output is lost after a gateway restart | Transcript is durable; the delivery is not. Wording conflates the two **[V]** |
| `POST /v1/runs/{id}/approval` "resolves a pending approval for a run that is waiting on a human decision… the run resumes" | `upstream:website/docs/user-guide/features/api-server.md:500-502` | unattended gate (§2.4) | Unreachable for bearer runs by default **[V]** |
| "Hermes durably reserves the key… including after a gateway restart" | `api-server.md:447` | true only when the SQLite store opened; silent `:memory:` fallback | Conditionally true **[V]** |
| "Statuses are retained briefly after terminal states" | `api-server.md:470` | 1 h memory, 24 h store for keyed runs; `interrupted` never swept from memory | "Briefly" is loose **[V]** |
| "Session turn leases serialize concurrent writers" | `api-server.md:449-452` | lease lives in `AIAgent`/`hermes_state` (`upstream:hermes_state.py:8412-8475`; waits up to 1800 s), not in the runs handler | Plausible, not traced through `/v1/runs` **[U]** |
| `Idempotency-Key` "cached by key for 5 minutes" | `api-server.md:697` | that is the chat-completions cache, not `/v1/runs` | Misplaced **[V]** |
| `/v1/runs` durability | release notes | absent from the notes entirely | Omission **[V]** |
| RoomLink (`/v1/room-members/*`, grants, `gateway.room_link_url`) | docs | undocumented | Omission **[V]** |
| `hermes peer` "stable `--idempotency-key` makes a retry return the original run" | `bot-mode.md:146-148` | true only if the peer is durable; CLI warns | Conditionally true **[V]** |

---

## 3. Verified current workflow behavior and gaps

Facts below are from `plugins/workflow/**` and `apps/desktop/src/app/workflows/**` at `89f2cb6ea9`.

**Schema and portability [V].** Two files: portable `<name>.yaml` and optional companion `<name>.hermes.yaml` (`plugins/workflow/schema.py:3013`). Node kinds are field names, not a `type` key: `command, prompt, bash, script, loop, approval, cancel` (+ `loop_group`, `include`) (`plugins/workflow/language_schema.py:1485-1496`). The companion is restricted to `_SIDECAR_FIELDS` (`language_schema.py:1977-2005`) — `execution_environment`, `outward_action_nodes`, `required_secrets` (names only), `limits`, `overlap_policy`, `scheduling`, … — and "may declare metadata and policy but never graph topology or trust authority" (`language_schema.py:3534-3538`; `schema.py:2424`). Downstream references use `$node.output` / `$node.output.field` (`language_schema.py:74-80, 258-283`). There is **no** schema or runtime notion of another profile, gateway, or machine; `profile` means the local `HERMES_HOME` or the language profile; `execution_environment` is a local containment gate (`plugins/workflow/trust.py:274-276`). The plugin "deliberately registers no permanent model-facing tools" (`plugins/workflow/__init__.py:3-4`).

**AI node [V].** Runs a fresh worker subprocess (`agent/plugin_agent.py:1634-1664`) inheriting the coordinator's `os.environ` (so the *owner's* `HERMES_HOME`) with tools scoped by allow/deny, MCP pinned from sealed bytes, `delegate_task` force-denied at five sites (`plugins/workflow/executors/ai.py:996-998, 1514-1517`; `agent/plugin_agent_worker.py:1931`), fresh session by default, structured output validated with one bounded repair. Bash/script nodes get an env allowlist `PATH, HOME, TMPDIR, TEMP, SystemRoot, ComSpec, PATHEXT` + run vars — **no `HERMES_HOME`, no secrets** (`plugins/workflow/executors/bash.py:148-158`).

**Durability [V].** Single hosting process per profile: the coordinator is a plugin background service on the `web` or `gateway` host with a SQLite leader lease (`plugins/workflow/__init__.py:45-49`; `coordinator_store.py:606-704`; lease 30 s, sweep ≤5 s). Stores are `<hermes_home>/workflows/{admission.sqlite3, runs/<id>/run.json+events.jsonl}` (`store.py:3180-3188`). Claims carry leases and attempt ids; every mutation requires the active claim (`store.py:301-311`). Journal-before-effect exists for processes (`record_spawn_intent`, `store.py:13117-13165`) and provider dispatch (`:13440-13446`). Effect classification is `replay_safe | outward` from the companion's `outward_action_nodes` (`store.py:12703`). On lease expiry / restart an attempt becomes `interrupted` (replay-safe) or `paused` with a `reconcile` interaction (outward or `outcome_uncertain`) and is **never auto-resumed** (`store.py:17555-17640`; tests `tests/plugins/workflow/test_crash_recovery.py:1819, 1905, 2008`). `reconcile_run` accepts `confirmed-succeeded | confirmed-failed | safe-to-retry`, the last gated on `termination_confirmed` (`store.py:20103-20150`). Cancellation is cooperative (`desired_status="cancelled"`, process-tree termination, unconfirmed outward termination → `paused`+`reconcile` with `cancelled_outward_outcome_uncertain`, `store.py:18202-18645`).

**Waiting without workers [V].** `paused` releases the claim and is re-woken by a durable `coordinator_wakes` row (`store.py:8621-8712`); `waiting_retry` is polled by the sweep (`store.py:17172-17336`); `waiting_resolution` uses a bounded ladder (`store.py:16978-17091`). Bash/script/AI executors hold a worker thread for the whole execution.

**Approvals [V].** The `approval` node returns `paused` synchronously with a `workflow_approval` interaction (`plugins/workflow/executors/approval.py:84-122`); no expiry exists. A tool approval inside an AI node is a *pause-then-replay-with-grant*: the worker's callback emits an `interaction`, pauses, returns `"deny"`; the operator's approval becomes a one-shot `action_grant`; a **fresh worker** replays the node with `approved_action_digest` (`agent/plugin_agent_worker.py:2006-2034`; `store.py:19682-19684`; `scheduler.py:4954-4957`). The child is never alive while the operator decides.

**Attention and board [V].** Columns `queued | active | attention | completed | stopped`; the "Needs attention" predicate is `status == paused` OR `health ∈ {coordinator_unavailable, stalled, storage_degraded}` (`apps/desktop/src/app/workflows/adapter.ts:21-45`). Inbox items come from node pending interactions (`approval, workflow_approval, loop_input, loop_signal_confirmation, reconcile`), failed runs (`kind=failure`), stalled/unavailable health (`kind=stalled`), and undelivered attention-kind notifications (`plugins/workflow/dashboard/plugin_api.py:2298-2416, 2527-2563`). Public vocabularies are closed sets in `plugins/workflow/sanitize.py:48-92`; unknown node states coerce to `interrupted`, unknown run statuses to `recovery_pending`. Operator actions: `approve, reject, provide-input, resume, retry, reconcile, cancel, abandon, archive, restore` (`plugins/workflow/actions.py:45-92`). There is no "missing credential" producer and the `capability` interaction type has no writer.

**Gaps relative to the goal [V→I].**

| Need | Today | Gap |
|---|---|---|
| Address another agent/peer | none | schema + admission resolution + sealing |
| Correlated remote wait without a worker | only `paused`/`waiting_retry`/`waiting_resolution` | new `waiting_remote` node state + scheduler poll |
| Durable receipt | `record_spawn_intent`/process identity only | remote intent + receipt sub-record + journal events |
| Restart re-adoption by receipt | attempts become `interrupted`/`reconcile` | `_observe_attempt` classification for remote receipts |
| Cancel the exact remote work | process-tree termination | `POST …/stop` + ack polling + unconfirmed → reconcile |
| Surface remote conditions | closed vocab; no credential/peer causes | new `blocking_reason`/`error_code` values (public node state can stay `running`) |
| Consume remote output | AI output artifact + resolver | reuse, add size/redaction bounds |

---

## 4. Interface options with worked examples

Shared scenario: an incident-triage workflow where a research bot on peer `spark` gathers evidence, a local `writer` profile drafts the summary, a human approves, and a bash node publishes. Peers are registered out of band: `hermes peer add spark --url https://spark.lan:8377 --key <API_SERVER_KEY>`; local profiles are addressed through the initiator's own gateway (`hermes peer add self --url http://127.0.0.1:8377 --key <key>` — see §8 for the per-profile key constraint).

### Option A — Compose with existing Bash nodes (zero schema change)

```yaml
# incident-triage.yaml
name: incident-triage
description: Research on a peer, draft locally, approve, publish
nodes:
  - id: research
    bash: |
      set -e
      hermes peer run spark/researcher --json \
        --idempotency-key "wf-$WORKFLOW_ID-research" \
        "Collect the last 24h of errors for $ARGUMENTS and list root causes as JSON {\"root_causes\":[...]}" \
        > "$ARTIFACTS_DIR/receipt.json"
      run_id=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["run_id"])' "$ARTIFACTS_DIR/receipt.json")
      while :; do
        hermes peer status spark/researcher "$run_id" --json > "$ARTIFACTS_DIR/status.json"
        if grep -qE '"status": *"(completed|failed|cancelled|interrupted)"' "$ARTIFACTS_DIR/status.json"; then break; fi
        sleep 10
      done
      python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d.get("output",""));sys.exit(0 if d["status"]=="completed" else 1)' "$ARTIFACTS_DIR/status.json"
    timeout: 1800000
  - id: draft
    prompt: "Write an incident summary for $ARGUMENTS. Evidence:\n$research.output"
    depends_on: [research]
  - id: review
    approval:
      message: "Publish this incident summary?"
      capture_response: true
    depends_on: [draft]
  - id: publish
    bash: "printf '%s\n' \"$draft.output\" > \"$ARTIFACTS_DIR/summary.md\""
    depends_on: [review]
```
```yaml
# incident-triage.hermes.yaml
language_compatibility: archon-2026-07
outward_action_nodes: [research]
```

- **Addressing:** whatever `hermes peer` resolves — but the bash env has no `HERMES_HOME` (`executors/bash.py:148-158`), so `hermes` reads the *default* profile's registry/keys, not the owning profile's; `hermes` must be on `PATH` **[V]**.
- **Downstream output:** stdout → `$research.output` (text). Structured `.field` access needs `output_format` on a bash node (`schema.py:1860-1866`) and `stdout` that parses as JSON — the script prints `output` verbatim, so only if the remote agent emitted pure JSON **[V]**.
- **Session:** canonical Bot Chat, unconditionally (`upstream:peer.py:370`) **[V]**.
- **Approval/cancel:** cancel kills the shell; the remote run keeps running (no `stop` on cancel) **[V]**. Remote approvals: destination policy (§2.4).
- **Footprint:** none in code; large in author effort.
- **Portability:** the node is syntactically portable bash but semantically Hermes-bound; other engines would run a broken script.
- **Misuse/invalid:** bash `timeout` is `min(options.timeout, subprocess_timeout)` with `subprocess_timeout` default **120 s** (`scheduler.py:4598-4616`; `models.py:1115-1130`) — the poll loop is killed after two minutes unless the profile raises the limit; the receipt lives only in an unverified artifact, so a coordinator restart yields `paused`+`reconcile` with no way to re-correlate; every worker slot is held for the whole wait **[V]**.
- **Poor fit:** anything longer than a couple of minutes, anything that must survive restarts, anything the operator must be able to cancel remotely.

### Option B — Route an existing prompt/command node with a node-level field

```yaml
# incident-triage.yaml
name: incident-triage
description: Research on a peer, draft locally, approve, publish
nodes:
  - id: research
    prompt: "Collect the last 24h of errors for $ARGUMENTS and list the root causes."
    agent: spark/researcher
    output_format:
      type: object
      properties: { root_causes: { type: array, items: { type: string } } }
      required: [root_causes]
    idle_timeout: 1800
    retry: { max_attempts: 2, on_error: transient }
  - id: draft
    prompt: "Write an incident summary for $ARGUMENTS. Root causes: $research.output.root_causes"
    agent: self/writer
    depends_on: [research]
  - id: review
    approval: { message: "Publish this incident summary?", capture_response: true }
    depends_on: [draft]
  - id: publish
    bash: "printf '%s\n' \"$draft.output\" > \"$ARTIFACTS_DIR/summary.md\""
    depends_on: [review]
```
```yaml
# incident-triage.hermes.yaml
language_compatibility: archon-2026-07
```

- **Addressing:** `<peer>[/<profile>]` alias grammar from `upstream:peer.py:45-46, 184-194`; raw URLs rejected.
- **Downstream output:** unchanged `$research.output[.field]`; the remote `output` becomes the node's output artifact; `output_format` validated at home.
- **Session:** fresh isolated session per attempt (default); optional `session: bot-chat`.
- **Approval:** destination-owned. **Cancel:** `POST …/stop` on the receipt; unconfirmed → reconcile.
- **Footprint:** one new node option in the portable inventory (`language_schema.py:1535-1797`), one executor, one node state, receipt record, admission sealing.
- **Portability:** a Hermes-only field in the *portable* document. Under the plugin's own contract every portable field is classified `portable | mapped | unsupported` against the Archon language (`compat.py:498-875`); `agent:` has no Archon counterpart and would render the document `unsupported` elsewhere. It also collides conceptually with Archon's inline `agents` map and the `Agent` tool alias (`compat.py:131-142`).
- **Misuse/invalid:** must be rejected with `agent`: `mcp`, `hooks`, `agents`, `allowed_tools`, `denied_tools`, `skills`, `systemPrompt`, `context: shared`, `persist_session`, `model`, `provider`, `effort`, `thinking`, `maxTurns`, `tool_call_contract`, `maxBudgetUsd`, `sandbox`, `fallbackModel`, `betas` (destination authority or untransportable); `agent` on `bash/script/approval/loop/cancel`.
- **Poor fit:** teams that import the same portable definition into other engines or other Hermes profiles without the companion.

### Option C — Assignment in the Hermes companion (portable document untouched) — **recommended**

```yaml
# incident-triage.yaml  (identical to a fully local workflow; runs locally when no companion assigns it)
name: incident-triage
description: Research on a peer, draft locally, approve, publish
nodes:
  - id: research
    prompt: "Collect the last 24h of errors for $ARGUMENTS and list the root causes."
    output_format:
      type: object
      properties: { root_causes: { type: array, items: { type: string } } }
      required: [root_causes]
    idle_timeout: 1800
    retry: { max_attempts: 2, on_error: transient }
  - id: draft
    prompt: "Write an incident summary for $ARGUMENTS. Root causes: $research.output.root_causes"
    depends_on: [research]
  - id: review
    approval: { message: "Publish this incident summary?", capture_response: true }
    depends_on: [draft]
  - id: publish
    bash: "printf '%s\n' \"$draft.output\" > \"$ARTIFACTS_DIR/summary.md\""
    depends_on: [review]
```
```yaml
# incident-triage.hermes.yaml
language_compatibility: archon-2026-07
assignments:
  research:
    agent: spark/researcher      # <peer>[/<profile>] from `hermes peer list`; never a URL
    effects: outward             # outward (default) | replay_safe — governs retry vs reconcile
  draft:
    agent: self/writer
    session: fresh               # fresh (default, v1) | bot-chat (later)
```

- **Addressing:** same alias grammar; resolution and sealing happen at admission (§6, §8).
- **Downstream output:** unchanged `$research.output[.field]`.
- **Session:** fresh per attempt by default.
- **Approval/cancel:** as Option B.
- **Footprint:** one companion field (`_SIDECAR_FIELDS`, `language_schema.py:1977-2005`; parser `schema.py:2405-2493`), one executor, one node state, receipt record, admission sealing, risk-digest inclusion. Identical runtime to B; only the declaration site differs.
- **Portability:** best. The portable document is byte-identical to a local workflow; placement is "policy", the category the companion already owns (`execution_environment`, `outward_action_nodes`). Child companions in an include closure are already `authenticated_ignored` (`dependency_manifest.py:1826-1834`), so assignments only ever come from the root package — a sensible trust property.
- **Misuse/invalid:** same field conflicts as B, reported as blocking `field_not_applicable`/`assignment_conflict` compatibility findings; `assignments` naming a node that does not exist → `unknown_sidecar_node` (existing check, `schema.py:2496-2505`); assigned node kind other than `prompt`/`command` → `assignment_node_type`; an alias containing `://`, `@`, or whitespace → `assignment_target_invalid`; an assignment is *not* topology, so `depends_on`/`when` stay in the portable doc.
- **Poor fit:** authors who read only the portable file and expect to see routing there; mitigate by surfacing assignments in the desktop "View workflow" companion inventory and `hermes workflow show`.

### Option D — Dedicated `agent_task` node kind

```yaml
name: incident-triage
description: Research on a peer, draft locally, approve, publish
nodes:
  - id: research
    agent_task:
      agent: spark/researcher
      task: "Collect the last 24h of errors for $ARGUMENTS and list the root causes."
      output_format:
        type: object
        properties: { root_causes: { type: array, items: { type: string } } }
        required: [root_causes]
      session: fresh
      effects: outward
    idle_timeout: 1800
    retry: { max_attempts: 2, on_error: transient }
  - id: draft
    agent_task:
      agent: self/writer
      task: "Write an incident summary for $ARGUMENTS. Root causes: $research.output.root_causes"
    depends_on: [research]
  - id: review
    approval: { message: "Publish this incident summary?", capture_response: true }
    depends_on: [draft]
  - id: publish
    bash: "printf '%s\n' \"$draft.output\" > \"$ARTIFACTS_DIR/summary.md\""
    depends_on: [review]
```

- Clear semantics and a clean validator (no "which prompt options are illegal" table), but it adds a *kind* to `NODE_TYPES` (`language_schema.py:1485`) — a portable-language change with no counterpart elsewhere, so every such workflow is `unsupported` outside Hermes; it duplicates `prompt`'s rendering, structured-output, retry and timeout options; and the desktop lint/schema contract (`workflow_authoring_contract`, `language_schema.py:3656-3724`) and builder skill must all learn a new kind. Poor fit while the plugin's language is deliberately Archon-shaped.

### Option E — Split dispatch / wait / collect lifecycle

```yaml
name: incident-triage
description: Fan out two peers, gather, draft, approve
nodes:
  - id: research_dispatch
    dispatch: { agent: spark/researcher, task: "List root causes for $ARGUMENTS as JSON" }
  - id: ops_dispatch
    dispatch: { agent: homelab/ops, task: "Attach the last deploy diff for $ARGUMENTS" }
  - id: gather
    wait: { for: [research_dispatch, ops_dispatch], mode: all }
    depends_on: [research_dispatch, ops_dispatch]
  - id: draft
    prompt: "Summarize. Causes: $gather.output.research_dispatch  Deploy: $gather.output.ops_dispatch"
    depends_on: [gather]
  - id: review
    approval: { message: "Publish?" }
    depends_on: [draft]
```

- Makes fan-out explicit and lets unrelated local work proceed while remote work runs — but the DAG already gives that: two assigned `prompt` nodes with a common successor *are* dispatch/wait/collect, and the scheduler already runs ready nodes in parallel (`scheduler.py:6088-6091`). The split leaks receipts into author space (`$gather.output.research_dispatch`), needs three kinds, complicates cancellation (which node owns the stop?) and reconcile (which node pauses?), and doubles the schema surface. Poor fit for v1; revisit only if authors need "dispatch now, wait much later" across an approval.

### Considered and rejected — the peer as a *model provider*

The destination api_server is OpenAI-compatible and per-node provider routes are already sealed with an `endpoint_sha256` (`plugins/workflow/provider_authority.py:219-234`), so "`provider: spark`" is tempting. It would run a *local* AIAgent whose "LLM" is a remote Hermes agent: two system prompts, the local tool schema sent to a server that executes its own tools, a synchronous connection for the whole turn, the local worker held, no `run_id`, no stop. Rejected.

---

## 5. Comparison and recommendation

| Criterion | A Bash | B node field | C companion | D node kind | E split |
|---|---|---|---|---|---|
| Durable receipt in the ledger | no | yes | yes | yes | yes |
| Worker released while waiting | no | yes | yes | yes | yes |
| Cancels the exact remote run | no | yes | yes | yes | partial |
| Portable definition unchanged | yes* | no | **yes** | no | no |
| Schema footprint | 0 | 1 option | 1 companion field | 1 kind | 3 kinds |
| Author readability | poor | best | good (two files) | good | fair |
| Invalid-combination surface | n/a | large | large | small | large |
| Consistent with plugin's language contract | n/a | violates | **matches** | violates | violates |

\* syntactically only.

**[R] Recommend Option C**, with B's readability recovered by surfacing assignments in the desktop workflow view and CLI. The runtime is the same for B, C and D — a `RemoteAgentExecutor`, a `waiting_remote` node state, a receipt sub-record, and admission-time sealing — so the choice is purely where the author writes the target. The companion is the plugin's own home for "where and under what policy this runs", it keeps the portable document importable anywhere, it keeps assignments out of child includes by construction, and it puts the target set into the trust/risk digest (trusting a package means trusting where it sends work). Implement the runtime once; if a later Archon revision gains an agent-target field, move the declaration into the portable document without touching the runtime.

**Which upstream primitive [R]:** direct `/v1/runs` from the coordinator process. Not `hermes peer run` as a subprocess: it pins the canonical Bot Chat, resolves the registry from the *subprocess's* profile, spawns a process per poll, and its `--json` contract is thinner than the API. Reuse `hermes_cli/subcommands/peer.py`'s registry helpers (`_load_peers`, `_peer_secret`, `_base_url`, `_peer_run_durability`, `_request`) by import — they are module-level functions and already redirect-safe — rather than re-implementing them. Not RoomLink grants: they require a room row and the `bot_room` toolset; bearer auth is what `hermes peer` already standardizes on.

---

## 6. Recommended durable state machine

Everything below is **[R]** unless cited. Vocabulary reuses the plugin's existing states wherever one fits; the only new node state is `waiting_remote`, and it is projected to the *public* vocabulary as `running` with a `blocking_reason` so the desktop's closed decoders need no change in the first release.

### 6.1 Persisted record (`node["remote"]`, journaled)

```
remote:
  assignment_seal:  { alias, profile, url_sha256, capabilities_digest, durable: true, sealed_at }
  dispatch_intent:  { attempt_id, idempotency_key, request_fingerprint, prepared_at }      # BEFORE network I/O
  receipt:          { run_id, session_id, replayed, accepted_at }                           # IMMEDIATELY after 202
  observation:      { status, last_event, observed_at, poll_failures, unreachable_since }
  cancel:           { requested_at, stop_status, acknowledged_at }
  settlement:       { outcome, output_sha256, usage, settled_at }
```

Journal events (additive to `events.jsonl`): `remote_dispatch_prepared`, `remote_dispatch_accepted`, `remote_dispatch_replayed`, `remote_status_observed` (only on status change), `remote_stop_requested`, `remote_stop_acknowledged`, `remote_settled`, `remote_lost`.

### 6.2 Transitions

| # | From | Trigger | Persist | To |
|---|---|---|---|---|
| 1 | node `ready` (assigned) | claim | attempt + claim (existing) | node `claimed` |
| 2 | `claimed` | executor start | `mark_node_started` (existing); re-resolve alias; compare to `assignment_seal` → mismatch = **capability/identity drift** | `running` |
| 3 | `running` | build request; compute key | `dispatch_intent` (**before** any I/O) | `running` (dispatching) |
| 4 | dispatching | `POST /v1/runs` → 202 | `receipt` (before releasing the worker) | node **`waiting_remote`**, claim released, run `running`, health `waiting`, `blocking_reason=remote_wait` |
| 5 | dispatching | 401/403 | attempt `failed/authentication` (existing FATAL class, `scheduler.py:345-359`) | node `failed` → run `failed` |
| 6 | dispatching | 404 profile unknown / alias missing at re-resolve | `failed/remote_target_missing` (FATAL) | `failed` |
| 7 | dispatching | 429 / 503 / connect refused (proven not admitted) | TRANSIENT (`service_unavailable`); a refused POST admitted nothing so a new attempt may use a **new key** | `waiting_retry` (existing ledger) |
| 8 | dispatching | timeout / 5xx after send / connection reset (**ambiguous admission**) | journal `remote_dispatch_replayed`; replay the identical POST with the identical key (bounded, backoff) — same attempt, **same key** | `running` → 4 or 7 |
| 9 | `waiting_remote` | `GET /v1/runs/{id}` → `queued`/`running` | `observation` (on change) | `waiting_remote`; next poll 2 s → 30 s backoff |
| 10 | `waiting_remote` | `completed` | write `output` as artifact, validate `output_format`, `usage` into evidence; `settlement` | node `succeeded` (via a `settle_remote_node` store method modelled on `_decide_run`, `store.py:19632-19790`) |
| 11 | `waiting_remote` | `failed` | classify: destination error text starting "Provider authentication failed" → `authentication` (FATAL); else `remote_failed` (FATAL unless `retry.on_error: all`) | `failed` or `waiting_retry` |
| 12 | `waiting_remote` | `interrupted` (peer restarted) | if `effects: replay_safe` → TRANSIENT (new attempt, new key); else `paused` + `reconcile` (`remote_interrupted_outcome_uncertain`) | `waiting_retry` or `paused` |
| 13 | `waiting_remote` | `cancelled` not requested by us | `remote_cancelled` (FATAL) | `failed` |
| 14 | `waiting_remote` | `waiting_for_approval` (not reachable today, §2.4) | keep waiting; after `remote_attention_seconds` set `blocking_reason=remote_approval_pending` | `waiting_remote`, health `stalled` |
| 15 | `waiting_remote` | poll error (network) | `poll_failures++`, `unreachable_since` | `waiting_remote`; after `remote_attention_seconds` (default 300, = `semantic_stall_seconds`) health `stalled`/`remote_unreachable`; after the node wall deadline → `paused`+`reconcile` (`remote_unreachable_timeout`) |
| 16 | `waiting_remote` | 404 `run_not_found` on a held receipt | `remote_lost` | `paused` + `reconcile` (`remote_status_lost`) |
| 17 | `waiting_remote` | operator/`cancel` node → `cancel_run` | `remote_stop_requested`; `POST …/stop` | `waiting_remote` (cancel pending) |
| 18 | cancel pending | terminal `cancelled`/`completed`/`failed` observed | `remote_stop_acknowledged`; if `completed` the output is still recorded but the node settles `cancelled` per run intent | node `cancelled` → run `cancelled` (existing cascade) |
| 19 | cancel pending | stop → 409 `run_not_active`, or unreachable past threshold | as existing `cancelled_outward_outcome_uncertain` (`store.py:18443-18540`) | `paused` + `reconcile` (`cancelled_remote_outcome_uncertain`) |
| 20 | any `paused`+`reconcile` | operator `reconcile` | `confirmed-succeeded` (node succeeded, no output), `confirmed-failed`, `safe-to-retry` — the last permitted only when a terminal remote status or stop ack was observed (`termination_confirmed`) | existing outcomes (`store.py:20103-20155`) |
| 21 | coordinator restart | new leader sweeps the `running` run | `waiting_remote` nodes hold no claim → polling resumes from the receipt (no operator action). A `running` attempt whose claim expired with `dispatch_intent` but no `receipt` → new observation `remote_dispatch_replayable` → re-adopt the **same attempt** under the new epoch and replay the key (extension of `_observe_attempt`/`_reclaim_still_running_claim`, `store.py:17642-17829`) | 4 or 7 |
| 22 | `failed`/`interrupted` node | operator `retry`/`resume` (`store.py:20042-20101, 18646-18773`) | new attempt → **new key**; requires no live receipt (a `waiting_remote` node must first settle or be reconciled) | `ready` |
| 23 | `waiting_retry` | scheduled retry due | new attempt → new key | `ready` |

### 6.3 Key derivation and reuse rules

- `idempotency_key = "wf-" + sha256(f"{run_id}\0{node_id}\0{attempt_id}").hexdigest()[:48]` — visible ASCII, ≤255, unguessable, stable for the life of the attempt. Persisted in `dispatch_intent` before I/O; never derived at replay time.
- **Reuse the same key** whenever the same attempt re-issues the POST: ambiguous admission (row 8) and coordinator restart before the receipt (row 21). The destination returns `replayed: true` if the first POST landed.
- **Mint a new key** only when a *new attempt* exists: scheduled retry after a proven-refused POST (row 7), deliberate operator retry/resume (row 22), scheduled retry after `interrupted` on a replay-safe node (row 12).
- The request body must be deterministic for the attempt (rendered prompt, `session_id`); never interpolate timestamps into `input`, or the fingerprint changes and a replay becomes a 409 conflict.
- Require `runs_idempotency.durable == true` at admission (probe `/v1/capabilities`). A non-durable peer cannot make row 8/21 safe; refuse rather than degrade.

### 6.4 Where workers are held and released

Held: one HTTP round-trip during dispatch (request timeout ≤30 s), plus a capability probe. Released: from row 4 onward. Polling runs inside the scheduler's `advance` loop (a `wake_due_remote_waits` sibling of `wake_due_retries`, `scheduler.py:6030`) with a short socket timeout (5 s) so it never blocks the sweep; the `worker_claims` row is released at row 4, so remote waits do not count against `max_total_workers`; add a separate `max_remote_in_flight` (profile-wide, default 4; per-run default 2).

### 6.5 Why API idempotency is not exactly-once business effect

The key dedupes *admission* of one run per (scope, key). It does not dedupe what the run does: the destination agent executes tools with side effects, may be interrupted by a peer restart after those effects (row 12), and a deliberate retry mints a new key and a second run. The scope is per (profile, `API_SERVER_KEY`) — rotating the peer key defeats replay; the durable store can silently be `:memory:`; the 24 h retention bounds replay; and any body drift turns a replay into a 409. Hence assignments default to `effects: outward` and every ambiguous or interrupted outcome routes to `reconcile`, exactly as the plugin already treats outward local work.

---

## 7. "Needs Attention" mapping

Existing board predicate: `status == paused` OR `health ∈ {coordinator_unavailable, stalled, storage_degraded}` (`adapter.ts:21-45`). Existing inbox producers: pending interactions, failed runs, stalled health, undelivered attention notifications (`plugin_api.py:2298-2416`). All rows below reuse those; new values appear only in `blocking_reason` and `error_code` (both free-form-ish strings in the projection — `error_code` allowlist in `sanitize.py:117-120` must be extended).

| Condition | Node state (public) | Run status / health / blocking_reason | Board column | Inbox item | Operator actions |
|---|---|---|---|---|---|
| Normal remote waiting | `running` (internal `waiting_remote`) | `running` / `waiting` / `remote_wait` | **Active** | none | `cancel` |
| Remote tool approval pending (destination reports `waiting_for_approval`; unreachable today) | `running` | `running` / `stalled` / `remote_approval_pending` after threshold | Attention | `stalled` / cause `remote_approval_pending` | `cancel` only — the workflow cannot answer it; the destination operator can |
| Missing peer at admission | — (run refused `workflow_remote_target_unknown`) | — | — | — (desktop preflight error, like `workflow_trust_required`) | fix registry, re-run |
| Missing peer at dispatch (alias removed after admission) | `failed` | `failed` / `terminal` / — | Stopped | `failure` / `workflow_failed`, `error_code=remote_target_missing` | `resume`, `retry`, `abandon` |
| Missing credential (`HERMES_PEER_<NAME>_KEY` unset) | — refused at admission (`workflow_remote_credential_missing`; extend `hermes workflow doctor`'s `missing_credential`) | — | — | — | set key, re-run |
| Credential rejected (401/403) | `failed` | `failed` / `terminal` | Stopped | `failure`, `error_code=authentication` | fix key → `retry` |
| Capability / target identity drift | `failed` | `failed` / `terminal` | Stopped | `failure`, `error_code=remote_target_drift` | re-trust package → `retry` |
| Temporary unreachability (< threshold) | `running` | `running` / `waiting` / `remote_wait` | Active | none | `cancel` |
| Prolonged unreachability (≥ 300 s) | `running` | `running` / `stalled` / `remote_unreachable` | Attention | `stalled` / `remote_unreachable` | `resume` (force poll now), `cancel` (best effort) |
| Unreachable past node deadline | `paused` | `paused` / `user_wait` | Attention | `reconcile` (`remote_unreachable_timeout`) | `reconcile`, `cancel`→`reconciliation_required` |
| Remote failure | `failed` (or `waiting_retry` if transient) | `failed` / `terminal` | Stopped | `failure`, `error_code=remote_failed` | `resume`, `retry`, `abandon` |
| Remote gateway restart (`interrupted`), replay-safe | `waiting_retry` → new attempt | `waiting_retry` / `retry_wait` | Active | none (retry notification only) | `cancel` |
| Remote gateway restart, outward (default) | `paused` | `paused` / `user_wait` | Attention | `reconcile` (`remote_interrupted_outcome_uncertain`) | `reconcile`, `cancel` |
| Ambiguous dispatch | `running` (replaying) | `running` / `waiting` | Active | none unless it turns into unreachability | `cancel` |
| Unconfirmed cancellation | `paused` | `paused` / `user_wait` | Attention | `reconcile` (`cancelled_remote_outcome_uncertain`) | `reconcile` |
| Expired / lost remote status (404) | `paused` | `paused` / `user_wait` | Attention | `reconcile` (`remote_status_lost`) | `reconcile` |
| Remote run cancelled by someone else | `failed` | `failed` | Stopped | `failure`, `error_code=remote_cancelled` | `retry`, `abandon` |
| Coordinator restart while waiting | `running` | `running` / `waiting` | Active | none | — |

**Approval interaction reuse [R]:** do **not** reuse the local `approval` interaction for remote approvals. The local one means "the child is dead; approve to replay it with a one-shot grant" (`plugin_agent_worker.py:2006-2034`); a remote run stays alive in `waiting_for_approval`, and the workflow cannot resolve it today (§2.4). If upstream ever makes API runs attended, add a distinct `remote_approval` interaction whose `approve` calls `POST …/approval {choice: once, request_id}` and leaves the node in `waiting_remote`; until then, surface it as `stalled/remote_approval_pending` with `cancel` only.

**Normal waiting is not attention [V]:** a `running` run with `waiting` health lands in "Active" (`adapter.ts:44`), so the architecture does not force remote waits into the inbox.

---

## 8. Profile / session / security model

### 8.1 Ownership split

| The initiating profile owns | The destination profile owns |
|---|---|
| workflow package, trust record, risk digest (now including assignment targets), run ledger, artifacts, evidence, retries, cancellation intent, board/attention/notifications, peer registry (`bot_peers`) and peer credentials (`HERMES_PEER_<NAME>_KEY`) | its system prompt/SOUL, `platform_toolsets.api_server`, memory, provider credentials and model routing, `approvals.mode`/`unattended_mode`, `max_concurrent_runs`, filesystem, session transcript in its own `state.db` |

**[V]** This preserves upstream's isolation model as long as: only `input` text and a `session_id` cross the wire (no `instructions`, `model`, `provider`, `model_options`, `conversation_history`); the initiator never opens the destination's `HERMES_HOME` (local profiles are addressed through the gateway API, not the filesystem — unlike `message_agent`'s `hermes -p <name> chat` subprocess, which reaches into the sibling profile's home); and the credential presented is the destination's *API key*, never its provider keys. Two coupling hazards are real and must be designed out: `bot_peers` is read from the root config by Bot Mode but from the profile config by `hermes peer` (§2.6) — the workflow must use the initiating profile's `load_config()` + `.env` and say so; and a named profile under `/p/<profile>/` needs *that profile's* `API_SERVER_KEY` (`api_server.py:1892-1948`), so one alias per (gateway, profile) with its own key is the honest registry shape — the `<peer>/<profile>` sugar only works when profiles share a key value.

### 8.2 Session semantics

| Mode | Contamination | Concurrency | Reproducibility | Observability | Prompt cache | Transitive a2a |
|---|---|---|---|---|---|---|
| Fresh isolated session per attempt (`session_id` omitted → `run_id`) | none | never contends for the session turn lease | best (prompt + destination state only) | transcript visible on the destination as an api_server session **[I]** | cold prefix per run; no cache breaks | `message_agent` is *not* injected (not a Bot Chat) → no fan-out; `delegate_task` per destination policy |
| Deterministic workflow/node session (`session_id = wf-<run>-<node>`) | previous attempt's transcript is loaded as history (`runs.py:600-603`) → a retry continues a possibly-broken conversation | serialized by the 300 s turn lease, 1800 s wait (`upstream:hermes_state.py:8465-8475`) | poor across attempts | good | warm across attempts | none |
| Canonical Bot Chat (`hermes peer run` behavior) | shares the human's conversation with the bot; every workflow turn lands in the user's chat | queues behind human turns (turn lease) and `bot_mode.turn_wait_seconds` semantics | poor | best for humans | warm | `message_agent` injected → the bot may DM other bots |

**[R] Default: fresh isolated session per attempt.** Offer `session: bot-chat` later as an explicit opt-in for conversational hand-offs where the human wants the exchange in the bot's chat.

### 8.3 Security and trust

- **Registry and credentials:** aliases/URLs in the initiating profile's `config.yaml bot_peers`, keys in its `.env`, read through `get_secret` (fail-closed under multiplex) — reuse `upstream:peer.py:53-83`. Workflow YAML never carries URLs, tokens, or headers; the companion validator already refuses secret *values* (`schema.py:2457-2465`) and must refuse `assignments[*].agent` containing `://`, `@`, or whitespace.
- **Target identity sealing:** at admission, resolve each alias to `{url_sha256, profile, capabilities_digest (sorted features), durable}` and seal it beside `provider-resolution.json`; include the alias list and `effects` in `build_risk_summary` (`trust.py:571-794`) so re-pointing an alias after trust invalidates trust. At dispatch, re-resolve and compare; mismatch → `remote_target_drift` (row 2). `/v1/capabilities` exposes no installation id **[V]**; RoomLink's `GET /v1/room-members/capabilities` does, but is undocumented and room-scoped **[U]** — do not depend on it in v1.
- **Capability negotiation:** require `run_submission`, `run_status`, `run_stop`, `runs_idempotency.durable`; ignore additive features.
- **Destination-owned policy:** never send `instructions`/`model`/`provider`; the destination's `unattended_mode` decides dangerous tool calls; `max_concurrent_runs` back-pressure is a transient at home.
- **Remote output as untrusted input:** same trust class as local AI output; cap at 500 000 chars (the worker's own `final_response` cap, `plugin_agent_worker.py:2877-2879`), `redact_sensitive_text` before persisting, record `provenance: remote:<alias>` in attempt evidence, validate `output_format` at home, consume through the existing v3 bash renderer (quoting/spills) for bash successors.
- **Secret redaction:** never journal the key or raw URL (alias + sha256 only); rely on `store._sanitize` key redaction and note its `token` rule (`store.py:1627-1635`) — persist usage under keys like `usage.input`/`usage.output`, or the numbers become `[REDACTED]` **[I from code]**.
- **Bounds:** node `idle_timeout`/`ai_wall_timeout` become the remote wall deadline (default 1800 s = the destination's own `HERMES_AGENT_TIMEOUT`); `max_remote_in_flight` per profile and per run; delegation depth is the destination's `delegation.max_spawn_depth` — the wire carries no depth header, so transitive workflow→workflow loops are bounded only by destination limits **[U]**.
- **Network/NAT:** polling needs only initiator→destination reachability; no callbacks. Require `https://` or loopback (mirror `validate_room_link_url`), which is stricter than `hermes peer add` today.
- **Prompt cache / tool schema:** no new model tools anywhere; the destination's system prompt is untouched (no `instructions`); the workflow AI node's own schema is unchanged.

---

## 9. Staged implementation plan

**Prerequisites**
1. Merge upstream v2026.8.31 into `base` (`main → base → each brand` per the fork's procedure); the durable runs shard and `hermes peer run/status/stop` do not exist locally.
2. Destination gateways run `api_server` with a strong `API_SERVER_KEY`; each addressed named profile has its own key.
3. Peers registered with `hermes peer add` in the initiating profile.
4. **[U]** Validate that local Bot Mode profiles are reachable through the initiating machine's own gateway `/p/<profile>/` mirror under the fork's desktop setup; if not, local targets wait for the deferred CLI-subprocess transport.

**Stage 0 — contract (no execution).** Companion field `assignments` in `_SIDECAR_FIELDS` + parser (`language_schema.py:1977-2005`; `schema.py:2405-2493`); conflict findings in `compat.py`; risk-digest inclusion (`trust.py:571-794`); admission resolution + sealing (`admission_service.py:226-297`; `api_admission.py:222-628`) with `/v1/capabilities` probe; `hermes workflow doctor` checks (`cli.py:1803-1813`); JSON-schema export; desktop "View workflow" shows assignments. Runs are still refused with `workflow_remote_unsupported`.

**Stage 1 — execute and settle.** `executors/remote_agent.py` (dispatch step only); store: `waiting_remote`, `defer_remote_wait`, `settle_remote_node`, remote observation in `_observe_attempt`/re-adoption, `cancel_run` remote stop path; scheduler: `wake_due_remote_waits`, `max_remote_in_flight`; evidence rows; output artifact + `output_format` validation; store schema version bump (`_STORE_SCHEMA_VERSION`, `store.py:1439`). Public projection keeps node state `running` + `blocking_reason`.

**Stage 2 — operator surfaces.** `error_code`/`blocking_reason` allowlists (`sanitize.py`), notification causes, desktop `run-inspector` rows and attention labels, `hermes workflow status` fields.

**Stage 3 — options.** `session: bot-chat` (find/create by title via `/api/sessions?title=…&include_hidden=1`, `upstream:peer.py:133-181`), `effects: replay_safe`, structured-output *repair* for remote output (a second bounded run), per-assignment `poll` tuning.

**Deferred, explicitly:** cross-gateway approval relay (blocked upstream), steer, SSE, dispatch/wait/collect kinds, RoomLink grants, a CLI-subprocess transport for local profiles, cost budgets over the wire, gateway `/workflow` chat commands for remote state.

**Reuse:** `peer.py` registry helpers and `open_credentialed_url`; `reconcile` interaction; `outward_action_nodes` classification and the retry ledger; `record_spawn_intent`-style journaling; `NotificationOutbox` kinds; `redact_sensitive_text`; `substitution_renderer`; the AI executor's output artifact writer; the `wake_due_*` scheduler pattern; the `_decide_run` finalize-without-claim pattern.

**Do not couple:** the hosted-room driver/service, `message_agent`/`bot_relay`, `delegate_task`, `hermes peer` as a subprocess, the canonical Bot Chat by default, and any new generic "transport" or "agent provider" abstraction — there is exactly one consumer.

**Operational/migration:** poll cadence vs the 1 h in-memory and 24 h durable retention; clock skew is irrelevant (no signed grants in v1); Windows uses the same HTTP path; desktop/backend version skew is covered by keeping the public node state `running`; document that `hermes peer remove` leaves keys in `.env` (`upstream:peer.py:281`).

---

## 10. End-to-end acceptance tests

Run with a real second gateway (`api_server` platform, temp `HERMES_HOME`, real `RunIdempotencyStore`) per the repo's E2E rule; mocks only for fault injection.

1. **Happy path:** assigned `prompt` → 202 → `waiting_remote` releases the worker (assert `worker_claims` empty, run `running`/`waiting`) → `completed` → output artifact, `$node.output.field` resolves downstream, usage in evidence, journal contains `remote_dispatch_prepared` before `remote_dispatch_accepted`.
2. **Lost acceptance:** kill the coordinator between `dispatch_intent` and `receipt`; restart; assert one remote run exists, the replay returned `replayed: true`, same attempt id, same key.
3. **Coordinator restart while waiting:** kill after receipt; new leader settles the node with no operator action.
4. **Peer restart mid-run:** destination gateway restarted → status `interrupted`; `effects: outward` → `paused`+`reconcile`; `effects: replay_safe` → new attempt with a new key; assert the old key still replays to the interrupted run.
5. **Cancel with ack:** `cancel_run` → `POST …/stop` → destination settles `cancelled` → node/run `cancelled`; a background process reaped on the destination.
6. **Cancel without ack:** destination returns 409 `run_not_active` → `paused`+`reconcile(cancelled_remote_outcome_uncertain)`; `cancel_run` then reports `reconciliation_required`.
7. **Unreachable thresholds:** block the port; `<300 s` stays Active; `≥300 s` health `stalled`/`remote_unreachable` and an inbox `stalled` item; past the wall deadline `paused`+`reconcile`.
8. **Auth and registry:** missing key refused at admission; 401 at dispatch → `failed/authentication`, never retried; alias removed after admission → `remote_target_missing`.
9. **Drift:** re-point the alias URL after trust → dispatch fails `remote_target_drift`; re-trust → retry succeeds.
10. **Idempotency conflict guard:** mutate the rendered prompt between replays (fault injection) → 409 surfaces as a FATAL `remote_request_conflict`, not a silent duplicate.
11. **Fan-out limit:** three assigned nodes with `max_remote_in_flight: 2` → third stays `ready`.
12. **Non-durable peer:** `runs_idempotency.durable=false` → admission refused.
13. **Session isolation:** two runs to the same target never share a `session_id`; `session: bot-chat` (Stage 3) resolves the hidden Bot Chat without creating a duplicate (`upstream:tests/gateway/test_peer_dm_hidden_e2e.py:108`).
14. **Secrets:** grep `events.jsonl`, `run.json`, evidence pages and notifications for the key and raw URL → none.
15. **Board/attention projections:** snapshot the `GET /runs?view=board` and `/attention` shapes for each row of §7 through the desktop decoder (`workflow-public-codec.ts`).
16. **Output bounds:** remote output > 500 000 chars → truncated/redacted per policy; > `max_output_bytes` → `output_limit`.

---

## 11. Risks, limitations, unresolved decisions

- **Remote approval is not possible today** (§2.4). A destination with the default `unattended_mode: deny` will block dangerous tool calls silently inside the remote run; the workflow sees only the agent's text. Decide whether assigned agents' destinations should run `unattended_mode: approve` (auto-approve, risky) or accept denial as a first-class outcome (safe; recommended). **[U]** whether `HERMES_EXEC_ASK=1` on the destination is an acceptable interim for terminal commands — verify live.
- **Local-profile reachability [U]:** whether the fork's desktop/Bot Mode installs run a messaging gateway with `api_server` and per-profile keys; if not, local targets need the deferred CLI transport.
- **Per-profile key constraint:** `<peer>/<profile>` only works when the named profile's `API_SERVER_KEY` equals the stored peer key; otherwise register one alias per profile. Decide the documented convention.
- **Registry location split** (root vs profile config) between Bot Mode and `hermes peer` — decide which the workflow follows (recommended: the initiating profile).
- **Coordinator host:** the coordinator runs in `hermes serve` or the gateway; calling the gateway's own api_server from the serve host requires the gateway to be running — "self" may be unreachable in some topologies.
- **Usage redaction quirk:** `store._sanitize` redacts keys containing `token` **[I]** — confirm at runtime before relying on cost evidence.
- **Output size:** `/v1/runs` status `output` is not capped upstream **[I]**; the home cap is the guard.
- **Delegation depth over the wire** is not expressible; loops across gateways are bounded only by destination limits.
- **`interrupted` never swept from memory** upstream (`runs.py:1468`) — harmless leak; note for upstream.
- **Unknown-status forward compatibility:** the poller must treat unknown remote statuses as waiting (never terminal) and surface them as `stalled/remote_status_unknown` after the threshold.
- **Version skew:** old desktop + new backend is covered; new desktop + old backend must tolerate missing `assignments` inventory.
- **Whether to expose `session: bot-chat` at all** — it reintroduces `message_agent` fan-out and human-chat contamination.

---

## 12. Evidence appendix

Upstream base: `https://github.com/NousResearch/hermes-agent/blob/v2026.8.31/`. Local base: `hermes-agent` @ `89f2cb6ea9` (`base`).

**Checkout state [V]:** `git branch --show-current` = `base`; `git rev-parse HEAD` = `89f2cb6ea9ebb65681cee8b60305ca33e56870c5`; `git status --short` = untracked docs only; merge-base with `origin/main` = `5fc308a707` ("chore: release v0.20.6 (2026.8.27)"); `6f6f8d88b0` "Merge upstream v0.20.6 into base".

**Upstream peer CLI:** `hermes_cli/subcommands/peer.py` lines 1-31 (design notes), 44-50 (Bot Chat title, timeouts), 53-83 (registry + secret), 86-122 (redirect-safe request), 125-130 (`/p/<profile>`), 133-181 (find/create Bot Chat), 184-194 (target grammar), 229-241 (durability probe), 296-340 (status/stop), 347-408 (run), 410-437 (dm), 443-541 (parser). Tests `tests/hermes_cli/test_peer_cmd.py:363, 397, 418, 477`.

**Upstream runs API:** `gateway/platforms/api_server_runs.py` 51-80 (live state), 83-91 (routes), 94-99 (capability contract), 116-153 (status + persist gating), 264-298 (scope), 339-401 (durable hydrate → `interrupted`), 404-1015 (`_handle_runs`: 452-462 key validation, 467-481 fingerprint, 546/607 session default, 562-593 replay, 596-598 concurrency, 613 approval key, 652-692 reserve, 735-764 approval notify, 775-803 platform binding, 861 executor, 862-917 terminal handling), 1017-1035 ownership, 1038-1069 status, 1072-1134 events, 1137-1272 approval, 1275-1350 steer, 1353-1422 stop, 1425-1474 sweeps. `gateway/platforms/api_server_run_idempotency.py` 17-32, 34-52, 56-95, 117-192, 194-235, 237-294, 296-326, 365-372. `gateway/platforms/api_server.py` 265, 1516-1523, 1758-1780, 1892-1969, 2151-2200, 3088-3095, 3134, 3312-3410, 4605-4622, 7211-7246, 7507-7508, 7682-7720, 7770-7772, 7836-7841. Tests `tests/gateway/test_api_server_runs.py:692, 742, 873, 897, 961, 985, 1019, 1046, 1060, 1284, 1323, 1413`.

**Upstream approvals:** `tools/approval.py` 242-249, 276-291, 319-345, 3450-3461, 3494-3511, 3560-3574, 3828-3887, 3915-3925, 4805-4832, 4938-4993, 5202-5208, 5476-5547, 5687, 5910-5912; `hermes_cli/config_defaults.py:2542-2562`; `tests/tools/test_approval.py:715-760`.

**Upstream Bot Mode:** `tools/bot_mode_dm.py` 1-39, 59-63, 75-126, 129-169, 187-224, 241-380, 383-445, 536-563, 565-620, 667-725; `tools/bot_mode_probe.py` 41, 95-110, 171-192, 227-239, 259-285, 323-410; `tools/bot_relay.py` 1-80; `tools/process_registry.py` 59, 2795-2855, 2856-2960; `tools/terminal_tool.py:3520-3535`; `gateway/run.py:14285-14305`; `hermes_state.py:8412-8475`; `run_agent.py:4034-4041`.

**Upstream hosted rooms:** `gateway/hosted_rooms.py` 1-7, 269-289, 440-604, 1014-1062; `gateway/hosted_room_peer.py` 32-35, 114-145, 352-405, 491-523, 703-864; `gateway/hosted_room_execution_policy.py` 44-160; `gateway/platforms/api_server_room_dispatch.py` 15-62, 65-186; `gateway/platforms/api_server_room_grants.py` 51-73, 76-118; `gateway/hosted_room_driver.py` 22-31, 287-355, 369-376, 1486-1573; `tui_gateway/hosted_room_peer_http.py` 372-451, 535-656, 733-841, 903-947; `tui_gateway/hosted_room_driver.py` 1200-1235, 1338-1349.

**Upstream docs:** `website/docs/user-guide/features/api-server.md:434-502, 697`; `website/docs/user-guide/bot-mode.md:97-99, 125-160`; `website/docs/reference/cli-commands.md:446-485`; release body lines 16, 20, 72.

**Local workflow plugin:** `plugins/workflow/__init__.py:3-4, 45-49`; `language_schema.py:74-80, 1485-1496, 1535-1797, 1977-2005, 3534-3538, 3656-3724`; `schema.py:2405-2505, 3013`; `compat.py:131-155, 498-875`; `models.py:1115-1215`; `store.py:301-311, 1439, 1627-1635, 3180-3188, 8621-8712, 12703, 13117-13165, 13440-13446, 17172-17336, 17555-17640, 17642-17829, 18202-18645, 19632-19790, 20042-20155`; `scheduler.py:336-415, 4598-4616, 4954-4957, 5575-5955, 6030, 6088-6091`; `coordinator.py:65-69, 296-522`; `coordinator_store.py:606-704`; `executors/ai.py:996-998, 1202-1203, 1514-1517, 2074-2082`; `executors/bash.py:148-158`; `executors/approval.py:66-122`; `executors/cancel.py:8-20`; `agent/plugin_agent.py:1634-1664`; `agent/plugin_agent_worker.py:1931, 2006-2034, 2877-2879`; `trust.py:274-276, 571-794, 797-818`; `admission_service.py:226-297`; `api_admission.py:222-628`; `actions.py:45-92`; `sanitize.py:48-92, 113-120, 227-257`; `notifications.py:27-48, 285-320`; `dashboard/plugin_api.py:2063-2136, 2298-2416, 2449-2601, 2882-3070`; `cli.py:1803-1813, 2299-2460`; `docs/upstream-customizations/workflow-orchestration.yaml` (ledger entries `workflow-phase5-inline-agent-authority`, `plugin-agent-runner`, `workflow-run-execution-limits`).

**Local desktop:** `apps/desktop/src/app/workflows/adapter.ts:4-73`, `index.tsx:192-235, 297-320`, `run-inspector.tsx:217-403`, `attention-inbox.tsx:44-65`, `catalog-run-policy.ts:124-190`; `apps/desktop/src/types/hermes.ts:320-393, 838-869`; `apps/desktop/src/lib/workflow-public-codec.ts`.

Method note: every citation was obtained by reading the two trees (directly, or via read-only delegated passes whose load-bearing claims were re-read directly); no file was executed or modified. Existing assessment/review/proposal documents in `docs/assessments`, `docs/reviews`, `docs/plans`, `docs/design`, `docs/handoffs` were not read.

---

## Repository state

During the assessment no repository files were created, modified, or deleted; no branch was changed; nothing was merged, fetched into the repository, or committed. The upstream tag was inspected from a throw-away shallow clone in the session scratchpad (outside the repository). This document was written to the scratchpad first and then saved to `docs/proposals/` at the author's request; it is the only repository change made.
