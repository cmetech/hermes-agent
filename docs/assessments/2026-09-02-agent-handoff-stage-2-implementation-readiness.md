# Agent Handoff Stage 2 Implementation Readiness Assessment

**Date:** 2026-09-02

**Verdict:** Ready to implement after plan approval, with the bounded protocol
clarifications in this assessment. The live tree has the required authenticated
peer, durable Runs, Workflow fencing, interaction, and Needs Attention
authorities. Stage 2 requires no new server route, model tool, supervisor,
database table, generic channel registry, or non-secret environment setting.

**Design authority:**
[`2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md`](../proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md)

**Foundation authority:**
[`2026-09-01-local-workflow-agent-handoff-stage-1.md`](../superpowers/plans/2026-09-01-local-workflow-agent-handoff-stage-1.md)
and
[`2026-09-02-local-workflow-agent-handoff-stage-1-adversarial-review-remediation.md`](../reviews/2026-09-02-local-workflow-agent-handoff-stage-1-adversarial-review-remediation.md)

## Scope and starting state

This assessment validates only Stage 2: authenticated remote Workflow task
handoffs through registered Hermes peers. It does not reopen Stage 1 or
authorize Bot Mode, Desktop migration, `message_agent`, peer-DM replacement,
GitLab+ICM, A2A, a generic channel registry, Windows CLI locking, relay
retirement, or any Stage 3–5 feature.

The checkout was verified before investigation:

```text
branch: base
HEAD: 96840791410c4723d3c4e9de7235afdafc723f81
intervening commits: none
```

Only unrelated untracked user files were present. They were not read as design
authority, modified, staged, or committed.

## Evidence-backed readiness matrix

| Stage 2 requirement | Exact live authority | Readiness |
|---|---|---|
| Consumer-neutral lifecycle | `AgentHandoffService`, `HandoffStore`, and `LocalHermesChannel` under `hermes_cli/handoff/` | Ready to extend. The service already journals before I/O, fences advancement, preserves mechanism immutability, and reconciles ambiguous submission (`hermes_cli/handoff/service.py:118-290`). |
| Strict peer endpoint | `HandoffEndpoint.parse()` | Small bounded extension. It currently accepts only `hermes://local/<profile>` and rejects controls, percent encoding, queries, fragments, and extra path segments (`hermes_cli/handoff/models.py:133-160`). |
| Configured peer resolution | `bot_peers`, `_peer_key_env()`, `_load_peers()`, `_base_url()`, and `_resolve_peer_target()` | Ready after extracting a small non-CLI registry seam. The current functions prove the configuration and credential naming but are private and CLI-shaped (`hermes_cli/subcommands/peer.py:53-130`, `207-219`). |
| Profile-specific peer routes | API-server `/p/{profile}` middleware and mirrored route table | Ready. Unknown profiles fail closed and a served profile enters its own runtime/secret scope (`gateway/platforms/api_server.py:2198-2309`). |
| Peer authentication | `_expected_api_key()` and `_check_auth()` | Ready. Named routes use the selected profile's `API_SERVER_KEY`, never the listener owner's key, and fail closed when missing (`gateway/platforms/api_server.py:1993-2069`). |
| Redirect-safe credential use | `open_credentialed_url()` and `SafeCredentialRedirectHandler` | Ready with a transport correction. Cross-origin redirects strip every non-safelisted header (`hermes_cli/urllib_security.py:30-96`, `141-225`). The Workflow rail must also disable ambient proxies explicitly. |
| Durable keyed admission | `POST /v1/runs` and `RunIdempotencyStore` | Ready. Lookup precedes capacity enforcement, reservation precedes execution, equivalent replay returns the original Run, and conflicting payload reuse returns `idempotency_key_conflict` (`gateway/platforms/api_server_runs.py:450-704`). |
| Authoritative status | `GET /v1/runs/{run_id}` and `_durable_run_status()` | Ready. Status is scoped to the authenticated profile/credential and an orphaned nonterminal owner becomes durable `interrupted` after restart (`gateway/platforms/api_server_runs.py:339-401`, `1056-1087`). |
| Capabilities | Authenticated `GET /v1/capabilities` | Ready. It advertises submission, durable idempotency, status, events, stop, steer, approval response, and approval events, plus exact route templates (`gateway/platforms/api_server.py:3414-3513`). |
| Approval response | `POST /v1/runs/{run_id}/approval` | Ready for exact approval requests. The route accepts an exact `request_id` and one of `once`, `session`, `always`, or `deny`; room-scoped callers are narrower (`gateway/platforms/api_server_runs.py:1155-1290`). |
| Steering and follow-up | `POST /v1/runs/{run_id}/steer` | Ready with one semantic mapping. The live API has one bounded steer rail for both guidance and correlated task follow-up; it only accepts while the Run is genuinely `running` (`gateway/platforms/api_server_runs.py:1293-1368`). |
| Stop and cancellation truth | `POST /v1/runs/{run_id}/stop` plus status polling | Ready. Stop is convergent, terminal status is returned unchanged, active work moves to `stopping`, and final truth still comes from status (`gateway/platforms/api_server_runs.py:1371-1440`). |
| Workflow wait/restart ownership | `HandoffPromptExecutor`, `RunScheduler.advance_due_handoffs()`, and `RunStore` handoff CAS methods | Ready to extend. Semantic identity is already stable across restart, the worker is released, and the elected/fenced coordinator owns observation and cancellation (`plugins/workflow/executors/handoff.py:89-159`; `plugins/workflow/scheduler.py:1126-1390`; `plugins/workflow/store.py:13048-13490`). |
| Workflow interaction/attention | Existing paused interactions, action validation, sanitization, dashboard API, and `NotificationOutbox` | Ready for one new closed interaction type. The existing surfaces already carry exact interaction IDs and CAS versions; `handoff_input` must be added to their closed enums and projections (`plugins/workflow/actions.py:45-90`; `plugins/workflow/sanitize.py:514-537`; `plugins/workflow/notifications.py:574-726`; `plugins/workflow/dashboard/plugin_api.py:189-199`). |
| Real-boundary testing | Real API adapter, HTTP socket, SQLite, multiplex scope, coordinator, and provider-boundary fixtures in the Stage 1 E2E suite | Ready to reuse. `tests/plugins/workflow/test_local_handoff_e2e.py` already replaces only inference while retaining the real service, Runs, middleware, profile secrets, coordinator, and stores. |

No blocking upstream gap remains.

## Reusable authorities and the smallest safe seams

### Peer registry and credentials

The registry authority is the initiating profile's normalized `config.yaml`
`bot_peers` mapping. `load_config(config_path=...)` already supports an explicit
profile path without mutating process-global `HERMES_HOME`
(`hermes_cli/config.py:3837-3885`). The credential name remains
`HERMES_PEER_<NAME>_KEY`; the value must be loaded lazily from the initiating
home with `build_profile_secret_scope()` (`agent/secret_scope.py:289-307`).

Stage 2 should extract only the shared registry/name/base-URL logic from
`hermes_cli/subcommands/peer.py`. The existing CLI continues to own printing,
argument parsing, Bot Chat lookup, DM compatibility, and its current process-env
fallback. The handoff service must not call `cmd_peer()`.

The peer entry is operator-controlled configuration, not endpoint input. The
handoff resolver should nevertheless fail closed unless the registered URL:

- uses `http` or `https`;
- has a hostname;
- has no userinfo, query, or fragment; and
- yields the exact profile prefix `/p/<profile>`.

A base path may remain because registered/cloud gateways can legitimately be
mounted below one. Raw URLs, hosts, ports, credentials, userinfo, queries, and
fragments remain impossible in `hermes://peer/<peer>/<profile>` itself.

### Runs protocol

The destination API server is the sole Runs admission authority. The handoff
adapter sends a bounded key derived as `handoff-<handoff_id>` and never creates
or reserves a remote Run locally. A lost response is reconciled by repeating
the same body and key. HTTP 409 `idempotency_key_conflict` is definitive; an
unkeyed replacement is forbidden.

The local channel already contains the bounded JSON reader, finite operation
deadline, direct opener, stable key construction, terminal-result normalization,
and status mapping (`hermes_cli/handoff/local.py:522-598`, `1030-1175`). Extract
those Runs-only helpers once and use them from both local and peer channels.
Do not duplicate the destination reservation logic or create a second handoff
manager.

Remote task Runs should let `POST /v1/runs` allocate the dedicated session. The
status record already returns `session_id`; this avoids touching canonical Bot
Chat and avoids a separate remote session-creation effect. The adapter persists
the returned Run ID immediately and the session ID on the first status that
contains it.

### Built-in channel selection

One small built-in Hermes dispatcher may select the existing local channel or
the new peer Runs channel from the already-parsed endpoint. This is not the
deferred generic channel registry: it has exactly the two accepted built-in
Hermes destinations and remains owned by `AgentHandoffService`.

The service's one-operation-per-advance and one fenced lease remain unchanged.
Workflow continues to call it from the existing elected coordinator. There is
no second peer supervisor or peer-specific ledger.

## Endpoint and immutable binding contract

The parser should accept exactly:

```text
hermes://local/<profile>
hermes://peer/<peer>/<profile>
```

`peer` is the existing lowercase peer slug; `profile` uses the existing profile
validator. Percent encoding, empty segments, extra segments, uppercase or
invalid peer slugs, userinfo, queries, fragments, and every other scheme or
authority are rejected.

The peer binding should persist only bounded non-secret facts:

```text
peer
profile
mechanism = peer_runs
capabilities (normalized closed set)
origin_sha256
auth_scope_sha256
```

`origin_sha256` detects registry retargeting without storing the URL.
`auth_scope_sha256` detects credential rotation before an ambiguous keyed
submission can accidentally enter a new server-side idempotency scope. It is a
one-way private ledger fact, never evidence or public projection. The resolver
must require a usable high-entropy peer key before computing it.

Every operation resolves the current registry entry and credential lazily,
then compares the two digests with the sealed binding. A mismatch is
`indeterminate` after binding; it never changes mechanism or destination.

## Authentication, redirects, proxies, and evidence

The current peer CLI calls `open_credentialed_url()` without an opener factory,
so it intentionally preserves an installed opener's proxy, cookie, and
instrumentation handlers (`hermes_cli/subcommands/peer.py:86-122`;
`hermes_cli/urllib_security.py:141-225`). That behavior is unsuitable for an
unattended Workflow credential boundary because an ambient proxy can receive
the authorization request.

Stage 2 must therefore combine:

1. the existing registered peer configuration and bearer-auth contract;
2. the existing cross-origin redirect sanitizer; and
3. an explicit `urllib.request.ProxyHandler({})`, matching the local Runs rail.

Same-origin redirects may proceed with credentials. Cross-origin redirects may
proceed only after the sanitizer removes authorization and every other
non-safelisted header. Tests must prove both the header boundary and ambient
proxy bypass.

Response bodies remain bounded before JSON parsing. Persist only closed status,
IDs, digests, normalized capability names, bounded command payloads required
for restart delivery, and stable failure codes. Raw headers, credentials,
configured URLs, unrestricted remote errors, approval command text, prompts,
and result text never enter public evidence. Terminal result text remains in
the private bounded handoff result exactly as Stage 1 requires for Workflow
output validation.

## Capability negotiation

The Runs capability document is authoritative at bind. Minimum task admission
requires:

| Handoff capability | Required advertised Runs feature |
|---|---|
| durable keyed admission | `run_submission == true` and `runs_idempotency.supported == true` and `.durable == true` |
| authoritative status | `run_status == true` |
| cancellation | `run_stop == true` |
| approval interaction | `run_approval_response == true` and `approval_events == true` |
| steering | `run_steer == true` |
| correlated follow-up | `run_steer == true` |

`run_events_sse` is optional. It may reduce latency later but is not part of
correctness or initial Stage 2 implementation.

`structured_output` remains initiator-side validation of the terminal Runs
output through the existing Workflow helper. It does not require a new peer
feature bit.

The Workflow assignment policy maps to required capabilities as follows:

| `interaction_policy` | Admission requirement | Runtime action on `needs_input` |
|---|---|---|
| `pause` | approval interaction | Create durable `handoff_input` and pause for an exact operator response. |
| `deny` | cancellation plus status; approval response when input actually occurs | Send exact `deny` when advertised; otherwise fail closed rather than hang. |
| `auto_cancel` | cancellation | Record and advance the existing cancellation path. |

The local CLI fallback cannot advertise approval interaction, so a `pause`
assignment never falls back to it. Peer DM is never considered.

## Interaction and command mapping

The private handoff checkpoint should retain only the exact remote approval
`request_id`, normalized allowed choices, status, Run ID, and session ID. The
remote command/description is not persisted or projected.

| Service command | Runs route/body | Reconciliation rule |
|---|---|---|
| `respond` | `POST .../approval` with exact `request_id` and normalized choice | A successful response is delivered. A lost response is never blindly resent; status may prove that the exact approval is no longer pending, otherwise the command remains indeterminate. |
| `steer` | `POST .../steer` with bounded `input` | Successful response is delivered. A lost response is irreducibly indeterminate because Runs has no command idempotency key. |
| `message` | Same steer route with a distinct local command kind and correlation ID | Same ambiguity rule as steer. This is a task follow-up, not peer DM. |
| `cancel` | Existing `POST .../stop` lifecycle operation | Repeated observation/stop is convergent; terminal truth comes from status. |
| `reconcile` | Read-only status/key reconciliation | Never substitutes a new Run or resends an ambiguous non-idempotent control. |

The existing `handoff_commands` table already supplies `(handoff_id,
command_id)` uniqueness, content fingerprints, payload storage, and a delivery
state (`hermes_cli/handoff/store.py:245-295`, `930-1028`). Extend its closed
command and delivery-state logic; do not add another table. Journal a command
attempt before HTTP. A crash after that journal leaves the command attempted or
indeterminate and forces read-only reconciliation.

## Durable state and schema assessment

No SQLite DDL migration is required.

- `handoffs.binding_json` can hold the peer/capability/digest binding.
- `handoffs.checkpoint_json` can hold Run, session, key, status, and exact
  approval routing facts.
- `handoff_commands` already holds content-bound commands and delivery state.
- `handoff_events` already holds bounded redacted evidence.

Keep `handoffs.db` at schema version 1 unless implementation discovers a real
column/index requirement. Expanding the closed JSON validators and adding
state-transition methods is sufficient.

Workflow's private `run.json` needs additive node facts for one pending
`handoff_input` and one recorded response command. No new Workflow SQLite table
or run-level lifecycle value is required. The public schema remains version 1
but its closed interaction enum must add `handoff_input` in Python and desktop
codecs in the same atomic task.

## Workflow projection and Needs Attention

`HandoffPromptExecutor` already derives the stable key
`<run_id>:<node_id>:<generation>` and reuses the same handoff after restart
(`plugins/workflow/executors/handoff.py:89-159`). Stage 2 adds the assignment's
interaction capability to the spec but does not change semantic generation.

When authoritative status maps to `needs_input`:

- `pause` changes the node from `waiting_handoff` to `paused`, records one
  private exact remote request and one public bounded `handoff_input`, and
  changes the run to its existing `paused` status;
- `approve` maps to Runs choice `once`, while `reject` maps to `deny`;
- the local decision is CAS-protected by exact interaction ID and Workflow
  state version;
- applying the decision records a stable handoff `respond` command, restores
  the node to `waiting_handoff`, restores the run to `running`, and wakes the
  coordinator; and
- the coordinator records the command in the shared handoff ledger before the
  service performs HTTP.

Cancellation wins a response race. Cancelling a paused handoff discards any
unsent response intent, restores the node to `waiting_handoff`, and uses the
existing exact handoff cancel projection until authoritative terminal truth.

Add `handoff_input_required` to the journal/notification allowlists and map it
to `approval_required`. `available_actions()` should expose
`approve`, `reject`, and `cancel` for a paused `handoff_input`. The public
projection exposes only type, local interaction ID, and node ID. It must not
expose the remote request ID, choices that were not advertised, the dangerous
command text, credentials, or peer URL.

## Restart, ambiguity, interruption, and cancellation

- **Initiator restart before submission:** the prepared handoff binds or
  submits once under the existing advance lease.
- **Initiator restart after submit journal, before response:** reconciliation
  repeats the identical keyed request and recovers the same Run.
- **Destination restart after keyed admission:** polling hydrates the durable
  record; a nonterminal orphan maps to `interrupted`, then the handoff maps to
  `indeterminate` with `run_interrupted`. It is never replaced.
- **Lost approval response:** no blind replay. Poll status; resolve only when
  authoritative state proves the exact approval is no longer pending.
- **Lost steer/follow-up response:** remain command-indeterminate; status cannot
  prove that text was consumed.
- **Stop race with completion:** completed wins and Workflow runs the existing
  outward-effect reconciliation path.
- **Stop race with approval response:** cancellation wins locally; no new
  response command is emitted after desired cancellation is durable.
- **Deadline:** the existing deterministic cancel command and scheduler
  priority remain authoritative.
- **Events:** never required. Destination restart may discard the live SSE
  stream without affecting correctness.

## Real-boundary test strategy

The Stage 2 E2E fixture should create separate temporary initiating and
destination Hermes homes, register the destination only in the initiating
profile's `bot_peers`, store the peer key only in the initiating profile's
`.env`, and start real authenticated API-server adapters on loopback sockets.
Use real profile middleware, Runs handlers, `RunIdempotencyStore`, session DB,
handoff DB, Workflow RunStore, coordinator election/fence, and HTTP. Replace
only external model inference with a deterministic boundary.

The acceptance cases are:

1. remote structured task success under the destination profile's home and
   provider credential;
2. lost first submission response, same-key recovery, and exactly one
   destination execution;
3. duplicate-key replay and conflicting-payload 409;
4. destination restart with the same durable Runs database and interrupted
   nonterminal truth;
5. capability mismatch before submission;
6. unknown peer, wrong profile credential, wrong peer credential, registry
   retarget, and credential rotation isolation;
7. cross-origin redirect credential stripping and ambient-proxy bypass;
8. approval -> durable `handoff_input` -> exact `once`/`deny` response ->
   continuation, including initiator restart while paused;
9. correlated `message`, `steer`, successful stop, interrupted status, and
   irreducibly lost control responses;
10. cancellation versus completion, cancellation versus approval response,
    and deadline races; and
11. returned session inspection with the destination credential while an
    unrelated profile credential receives 401/404.

Unit/contract tests should inject failures at the narrow seams. E2E tests must
not mock the registry resolver, bearer auth, redirect policy, API handlers,
idempotency store, handoff service/store, Workflow store, or coordinator.

## Live-code clarifications to the accepted design

These are implementation constraints discovered in the live tree, not a
replacement architecture:

1. **`handoff_input` is approval-backed in Stage 2.** Runs exposes durable
   approval requests/responses, not a generic arbitrary question/answer route.
   Stage 2 supports exact approval `request_id` plus `once`, `session`,
   `always`, and `deny`. A future generic question capability must be separately
   advertised and is not inferred from `run_approval_response`.
2. **Follow-up and steer share one remote route.** `/steer` is the only live
   input route for an active Run. Keep distinct local command kinds and
   correlation IDs, but do not invent a peer message endpoint or use peer DM.
3. **Runs control routes are not remotely idempotency-keyed.** Local command
   IDs remain durable and content-bound, but a lost approval/steer response
   must reconcile from status or remain indeterminate. Only Run admission has
   the strong remote key contract.
4. **The peer CLI's opener preserves ambient proxies.** Stage 2 reuses its
   registry/auth meaning and the shared redirect sanitizer, but the Workflow
   transport must use a proxy-free opener. Existing peer CLI and Bot behavior
   remain unchanged.
5. **One peer entry has one peer credential.** A profile-specific Runs route
   authenticates with that destination profile's `API_SERVER_KEY`. Operators
   targeting remote profiles with different keys need distinct registered peer
   names/credentials today. Expanding the peer registry to per-profile keys is
   not required for Stage 2 and must not be invented implicitly.

## Residual risks and platform gaps

| Risk/gap | Stage 2 disposition |
|---|---|
| HTTP peer URL carries bearer auth without transport encryption | Preserve existing registered-peer behavior; recommend configured HTTPS/secure network but do not add a new setting. Tests use loopback HTTP only. |
| Peer credential rotates after bind | Compare the private auth-scope digest and fail indeterminate; never replay a possibly accepted key under a new auth scope. |
| Pending approval is nonterminal when destination restarts | The Runs authority maps it to `interrupted`; Workflow surfaces reconciliation rather than pretending the approval survived. |
| Lost steer/follow-up response cannot be proved | Keep the command indeterminate and bounded in evidence; do not resend. |
| Windows CLI destination lock | Unchanged and irrelevant to peer Runs; remains Stage 5. |
| Whole-Workflow macOS shutdown bus error | Existing `test_scheduling_middleware_e2e.py` background-thread/SQLite lifecycle defect. It is unchanged at the Stage 2 merge base and is not a prerequisite unless Stage 2 evidence proves a regression. |
| Load-sensitive hard-maximum test | Existing diagnostic passed immediately in isolation. Keep it in broad diagnostics, not Stage 2 scope. |

## Focused pre-planning baseline

The required live baseline ran with file retries disabled and collected real
tests:

```text
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_models.py \
  tests/hermes_cli/handoff/test_store.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/test_peer_cmd.py \
  tests/gateway/test_api_server_run_idempotency.py \
  tests/gateway/test_api_server_runs.py \
  tests/plugins/workflow/test_handoff_executor.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py -q

299 passed, 0 failed
```

Installed-distribution registration also ran explicitly:

```text
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  -m integration \
  -k extracted_wheel_registers_workflow_cli_from_a_clean_home -q

1 passed, 0 failed, 5 deselected
```

The inherited whole-Workflow diagnostic is recorded without claiming a fresh
pass: 5,919 passed, 1 failed, 5 skipped. The isolated hard-maximum test passed;
the reproducible post-test macOS bus error in
`test_scheduling_middleware_e2e.py` remains the preexisting background-thread
SQLite initialization defect described by the Stage 1 handoff.

## Implementation gate

Stage 2 is implementation-ready, but production code remains blocked until
[`2026-09-02-remote-workflow-agent-handoff-stage-2.md`](../superpowers/plans/2026-09-02-remote-workflow-agent-handoff-stage-2.md)
is reviewed and accepted. Implementation must follow its red-green-refactor
tasks and stop at the adversarial review gate before completion.
