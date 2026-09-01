# Shared Agent Handoff Facade for Workflows and Bot Mode

**Status:** Accepted implementation baseline; implementation not started

**Date:** 2026-09-01

**Scope:** Hermes local and peer communication, Workflow integration, Bot Mode,
Desktop support, GitLab+ICM asynchronous communication, durability, audit, and
failure recovery

This proposal consolidates the independent Codex and Claude assessments of
agent-to-agent communication. Where they disagree, this document records the
decision that should govern implementation. The source reports remain useful
as evidence and alternatives analyses, but this document supersedes their
architecture recommendations.

## 1. Reader and intended outcome

The primary reader is an engineer planning or implementing agent handoffs in
this Hermes fork. A secondary reader is an operator or product designer who
needs to understand what users will see when communication succeeds, stalls,
or fails.

After reading this proposal, an engineer should be able to:

1. identify the shared boundary that Workflow and Bot Mode must call;
2. implement one local handoff without coupling either consumer to a
   transport;
3. add a new communication channel without modifying Workflow, Bot Mode, or
   the core agent loop;
4. preserve idempotency and avoid duplicate work after uncertain delivery;
5. expose an evidence timeline that explains what happened between agents;
   and
6. extend the design to GitLab+ICM without putting GitLab types or credentials
   in Hermes core.

This is a solution proposal, not an implementation plan or a claim that the
feature already exists.

## 2. Executive decision

Hermes should introduce a small, host-owned **Agent Handoff Service** shared by
Workflow and Bot Mode. The service owns the durable meaning of a handoff. A
channel adapter owns only the protocol required to communicate with a
destination.

```text
Workflow prompt node ─────┐
                         │
Bot Mode message_agent ──┼── Agent Handoff Service
                         │      ├── HandoffStore and event ledger
Desktop Bot actions ─────┘      ├── lifecycle/reconciliation supervisor
                                ├── return delivery
                                └── channel registry
                                     ├── hermes://       built in
                                     └── gitlab+icm://  standalone plugin
```

The central decisions are:

- A workflow remains owned by one profile. Delegating one node does not change
  ownership of the workflow run, its policy, or its final result.
- Local profile-to-profile communication is a primary use case. It is not
  modeled as a remote network call merely because remote Hermes uses the same
  facade.
- The endpoint selects the communication strategy. The same logical agent may
  have several addresses, and several channel implementations may be active at
  once.
- A logical handoff binds to exactly one endpoint and one mechanism before its
  first submission attempt. It never changes channel after admission may have
  occurred.
- Workflow and Bot Mode share persistence, reconciliation, security policy,
  audit events, and adapters. They retain different authoring and return
  experiences.
- The existing Bot-Chat-only `message_agent` tool remains the only model-facing
  Bot send surface. No new core model tool is added.
- The built-in Hermes channel supports local profiles and registered peers.
  GitLab+ICM is implemented as a separately distributed plugin registered
  through one generic host extension point.
- The system promises idempotent local creation and the strongest admission
  guarantee the selected channel supports. It never promises exactly-once
  agent execution.

## 3. Consolidation of the two source proposals

The reports agree on most architectural boundaries. Claude adds valuable
lifecycle precision; Codex better preserves the fork's plugin boundary and a
transport-neutral source of truth. The consolidated decisions are:

| Concern | Consolidated decision |
|---|---|
| Service ownership | A neutral host-owned package, owned by neither Workflow nor Bot Mode. |
| Consumer API | Split durable `create()` from bounded convergent `advance()`; retain `command()`, inspection, and evidence operations. |
| Adapter API | Bind, submit, reconcile, observe, and deliver through a bounded, versioned checkpoint. |
| Local default | Preserve Bot Chat for conversations; prefer loopback Runs for controlled workflow tasks when available; retain a bounded CLI one-shot fallback. |
| Remote Hermes | Use registered peers and the merged v2026.8.31 keyed Runs implementation. Preserve peer DM for conversational compatibility. |
| Workflow authoring | Add assignments to the Hermes companion sidecar; do not add a portable workflow node type. |
| Workflow wait state | Add explicit `waiting_handoff`, release the worker, and wake the coordinator when the handoff changes. |
| Bot integration | Keep `message_agent`, friendly targets, attribution, and Bot Chat gating; add canonical URI targets and an optional `handoff_id`. |
| GitLab packaging | Standalone plugin installed through the existing external-plugin mechanisms, not a bundled in-tree GitLab plugin. |
| GitLab lifecycle | The initiator's HandoffStore is orchestration truth; issue notes are external events; branches and commits own bytes; labels are projections. |
| Audit | One normalized, redacted append-only event timeline, projected into Workflow Needs Attention and Bot attention. |
| Unknown outcomes | Use non-terminal `indeterminate`, require reconciliation, and prohibit blind retry. |
| Cancellation | Record a durable cancel-request fact and use the `cancelling` phase until the destination confirms a terminal outcome. |
| Late Bot results | Persist first. Notify the Bot/UI durably. Automatic agent wake-up is policy-controlled and loop-bounded. |

### 3.1 Feasibility corrections carried into this design

Several details in the source reports must not be copied into implementation:

1. A workflow handoff key uses the stable semantic node attempt generation,
   never a worker claim or lease epoch. Recovery changes lease ownership but
   must continue the same handoff.
2. A CLI-created titled session is not automatically hidden. If a dedicated
   task session is meant to be hidden, the mechanism must explicitly create or
   update it as hidden.
3. Git commit author name and email are caller-controlled metadata. They are
   not proof of which GitLab account completed work.
4. GitLab labels are mutable, and scoped-label exclusivity is not universally
   available. Label drift is a repairable projection error, not orchestration
   truth.
5. A store row owned by the initiating profile cannot serialize two different
   source profiles targeting the same local destination. The existing
   destination-profile turn lock must remain the serialization point and gain
   a Windows implementation.
6. A late handoff result injected as an ordinary Bot Chat user message could
   cause ping-pong between agents. Handoff returns need structural origin,
   delivery identity, and hop-limit metadata.

## 4. Verified merged baseline

### 4.1 Current `base`

The exact upstream v0.21.0 release (`v2026.8.31`) is now an ancestor of
`base`. The real merge commit is `59de9bfea2`, and the post-merge report records
the canonical, Desktop, branding, and customization-ledger verification. The
shared Agent Handoff Service described here does not yet exist.

The merged fork already provides the transport and host pieces the service
should reuse:

- `message_agent` is injected only into a managed profile's canonical Bot Chat
  and is gated again during dispatch. It resolves local profiles, registered
  peers, and Desktop relay teammates.
- Local Bot delivery runs a separate `hermes -p <profile> chat` process into
  the destination's canonical Bot Chat. It uses a query file rather than shell
  interpolation and serializes turns with a destination-profile file lock.
- Peer delivery uses the configured peer registry. It now includes
  `hermes peer dm`, `run`, `status`, and `stop`.
- The Desktop relay waits for a reply file for a bounded period and sends the
  result through the background-process completion path. That path is useful
  for latency but is not a durable multi-process handoff ledger.
- The API server exposes Runs submission, status, events, approval, steer, and
  stop routes. Keyed Runs use the merged SQLite reservation store for durable
  admission and terminal/interrupted status. Live stream and active-agent
  objects remain process-local by design.
- Workflow already has durable runs, worker claims, outward-action recovery,
  reconciliation interactions, notifications, evidence pages, and Needs
  Attention. Those mechanisms should be extended, not duplicated.
- The plugin host already supervises background services for long-lived web
  and gateway processes.

### 4.2 Capabilities inherited from v2026.8.31

The merged release supplies the reliable Hermes-to-Hermes task rail:

- `hermes peer run`, `status`, and `stop`;
- a bounded `Idempotency-Key` on Runs submission;
- a SQLite reservation store with atomic key admission and payload-conflict
  detection;
- durable status for keyed runs, including terminal and interrupted outcomes;
  and
- profile-aware peer routing.

The upstream prerequisite is complete. Implementation must consume and test
the merged code rather than copying the pre-merge source assessments. An
unkeyed or memory-only compatibility Run remains unacceptable for production
Workflow dispatch.

### 4.3 Why existing mechanisms alone are insufficient

Calling `hermes peer` directly from every consumer would leave each caller to
solve idempotency, restart recovery, cancellation, audit, and ambiguous
delivery. Exposing `message_agent` to workflow workers would make a Bot
conversation tool responsible for workflow correctness. Putting GitLab logic
inside Workflow or the Desktop would duplicate the same policy and make the UI
the owner of backend delivery.

The missing component is not another transport. It is one durable semantic
resource above the transports.

## 5. Goals, non-goals, and invariants

### 5.1 Goals

The design must:

- support local, remote peer, and asynchronous repository-mediated handoffs;
- let different workflow nodes use different channels;
- let Bot Mode use the same channels without becoming workflow-aware;
- survive initiator, supervisor, UI, and destination restarts;
- support correlated replies, questions, answers, cancellation, and terminal
  results where the selected channel advertises those capabilities;
- make uncertain outcomes explicit and safe;
- provide an operator-readable timeline across consumers and channels;
- keep secrets and transport-specific types out of durable public envelopes;
  and
- preserve Hermes prompt caching and the narrow core tool surface.

### 5.2 Non-goals

The first implementation does not need:

- exactly-once model or tool execution across machines;
- arbitrary routing graphs or an enterprise message broker;
- automatic migration of an admitted handoff between channels;
- a new portable workflow node type;
- a new globally available model tool;
- group-room orchestration through the facade;
- replacement of the A2A interoperability plugin;
- replacement of GitLab with a generic source-control abstraction; or
- automatic trust in content merely because it arrived through a configured
  channel.

### 5.3 Load-bearing invariants

1. **Persist before effects.** The handoff and its stable key exist locally
   before an adapter performs external I/O.
2. **One key, one specification.** Reusing a key with an equivalent
   specification returns the existing handoff. Reusing it with a different
   specification is a conflict.
3. **One handoff, one bound mechanism.** The channel and mechanism are sealed
   no later than the first submit attempt.
4. **Unknown is not failure.** A timeout or lost response never proves that a
   destination did not admit work.
5. **No blind retry.** An `indeterminate` handoff must reconcile by stable
   identity before another submission can be considered.
6. **Consumers do not own transport truth.** Workflow and Bot Mode store only
   a `handoff_ref` plus their own projection.
7. **Adapters do not own lifecycle policy.** They translate channel facts into
   common observations; the service applies transitions.
8. **Credentials never enter endpoint URIs, specifications, checkpoints, event
   previews, or Git commits.**
9. **Destination identity owns execution.** A local handoff runs under the
   destination profile's home, credentials, tools, memory, approvals, and
   session store.
10. **Returns are idempotent.** Replaying an observation may not create two
    Workflow completions, Bot notifications, or Bot wake turns.
11. **System prompts and tool schemas remain stable for a conversation.** Any
    `message_agent` schema evolution is a one-time protocol epoch change, not a
    per-turn mutation.
12. **Evidence is safe by default.** The event ledger retains identifiers,
    outcomes, hashes, and bounded redacted previews rather than blindly copying
    every prompt, tool result, or credential-bearing error.

## 6. Shared architecture and ownership

### 6.1 Agent Handoff Service

The host-owned service is the only API used by Workflow, Bot Mode, and UI
controllers. Its responsibilities are:

- endpoint validation and policy checks;
- idempotent handoff creation;
- specification fingerprinting;
- channel lookup and mechanism binding;
- lifecycle transitions;
- command idempotency;
- claim leases and fencing for concurrent supervisors;
- retry scheduling and deadline evaluation;
- normalized event recording;
- return delivery; and
- safe inspection and evidence export.

The natural location in this fork is a focused `hermes_cli.handoff` package.
That package is host infrastructure, despite its location; it must not import
Workflow, Desktop, or GitLab modules.

### 6.2 HandoffStore

Each initiating profile owns a SQLite store under its profile-aware Hermes
home. The minimum durable concepts are:

- `handoffs`: current specification, binding, checkpoint, phase, scheduling,
  terminal result, and failure facts;
- `events`: append-only normalized evidence ordered by a per-handoff sequence;
- `commands`: unique semantic commands and their delivery outcomes; and
- `deliveries`: unique return deliveries to Workflow, Bot, or operator
  surfaces.

The store should reuse the project's SQLite WAL/fallback handling, owner-only
file permissions, bounded transaction patterns, and lease/fencing conventions.
It should not become a second general session database.

The database enforces uniqueness on `(key_scope, handoff_key)`. The
`handoff_id` itself is a standard-library UUID v4 generated during the first
insert. A new root-wide `install_id` or identifier dependency is unnecessary
merely to derive deterministic handoff IDs.

### 6.3 Channel registry

The registry maps an endpoint scheme to a factory. Hermes registers the
`hermes` scheme as a built-in host channel. A single generic plugin method,
conceptually `register_handoff_channel(scheme, factory)`, permits an installed
plugin to register `gitlab+icm`.

This extension point is not speculative: both the built-in Hermes channel and
the separately distributed GitLab+ICM channel are concrete consumers. GitLab
libraries, configuration objects, and protocol types do not cross the
registry boundary.

Duplicate scheme registration is an error. Registration occurs during normal
plugin loading and remains stable for the process generation.

### 6.4 Supervisor

Long-lived web and gateway processes run a handoff supervisor through the
existing background-service host. The supervisor repeatedly:

1. claims a bounded batch of due handoffs;
2. calls one bounded `advance()` step per handoff;
3. persists the observation and next due time;
4. enqueues idempotent return deliveries; and
5. releases or renews its fenced claim.

The supervisor does not hold a database transaction across network I/O. A
stale worker may not write after a newer claim epoch has taken ownership.

CLI-only installations do not gain an unowned daemon. They advance through an
explicit `hermes handoff advance` command, a configured cron invocation, or a
foreground Workflow scheduler using the same convergent operation.

### 6.5 Return delivery

A handoff stores a bounded return route created by its initiator:

- Workflow: profile, run, node, and semantic attempt generation;
- Bot: profile, canonical Bot Chat session, initiating tool call, and delivery
  policy; or
- operator: initiating profile and UI inbox/session reference.

Return delivery is a projection, not another copy of transport state. A unique
delivery ID ensures that a supervisor restart cannot complete a node twice or
inject a Bot result twice.

## 7. Facade and adapter contracts

### 7.1 Consumer-facing facade

The following is a semantic contract, not a required final Python signature:

```python
class AgentHandoffService:
    def validate_endpoint(self, endpoint, initiator) -> EndpointAssessment: ...

    def create(self, spec, initiator, *, handoff_key) -> HandoffSnapshot: ...

    def advance(self, handoff_id, *, budget) -> AdvanceResult: ...

    def command(self, handoff_id, command, *, command_id, actor) -> HandoffSnapshot: ...

    def get(self, handoff_id) -> HandoffSnapshot: ...
    def list(self, query) -> list[HandoffSummary]: ...
    def evidence(self, handoff_id, cursor=None) -> EvidencePage: ...
```

`validate_endpoint()` is a side-effect-free preview for workflow admission,
configuration, and UI. `create()` repeats all security-sensitive validation,
binds the endpoint, fingerprints the specification, and inserts `prepared`
atomically before returning. It never performs an external submission before
the row exists.

`advance()` takes only a durable ID and a bounded work budget. It may submit,
reconcile, observe, deliver one command, or finalize one return, but it must
not turn into an unbounded polling loop. Repeated calls converge on the same
state.

`command()` initially supports:

- `message`: send a correlated follow-up;
- `respond`: answer an exact question or approval request;
- `cancel`: request cancellation;
- `reconcile`: force a safe observation pass after an uncertain outcome; and
- `acknowledge`: clear a user-facing attention item without deleting evidence.

Every command has a caller-supplied stable `command_id`. Repeating the same
command returns its existing result. Reusing the ID for different content is a
conflict.

### 7.2 Handoff specification

A task or conversation specification contains only consumer-neutral data:

- mode: `task` or `conversation`;
- rendered prompt or message;
- bounded structured-output schema, if any;
- immutable input artifact descriptors and content hashes;
- deadline;
- required channel capabilities;
- attribution and correlation metadata; and
- content classification needed by local policy.

It never contains channel credentials, raw peer URLs, GitLab project tokens,
profile filesystem paths, or executable adapter objects.

### 7.3 Channel-facing contract

An adapter implements the minimum protocol-specific operations:

```python
class HandoffChannel:
    scheme: str

    def bind(self, endpoint, context) -> BoundEndpoint: ...
    def submit(self, envelope, checkpoint) -> ChannelObservation: ...
    def reconcile(self, envelope, checkpoint) -> ChannelObservation: ...
    def observe(self, binding, checkpoint) -> ChannelObservation: ...
    def deliver(self, binding, command, checkpoint) -> ChannelObservation: ...
```

The adapter checkpoint is bounded, versioned, JSON-serializable, and free of
secrets. It may contain a mechanism name, peer name, profile, remote run ID,
GitLab project and issue IDs, branch name, immutable commit SHA, note cursor,
and protocol version.

An adapter reports facts and confidence, including:

- accepted with an authoritative external reference;
- definitely not accepted;
- outcome indeterminate;
- active or waiting for input;
- terminal result;
- unsupported capability;
- authentication or policy refusal; and
- retryable observation failure.

It does not decide whether Workflow retries a node, whether Bot Mode wakes an
agent, or whether an operator must be notified.

## 8. Common lifecycle

### 8.1 Phases

```text
prepared
   │
   ├── submit accepted ──> submitted ──> active ──────────────┐
   │                                      │                    │
   │                                      └──> needs_input ───┤
   │                                                           │
   ├── outcome uncertain ──> indeterminate ── reconcile ──────┤
   │                                                           │
   └── definitive refusal/failure ─────────────────────────────┤
                                                               v
                                                   succeeded | failed | cancelled

Any non-terminal phase --cancel--> cancelling --observe/reconcile--> terminal
```

The phases mean:

| Phase | Meaning |
|---|---|
| `prepared` | Durable local intent exists; no submit attempt has been recorded. |
| `submitted` | The destination authoritatively admitted the handoff, but execution may not have started. |
| `active` | Destination execution or conversation processing is in progress. |
| `needs_input` | The destination is waiting for a correlated answer or approval. |
| `cancelling` | A durable cancel request exists; the destination has not confirmed a terminal outcome. |
| `indeterminate` | Admission, command delivery, or terminal truth cannot currently be proved. Reconciliation is required. |
| `succeeded` | A result was accepted and passed required integrity/schema checks. |
| `failed` | A definitive terminal failure was observed or local result validation failed. |
| `cancelled` | Destination cancellation was confirmed, or the channel authoritatively proved work never started after cancellation. |

`cancel_requested_at`, requesting actor, and command ID remain durable facts
regardless of the current phase. Cancellation is not represented only by a
transient state.

### 8.2 Transition rules

- A submit attempt is journaled before the adapter call. If the process dies
  after that point, recovery begins with `reconcile()`, not `submit()`.
- `prepared` may move directly to `failed` only for a definitive local
  validation, policy, authentication, or capability refusal that proves no
  external admission occurred.
- `indeterminate` is non-terminal. Time alone does not convert it into failed
  or safe-to-retry.
- A terminal state is immutable except for additional evidence, return
  delivery status, or operator acknowledgement.
- A terminal result from an untrusted channel is provisional until actor,
  correlation, artifact, and structured-output validation succeeds.
- Deadline expiry records a local fact and normally requests cancellation. It
  does not falsely claim the remote execution stopped.
- A workflow retry after a definitive terminal failure creates a new semantic
  attempt generation and therefore a new handoff. Process recovery does not.

### 8.3 Pre-admission fallback

An explicitly configured alternate endpoint may be considered only when bind
or validation fails definitively and the store proves no submit attempt was
recorded. Once any submit attempt exists, automatic cross-channel fallback is
forbidden.

This prevents a lost HTTP response from causing the same work to execute both
through Runs and through GitLab.

## 9. Endpoint, directory, and strategy selection

### 9.1 Canonical endpoint grammar

The initial endpoint forms are deliberately unambiguous:

```text
hermes://local/<profile>
hermes://peer/<peer>/<profile>
gitlab+icm://<townhall>/<inbox>
```

Examples:

```text
hermes://local/security-reviewer
hermes://peer/spark/researcher
hermes://peer/spark/default
gitlab+icm://corp-townhall/security-reviewer
```

The URI selects a configured strategy and logical destination. It is not a
network URL. In particular:

- `hermes://` contains no host, port, API key, bearer token, user information,
  query, or fragment;
- `local` resolves only against profiles under the current Hermes root;
- `peer` resolves only through the configured peer registry;
- `gitlab+icm` resolves the town-hall name through plugin configuration and
  the inbox through that town hall's allowlist; and
- all secrets are resolved lazily from the initiating profile's approved
  credential configuration.

The design rejects an ambiguous `hermes://<name>` shorthand. Friendly names
are useful, but they should be resolved by the consumer's agent directory into
a canonical endpoint before URI validation. This keeps the URI itself honest
about the selected communication strategy.

### 9.2 Agent directory

An agent may advertise more than one endpoint:

```yaml
handoff:
  agents:
    security-reviewer:
      default: hermes://local/security-reviewer
      endpoints:
        - hermes://local/security-reviewer
        - gitlab+icm://corp-townhall/security-reviewer
```

The exact configuration schema should follow existing validated
`config.yaml` conventions. The important contract is:

- a friendly name is presentation and directory data;
- an endpoint is a canonical strategy selection;
- credentials remain outside the directory;
- policy can allow or deny schemes and destinations per initiating profile;
  and
- selecting a default endpoint is not permission to fall back to another
  endpoint after submission.

Workflow sidecars should normally store the canonical endpoint. Bot Mode may
accept friendly roster names for backward compatibility and resolve them using
this order:

1. explicit canonical endpoint URI;
2. configured agent-directory alias and its default endpoint; then
3. existing local, peer, or Desktop-relay roster resolution.

Ambiguity remains a user-visible error that lists canonical choices.

### 9.3 Capabilities

Binding produces an immutable capability snapshot such as:

- durable keyed admission;
- authoritative status;
- correlated conversations;
- structured task result;
- questions and answers;
- approvals;
- cancellation;
- steering;
- artifact input/output;
- maximum message or artifact size; and
- expected observation latency.

A consumer declares only capabilities it actually requires. A workflow with
`interaction_policy: pause` must fail admission when the selected mechanism
cannot carry a question or approval. A warning is insufficient because the
destination would silently deny or hang on required input.

## 10. Built-in Hermes channel

The built-in channel handles both local profiles and registered remote peers.
It may bind one of several existing mechanisms, but the binding is explicit
and durable.

### 10.1 Capability matrix

| Mechanism | Intended use | Keyed admission | Status/control | Durable transcript | Interaction |
|---|---|---:|---:|---:|---:|
| Local Bot Chat CLI turn | Bot conversation | Local facade key; transport receipt may be ambiguous | Bounded process result only | Yes, canonical Bot Chat | Destination one-shot approval policy only |
| Local loopback Runs | Workflow task or controlled conversation | Yes, merged v0.21.0 | Status, events, approval, steer, stop | Yes, returned session | Yes |
| Local dedicated CLI task session | Bounded unattended workflow task | Local facade key; process result may be ambiguous | Bounded process result only | Yes | No interactive pause |
| Peer DM | Existing Bot conversation | Conversation/session semantics | Bounded synchronous reply | Yes, canonical Bot Chat | Limited to the turn |
| Peer Runs | Remote workflow task | Yes, merged v0.21.0 | Status, events, approval, steer, stop | Yes, returned session | Yes |

The adapter records the selected mechanism in its binding. Consumers never
infer capabilities merely from `local` versus `peer`.

### 10.2 Local Bot conversations

Bot-to-bot conversation should initially preserve the proven path:

```text
hermes -p <profile> chat --in ~ -c "Bot Chat"
       --create-if-missing -Q --query-file <file>
```

The adapter reuses the existing query-file construction, attribution rules,
destination-profile turn lock, and canonical Bot Chat session. This preserves
the Bot's identity, memory, tools, approvals, transcript, and existing user
experience.

The facade improves this path without changing its conversational meaning:

- the handoff exists before the subprocess starts;
- the trusted attribution envelope carries the stable handoff marker needed
  to correlate the destination transcript and reply;
- the destination and mechanism are recorded;
- the process receipt, session reference, reply, and failure classification
  become normalized events;
- a lost or timed-out process result becomes `indeterminate`, not a blind
  resend; and
- return delivery survives the initiating Bot's foreground turn.

The destination-profile file lock remains necessary because multiple source
profiles can target the same Bot Chat. Its current POSIX `flock` behavior
should be extended with standard-library Windows file locking. It must not be
replaced by an initiating-profile database lease.

### 10.3 Local workflow tasks

For a workflow task, the preferred local mechanism is loopback Runs when the
target profile is served by the local multiplexed gateway and the initiator
can authenticate to that profile. This provides the
same correlated status, approval, steering, and stop contract used remotely.

The loopback route is profile-specific. The endpoint binds to the destination
profile and resolves the proper profile route and credential; it does not use
an ambient process-wide key. Reachability and required capabilities are
checked at admission.

A bounded CLI one-shot remains a valid fallback for an explicitly
non-interactive task:

- it runs under the destination profile;
- it writes to a dedicated `Handoff: <handoff_id>` session rather than Bot
  Chat;
- it uses the destination's default-deny one-shot approval policy;
- it advertises no pause/approval capability;
- it has a bounded wall time; and
- it records a timeout or lost receipt as `indeterminate`.

Creating a titled session through the current CLI does not make it hidden. If
the product requires task sessions to stay out of ordinary session lists, the
adapter must use a supported session operation to mark them hidden and test
that behavior explicitly.

### 10.4 Why not run the destination agent in process

The facade must not construct a destination profile's `AIAgent` inside the
initiating profile's process. Hermes profile selection affects home paths,
configuration, credentials, plugin state, secret scope, memory, approvals, and
session storage. Several of those facilities are process- or context-scoped.
An in-process shortcut would create a confused identity and leak policy across
profiles.

The workflow's existing plugin agent runner is likewise correct for executing
an ordinary same-profile node, but it is not a cross-profile handoff
mechanism.

### 10.5 Remote Hermes peers

Remote profiles are resolved only through the registered peer configuration.
For task mode, the adapter uses the upstream Runs contract:

1. derive a stable bounded `Idempotency-Key` from the local handoff identity;
2. submit to the peer's profile-specific Runs route;
3. durably store the returned run and session references;
4. observe status by run ID, using events only as an acceleration path;
5. forward exact approvals, steering, follow-up messages, or stop commands
   when supported; and
6. retain the peer's terminal receipt and output as evidence.

The adapter always sends a key. Keyed admission is what lets a lost response
be reconciled without starting another run. If the peer does not advertise
durable keyed Runs, production workflow-task admission fails. An existing Bot
conversation may still use peer DM because that preserves current behavior,
but that weaker rail must be visible in the handoff's capabilities and
evidence.

The synchronous session-chat endpoint is not a transparent workflow fallback.
A lost response has no strong admission receipt, so it may be used only for an
explicit compatibility mode whose limitations are accepted at admission.

### 10.6 Relationship to relay, A2A, and Kanban

- The Desktop relay remains a separate compatibility mechanism until the
  facade reaches the same cross-connection targets and return experience. It
  is not initially exposed as a durable channel because its delivery depends
  on a connected Desktop and temporary relay files.
- The existing A2A plugin remains an interoperability platform. A future
  `a2a://` adapter is possible, but it is not required by this proposal.
- Kanban remains a work-board orchestration feature. It may consume or display
  handoffs later, but it is not the transport abstraction.

## 11. GitLab+ICM town-hall channel

### 11.1 Role and packaging

GitLab+ICM provides a non-real-time communication channel for environments
where direct peer traffic, email, chat gateways, or Desktop relay are not
available or permitted. It uses GitLab's repository history and collaboration
objects to create an inspectable mailbox and work record.

The implementation is a **standalone Hermes plugin repository**, installed
through `~/.hermes/plugins/` or a supported Python entry point. The Hermes core
repository contains only the generic handoff-channel registration seam. The
plugin may reuse protocol ideas or client patterns from existing GitLab work,
but core, Workflow, Bot Mode, and Desktop do not import GitLab types.

The methodology is inspired by ICM's explicit, interpretable folder
organization. ICM itself is not copied as a multi-agent protocol; the plugin
adds correlation, claims, actors, lifecycle events, and recovery rules needed
for concurrent agents.

### 11.2 Town-hall configuration

`gitlab+icm://<townhall>/<inbox>` names configuration, not a raw URL. A town
hall definition supplies:

- an allowlisted GitLab origin and project ID;
- a secret reference resolved at runtime;
- permitted destination inboxes;
- the authenticated service-account identities allowed to claim or complete
  work;
- repository protocol version; and
- polling and retention policy.

Non-secret behavior belongs in `config.yaml`. Credentials are stored through
the existing secret/configuration mechanisms and never embedded in workflow
packages, endpoint URIs, issue bodies, notes, or commits.

### 11.3 Canonical object per concern

GitLab objects have distinct jobs:

| Concern | Canonical object |
|---|---|
| Initiator orchestration phase | Initiating profile's HandoffStore |
| External handoff identity and human inbox | One GitLab issue with deterministic handoff marker |
| Request, context, inputs, and result bytes | Dedicated branch and immutable commit SHAs |
| Claims, questions, answers, progress, completion, and cancellation | Versioned machine-headed issue notes |
| Search, board display, and convenience status | Labels, assignee, and open/closed issue state |
| Proposed product repository changes | Optional merge request |

Labels are never the sole source of lifecycle truth. If labels are missing,
duplicated, or stale but the event protocol is internally consistent, the
plugin repairs the labels. Conflicting valid terminal events or an unverifiable
claim may place the local handoff in `indeterminate`.

### 11.4 Repository structure

Each handoff uses a branch named from its stable ID, for example
`handoffs/<handoff_id>`, and a versioned directory:

```text
.hermes-handoffs/v1/<handoff_id>/
├── request.md
├── request.json
├── context/
│   └── manifest.json
├── inputs/
│   └── ...
└── output/
    ├── result.md
    ├── result.json
    └── manifest.json
```

The machine-readable request contains the protocol version, handoff ID,
semantic generation, mode, inbox, deadline, content hashes, output-schema
hash, and initiator attribution. The Markdown request exists for humans. Large
or sensitive inputs are included only when policy permits and are always
listed by hash and size in a manifest.

Output manifests identify the immutable completion commit and hash every
result artifact. An optional merge request is created only when the task is
supposed to propose changes to the town-hall repository. It is not required
for messaging or ordinary task results.

### 11.5 External admission and reconciliation

GitLab does not provide the same Runs `Idempotency-Key` contract. The plugin
therefore uses a deterministic marker such as `[HF:<handoff_id>]` in the issue
title and machine metadata.

Submission follows this rule:

1. create the branch and request commit;
2. create the issue with the deterministic marker and immutable request SHA;
3. if any create call returns ambiguously, search by the marker and verify the
   referenced branch, path, handoff ID, generation, and hash;
4. create missing objects only when their absence is authoritative; and
5. record all external IDs and SHAs in the adapter checkpoint.

A marker collision with different content is a protocol conflict, not a match.
The plugin never assumes that a timeout means an issue or commit was not
created.

### 11.6 Claims and actors

The destination inbox poller attempts a repository-level claim on the
handoff branch and, after success, posts a machine `claimed` event. The claim
must be designed so two concurrent claim attempts cannot both be accepted
silently; a create-if-absent claim file or an optimistic commit precondition is
preferable to label-only claiming.

The initiator verifies the author ID returned by the GitLab Notes API against
the configured service-account allowlist. Git author name or email is never
used as authentication because commit metadata can be chosen by the caller.

If more than one allowlisted destination produces a plausible claim, or a
claim file and authenticated note disagree, the adapter records the evidence
and enters `indeterminate` instead of choosing a winner silently.

### 11.7 Machine event notes

Each protocol note carries a parseable machine header containing at least:

- protocol version;
- event ID;
- handoff ID and semantic generation;
- event kind;
- referenced request or result commit SHA;
- correlation ID for a question, answer, or command;
- payload hash; and
- originating agent/inbox declaration.

Initial event kinds are:

```text
claimed, started, progress, question, answer,
cancel-requested, cancel-acknowledged,
completed, failed, verification-failed
```

The GitLab API's authenticated note author is the actor evidence. The note body
and repository content remain untrusted input even when the actor is allowed.

GitLab issue queries can use issue `updated_at` to find changed handoffs, but
per-issue notes do not provide an `updated_after` filter. The plugin therefore
stores a per-issue note cursor and fetches notes for changed or due issues.
Polling is bounded, paginated, backoff-aware, and tolerant of repeated events.

### 11.8 Questions, answers, and cancellation

A destination question creates a `question` event with a unique correlation
ID. The facade maps it to `needs_input`. An operator or initiating agent sends
an idempotent `respond` command, which becomes one `answer` event referring to
that exact question.

Cancellation is cooperative:

1. the initiator durably records `cancel_requested_at`;
2. the plugin posts a correlated `cancel-requested` event;
3. the destination stops at a safe boundary and posts
   `cancel-acknowledged`, `completed`, or `failed`; and
4. the facade remains `cancelling` until it observes an authoritative terminal
   event.

Closing an issue or changing a label does not by itself prove execution was
cancelled.

### 11.9 Result acceptance

On a completion event, the initiator:

1. verifies that the event author is an allowed destination identity;
2. checks handoff ID, semantic generation, inbox, and protocol version;
3. fetches files at the exact result commit SHA, never from a mutable branch
   head alone;
4. verifies the manifest, file sizes, and SHA-256 hashes;
5. validates structured output against the specification admitted by the
   initiator; and
6. records the verified result or a stable integrity/schema failure.

Signed commits may later raise the assurance level, but they are not required
to make the first protocol coherent. A signature would supplement, not
replace, authenticated event authors and content hashes.

### 11.10 Inbox ownership

The standalone plugin owns both its outbound adapter and inbound inbox
service, sharing one protocol parser and GitLab client. A receiving profile
explicitly configures the inbox it serves. Only that profile's long-lived host
polls and executes that inbox.

CLI-only destinations can invoke an explicit `hermes handoff advance` or
plugin-provided inbox command from cron. The system does not infer one global
worker merely because several profiles share a GitLab project.

## 12. Workflow integration

### 12.1 Ownership model

The initiating profile continues to own:

- workflow definition and trust decision;
- run and node state;
- input resolution;
- allowed destination policy;
- retry and deadline policy;
- acceptance of the returned output; and
- final workflow completion.

The destination profile or external inbox owns only the delegated execution
and its own transcript, tools, approvals, and evidence. A handoff does not make
the destination a co-owner of the workflow database.

### 12.2 Authoring surface

Routing belongs in the Hermes companion sidecar so the portable workflow graph
remains valid and transport-neutral. An illustrative contract is:

```yaml
# review.hermes.yaml
language_compatibility: archon-2026-07

outward_action_nodes:
  - security-review

assignments:
  security-review:
    endpoint: hermes://local/security-reviewer
    interaction_policy: pause
    deadline: PT4H
    input_artifacts:
      - $research.output
    on_deadline: cancel_and_fail
```

The assigned node remains a normal prompt node. Its prompt, output type, and
output format continue to be declared in the portable workflow. The sidecar
changes where it runs, not what the node means.

Admission rules for the first version are:

- only prompt nodes may be assigned;
- every assigned node must be declared outward so existing uncertain-effect
  recovery rules apply;
- shared or persisted initiator-side agent sessions are not transferred to
  the destination;
- the endpoint must validate under the initiating profile's policy before the
  workflow package is trusted;
- required interaction and artifact capabilities must be present;
- credentials may be referenced by configured secret name but never embedded;
  and
- the assignment appears in the workflow trust/risk summary.

This supports the same workflow running locally without an assignment or
delegating selected prompt nodes through different channels without editing
the graph.

### 12.3 Stable workflow identity

The idempotency scope is the initiating workflow profile. The key contains:

```text
<run_id>:<node_id>:<attempt_generation>
```

`attempt_generation` is a semantic execution attempt created by the workflow
engine. It remains stable across process restarts, worker reclaim, coordinator
leadership changes, and lease-epoch changes.

If a definitive failure is eligible for workflow retry, the engine increments
the semantic attempt generation and creates a new handoff. A recovery pass of
an uncertain attempt reuses the existing key and handoff.

### 12.4 Dispatch and waiting

When the executor claims an assigned prompt node, it:

1. renders the prompt using the ordinary prompt-node path;
2. resolves and hashes declared input artifacts;
3. carries forward the admitted output schema and deadline;
4. calls `create()` with the stable workflow key;
5. calls one bounded `advance()` step; and
6. records `handoff_ref` and returns `waiting_handoff` unless the handoff is
   already terminal.

Completing the executor attempt as `waiting_handoff` releases the workflow
worker claim. The workflow run remains active, but no worker or thread is held
while the other agent works.

`waiting_handoff` is preferable to a generic `waiting` state because it makes
the external dependency visible in projections, diagnostics, and restart
logic. It carries at least the handoff ID, semantic attempt generation,
endpoint display, current phase, next observation time, and deadline.

The ordinary runnable-work stall detector exempts nodes whose only reason for
waiting is a healthy handoff. Handoff-specific deadline and observation-health
rules replace the generic short runnable-stall threshold.

### 12.5 Observation and wake-up

The handoff supervisor records changes and sends an idempotent Workflow return
delivery. That delivery writes a durable coordinator wake for the owning run.
The coordinator then reads the shared handoff snapshot and advances the node
projection.

Correctness never depends on a live event stream or an in-memory callback.
Foreground and CLI-only workflow schedulers can advance due handoffs during
their normal loop. Repeated wakes and repeated observations are harmless.

### 12.6 Input and approval

When a handoff reaches `needs_input`:

- `pause` creates a durable `handoff_input` workflow interaction and a Needs
  Attention item;
- the operator's answer calls `command(respond)` with the exact remote
  interaction ID;
- `deny` sends an explicit denial only if the channel supports it; and
- `auto_cancel` requests cancellation.

If `pause` was required but the bound mechanism has no interaction capability,
workflow admission fails before submission. The system does not silently
convert a required approval into the destination's default one-shot decision.

### 12.7 Terminal mapping

On `succeeded`, the coordinator:

1. verifies result integrity and provenance;
2. parses and validates the result through the existing structured-output
   path;
3. writes an ordinary workflow output descriptor with handoff provenance;
4. records remote usage/cost when the channel provides it; and
5. completes the node normally.

Invalid output is a definitive local terminal failure such as
`handoff_output_invalid`; it does not mutate the remote record to pretend the
agent did not complete.

Remote `failed` and `cancelled` outcomes map to the corresponding node outcome
with a stable `handoff_` failure classification. Retry policy applies only
after that definitive mapping. `indeterminate` pauses through the existing
outward-effect reconciliation experience.

### 12.8 Restart and cancellation

On restart, Workflow discovers `waiting_handoff` from durable node state,
loads the existing handoff, and resumes observation. It does not infer
liveness from a process ID and does not create a new handoff.

Cancelling a workflow run sends one idempotent cancel command to every
non-terminal assigned node. Nodes remain in `waiting_handoff` with cancelling
health until the destination confirms a terminal outcome. If the cancellation
outcome remains indeterminate, the run enters the established reconciliation
path rather than claiming clean cancellation.

## 13. Bot Mode and Desktop integration

### 13.1 Preserve the existing model-facing surface

`message_agent` keeps its name and its containment contract:

- available only in a managed profile's canonical Bot Chat;
- absent from ordinary CLI sessions, workflows, cron agents, subagents, and
  group-room members;
- gated again at execution time;
- attribution applied server-side; and
- tool schema byte-stable for the life of a conversation.

The schema gains only what the shared handoff model requires:

- `target` may be an existing friendly target or a canonical endpoint URI;
- optional `handoff_id` correlates a follow-up, answer, or continuation with an
  existing handoff; and
- the Bot Mode protocol epoch increments once so existing long-lived Bot Chats
  refresh to the new stable schema.

No transport-specific parameter, GitLab issue ID, peer URL, token, polling
interval, or workflow field appears in the model tool.

### 13.2 Sending a new Bot message

For a new send, `message_agent`:

1. verifies its Bot Chat gate;
2. resolves the friendly name or validates the explicit endpoint;
3. adds the trusted attribution envelope;
4. creates `HandoffSpec(mode="conversation")` with the model tool-call ID as
   its stable Bot key;
5. advances once; and
6. returns a short acknowledgement containing destination, state, and
   `handoff_id`.

The Bot key scope includes the initiating profile and session. The provider
tool-call ID is the handoff key within that scope; if a wire does not supply a
stable tool-call ID, the host uses its durable session/turn operation identity
rather than message text or a timestamp.

The tool remains asynchronous. It does not poll inside the model turn. Its
instructions continue to tell the sender to finish the turn and expect a
later result.

### 13.3 Follow-ups and answers

When `handoff_id` is supplied, the service verifies that the initiating Bot is
authorized to act on that handoff. A normal follow-up becomes an idempotent
`message` command. If the handoff has exactly one pending question, an answer
becomes `respond` with the server-resolved correlation ID. Zero or multiple
pending questions are an ambiguity error; the exact-question operator UI is
used instead of adding transport identifiers to the model tool.

The service rejects a mismatched destination, terminal handoff, stale question
ID, or actor that does not own the originating return route. The model never
chooses a raw GitLab note ID or remote Runs approval token.

### 13.4 Fast and late return behavior

The current background completion rail may remain as a fast path for local or
peer conversations that finish promptly. It is not the source of truth.

Every observed reply or terminal result is first stored as a normalized
handoff event and assigned a unique return-delivery ID. The Bot return route
then applies policy:

1. update the Bot/agent attention projection;
2. show the reply in the relevant Bot Chat or inspector;
3. optionally wake the initiating Bot with a structurally marked internal
   handoff-return turn; and
4. record delivery success or failure.

Automatic wake-up is allowed only when the session supports durable wake
delivery and Bot policy permits it. The internal event carries source handoff,
originating agent, hop count, and delivery ID. A Bot may not automatically
acknowledge an internal return by creating an uncorrelated new handoff, and a
maximum automatic-hop limit prevents ping-pong.

If automatic wake is disabled or fails, the reply remains visible and creates
attention. It is never discarded merely because the original 900-second relay
waiter exited.

### 13.5 Bot page and agent directory

The Desktop Bot page should remain a Bot/agent experience, not become a
transport console. It consumes transport-neutral handoff operations:

- list configured agents and their available endpoints;
- choose the agent and, when useful, the endpoint/strategy;
- send a message or task;
- show a channel badge, phase, elapsed time, and Needs Attention state;
- open the normalized timeline;
- follow safe links to Bot Chat, a peer run, or a GitLab issue; and
- answer, cancel, reconcile, or acknowledge when authorized.

The Desktop does not call GitLab or peer endpoints directly. It invokes the
same host service through the existing gateway RPC pattern. The server derives
the authenticated operator actor; a renderer request cannot label itself as a
Bot actor.

Initially, the minimal RPC surface is equivalent to:

```text
handoff.create
handoff.get
handoff.list
handoff.evidence
handoff.command
```

A streaming subscription can be added only if the existing gateway event
stream cannot carry invalidation notices. Polling `get/list` remains a valid
recovery path.

### 13.6 Existing relay migration

The relay is not removed when the facade first lands. `message_agent` may
continue to route a legacy relay-only roster target through the existing path,
while recording the weaker capability and evidence available.

Retirement requires demonstrated parity for cross-connection routing, offline
delivery, attribution, replies, refusal codes, and Desktop presentation. Until
then, legacy relay and shared handoffs coexist behind the Bot resolver rather
than being falsely presented as equivalent durable channels.

## 14. Audit, observability, and user diagnosis

Auditability is part of correctness, not an optional dashboard. When a
handoff stalls or fails, a user must be able to determine:

- who initiated it and on whose behalf;
- which destination and channel were selected;
- whether the destination admitted the work;
- which remote run, session, issue, branch, or commit is involved;
- when the last successful observation occurred;
- whether the system is waiting, retrying observation, cancelling, or
  reconciling;
- what question or approval is blocking progress;
- whether a result failed identity, integrity, or schema validation;
- whether the workflow or Bot received the return; and
- which action is safe next.

### 14.1 Normalized event ledger

Every lifecycle change and material protocol fact appends an event with:

- monotonically increasing local sequence and timestamp;
- handoff ID, key scope, and semantic generation;
- initiating actor and return route type;
- destination identity and canonical endpoint;
- channel scheme, adapter version, and bound mechanism;
- phase before and after;
- stable event/failure code;
- external reference type and identifier;
- request, command, question, or delivery correlation ID;
- bounded redacted summary;
- local payload or artifact digest when useful;
- retry/reconciliation classification; and
- next safe action or next observation time.

Events are append-only. Correcting a projection appends a repair event rather
than rewriting the original observation.

### 14.2 Actor model

Actor kinds are:

- `workflow`;
- `bot`;
- `operator`;
- `operator_on_behalf_of`; and
- `destination_agent`.

The host derives the actor from trusted execution context. A public API or
Desktop renderer may request an operator action but may not mint a Bot,
Workflow, or destination-agent identity. Channel-reported actors include the
assurance source, such as local profile execution, authenticated peer receipt,
or GitLab note author ID.

### 14.3 Failure taxonomy

Stable failure classes allow UI, notifications, tests, and support tooling to
agree without parsing prose:

| Class | Meaning | Default user action |
|---|---|---|
| `endpoint_invalid` | URI, alias, or destination cannot be resolved safely. | Correct configuration. |
| `capability_mismatch` | Selected mechanism cannot satisfy required interaction, control, or artifact behavior. | Select another explicit endpoint/mechanism. |
| `policy_denied` | Initiating profile is not allowed to contact the destination or release the content. | Review policy; do not retry blindly. |
| `authentication_failed` | Peer or town-hall credential was rejected. | Repair credentials, then reconcile. |
| `submission_rejected` | Channel authoritatively rejected admission. | Correct request or start a new semantic attempt. |
| `submission_indeterminate` | Submit may have succeeded but no authoritative receipt is available. | Reconcile; never resend automatically. |
| `destination_busy` | Local turn lock, peer capacity, or inbox claim could not proceed within its bound. | Wait or retry observation according to policy. |
| `needs_input` | A correlated question or approval is waiting. | Answer or deny the exact interaction. |
| `deadline_exceeded` | Local deadline passed; remote stop may still be pending. | Inspect and cancel/reconcile. |
| `remote_failed` | Destination reported definitive execution failure. | Inspect evidence; apply workflow retry policy if eligible. |
| `protocol_violation` | External objects or events do not obey the admitted protocol. | Reconcile or escalate. |
| `identity_unverified` | Claimed actor does not match an allowed destination. | Do not accept result; investigate access. |
| `integrity_failed` | Result SHA, manifest, immutable reference, or correlation check failed. | Do not consume result; investigate tampering or race. |
| `output_invalid` | Verified bytes do not satisfy the admitted output schema. | Correct destination behavior or retry as a new attempt. |
| `supervisor_unhealthy` | Observation service cannot make progress. | Restore the host service; state remains durable. |
| `return_delivery_failed` | Result exists but Workflow/Bot projection could not be delivered. | Retry the idempotent return delivery. |

Provider and network prose is retained only as a bounded redacted detail. It
does not replace the stable class.

### 14.4 Workflow Needs Attention

Workflow projects handoff facts into its existing Needs Attention model.
Initial triggers are:

- `needs_input` or a pending remote approval;
- `indeterminate` admission, command, cancellation, or completion;
- missed deadline;
- terminal remote failure;
- result identity, integrity, or schema failure;
- supervisor/storage degradation that prevents observation; and
- failed return delivery to the workflow coordinator.

Each item displays the exact handoff, node, destination, channel, last known
phase, age, last successful observation, and one or more safe actions. It
links to the normalized evidence timeline and, when policy allows, to the
external run/session/issue.

The UI must distinguish **Retry observation** or **Reconcile** from **Start a
new attempt**. It must never present an ambiguous submit timeout as a harmless
Retry button.

### 14.5 Bot attention

Bot Mode receives a parallel projection rather than borrowing workflow rows.
Attention is raised for:

- late replies not yet opened or delivered;
- remote questions or approvals;
- indeterminate delivery;
- refusal, failure, deadline, or cancellation problems;
- integrity/identity failure; and
- return delivery that could not wake or update Bot Chat.

The Bot page may badge an agent and provide a combined handoff inbox, while the
shared store and ledger remain the source of the displayed facts.

### 14.6 Operator surfaces

The first operator surfaces should be small and consistent:

```text
hermes handoff list
hermes handoff show <id>
hermes handoff evidence <id>
hermes handoff reconcile <id>
hermes handoff respond <id> ...
hermes handoff cancel <id>
hermes handoff advance [<id>]
```

Machine-readable output should expose stable codes and identifiers. Human
output should lead with current state, last proven fact, uncertainty, and the
safe next action. Logs include the handoff ID and external reference so support
can correlate events without searching message content.

### 14.7 Redaction and evidence retention

The audit ledger stores bounded redacted previews. Full prompts, answers, and
results remain in their owning session, workflow artifact store, or external
repository according to policy. Locally stored digests can prove that two
observations referred to the same bytes without displaying them.

Secrets, authorization headers, embedded credentials, environment values, and
raw provider errors pass through existing redaction before persistence or
display. Evidence export omits sensitive payloads and local-only digests unless
the authorized operator explicitly requests the higher-detail form.

Terminal handoffs and events are retained under a bounded `config.yaml`
policy, coordinated with Workflow evidence retention and external GitLab
retention. Pruning a local record never deletes an external issue, branch, or
session unless a separate authorized cleanup policy says so.

## 15. Security and trust boundaries

### 15.1 Endpoint and network safety

An endpoint can select only a configured local profile, registered peer, or
configured town hall. User-supplied endpoint text never becomes an arbitrary
HTTP origin.

Remote adapters reuse Hermes's credentialed URL and redirect protections.
Non-loopback peer and GitLab origins require the transport security configured
by host policy. Redirects may not forward credentials to a different origin.
DNS, proxy, and private-network policy should follow the same trust boundary as
the existing peer and plugin HTTP clients rather than introducing a looser
handoff-specific client.

### 15.2 Authorization

Authorization is evaluated at four different points:

1. **Workflow trust:** may this installed workflow release these inputs to this
   endpoint?
2. **Initiator action:** may this profile, Bot, or operator create or command
   this handoff?
3. **Channel authentication:** will the destination accept the configured peer
   or GitLab credential?
4. **Return acceptance:** is the observed result attributable to the admitted
   destination and generation?

Passing one check does not imply the others. In particular, possession of a
GitLab project token does not make arbitrary issue content trusted agent
output.

Allow/deny settings and behavioral controls belong in `config.yaml`, not new
non-secret environment variables. Credentials remain secrets and use the
existing setup/configuration experience.

### 15.3 Content release and prompt injection

Handoff prompts, GitLab notes, peer replies, and result files are untrusted
content at every boundary. The initiating workflow determines which artifacts
may leave the profile and validates returned structure before downstream use.

The GitLab inbox agent must not interpret arbitrary issue comments as protocol
commands. It processes only correctly versioned, correlated machine events
from allowed actors, while treating their payload as untrusted task input.
Human comments can remain visible for collaboration without automatically
driving the agent.

### 15.4 Destination execution policy

The destination profile owns its model, tools, approval rules, filesystem,
memory, and credentials. The initiator may express required capabilities and a
task deadline, but it cannot silently expand the destination's tool authority.

Workflow trust in the initiator does not authorize destination side effects.
Remote approvals must be surfaced and answered through the destination's
authoritative interaction reference.

### 15.5 False attribution and replay

- Bot attribution is added by trusted server code, not accepted from the
  model's message body.
- Workflow and Bot actors are derived from execution context, not request
  fields.
- GitLab agent actors are verified using authenticated API author identities,
  not commit email.
- Every external event and command carries handoff ID, semantic generation,
  and unique event/command ID.
- Old-generation questions, results, and cancellations are retained as
  evidence but cannot mutate the current handoff.
- Return deliveries may retry internally, but a unique delivery ID makes each
  consumer projection effectively once.

### 15.6 Audit access

The evidence API may reveal agent names, task descriptions, external project
references, and failure details. It uses the same profile/session
authorization boundary as the handoff itself. Safe external links are built by
the server from configured origins and identifiers; the UI does not render
arbitrary event text as a trusted URL.

## 16. Delivery semantics and reconciliation

### 16.1 Honest guarantees

The common guarantee is:

- **Local creation:** effectively once per `(key_scope, handoff_key)` through a
  database uniqueness constraint and specification fingerprint.
- **Submission:** keyed at least once. The same handoff may repeat a protocol
  request only with the same stable external key while reconciling.
- **Remote admission:** effectively once where the remote system supplies an
  authoritative keyed admission contract, such as upstream Runs.
- **Execution:** never claimed to be exactly once. A destination agent may
  crash after side effects, and a GitLab claim protocol can still encounter
  an irreducible conflicting actor or repository race.
- **Observation:** at least once and idempotently folded into one local phase.
- **Return delivery:** at least once internally, effectively once at each
  Workflow/Bot projection through a unique delivery record.
- **Cancellation:** best effort until an authoritative terminal receipt is
  observed.

### 16.2 Channel-specific admission

| Channel/mechanism | Admission evidence | Ambiguous outcome recovery |
|---|---|---|
| Keyed local/peer Runs | Durable key-to-run reservation and returned run ID | Query/re-submit with the same key; payload conflict is terminal configuration error. |
| Local CLI turn | Process start plus destination session and transcript evidence | Inspect the dedicated or Bot Chat session marker; remain indeterminate if execution cannot be proved. |
| Peer DM | Peer session/turn response and canonical Bot Chat evidence | Reconcile conversation/session marker where available; never resend solely on timeout. |
| GitLab+ICM | Verified issue marker, branch, request commit, and event stream | Search deterministic marker and validate content; create only what is authoritatively absent. |

### 16.3 Reconciliation obligations

An `indeterminate` record contains:

- the operation whose outcome is uncertain;
- whether a submit or command attempt began;
- stable external key/marker;
- last authoritative fact;
- adapter-specific reconciliation cursor;
- next permitted observation time; and
- whether operator action is currently required.

Reconciliation is channel-specific but service-controlled. It may conclude:

- admitted and active;
- terminal;
- definitively not admitted;
- conflicting external state; or
- still indeterminate.

Only a definitive not-admitted result can reopen submission of the same
handoff. A new workflow retry is a new semantic handoff, not an escape hatch
from uncertainty.

### 16.4 Failure and restart scenarios

| Scenario | Required behavior |
|---|---|
| Initiator crashes before submit attempt is journaled | Handoff remains `prepared`; supervisor may submit. |
| Initiator crashes after journal but before receipt | Resume in `indeterminate`; reconcile by key/marker. |
| Supervisor crashes while observing | Fenced claim expires; another supervisor repeats observation safely. |
| Destination Hermes restarts during keyed Run | Observe durable `interrupted` or later terminal state; do not create another Run automatically. |
| Workflow coordinator restarts | Rediscover `waiting_handoff`, read shared state, and continue. |
| Bot foreground turn ends before reply | Durable return remains pending; supervisor projects it later. |
| GitLab issue POST times out | Search deterministic marker and verify request SHA before creating anything. |
| GitLab labels conflict | Repair labels from valid events; do not infer lifecycle from labels. |
| Two GitLab agents claim | Verify claim fence and authenticated events; conflicting valid claims become `indeterminate`. |
| Result manifest or actor fails verification | Record stable integrity/identity failure; never pass result downstream. |
| Cancel request times out | Remain `cancelling` or `indeterminate`; observe before repeating with the same command ID. |
| Return delivery crashes after consumer update | Unique delivery ID makes replay a no-op. |

## 17. Compatibility and upstream integration

### 17.1 Prompt caching and tool footprint

The service is host infrastructure, not a globally registered model tool.
Workflow calls it from deterministic executor code. Bot Mode calls it from the
existing session-gated `message_agent` implementation.

The `message_agent` schema changes once and increments the Bot protocol epoch.
After that refresh, the schema and Bot system-prompt section remain byte-stable
for the conversation. Handoff return events enter as new turns or UI
notifications; they never mutate prior messages or rebuild the system prompt.

### 17.2 Existing behavior retained

- Friendly local profile and peer names continue to work in Bot Mode.
- Existing direct CLI commands remain available for humans and older Bot
  prompts.
- Canonical Bot Chat remains the conversation transcript.
- Workflow prompt nodes without assignments execute exactly as before.
- Desktop relay remains until its product behavior has facade parity.
- A2A and Kanban remain independent features.

### 17.3 Merged upstream boundary

Stage 0 merged v2026.8.31 into `base` using the fork's upstream-merge process.
Implementation planning and execution must use the live merged tree and
revalidate:

- Runs idempotency scope, retention, payload fingerprint, and conflict shape;
- durable status behavior across gateway restart;
- peer capability probing and profile routing;
- approval, steer, stop, and interrupted semantics;
- Bot Mode changes that landed upstream; and
- any overlap with the current fork's API server, Desktop relay, plugin host,
  or Workflow customizations.

The facade should wrap the merged Runs implementation, not fork or duplicate
its reservation store.

### 17.4 Smallest required host extension

The core-specific permanent surface is limited to:

- the shared handoff service/store;
- the built-in Hermes channel;
- one generic plugin channel-registration operation;
- one core registration path into the existing background-service host; and
- transport-neutral inspection/action methods for authorized UI consumers.

The exact registration code should reuse the current PluginManager and
BackgroundServiceHost lifecycle rather than creating a second service
supervisor. The generic channel seam should land with the real external
GitLab+ICM plugin work, not as unused scaffolding.

## 18. Staged delivery

Each stage is a working vertical slice. No stage consists only of speculative
interfaces.

### Stage 0 — upstream prerequisite (complete)

- Merged exact v2026.8.31 into `base` as ancestry-preserving commit
  `59de9bfea2`.
- Passed the post-merge canonical, Desktop, customization-ledger, and branded
  verification recorded in the v0.21.0 post-merge report.
- Advanced all customization manifests to the exact v0.21.0 baseline.

### Stage 1 — local Workflow handoff

- Add the minimum service, SQLite store, event ledger, and built-in Hermes
  local adapter.
- Implement canonical endpoint parsing for `hermes://local`.
- Route one assigned prompt node through a local destination profile.
- Add `waiting_handoff`, restart recovery, output validation, cancellation,
  and Workflow Needs Attention.
- Expose CLI `list/show/evidence/reconcile` sufficient to diagnose the slice.
- Exercise both loopback Runs and the bounded non-interactive CLI fallback.

This proves the core semantics against the primary local use case before
adding a second consumer.

### Stage 2 — remote Hermes

- Add `hermes://peer` binding through the merged peer registry and Runs API.
- Implement approval, steering/follow-up, stop, interrupted, and durable status
  mapping.
- Add remote failure injection and restart tests.

### Stage 3 — Bot Mode and Desktop

- Route supported `message_agent` targets through the service while retaining
  legacy resolution and relay compatibility.
- Add URI targets, optional `handoff_id`, and one Bot protocol epoch bump.
- Implement durable Bot return deliveries, attention projection, loop-bounded
  optional wake-up, and transport-neutral Desktop operations.
- Demonstrate recovery when the initiating Bot, gateway, or Desktop restarts
  before the reply.

### Stage 4 — GitLab+ICM standalone plugin

- Build the plugin in a separate repository with the issue/branch/note
  protocol and inbox service.
- Land the generic channel registration seam together with the plugin proof.
- Validate against a real test GitLab project, including ambiguous requests,
  pagination, rate limiting, label drift, conflicting claims, actor checks,
  and immutable result retrieval.
- Add endpoint selection to Workflow and Bot UI without adding GitLab logic to
  either consumer.

### Stage 5 — hardening and retirement decisions

- Tune scheduling and retention from measured behavior.
- Complete Windows destination-lock validation.
- Run security review and operational failure drills.
- Consider relay retirement only after explicit parity tests.
- Consider additional channels such as `a2a://` only after a concrete consumer
  requires them.

## 19. Test and acceptance strategy

### 19.1 Contract and store tests

- endpoint parser rejects credentials, raw hosts, ports, query strings,
  fragments, ambiguity, and unregistered aliases;
- duplicate `create()` with equivalent specification returns one handoff;
- same key with different specification returns a conflict;
- command IDs are idempotent and content-bound;
- every legal lifecycle transition succeeds and every illegal transition is
  rejected;
- a submit-attempt journal forces reconciliation after restart;
- stale supervisor claims cannot write after a newer fence;
- terminal state and result are immutable;
- return delivery replay updates each consumer once; and
- evidence redaction removes credentials and secret-bearing errors.

### 19.2 Real local path tests

Using a temporary Hermes root with two real profiles:

- a Bot message reaches the destination's canonical Bot Chat with correct
  attribution;
- a workflow task executes under the destination profile's credentials,
  tools, memory, home, and approval policy;
- duplicate keyed local Runs start one execution;
- a dedicated CLI task does not pollute Bot Chat;
- a hidden-session assertion is required if hidden task sessions are
  implemented;
- two initiators targeting one profile serialize on the destination lock;
- interaction-required admission rejects the CLI one-shot mechanism;
- a workflow restart resumes the same semantic handoff; and
- Windows and POSIX destination locks have the same bounded-busy behavior.

### 19.3 Real peer path tests

Against two authenticated Hermes gateways:

- profile route and credential selection are destination-specific;
- a lost first submit response followed by the same key returns the same Run;
- a conflicting payload with the same key is rejected;
- status survives the destination gateway restart for the documented
  retention window;
- approval, response, steer, stop, and interrupted outcomes map correctly;
- unsafe redirects do not forward credentials; and
- a returned session can be inspected without granting unrelated profile
  access.

### 19.4 Workflow tests

- assigned and unassigned copies of the same portable workflow both run;
- `waiting_handoff` releases worker capacity and does not trigger the ordinary
  runnable-stall detector;
- handoff changes wake the correct coordinator after restart;
- `needs_input`, `indeterminate`, deadline, remote failure, and integrity
  failure create actionable Needs Attention items;
- semantic retry creates a new handoff, while worker reclaim does not;
- cancellation targets the exact handoff and waits for terminal truth; and
- verified structured output follows the ordinary downstream reference path.

### 19.5 Bot and UI tests

- `message_agent` remains absent outside canonical managed Bot Chat;
- its schema is stable after one protocol epoch refresh;
- friendly names and canonical endpoints resolve predictably;
- a fast reply and supervisor observation do not deliver twice;
- a result received after the waiter, gateway, or Desktop exits remains
  inspectable;
- internal returns cannot cause an automatic Bot ping-pong loop;
- renderer calls cannot forge Bot attribution; and
- UI actions call the shared service and never contact GitLab directly.

### 19.6 GitLab acceptance tests

Mocks may cover parsing and local error mapping, but release acceptance uses a
real disposable GitLab project:

- issue-create and commit-create timeouts reconcile by deterministic marker;
- two inbox pollers cannot silently own the same claim;
- unauthorized and malformed notes are ignored and recorded safely;
- labels can be removed or duplicated without losing lifecycle truth;
- note pagination and per-issue cursors survive restart;
- question/answer and cancellation correlation is idempotent;
- result files are fetched at immutable SHA and every manifest hash is checked;
- forged commit author email does not pass actor verification;
- conflicting terminal events become `indeterminate`; and
- credentials and sensitive content do not appear in logs or exported
  evidence.

### 19.7 User-visible acceptance criteria

The feature is ready only when a user can:

1. assign a workflow prompt node to a local profile and see the workflow
   resume with its validated output;
2. send from the Bot page or `message_agent` through a selected endpoint
   without understanding its transport commands;
3. use local Hermes and GitLab+ICM channels in the same installation and even
   in the same workflow;
4. restart the initiator while an agent works and receive the eventual result;
5. answer a remote question or cancel the exact handoff;
6. open one timeline that explains admission, execution, communication,
   validation, and return delivery; and
7. distinguish a safe retry from an outcome that must be reconciled.

## 20. Resolved decisions and remaining risks

### 20.1 Resolved by this consolidation

| Question | Decision |
|---|---|
| Does handoff identity need a new stable installation ID? | No. Use a random globally unique handoff ID plus database uniqueness on the initiator scope and stable consumer key. |
| What happens when a workflow requires interaction but a mechanism cannot pause? | Admission fails. The user selects a capable endpoint or changes the declared policy. |
| Who owns GitLab inbox polling? | The same standalone plugin owns outbound and inbox protocol code; an explicitly configured destination profile owns each inbox. |
| Is `hermes://<name>` allowed to guess local versus peer? | No. Friendly names resolve to canonical URIs before binding. |
| How is local Windows serialization handled? | Extend the existing destination-profile file lock using the standard Windows locking facility. |
| Are GitLab labels authoritative? | No. They are repairable human projections. |
| Does a Git commit email prove the completing agent? | No. Use authenticated event-author identity plus immutable content verification. |
| Are task sessions automatically hidden? | No. Hidden state requires an explicit supported operation and test. |
| Does every late Bot reply automatically run the initiating Bot? | No. Persist and notify first; automatic wake is policy-controlled and loop-bounded. |

### 20.2 Remaining implementation risks

- The upstream merge may alter current fork seams around API server, peer,
  Bot Mode, or Desktop relay and must be resolved before detailed planning.
- Cross-platform file locking needs real Windows validation, not only a mocked
  import path.
- Local loopback Runs require a clear, secure profile-specific credential and
  multiplex setup experience.
- GitLab Free-tier features and self-managed versions vary; the plugin must
  capability-probe behavior it depends on and degrade visibly.
- GitLab cannot provide exactly-once agent execution. The claim and result
  protocol minimizes and exposes conflicts but cannot erase distributed-system
  ambiguity.
- Automatic Bot wake-up can still create surprising agent work unless its
  origin, hop limit, policy, and UI are carefully tested.
- Handoff evidence may contain sensitive business context even after secret
  redaction; retention and access need a security review.
- Very large artifacts need a demonstrated transfer path and limits before
  the common envelope is expanded.

These are implementation and validation risks, not reasons to introduce a
larger broker or a more generic protocol now.

## 21. Final recommendation

Implement a durable convergent Agent Handoff Service as the narrow waist
between two current consumers—Workflow and Bot Mode—and two concrete channel
families—Hermes and GitLab+ICM.

Start with the primary local use case and one real workflow vertical slice.
Use the upstream keyed Runs contract for controlled local and remote tasks,
while preserving canonical Bot Chat for conversations. Route `message_agent`
through the same service without broadening its session gate or adding a core
model tool. Deliver GitLab+ICM as a standalone plugin whose issue, branch,
commit, and note protocol is visible to humans but normalized behind the same
facade.

Treat audit and failure diagnosis as required product behavior. A handoff is
not robust merely because a message was sent; it is robust when the system can
prove what it knows, admit what it does not know, avoid unsafe duplicate work,
and give the user a safe next action.

## 22. Source and evidence index

### 22.1 Local design sources

- `docs/proposals/2026-09-01-workflow-agent-to-agent-integration-codex.md`
  — original Workflow assessment and shared-facade addendum.
- `docs/proposals/2026-09-01-workflow-agent-to-agent-integration-claude.md`
  — independent original Workflow assessment.
- `docs/proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-claude.md`
  — independent facade, Bot Mode, local, Runs, and GitLab+ICM assessment.
- `tools/bot_mode_dm.py` and `tools/bot_relay.py` — current Bot tool,
  resolution, local transport, relay waiter, and destination turn lock.
- `hermes_cli/subcommands/peer.py` — current peer registry and commands.
- `gateway/platforms/api_server.py` — current Runs, session, profile route, and
  capability surfaces.
- `hermes_cli/plugin_services.py` and `hermes_cli/plugins.py` — existing
  background-service host and plugin registration surface.
- `plugins/workflow/` — durable workflow store, coordinator, executor,
  outward-action reconciliation, notification, evidence, and Needs Attention
  machinery.
- `plugins/platforms/a2a/` — existing but separate A2A interoperability
  platform.

### 22.2 Upstream Hermes sources

- [Hermes Agent v0.21.0 / v2026.8.31 release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.8.31)
- [Durable Runs idempotency store](https://github.com/NousResearch/hermes-agent/blob/v2026.8.31/gateway/platforms/api_server_run_idempotency.py)
- [Runs lifecycle handlers](https://github.com/NousResearch/hermes-agent/blob/v2026.8.31/gateway/platforms/api_server_runs.py)
- [Peer run/status/stop commands](https://github.com/NousResearch/hermes-agent/blob/v2026.8.31/hermes_cli/subcommands/peer.py)

### 22.3 GitLab and ICM sources

- [GitLab Issues API](https://docs.gitlab.com/api/issues/)
- [GitLab Notes API](https://docs.gitlab.com/api/notes/)
- [GitLab Repository Files API](https://docs.gitlab.com/api/repository_files/)
- [GitLab Commits API](https://docs.gitlab.com/api/commits/)
- [GitLab Merge Requests API](https://docs.gitlab.com/api/merge_requests/)
- [GitLab labels documentation](https://docs.gitlab.com/user/project/labels/)
- [Interpretable Context Methodology repository](https://github.com/RinDig/Interpretable-Context-Methodology)
- [ICM paper](https://arxiv.org/abs/2603.16021)
