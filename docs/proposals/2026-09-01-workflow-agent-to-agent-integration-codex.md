# Workflow Agent-to-Agent Integration Proposal (Codex)

**Status:** Proposed

**Date:** September 1, 2026

**Audience:** Workflow, gateway, Bot Mode, and Desktop maintainers

**Decision requested:** Select the workflow authoring contract and authorize a
workflow-owned durable remote-run implementation.

> **September 1 addendum:** The approved direction evolved after a second
> concrete communication strategy, GitLab+ICM, was defined and Bot Mode was
> added as a required consumer. The addendum at the end of this document
> supersedes the workflow-owned facade and single-Runs-transport conclusions
> while preserving the original analysis as decision history.

## Executive assessment

Hermes workflows should treat another Hermes agent as a durable external
executor. The workflow coordinator should use the asynchronous `/v1/runs`
protocol behind `hermes peer run`, `status`, and `stop`. It should not expose
`message_agent` to workflow workers, shell out to `hermes peer`, or add a new
permanent core model tool.

The existing ownership model should remain unchanged:

- The initiating profile owns the workflow, trust decision, execution ledger,
  retries, artifacts, notifications, and Activity Board card.
- The destination profile owns its system prompt, model/provider credentials,
  memory, tools, approvals, filesystem, and execution cost.
- Communication crosses that boundary explicitly. Profiles do not inherit or
  share configuration.

The smallest correct authoring design is to route an existing workflow
`prompt` node through the Hermes companion sidecar. This preserves the portable
workflow definition and reuses the existing prompt, dependency, output,
timeout, and retry semantics. A dedicated `agent` node can be added later as
authoring sugar if usage proves that the two-file package is too opaque.

Normal remote execution belongs in the Activity Board's Active column. Only
conditions on which a person can act—remote approval, missing configuration,
identity or capability drift, ambiguous dispatch, interrupted work, or an
unconfirmed stop—belong in Needs Attention.

## Scope and desired outcome

After this proposal is implemented, a workflow author should be able to:

- Assign a prompt node to another named profile on the same Hermes
  installation.
- Assign it to a named profile on a registered peer gateway.
- Wait without holding a workflow worker.
- Consume the agent's bounded result through ordinary downstream node output.
- Recover correlation after a coordinator restart.
- Cancel the exact remote run.
- Respond to an exact remote approval from Needs Attention.
- Reconcile work whose outcome cannot safely be inferred.

This proposal does not attempt to provide a generic agent protocol, arbitrary
internet A2A interoperability, exactly-once business effects, large artifact
transfer, consensus, detached jobs, or automatic multi-hop delegation.

## Verified baseline

### The current fork needs the upstream Runs lifecycle

At the time of this assessment, the fork's `base` branch contains peer
registration and synchronous direct messaging but not the complete
v2026.8.31 `run`, `status`, and `stop` CLI lifecycle or its durable idempotency
hardening.

The upstream v2026.8.31 release is Hermes Agent v0.21.0, published August 31,
2026. It adds the relevant asynchronous peer commands and durable run replay.
The release must be merged into `base` through the fork's normal upstream-sync
process before workflow integration begins.

References:

- [Hermes Agent v2026.8.31 release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.8.31)
- [Upstream peer command implementation](https://github.com/NousResearch/hermes-agent/blob/v2026.8.31/hermes_cli/subcommands/peer.py)
- [Upstream Runs API](https://github.com/NousResearch/hermes-agent/blob/v2026.8.31/gateway/platforms/api_server_runs.py)
- [Upstream run idempotency store](https://github.com/NousResearch/hermes-agent/blob/v2026.8.31/gateway/platforms/api_server_run_idempotency.py)

### Upstream exposes two different communication contracts

#### Conversational delivery

`message_agent` and `hermes peer dm` deliver into the destination's canonical
Bot Chat.

`message_agent` is intentionally contained:

- It is available only in canonical Bot Chat sessions on Bot-Mode-managed
  installations.
- It is absent from workflow workers, cron, ordinary chats, group-room member
  sessions, subagents, and the core tool registry.
- Delivery is fire-and-forget from the sender's current turn.
- The destination conversation remains durable and human-inspectable.

This is the correct mechanism for conversational coordination, FYI messages,
and work where the sender does not need a correlated result before continuing.
It is not a workflow dependency contract.

References:

- [Bot Mode agent-to-agent tool](https://github.com/NousResearch/hermes-agent/blob/v2026.8.31/tools/bot_mode_dm.py)
- [Bot Mode peer documentation](https://github.com/NousResearch/hermes-agent/blob/v2026.8.31/website/docs/user-guide/bot-mode.md)

#### Correlated remote work

The Runs API is the workflow-grade substrate:

- `POST /v1/runs` starts an agent turn and returns a `run_id` immediately.
- `GET /v1/runs/{id}` returns queued, running, approval, or terminal state.
- `POST /v1/runs/{id}/stop` interrupts the exact run.
- `POST /v1/runs/{id}/approval` resolves an exact pending approval.
- A stable `Idempotency-Key` returns the original admission instead of starting
  duplicate work.

The upstream tag stores idempotency reservations and public status in SQLite,
scoped by the authenticated tenant and profile. Request bodies and credentials
are deliberately excluded from that store.

Durable correlation is not durable execution. If the destination gateway
restarts during active work, the persisted run becomes `interrupted`; Hermes
does not recreate the in-flight agent process. Idempotent terminal records also
have finite retention, normally 24 hours. The protocol therefore provides
duplicate-resistant admission and restart-visible outcomes, not exactly-once
remote side effects.

### Existing workflow machinery is already the right owner

The workflow plugin already provides the necessary local orchestration
foundation:

- It registers no permanent model-facing tools.
- Workflows, trust decisions, stores, and board queries are profile-scoped.
- RunStore is the execution authority.
- Waiting and paused runs can release workers.
- Output contracts, artifacts, retries, cancellation, reconciliation, and
  durable notification facts already exist.
- Inline `agents` use bounded, synchronous, separately spawned
  `workflow_agent` workers.
- Raw `delegate_task` is deliberately unavailable because its child lifecycle
  can outlive an ephemeral workflow node worker.

Inline agents should continue serving ephemeral roles within one node. They
should not be stretched into named-profile or cross-gateway execution.

Upstream hosted rooms already demonstrate the distributed lifecycle required
here: deterministic idempotency, durable local receipts, status polling,
approval, cancellation, restart recovery, and exact task-attempt fencing.
Workflow should reuse that pattern, but not depend on room-specific grants,
authority epochs, dispatch payloads, or hosted-room tables.

Reference:

- [Hosted-room peer Runs client](https://github.com/NousResearch/hermes-agent/blob/v2026.8.31/tui_gateway/hosted_room_peer_http.py)

## User-interface options

### Option 1: Compose the existing CLI in a Bash node

```yaml
- id: research
  bash: |
    hermes peer run spark/researcher --json < request.txt
```

This has almost no implementation cost and is useful as a connectivity spike.
It is not a production workflow contract.

Problems include:

- The workflow Bash environment is intentionally narrow and does not preserve
  the owning profile's `HERMES_HOME`, so peer configuration and credentials can
  resolve from the wrong profile.
- Polling holds a worker unless another state machine is built around the
  shell command.
- The workflow must parse CLI output and reconstruct typed failures.
- A crash between remote admission and local output parsing loses the receipt.
- Approval and cancellation cannot cleanly participate in workflow state.

**Verdict:** retain only as a manual diagnostic or early spike.

### Option 2: Add a first-class `agent` node

```yaml
nodes:
  - id: research
    agent:
      target: peer:spark/researcher
      prompt: |
        Analyze $collect.output and return a structured risk assessment.
    depends_on: [collect]
    output_format:
      type: object
      required: [risks, recommendation]
```

This provides the clearest one-file authoring experience. It also creates a new
node type across parsing, normalization, validation, portable-language
compatibility, topology, doctor, execution, reports, tests, and documentation.
The singular `agent` node can also be confused with the existing plural inline
`agents` declaration.

**Verdict:** a reasonable future authoring layer, but too much initial schema
surface when the execution semantics already match a routed prompt node.

### Option 3: Route an existing prompt node through the companion sidecar

Portable workflow definition:

```yaml
nodes:
  - id: research
    prompt: |
      Analyze $collect.output and return a structured risk assessment.
    depends_on: [collect]
    output_format:
      type: object
      required: [risks, recommendation]

  - id: decide
    prompt: |
      Decide what to do using this agent assessment:
      $research.output
    depends_on: [research]
```

Hermes companion sidecar:

```yaml
agent_routes:
  research:
    target: peer:spark/researcher
    session: fresh
```

This treats destination selection as Hermes deployment policy rather than a
portable DAG primitive. The sidecar already participates in package digest,
trust, and risk evaluation. The node retains ordinary dependencies, timeout,
retry, output, and structured-output behavior.

The workflow builder and Desktop can present one “Run on agent” control while
persisting portable work and Hermes-specific routing separately.

**Verdict:** recommended for the first release.

## Recommended authoring contract

Only the symbolic target should be required initially:

- `profile:researcher` selects a named profile on the owning installation.
- `peer:spark/researcher` selects profile `researcher` on registered peer
  `spark`.
- `peer:spark/hermes` selects that peer's default profile.

Workflow packages must not contain raw URLs, API keys, bearer tokens, HTTP
headers, filesystem profile paths, or mutable display names.

Use fixed safe behavior in the first release instead of exposing configuration
for every lifecycle choice:

- Await the remote result.
- Propagate workflow cancellation to the exact remote run.
- Surface remote approvals in Needs Attention.
- Reconcile interrupted or ambiguous work.
- Never detach.
- Never blindly retry an uncertain outward action.

Fields such as `await`, `detach`, `cancel`, `on_interrupt`, polling intervals,
or arbitrary transport selection should wait for demonstrated use cases.

## Durable execution lifecycle

```text
Persist dispatch intent and deterministic idempotency key
                          |
                          v
                   POST /v1/runs
                          |
                          v
       Persist run_id and session_id receipt immediately
                          |
                          v
         waiting_external; release workflow worker
                          |
                          v
          Poll durable status with bounded backoff
                /              |              \
        completed        approval wait      interrupted/unknown
            |                  |                    |
    validate output        pause for             pause for
    publish result         operator              reconciliation
    continue DAG           attention             decision
```

### Before network I/O

Persist a dispatch intent containing:

- Workflow run, node, and attempt identity.
- Resolved non-secret destination identity and digest.
- Request fingerprint.
- Deterministic idempotency key.
- Capability and policy digest where available.
- Dispatch state and timestamps.

The key should identify one semantic node attempt:

```text
wf:<workflow-run-id>:<node-id>:<attempt-generation>
```

The authenticated Runs scope already separates profiles and tenants.

Transport retries reuse the same key and exact request. A deliberate workflow
retry increments the attempt generation and receives a new key. Upstream's
request fingerprint must reject reuse of a key with different work.

### After remote acceptance

Persist the receipt before reporting successful dispatch:

- Resolved target.
- Request fingerprint and idempotency key.
- Remote `run_id` and `session_id`.
- Destination capability/policy digest.
- Admission and observation timestamps.

No URL credential, API key, prompt secret, raw authorization header, or remote
environment value belongs in the receipt.

The node then enters a durable external-wait state and releases its worker.
Status polling is the correctness path. An event stream may later reduce
latency, but disconnection from that stream must never strand a workflow.

### Recovery and retry rules

- Intent without receipt: replay the exact admission with the original key.
- Receipt present: inspect the exact remote run.
- Completed remote run: copy and validate the result promptly because upstream
  retention is finite.
- Known pre-admission failure: retry only if normal retry policy permits it.
- Ambiguous admission: replay only with the same idempotency key.
- Remote `interrupted`: require reconciliation by default.
- Missing or expired status for a known receipt: require reconciliation.
- Deliberate retry after a reconciled failure: create a new workflow attempt and
  key.

Idempotent admission prevents duplicate Runs records. It cannot make tool calls
inside the destination agent exactly once. A remote command may have produced
an outward effect before the process was interrupted.

## Session policy

The `hermes peer run` CLI uses the destination's canonical Bot Chat. That is a
useful interactive default but a poor workflow default:

- Separate workflow runs contaminate one another's context.
- Concurrent tasks can interleave in one transcript.
- Results become less reproducible.
- Canonical Bot Chat can expose `message_agent`, enabling transitive
  communication outside the workflow's direct lifecycle.

The Runs API supports isolated execution by omitting `session_id`; the new
`run_id` becomes the session identity.

Recommended session policies are:

- `fresh`: default; isolated per semantic task attempt.
- `workflow`: later option for intentional continuity, with serialized access
  and a sealed cache fingerprint.
- `bot_chat`: explicit opt-in when a durable human-visible conversation is more
  important than isolation and reproducibility.

## Activity Board and Needs Attention

No new board column is necessary.

| Remote condition | Workflow state | Board behavior | Operator action |
| --- | --- | --- | --- |
| `queued` or `running` | External wait | Active | None |
| `waiting_for_approval` | Paused with exact remote approval | Needs Attention | Approve once or deny |
| Missing target, credential, or required capability | Paused capability remediation | Needs Attention | Repair configuration and resume |
| Temporary unreachability within retry policy | Waiting with backoff | Active | None |
| Prolonged unreachability | Stalled or capability pause | Needs Attention | Retry, repair, or cancel |
| `completed` | Validate and publish output | Completed or next active node | None |
| `failed` | Terminal failure | Failed / stopped and attention inbox | Retry or inspect |
| Confirmed cancellation | Cancelled | Failed / stopped | None |
| `interrupted` | Paused reconciliation | Needs Attention | Record outcome or retry |
| Ambiguous dispatch | Paused reconciliation | Needs Attention | Inspect or record outcome |
| Unconfirmed stop | Paused reconciliation | Needs Attention | Retry stop or reconcile |
| Missing/expired status for a known receipt | Paused reconciliation | Needs Attention | Reconcile |

Normal waiting is not an attention condition. Needs Attention means that a
person can take a useful action.

### Remote approvals require a distinct interaction

Existing local workflow approval terminates a worker and later reruns the node
with a narrowly scoped grant. A remote approval targets an agent process that
is already alive and waiting on `/v1/runs/{id}/approval`.

The workflow attention model should therefore add an interaction such as
`agent_run_approval` rather than pretending it is a local approval. It must
include the exact remote `run_id`, request ID, target, sanitized summary, and
state version.

The initial UI should expose only:

- Approve once.
- Deny.

It must not convert a workflow-board action into session-wide or permanent
approval on another profile.

## Profile, security, and trust model

### Ownership boundary

The initiating profile owns:

- Workflow package and trust decision.
- RunStore and remote receipt.
- Retry and reconciliation policy.
- Output validation and artifacts.
- Activity Board and notification state.

The destination profile owns:

- System prompt and memory.
- Model/provider selection and credentials.
- Tool and MCP availability.
- Filesystem and terminal backend.
- Tool approvals and resource limits.
- Its own agent transcript and usage.

The source workflow supplies a user-level task and expected result contract. It
must not silently override destination tools, credentials, system instructions,
or approval rules.

### Admission and trust requirements

Workflow doctor/admission should:

- Resolve the symbolic target through the owner profile's configuration.
- Confirm the destination profile exists.
- Confirm credentials without exposing them.
- Probe `/v1/capabilities`.
- Require `runs_idempotency.supported=true` and `durable=true` for unattended
  execution.
- Seal a non-secret target identity and capability/policy digest.
- Stop for attention if an alias later resolves to another destination or if
  required capabilities/policy drift.
- Add the destination and data boundary to the workflow risk summary.

Peer aliases and behavioral configuration live in `config.yaml`. API keys
remain credentials in the profile `.env` or future credential store.

### Data and execution safety

- Treat the returned agent output as untrusted data, never as a system-prompt
  mutation.
- Validate structured output locally before publishing it downstream.
- Enforce existing prompt, output, artifact, runtime, token, cost, fan-out, and
  descendant bounds.
- Redact credentials, headers, raw command details, and remote error bodies from
  stores, events, approvals, and UI projections.
- Do not silently truncate structured output. Fail with a typed bounded-output
  error or store an approved artifact reference.
- Preserve the destination's approval policy.
- Keep transitive remote delegation disabled by default. A fresh non-Bot-Chat
  session naturally avoids the Bot Chat-only `message_agent` tool.
- Report peer/network reachability constraints during doctor; direct gateways
  still require a valid LAN, VPN, Tailscale, public route, or equivalent path.

No new core tool is needed, and the model tool schema remains stable. This
preserves the core narrow waist and per-conversation prompt caching.

## Implementation direction

The workflow plugin should call the Runs HTTP API directly. `hermes peer` is a
human CLI facade, not a runtime library contract, and it does not expose every
operation workflow needs.

The implementation should reuse:

- Existing peer target and credential conventions.
- Credentialed URL redirect hardening.
- Runs API idempotency and status shapes.
- Hosted-room receipt, polling, and recovery patterns.
- Workflow admission, store transactions, external-effect reconciliation,
  pending interactions, output validation, artifacts, and notification outbox.

It should not directly depend on:

- CLI printing or argument parsing.
- Bot Chat's `message_agent` tool.
- Hosted-room tables, grants, authority epochs, or room-specific payloads.
- Raw `delegate_task`.
- Process-wide environment changes for profile selection.

Do not introduce a generic broker, transport factory, or protocol interface for
one Hermes Runs implementation. Extract a shared client only if hosted rooms
and workflow can both consume a genuinely generic helper without importing one
another's domain state.

## Staged delivery

### Stage 0: prerequisite merge

Merge upstream v2026.8.31 into `base` through the literal-main synchronization
workflow. Verify the exact peer, Runs, idempotency, approval, stop, HTTP
hardening, and hosted-room behavior after conflict resolution.

### Stage 1: durable routed execution

- Add companion-sidecar routing for existing prompt nodes.
- Resolve explicit local-profile and peer-profile targets.
- Add dispatch-intent and receipt persistence.
- Submit with deterministic idempotency.
- Add external-wait scheduling that releases workers.
- Poll status and copy terminal output promptly.
- Validate ordinary and structured output.
- Propagate cancellation and verify its terminal outcome.
- Map interruption and ambiguous outcomes to reconciliation.

### Stage 2: operator integration

- Add `agent_run_approval` projection and actions.
- Put exact remote approvals in Needs Attention.
- Add capability/credential remediation.
- Add target/capability/policy drift detection.
- Surface remote target, state, elapsed time, and bounded usage on the run
  detail without adding another board lane.

### Stage 3: only after demonstrated demand

Consider:

- A first-class `agent` node as sugar over routed prompt semantics.
- Serialized workflow-scoped session continuity.
- Explicit Bot Chat session routing.
- Steering.
- Bounded fan-out or consensus patterns.
- External artifact exchange.
- Non-Hermes A2A transports.

Do not implement these in the initial release.

## Acceptance tests

The feature is not complete without end-to-end tests using real imports,
temporary profile homes, SQLite stores, and an actual local HTTP gateway path.

Required cases:

1. A routed prompt starts one remote run, returns bounded output, and feeds a
   downstream node.
2. A lost admission response followed by replay returns the same remote
   `run_id`.
3. A coordinator restart after intent but before receipt replays the same
   admission.
4. A coordinator restart after receipt resumes observation without
   redispatching.
5. A destination gateway restart changes an active run to `interrupted` and
   places the workflow in reconciliation instead of retrying blindly.
6. A remote approval creates exactly one Needs Attention interaction and
   approval targets the exact request ID.
7. Denial reaches the exact remote run and produces a stable workflow outcome.
8. Workflow cancellation stops the exact run and does not report cancellation
   until the outcome is confirmed.
9. An unreachable stop produces reconciliation, not optimistic cancellation.
10. Ordinary queued/running remote work remains Active and consumes no worker.
11. Missing credentials or required capabilities pause with typed remediation.
12. Target alias, destination profile, capability, or execution-policy drift
    fails closed.
13. Invalid structured output never reaches a downstream node.
14. Oversized output fails safely or becomes a permitted bounded artifact; it
    is never silently truncated into valid-looking data.
15. No URL credential, API key, authorization header, prompt secret, or raw
    remote error enters RunStore, artifacts, logs, events, or UI projections.
16. Profile A cannot use profile B's workflow trust or peer credentials.
17. The destination applies its own tool and approval policy, even if the
    source workflow requests something broader.
18. A GUI/workflow session resolves its profile from session and store context,
    not an ambient process environment variable.

## Risks and limitations

- Upstream idempotency prevents duplicate admission but cannot guarantee
  exactly-once remote side effects.
- Active work does not survive a destination gateway restart.
- Terminal status retention is finite; the workflow must copy results promptly
  and reconcile after retention gaps.
- Text/result transport is not a general artifact-transfer protocol.
- Cross-machine execution depends on direct network reachability.
- A canonical Bot Chat is inspectable but creates context contamination and
  possible transitive communication.
- Destination tools may perform outward effects outside the source workflow's
  visibility. Trust and risk summaries must make that boundary explicit.
- Remote cost and usage must eventually be surfaced coherently, even though the
  destination profile remains the authority for provider accounting.

## Final recommendation

Proceed with a workflow-owned durable external-run state machine using the
v2026.8.31 Runs API. Keep one profile as the workflow owner, preserve the
destination profile's independent authority, and route existing prompt nodes
through digest-bound companion-sidecar configuration.

Use fresh isolated sessions by default. Persist intent before dispatch and the
remote receipt immediately after admission. Release workers while polling.
Map exact remote approvals and genuinely ambiguous outcomes into the existing
Needs Attention experience. Reuse existing workflow reconciliation, output,
artifact, trust, and notification machinery.

Do not expose `message_agent` to workflow agents, shell out to `hermes peer` in
the production path, add a core model tool, or build a generic transport layer
before another concrete protocol requires one.

The governing boundary is:

> The workflow owns orchestration and durable correlation; the destination
> profile owns agent behavior and permission; `/v1/runs` owns remote execution
> identity.

---

# Addendum: Shared Agent Handoff Facade for Workflows and Bot Mode

**Status:** Recommended direction approved for consolidation

**Date:** September 1, 2026

**Audience:** Agent core, workflow, gateway, Bot Mode, Desktop, and plugin
maintainers

## Addendum decision

The handoff facade must be a neutral Hermes service shared by workflows and Bot
Mode. It must not be owned by either subsystem.

The selected interface is a durable, convergent handoff state machine. A
handoff represents one correlated unit of agent work or communication with an
eventual result, failure, or request for input. URI schemes select the
communication strategy. Hermes gateway communication and GitLab+ICM may be
installed and used concurrently, including by different nodes in the same
workflow or by the same logical agent through different endpoints.

The revised governing boundary is:

> The shared handoff service owns durable communication identity, lifecycle,
> reconciliation, and evidence. Each initiating consumer owns its domain
> behavior and presentation. Each destination owns its agent behavior and
> permissions. Each channel adapter owns its external protocol.

## Why the original boundary changed

The original proposal correctly avoided a generic transport abstraction while
Hermes Runs was the only concrete workflow-grade execution protocol. A second
concrete requirement now exists: a non-real-time GitLab town hall based on the
Interpretable Context Methodology, repository folders, issues, comments,
branches, commits, and merge requests.

Bot Mode is also a required consumer. Its agents must eventually be able to
select GitLab+ICM or another installed strategy from the Bot experience, not
only direct local, peer, or Desktop-relay delivery.

These are not speculative extension points. They are two real consumers and
two materially different communication strategies. A narrow handoff facade is
therefore the smallest design that prevents workflow, Bot Mode, Desktop, and
future channel plugins from independently reimplementing lifecycle and failure
handling.

This remains deliberately narrower than a universal message bus. It models a
correlated handoff with durable admission, observation, interaction, and a
terminal outcome. It does not attempt to model arbitrary social chat,
consensus, or every messaging-platform feature.

## Shared architecture

```text
Workflow coordinator ─────────────┐
                                  │
Bot Mode message_agent tool ──────┼── AgentHandoffService
                                  │         │
Desktop Bot action/RPC ───────────┘         ├── Hermes channel
                                            ├── GitLab+ICM channel
                                            └── future explicit channels
```

The shared subsystem owns:

- Endpoint parsing, validation, and resolution.
- A channel registry keyed by URI scheme.
- Durable handoff records and stable operation identifiers.
- Intent-before-I/O journaling and request-digest collision checks.
- Adapter checkpoints, observation cursors, and poll scheduling.
- Reconciliation after ambiguous delivery.
- Questions, replies, cancellation requests, and terminal results.
- Normalized, redacted audit evidence.
- Delivery of state changes to the initiating consumer.

Consumers retain their own responsibilities:

- The workflow coordinator binds a workflow node execution to a `handoff_id`,
  applies workflow timeout and result contracts, and projects actionable states
  into workflow Needs Attention.
- Bot Mode binds a handoff to the initiating profile and canonical Bot Chat,
  uses its existing notification behavior for eventual replies, and projects
  actionable states into Bot attention badges.
- The Desktop renders targets, channels, status, evidence, and actions. It does
  not implement Hermes, GitLab, or other channel protocols.

The shared handoff record is canonical for communication lifecycle. Workflow
RunStore, Bot Chat state, and Desktop state store references and projections;
they do not duplicate transport truth.

## Facade contract

The public facade should remain small:

```python
class AgentHandoffFacade(Protocol):
    def validate(self, endpoint: str) -> BoundEndpoint: ...

    def advance(
        self,
        spec: HandoffSpec,
        context: HandoffContext,
    ) -> HandoffSnapshot: ...

    def command(
        self,
        context: HandoffContext,
        command: HandoffCommand,
        command_id: str,
    ) -> HandoffSnapshot: ...
```

`advance` is convergent and performs at most one bounded interaction:

- Before submission, it persists intent and asks the selected adapter to
  create or find the external handoff.
- After submission, it observes the existing handoff rather than dispatching
  again.
- After restart, it resumes from the stored adapter checkpoint.
- After ambiguous delivery, it reconciles using the stable operation marker.
- While waiting, it returns a next-observation time and releases the caller's
  worker.
- After a terminal state, repeated calls return the same durable outcome.

`command` initially needs only three semantic operations:

- Post a follow-up message.
- Respond to an exact input or approval request.
- Request cancellation.

The common lifecycle is:

```text
prepared
submitted
active
needs_input
cancel_requested
succeeded
failed
cancelled
unknown
```

`unknown` means admission or command delivery cannot be proved. It must create
an actionable reconciliation state rather than cause an automatic duplicate
submission.

## Channel adapter boundary

Channel implementations remain behind a smaller plugin-facing contract:

```python
class AgentHandoffChannel(Protocol):
    schemes: frozenset[str]

    def inspect(self, endpoint) -> ChannelCapabilities: ...
    def advance(self, envelope, checkpoint) -> ChannelTransition: ...
    def apply(self, binding, command, command_id) -> ChannelTransition: ...
```

The facade owns durable semantics. An adapter owns authentication, protocol
calls, protocol-specific identifiers, polling cursors, and reconciliation
against its external system. Adapter checkpoints must be bounded,
versioned, JSON-serializable, and free of credentials.

The plugin system should expose one generic registration operation for this
concrete category:

```python
ctx.register_handoff_channel(channel)
```

Hermes communication supplies the built-in `hermes` scheme. GitLab+ICM is a
standalone plugin that registers `gitlab+icm`. GitLab dependencies and object
types must not enter the workflow plugin, Bot Mode, Desktop, or agent loop.

The existing A2A protocol plugin could later register an `a2a` channel without
changing either consumer, but that integration is not required for the first
implementation.

## Endpoint and selection model

The endpoint URI determines the strategy:

```text
hermes://local-researcher
hermes://spark-reviewer
gitlab+icm://corp-townhall/security-reviewer
```

The URI carries a configured connection alias and adapter-specific target. It
never carries an API key, bearer token, GitLab token, or embedded credential.
Secrets remain in the initiating profile's secret store.

Both local and remote Hermes targets use the same channel. A local alias
resolves to the multiplexed loopback gateway and destination-profile route. A
remote alias resolves to a registered peer. This preserves the same security,
status, cancellation, and audit boundary across machines.

Target resolution should follow this order:

1. An endpoint explicitly selected for the current handoff.
2. The target agent's configured default endpoint.
3. The existing direct Hermes route for backward compatibility.

Multiple channel implementations may be registered simultaneously. A workflow
may use Hermes for one node and GitLab+ICM for another. One agent may advertise
a fast Hermes endpoint and a durable GitLab endpoint.

One logical handoff selects exactly one endpoint. It must not be mirrored or
automatically failed over after external admission is accepted or uncertain;
doing so could execute the same work twice. Intentional fan-out belongs in
explicit workflow structure. Future fallback may try another endpoint only
when the facade can prove that no external handoff was admitted.

## Interaction style is separate from channel

The endpoint determines how communication travels. The initiating consumer
determines the interaction style:

```text
Workflow: task + fresh execution + result contract
Bot Mode: message + continuing relationship + eventual reply
```

For a Hermes endpoint, a workflow normally uses fresh Runs execution while Bot
Mode preserves the target's canonical Bot Chat. For GitLab+ICM, a workflow
creates a task handoff while Bot Mode may create a handoff or continue an
existing thread. Those distinctions remain inside the channel adapter and the
handoff specification; consumer code does not call protocol-specific APIs.

## Bot Mode integration

Bot Mode already centralizes agent-originated communication in the
Bot-Chat-only `message_agent` tool. Preserve that tool and its existing session
gate. Do not add another permanent model tool.

The existing call remains valid and resolves to the target's default endpoint:

```json
{
  "target": "researcher",
  "message": "Please review this design."
}
```

An explicit endpoint chooses another strategy:

```json
{
  "target": "gitlab+icm://corp-townhall/researcher",
  "message": "Please review this design and return findings."
}
```

A follow-up may continue an exact handoff:

```json
{
  "target": "gitlab+icm://corp-townhall/researcher",
  "handoff_id": "hnd_01...",
  "message": "Here is the clarification you requested."
}
```

Only an optional `handoff_id` is added to the model-facing contract. Separate
send, reply, status, and cancel tools are unnecessary. The returned
acknowledgement includes the durable handoff ID, selected channel, resolved
target, and safe status.

The current local subprocess, peer-DM, and Desktop-relay paths become
implementation details of Hermes endpoint handling during migration. Friendly
target names continue to work. The tool remains absent from workflows,
ordinary sessions, subagents, and other surfaces where Bot Mode currently
excludes it.

Non-real-time channels cannot depend on the existing short-lived background
waiter. A gateway-owned handoff supervisor observes durable Bot-originated
handoffs and survives Bot-page navigation or Desktop restart. When a result or
failure arrives, it uses the existing background-completion notification shape
to wake the originating Bot Chat. `needs_input`, `failed`, `unknown`, and
unacknowledged cancellation also update the Bot attention projection.

## Desktop Bot experience

The Desktop consumes transport-neutral gateway methods, for example:

```text
handoff.targets.list
handoff.submit
handoff.get
handoff.events
handoff.command
```

This supports communication-channel badges, an agent's default channel, a
"Send via" override, active and completed handoffs, Needs Attention indicators,
and safe links to Hermes runs or GitLab resources. The renderer never embeds a
GitLab client or credentials.

Two initiation paths are valid:

- A user asks the active bot to contact another agent; the bot composes the
  message and invokes `message_agent`.
- A future direct action on an agent card creates a handoff through the gateway
  facade.

Audit attribution must distinguish a bot-authored handoff from an operator
action performed on behalf of a bot. A direct UI action must never be presented
as if the model authored it.

## Workflow integration

Workflow authoring continues to route an existing prompt node through an
endpoint assignment. The workflow coordinator creates a task-style handoff,
stores its `handoff_id` with the node attempt, releases the worker between
observations, and maps the normalized terminal result into ordinary node
output.

Workflow-specific trust, output schemas, timeouts, retries, artifacts, and
Needs Attention remain owned by the workflow subsystem. Transport state,
external resource identifiers, and communication events remain owned by the
shared handoff service.

## GitLab+ICM role

The GitLab channel adapts ICM's human-readable folder and Markdown conventions
into an explicit multi-agent lifecycle. A handoff uses a deterministic branch
and request folder, an issue as its mailbox and discussion thread, commits as
content history and concurrency evidence, and an optional merge request when
repository changes require review.

The workflow or Bot consumer sees the same handoff states regardless of
whether the adapter is polling a Hermes run or a GitLab issue and branch.
GitLab polling is outbound-only and therefore remains viable in environments
where direct peer connections, Outlook, Teams, Slack, or gateway chat are not
permitted.

References:

- [Interpretable Context Methodology](https://github.com/RinDig/Interpretable-Context-Methodology)
- [ICM methodology paper](https://arxiv.org/abs/2603.16021)
- [GitLab Repository Files API](https://docs.gitlab.com/api/repository_files/)
- [GitLab Issues API](https://docs.gitlab.com/api/issues/)
- [GitLab Notes API](https://docs.gitlab.com/api/notes/)
- [GitLab Merge Requests API](https://docs.gitlab.com/api/merge_requests/)
- [GitLab Commits API](https://docs.gitlab.com/api/commits/)

## Shared audit and failure behavior

Every handoff appends normalized, redacted evidence recording:

- Initiating consumer, actor, profile, workflow node or Bot Chat session.
- Logical destination agent and selected endpoint.
- Channel and sealed adapter/configuration identity.
- Stable operation and handoff IDs.
- Admission acknowledgement and external safe references.
- State transitions, questions, replies, and observed actors.
- Request and result content digests.
- Cancellation attempts and acknowledgements.
- Last successful observation, next poll time, and deadline.
- Failure classification and reconciliation decisions.

The evidence can project into both workflow Needs Attention and Bot attention
without duplicating canonical state. A user must be able to determine who sent
work to whom, which strategy carried it, whether it was admitted, who acted on
it, what was returned, why it failed, and which next action is safe.

Actionable states include remote questions or approvals, authentication or
authorization failure, target rejection, ambiguous admission, stalled work,
invalid or tampered results, identity drift, missed deadlines, and cancellation
without acknowledgement.

## Compatibility and rollout

The migration should preserve existing Bot Mode behavior before adding new UI:

1. Introduce the neutral facade, store, channel registry, and normalized
   evidence model.
2. Add the Hermes channel and route workflow Runs through it.
3. Route `message_agent` through the facade while retaining friendly targets,
   canonical Bot Chat behavior, local delivery, peer delivery, and Desktop
   relay compatibility.
4. Add the gateway handoff supervisor and transport-neutral query/action RPCs.
5. Add Bot and workflow activity projections.
6. Close Hermes Runs correlation, idempotency, and durable-retention gaps.
7. Implement GitLab+ICM as a separately installed channel plugin.
8. Add Desktop channel selection and direct handoff actions after the backend
   contract is stable.

The initial implementation should not add automatic cross-channel failover,
message mirroring, a universal capability-policy language, a new core model
tool, or GitLab logic in Desktop or workflow code.

## Superseded conclusions from the original proposal

This addendum supersedes these earlier conclusions:

- The facade is not workflow-owned; it is a neutral Agent Handoff service.
- Hermes Runs is not the permanent abstraction; it is one channel behind the
  facade.
- GitLab+ICM is no longer deferred as an unspecified non-Hermes transport; it
  is the concrete asynchronous channel that justifies the facade.
- Bot Mode is not merely an unrelated conversational path; it is a second
  first-class consumer of the shared lifecycle and evidence service.
- The restriction against a generic transport layer remains valid only for a
  universal bus. It does not prohibit this bounded, concrete handoff-channel
  interface.
