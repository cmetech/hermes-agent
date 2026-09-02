# Remote Workflow Agent Handoff Stage 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to execute this plan one task at a time. Use
> `superpowers:test-driven-development` for every behavior change and
> `superpowers:verification-before-completion` before every commit.

**Goal:** Extend the Stage 1 consumer-neutral handoff facade so a Workflow
prompt node can execute as a durable, authenticated Runs task on a configured
Hermes peer, recover across either peer's restart, and pause for an exact
approval response without changing Bot Chat or `message_agent`.

**Architecture:** Keep `AgentHandoffService`, its profile-local SQLite ledger,
and the Workflow coordinator as the only lifecycle authorities. Add strict peer
endpoint parsing, extract the existing peer registry and Runs HTTP seams, and
add one peer Runs channel selected by a two-way built-in Hermes dispatcher.
Polling by Run ID remains authoritative; events are optional and are not part
of this implementation. Extend the existing Workflow paused-interaction and
Needs Attention projections for approval-backed `handoff_input` only.

**Tech stack:** Python 3.11+, stdlib `urllib`, `sqlite3`, `aiohttp` API server,
pytest through `scripts/run_tests.sh`, existing Workflow JSON/SQLite stores,
TypeScript/Vitest for the desktop public codec.

**Spec:**
[`docs/proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md`](../../proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md)

**Readiness evidence:**
[`docs/assessments/2026-09-02-agent-handoff-stage-2-implementation-readiness.md`](../../assessments/2026-09-02-agent-handoff-stage-2-implementation-readiness.md)

## Execution rules

- Start every task on `base`; literal `main` remains synchronization-only.
- Before the first edit, verify `git branch --show-current` and the accepted
  plan commit. Abort on an unexpected branch or unexplained commits.
- Follow strict red-green-refactor: add the named failing tests, run the exact
  RED command and inspect the expected failure, implement only that task, run
  the GREEN command, then refactor without changing behavior and rerun GREEN.
- Run tests only through `scripts/run_tests.sh` with
  `HERMES_TEST_FILE_RETRIES=0`.
- Stage exactly the task-owned paths listed under each task. Do not stage
  unrelated tracked or untracked files.
- Make one atomic commit per task with the specified subject. Do not combine
  tasks or leave production-only compatibility scaffolding for later stages.
- Keep credentials, authorization headers, registered URLs, raw remote errors,
  prompts, and unbounded results out of public evidence and logs.
- Preserve mechanism and destination binding after any submission attempt.
- Do not modify peer DM, Bot Mode, Desktop chat, `message_agent`, the generic
  plugin/channel surface, or any Stage 3-5 behavior.

## Baseline gate

Before Task 1, collect real tests with retries disabled:

```bash
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

HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  -m integration \
  -k extracted_wheel_registers_workflow_cli_from_a_clean_home -q
```

Planning baseline on `96840791410c4723d3c4e9de7235afdafc723f81`:

```text
299 passed, 0 failed
1 passed, 0 failed, 5 deselected
```

If execution starts from a descendant, rerun both commands and record the new
counts before editing.

## Task 1: Extend the closed handoff contract for peer destinations

**Owns:**

- `hermes_cli/handoff/models.py`
- `hermes_cli/handoff/__init__.py`
- `tests/hermes_cli/handoff/test_models.py`

**Consumes:** Existing `validate_profile_name()`, bounded checkpoint/binding
normalizers, immutable `HandoffSpec` fingerprinting.

**Produces:** Strict `hermes://peer/<peer>/<profile>` parsing and the smallest
closed capability vocabulary needed by Stage 2.

### RED

Add table-driven tests proving:

- `hermes://peer/reviewer/qa` parses to kind `peer`, peer `reviewer`, profile
  `qa`, and round-trips byte-for-byte through `.canonical`;
- local endpoints keep their current shape and fingerprints;
- peer endpoints reject uppercase/invalid peer slugs, controls, percent
  encoding, userinfo, query, fragment, empty or extra path segments, and every
  non-Hermes scheme;
- `approval`, `steering`, and `follow_up` are accepted required capabilities;
- unknown capabilities and secret-shaped binding/checkpoint keys still fail;
- peer binding facts accept only the closed keys `peer`, `profile`,
  `mechanism`, `capabilities`, `origin_sha256`, and `auth_scope_sha256`;
- checkpoint approval facts accept only an exact bounded `approval_request_id`
  and normalized advertised choices; and
- neither digest nor private approval routing appears in public evidence.

Run and confirm failures are caused by the current local-only grammar and
closed validators:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_models.py -q
```

### GREEN

Implement the closed model changes without accepting raw network material:

```python
@dataclass(frozen=True, slots=True)
class HandoffEndpoint:
    canonical: str
    profile: str
    peer: str | None = None

    @property
    def kind(self) -> Literal["local", "peer"]: ...
```

Preserve the current `canonical, profile` constructor shape and derive `kind`
from the optional peer field. Keep parsing explicit by endpoint kind. `peer`
must use the existing lowercase safe peer slug; `profile` must use the existing
profile validator. `__post_init__` must reject inconsistent direct construction.
Do not store or derive a URL in this type.

Extend only the existing binding/checkpoint normalizers. Keep digests as exact
lowercase SHA-256 values, capabilities as a sorted closed tuple, identifiers
bounded by the existing limits, and approval choices limited to the four Runs
values `once`, `session`, `always`, `deny`.

Run GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_models.py \
  tests/hermes_cli/handoff/test_store.py \
  tests/hermes_cli/handoff/test_service.py -q
```

### REFACTOR and commit

Reuse `_safe_identifier`, `_SHA256`, `_freeze`, and existing canonical JSON
fingerprinting. Do not add a URL parser abstraction or a generic endpoint
registry.

```bash
git add hermes_cli/handoff/models.py \
  hermes_cli/handoff/__init__.py \
  tests/hermes_cli/handoff/test_models.py
git diff --cached --check
git commit -m "feat(handoff): define strict peer endpoint contract"
```

## Task 2: Extract registered-peer resolution for non-CLI consumers

**Owns:**

- `hermes_cli/peers.py` (new)
- `hermes_cli/subcommands/peer.py`
- `tests/hermes_cli/test_peer_cmd.py`
- `tests/hermes_cli/test_peers.py` (new)

**Consumes:** `load_config(config_path=...)`, `build_profile_secret_scope()`,
the existing `bot_peers` shape, `_peer_key_env()`, `_base_url()`, and peer CLI
name rules.

**Produces:** One lazy, profile-scoped resolver used by both peer CLI and the
handoff channel. It returns private transport material plus non-secret binding
digests and never mutates global profile state.

### RED

Add tests using two temporary profile homes and conflicting process variables:

- resolve only from the initiating profile's `config.yaml` `bot_peers` entry;
- load `HERMES_PEER_<NAME>_KEY` from that profile's `.env` lazily;
- ignore another profile's key and a conflicting ambient key on the handoff
  path;
- keep the existing peer CLI ambient-env compatibility where its caller
  explicitly requests it;
- reject unknown peers and registered URLs without `http(s)`, hostname, or
  with userinfo/query/fragment;
- retain a valid operator-configured base path and append exactly
  `/p/<profile>`;
- compute deterministic `origin_sha256` and `auth_scope_sha256` without
  exposing the URL or key; and
- reject missing or weak peer credentials before any HTTP request.

Run RED:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/test_peers.py \
  tests/hermes_cli/test_peer_cmd.py -q
```

### GREEN

Move only the reusable registry/name/base-URL logic into `hermes_cli/peers.py`.
The shared result should be immutable and keep all private fields out of repr:

```python
@dataclass(frozen=True, slots=True)
class ResolvedPeer:
    name: str
    profile: str
    profile_base_url: str = field(repr=False)
    origin_sha256: str = field(repr=False)
    auth_scope_sha256: str = field(repr=False)
    key: str = field(repr=False)
```

Provide one resolver with an explicit initiating home and destination profile.
Allow the peer CLI to opt into its current ambient fallback; the handoff path
must not. `cmd_peer()` keeps parsing, printing, DM behavior, and Bot Chat
compatibility and consumes the helper instead of being called by the handoff
service.

Domain-separate the hashes: origin covers the canonical registered base plus
destination profile, while auth scope covers peer name, destination profile,
and key. The raw inputs never leave the resolver.

Run GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/test_peers.py \
  tests/hermes_cli/test_peer_cmd.py \
  tests/gateway/test_peer_dm_hidden_e2e.py -q
```

### REFACTOR and commit

Keep one URL canonicalizer and one peer-key-name helper. Do not introduce a
peer client class, per-profile credential registry, or config migration.

```bash
git add hermes_cli/peers.py \
  hermes_cli/subcommands/peer.py \
  tests/hermes_cli/test_peer_cmd.py \
  tests/hermes_cli/test_peers.py
git diff --cached --check
git commit -m "refactor(peer): expose profile-scoped registry resolution"
```

## Task 3: Share the bounded Runs HTTP rail without changing local behavior

**Owns:**

- `hermes_cli/handoff/runs.py` (new)
- `hermes_cli/handoff/local.py`
- `tests/hermes_cli/handoff/test_local_runs.py`
- `tests/hermes_cli/handoff/test_runs_client.py` (new)

**Consumes:** Existing local `_request_json()`, proxy-free opener, stable
`handoff-<handoff_id>` key, bounded body reader, status map, and terminal result
normalizer; `SafeCredentialRedirectHandler`.

**Produces:** A small internal Runs client usable by local and peer channels.
It owns protocol mechanics only, not peer lookup or handoff lifecycle policy.

### RED

Write direct HTTP fixture tests that prove the extracted rail:

- sends the exact bounded idempotency key and identical canonical JSON on
  retry;
- caps response bytes before JSON decoding and rejects malformed/non-object
  responses with stable errors;
- disables ambient HTTP/HTTPS proxies;
- preserves bearer credentials only on same-origin redirects and strips all
  credential/non-safelisted headers on cross-origin redirects;
- maps Run states exactly, including `waiting_for_approval` to `needs_input`
  and `interrupted` to an indeterminate handoff observation;
- carries bounded Run/session IDs and terminal result but no raw error body;
  and
- leaves all current local Runs and CLI fallback tests unchanged.

Run RED:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_runs_client.py \
  tests/hermes_cli/handoff/test_local_runs.py -q
```

### GREEN

Extract the Runs-only request, admission, reconciliation, status, stop,
approval, and steer primitives into `handoff/runs.py`. Accept a fully resolved
base URL and optional bearer key from the channel; construct the opener once
per request with:

```python
urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    SafeCredentialRedirectHandler(),
)
```

Do not teach this client about the peer registry, Workflow, the handoff store,
or event streams. Local Runs delegates to it and preserves its existing
listener/multiplex binding, CLI fallback, evidence, and status behavior.

Run GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_runs_client.py \
  tests/hermes_cli/handoff/test_local_runs.py \
  tests/hermes_cli/handoff/test_local_cli.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/test_urllib_security.py -q
```

### REFACTOR and commit

Delete the duplicated local protocol helpers only after parity tests pass. Do
not change API-server reservation logic or peer CLI transport.

```bash
git add hermes_cli/handoff/runs.py \
  hermes_cli/handoff/local.py \
  tests/hermes_cli/handoff/test_local_runs.py \
  tests/hermes_cli/handoff/test_runs_client.py
git diff --cached --check
git commit -m "refactor(handoff): share bounded Runs transport"
```

## Task 4: Bind and execute through authenticated peer Runs

**Owns:**

- `hermes_cli/handoff/peer.py` (new)
- `hermes_cli/handoff/service.py`
- `hermes_cli/handoff/__init__.py`
- `tests/hermes_cli/handoff/test_peer.py` (new)
- `tests/hermes_cli/handoff/test_service.py`

**Consumes:** Task 1 endpoint model, Task 2 registry resolver, Task 3 Runs
client, current `AgentHandoffService` fencing and one-operation advance policy.

**Produces:** One peer Runs channel and a fixed two-way local/peer dispatcher.

### RED

Add channel tests with a real authenticated API adapter or narrow HTTP server
for:

- peer endpoint validation fetches authenticated capabilities from the exact
  profile route and refuses unknown peer/profile/auth;
- bind requires durable keyed submission, status, stop, and all capabilities
  declared by the handoff spec;
- events are not required;
- bind stores only peer/profile/mechanism, normalized capabilities, and the two
  digests;
- submission always uses `handoff-<handoff_id>` and stores the returned Run ID;
- a lost submission response repeats the identical keyed request and recovers
  the existing Run;
- duplicate key/same payload returns the same Run while duplicate key/different
  payload fails definitively with `idempotency_key_conflict`;
- status observation persists `session_id` when first advertised and maps every
  terminal/nonterminal Run state;
- registry retarget or credential rotation after bind becomes indeterminate
  before further I/O under the changed scope; and
- service dispatch chooses local only for local endpoints and peer only for
  peer endpoints while keeping one lease and one operation per advance.

Run RED:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_peer.py \
  tests/hermes_cli/handoff/test_service.py -q
```

### GREEN

Implement `PeerHermesChannel` against an explicit initiating profile home. On
every operation, resolve the current registered peer/key lazily and compare
the sealed origin/auth digests. Bind against authenticated capabilities before
submission. Use Runs admission and status only; do not use peer DM or SSE.

Change the service default from one local channel to one internal dispatcher
containing exactly `LocalHermesChannel` and `PeerHermesChannel`. Keep custom
channel injection for tests and existing consumers. This is a fixed built-in
switch, not the deferred generic channel registry.

Extend `EndpointAssessment` with a normalized immutable capability set so
Workflow admission can compare policy requirements before creating a Run.
Bind repeats the capability check and seals the same normalized snapshot so a
race or downgrade cannot weaken an admitted handoff.

Run GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_peer.py \
  tests/hermes_cli/handoff/test_models.py \
  tests/hermes_cli/handoff/test_store.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/handoff/test_local_runs.py \
  tests/hermes_cli/test_peer_cmd.py \
  tests/gateway/test_session_api.py -q
```

### REFACTOR and commit

Keep registry resolution in `peers.py`, wire protocol in `handoff/runs.py`,
peer mapping in `handoff/peer.py`, and lifecycle policy in `service.py`. Remove
any cross-layer helper that duplicates one of those authorities.

```bash
git add hermes_cli/handoff/peer.py \
  hermes_cli/handoff/service.py \
  hermes_cli/handoff/__init__.py \
  tests/hermes_cli/handoff/test_peer.py \
  tests/hermes_cli/handoff/test_service.py
git diff --cached --check
git commit -m "feat(handoff): execute tasks through registered peers"
```

## Task 5: Make peer control commands crash-honest

**Owns:**

- `hermes_cli/handoff/store.py`
- `hermes_cli/handoff/service.py`
- `hermes_cli/handoff/peer.py`
- `tests/hermes_cli/handoff/test_store.py`
- `tests/hermes_cli/handoff/test_service.py`
- `tests/hermes_cli/handoff/test_peer.py`

**Consumes:** Existing `handoff_commands` unique key/content fingerprint/payload
columns and delivery state; peer approval, steer, and stop Runs primitives.

**Produces:** Durable `respond`, `steer`, `message`, `cancel`, and `reconcile`
commands with journal-before-I/O and bounded ambiguity handling. No DDL change.

### RED

Add store/service/channel tests proving:

- command IDs are idempotent for identical kind/payload and conflicting reuse
  raises `HandoffConflict`;
- payloads are closed and bounded: `respond` stores exact request ID and choice,
  `steer`/`message` store bounded input and a safe correlation ID, and no command
  accepts URL/header/credential-shaped fields;
- the store transitions `pending -> attempted -> delivered|indeterminate` with
  CAS protection and does not permit `attempted -> pending`;
- an attempt is durable before the HTTP boundary is entered;
- exact approval response posts the sealed request ID and one advertised choice;
- `steer` and correlated `message` both use `/steer` but retain distinct local
  kinds and evidence;
- a successful response becomes delivered;
- a lost approval response uses read-only status to decide whether the exact
  request is still pending, otherwise remains indeterminate;
- a lost steer/message response remains indeterminate and is never resent;
- stop remains convergent and final phase comes from status; and
- restart with an attempted non-idempotent command performs only read-only
  reconciliation.

Run RED:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_store.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/handoff/test_peer.py -q
```

### GREEN

Extend the existing `_COMMAND_KINDS` and delivery-state transitions. Add only
the command lookup/claim/complete methods needed for the service to select one
pending command under its existing advance lease. Preserve the existing cancel
phase transition and stable reconcile behavior.

The service operation selector processes a pending command before ordinary
observation only when doing so cannot violate durable cancellation. Once a
command is journaled attempted, the peer channel may send it at most once.
`respond`, `steer`, and `message` never return to `pending` after an ambiguous
response. Public evidence records stable IDs/kinds/states and safe codes only,
never input or remote approval text.

Add one internal `deliver_command` advance operation. Under the existing
handoff advance lease it claims exactly one pending command, marks it attempted,
calls `channel.deliver_command(snapshot, command, budget_seconds=...)`, and
folds only that command's delivery state. It does not reuse the handoff-level
submit-attempt columns.

Keep one closed service entry point:

```python
service.command(
    handoff_id,
    kind,
    *,
    command_id,
    actor,
    request_id=None,
    choice=None,
    text=None,
    correlation_id=None,
)
```

Reject irrelevant argument combinations before `record_command()`.

Run GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_store.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/handoff/test_peer.py \
  tests/hermes_cli/handoff/test_local_cli.py -q
```

### REFACTOR and commit

Keep schema version 1. Reuse `safe_payload_json`, `content_fingerprint`, and
`delivery_state`; do not add a second queue/table or remote command key that
the Runs API does not honor.

```bash
git add hermes_cli/handoff/store.py \
  hermes_cli/handoff/service.py \
  hermes_cli/handoff/peer.py \
  tests/hermes_cli/handoff/test_store.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/handoff/test_peer.py
git diff --cached --check
git commit -m "feat(handoff): journal peer control commands"
```

## Task 6: Expose bounded operator handoff controls

**Owns:**

- `hermes_cli/handoff/cli.py`
- `tests/hermes_cli/handoff/test_local_cli.py`

**Consumes:** Task 5 service commands and existing `hermes handoff` command
parsing, exact handoff lookup, and redacted output.

**Produces:** Operator access to exact response, steering, and correlated
follow-up commands without introducing a model tool or peer-DM fallback.

### RED

Add CLI tests proving:

- `hermes handoff respond <id> --request-id <id> --choice <choice>` records the
  exact bounded response;
- `hermes handoff steer <id> <text>` and
  `hermes handoff message <id> --correlation-id <id> <text>` record distinct
  commands;
- missing/extra/oversized arguments, unsupported choices, local CLI fallback,
  wrong phase, and unknown handoff fail before a command is recorded;
- rerunning the same generated command is idempotent while content-conflicting
  reuse fails closed; and
- command/list/evidence output omits payload text, request IDs that are private,
  credentials, peer URLs, and authorization material.

Run RED:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_local_cli.py -q
```

### GREEN

Extend the existing parser/dispatcher only. Convert CLI input to the closed
Task 5 service payloads, generate bounded command IDs with the current pattern,
and print only final local delivery state plus safe failure code. The command
does not poll indefinitely and does not call peer CLI code.

Run GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_local_cli.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/test_peer_cmd.py -q
```

### REFACTOR and commit

Reuse the current handoff CLI error/redaction helpers and keep all command
handlers in the existing module.

```bash
git add hermes_cli/handoff/cli.py \
  tests/hermes_cli/handoff/test_local_cli.py
git diff --cached --check
git commit -m "feat(handoff): add peer task control commands"
```

## Task 7: Admit remote assignments only when policy capabilities hold

**Owns:**

- `plugins/workflow/schema.py`
- `plugins/workflow/language_schema.py`
- `plugins/workflow/trust.py`
- `plugins/workflow/admission.py`
- `plugins/workflow/executors/handoff.py`
- `tests/plugins/workflow/test_schema.py`
- `tests/plugins/workflow/test_language_schema.py`
- `tests/plugins/workflow/test_trust_policy.py`
- `tests/plugins/workflow/test_admission.py`
- `tests/plugins/workflow/test_handoff_executor.py`

**Consumes:** Existing assignment sidecar, Workflow-owner profile derivation,
`AgentHandoffService.validate_endpoint()`, stable semantic key
`<run>:<node>:<generation>`, and structured-output validation.

**Produces:** Peer-aware admission and immutable spec capabilities for
`interaction_policy: pause|deny|auto_cancel`.

### RED

Add table-driven schema/trust tests proving the exact three policies are
accepted and every other value is rejected. Add admission/executor tests for:

- local profile existence checks remain local-only;
- local same-profile self-target still fails, while a peer endpoint with the
  same profile name is not rejected merely because the initiating machine has
  a local profile with that name;
- unknown peer, auth failure, profile route failure, or missing durable Runs
  capability fails admission before Workflow creates a Run;
- `pause` requires approval response plus approval events;
- `auto_cancel` requires stop;
- `deny` requires stop/status and, when approval input is advertised, can send
  exact deny; it never silently falls back to peer DM or local CLI;
- structured output remains an initiator-side requirement, not a peer feature;
- one endpoint capability assessment is memoized within the existing bounded
  admission budget; and
- executor specs add the exact capabilities without changing generation,
  prompt rendering, output validation, or destination credential scope.

Run RED:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_trust_policy.py \
  tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_handoff_executor.py -q
```

### GREEN

Change the closed literal/JSON schema from deny-only to the three accepted
values. Route admission through the consumer-neutral service or its fixed
built-in dispatcher instead of constructing `LocalHermesChannel` directly.
Preserve the two-second bounded probe and semaphore.

Map policies to `HandoffSpec.required_capabilities` in the executor. Do not add
a new lifecycle manager or execute any remote I/O from the worker after the
handoff has entered `waiting_handoff`.

Run GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_trust_policy.py \
  tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_handoff_executor.py \
  tests/plugins/workflow/test_local_handoff_e2e.py -q
```

### REFACTOR and commit

Use one policy-to-capability table in Workflow code. Do not introduce a generic
capability-negotiation framework or advertise unsupported arbitrary questions.

```bash
git add plugins/workflow/schema.py \
  plugins/workflow/language_schema.py \
  plugins/workflow/trust.py \
  plugins/workflow/admission.py \
  plugins/workflow/executors/handoff.py \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_trust_policy.py \
  tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_handoff_executor.py
git diff --cached --check
git commit -m "feat(workflow): negotiate remote handoff policy"
```

## Task 8: Persist approval-backed `handoff_input` and resume exactly once

**Owns:**

- `plugins/workflow/models.py`
- `plugins/workflow/store.py`
- `plugins/workflow/scheduler.py`
- `plugins/workflow/coordinator.py`
- `tests/plugins/workflow/test_store.py`
- `tests/plugins/workflow/test_coordinator.py`
- `tests/plugins/workflow/test_coordinator_multiprocess.py`
- `tests/plugins/workflow/test_approval_races.py`
- `tests/plugins/workflow/test_cancel_node.py`
- `tests/plugins/workflow/test_crash_recovery.py`

**Consumes:** Existing handoff observation CAS/fencing, paused interaction
storage, exact interaction actions, coordinator maintenance fairness, stable
handoff commands, cancellation/deadline paths, and outward-effect
reconciliation.

**Produces:** One durable private remote approval projection, one bounded public
`handoff_input`, and exact response-command handoff between RunStore and the
coordinator.

### RED

Add store/coordinator tests proving:

- `needs_input` under `pause` atomically changes the node
  `waiting_handoff -> paused`, run `running -> paused`, and records one exact
  local interaction ID plus private remote request ID/advertised choices;
- public projection contains only interaction type, local interaction ID, and
  node ID;
- approve records choice `once`; reject records `deny`; stale interaction IDs
  or state versions fail without a handoff command;
- a recorded decision creates one stable pending `respond` command intent,
  restores node `waiting_handoff`, restores run `running`, and wakes handoff
  maintenance;
- coordinator records that exact command in `HandoffStore` before network I/O,
  marks the Workflow intent recorded by fenced CAS, and remains restart-safe at
  each cut;
- `deny` policy records exact deny without pausing when supported and fails
  closed otherwise;
- `auto_cancel` uses the existing cancellation path;
- cancellation wins against a simultaneous local response and no response is
  emitted after durable cancellation;
- completion-versus-cancel preserves the Stage 1 outward-effect
  reconciliation path;
- deadline versus approval uses the existing deterministic deadline cancel;
  and
- multi-process coordinators still perform at most one maintenance action per
  fenced claim with fair scheduling.

Run RED:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_store.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_approval_races.py \
  tests/plugins/workflow/test_cancel_node.py \
  tests/plugins/workflow/test_crash_recovery.py -q
```

### GREEN

Add closed durable record parsing for the private handoff input and response
intent. Implement narrow RunStore CAS methods alongside the existing
`refresh_handoff_wait()`, `request_handoff_cancel()`, and
`mark_handoff_cancel_recorded()` methods; do not reuse generic loop input
records whose semantics differ.

The coordinator keeps ownership: it reads one pending response intent, calls
`service.command(..., "respond", ...)` to journal it in the handoff ledger,
then marks the Workflow intent recorded. Existing handoff advancement performs
the HTTP operation. Preserve cancellation priority over response delivery and
the bounded fair-maintenance loop.

Run GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_store.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_approval_races.py \
  tests/plugins/workflow/test_cancel_node.py \
  tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_deadlines.py \
  tests/plugins/workflow/test_local_handoff_e2e.py -q
```

### REFACTOR and commit

Keep the run-level lifecycle values unchanged. Use the current JSON run state
and handoff ledger; do not add a Workflow table or polling thread.

```bash
git add plugins/workflow/models.py \
  plugins/workflow/store.py \
  plugins/workflow/scheduler.py \
  plugins/workflow/coordinator.py \
  tests/plugins/workflow/test_store.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_approval_races.py \
  tests/plugins/workflow/test_cancel_node.py \
  tests/plugins/workflow/test_crash_recovery.py
git diff --cached --check
git commit -m "feat(workflow): pause remote handoffs for input"
```

## Task 9: Project `handoff_input` through Needs Attention safely

**Owns:**

- `plugins/workflow/actions.py`
- `plugins/workflow/sanitize.py`
- `plugins/workflow/notifications.py`
- `plugins/workflow/dashboard/plugin_api.py`
- `plugins/workflow/gateway_command.py`
- `apps/desktop/src/types/hermes.ts`
- `apps/desktop/src/lib/workflow-public-codec.ts`
- `apps/desktop/src/app/workflows/adapter.ts`
- `tests/plugins/workflow/test_run_queries.py`
- `tests/plugins/workflow/test_notifications.py`
- `tests/plugins/workflow/test_notification_delivery.py`
- `tests/plugins/workflow/test_desktop_api.py`
- `apps/desktop/src/lib/workflow-public-codec.test.ts`
- `apps/desktop/src/app/workflows/adapter.test.ts`

**Consumes:** Existing closed public interaction projection, action validation,
notification outbox, gateway action dispatch, Needs Attention API, and desktop
decoder.

**Produces:** A bounded `handoff_input` projection using the existing
approve/reject/cancel wires. No second dialog or chat surface.

### RED

Add contract tests proving:

- paused `handoff_input` advertises exactly status/events/approve/reject/cancel;
- public sanitizer removes remote request ID, choices, description/command,
  prompt/result, URL, credentials, and unknown fields;
- journal event `handoff_input_required` maps to outbox
  `approval_required`, deduplicates under existing notification identity, and
  survives outbox restart;
- Needs Attention returns the closed interaction and exact local interaction
  ID, and approve/reject require that ID;
- gateway commands route existing approve/reject actions without a new command;
- desktop codec accepts the additive type, rejects extra/private fields, and
  the existing attention adapter maps it to the existing review action; and
- all existing approval, loop input, and reconciliation projections remain
  byte-compatible.

Run RED:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_run_queries.py \
  tests/plugins/workflow/test_notifications.py \
  tests/plugins/workflow/test_notification_delivery.py \
  tests/plugins/workflow/test_desktop_api.py -q

(cd apps/desktop && npm run test:ui -- \
  src/lib/workflow-public-codec.test.ts \
  src/app/workflows/adapter.test.ts)
```

### GREEN

Add `handoff_input` to each existing closed enum/validator in one atomic task.
Project only `type`, `interaction_id`, and `node_id`. Map approve to the Task 8
store's `once` response and reject to `deny`; use the existing action request,
state-version, gateway, and desktop flows.

Do not add a new desktop component: the current review dialog already renders
closed interaction actions and does not need remote command text.

Run GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_run_queries.py \
  tests/plugins/workflow/test_notifications.py \
  tests/plugins/workflow/test_notification_delivery.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_operator_e2e.py -q

(cd apps/desktop && npm run test:ui -- \
  src/lib/workflow-public-codec.test.ts \
  src/app/workflows/adapter.test.ts)

(cd apps/desktop && npm run typecheck)
```

### REFACTOR and commit

Keep public Workflow schema version 1 because this is an additive closed enum
value. Reuse the existing approval notification/action surface.

```bash
git add plugins/workflow/actions.py \
  plugins/workflow/sanitize.py \
  plugins/workflow/notifications.py \
  plugins/workflow/dashboard/plugin_api.py \
  plugins/workflow/gateway_command.py \
  apps/desktop/src/types/hermes.ts \
  apps/desktop/src/lib/workflow-public-codec.ts \
  apps/desktop/src/app/workflows/adapter.ts \
  tests/plugins/workflow/test_run_queries.py \
  tests/plugins/workflow/test_notifications.py \
  tests/plugins/workflow/test_notification_delivery.py \
  tests/plugins/workflow/test_desktop_api.py \
  apps/desktop/src/lib/workflow-public-codec.test.ts \
  apps/desktop/src/app/workflows/adapter.test.ts
git diff --cached --check
git commit -m "feat(workflow): surface remote handoff input"
```

## Task 10: Prove the authenticated peer path at real boundaries

**Owns:**

- `tests/plugins/workflow/test_remote_handoff_e2e.py` (new)
- `tests/hermes_cli/handoff/test_peer_e2e.py` (new)

**Consumes:** Real `APIServerAdapter` route tables, profile middleware,
authentication, `RunIdempotencyStore`, session DB, handoff SQLite ledger,
Workflow `RunStore`, coordinator election/fencing, loopback HTTP, and the
existing Stage 1 inference-boundary fixture pattern.

**Produces:** Required Stage 2 authenticated, restart, failure-injection,
ambiguity, and race evidence.

### Acceptance test construction

Build a fixture with separate temporary initiating and destination Hermes
homes. Start authenticated source and destination API-server adapters on
loopback sockets. Register only the destination in the initiating profile's
`bot_peers`; place only that peer key in the initiating profile's `.env` and
the matching `API_SERVER_KEY` in the destination profile. Use the real HTTP
stack and stores; replace only external model inference with a deterministic
boundary.

Add these named acceptance cases:

1. `test_remote_workflow_handoff_uses_destination_profile_and_credentials`
   proves structured task success, destination provider-secret isolation,
   persisted Run/session references, and returned session visibility only with
   the destination credential.
2. `test_remote_handoff_lost_submit_response_recovers_same_key_once` drops the
   first response only after durable reservation and proves one Run and one
   inference execution.
3. `test_remote_handoff_duplicate_key_replays_and_conflict_rejects` exercises
   same-payload replay plus conflicting-payload 409 through HTTP.
4. `test_remote_handoff_destination_restart_reports_interrupted` stops the
   destination after admission, restarts it on the same home/database, and
   proves the nonterminal Run becomes durable `interrupted` without replacement.
5. `test_remote_handoff_capability_mismatch_fails_before_submit` removes each
   policy-required advertised capability in turn and proves no Run reservation.
6. `test_remote_handoff_redirect_and_proxy_boundaries` proves same-origin
   preservation, cross-origin authorization stripping, and an ambient proxy
   that receives no request.
7. `test_remote_handoff_registry_and_credential_isolation` covers unknown peer,
   wrong profile key, unrelated profile key, retarget after bind, and credential
   rotation after bind without persisting secrets/URLs.
8. `test_remote_handoff_approval_pause_restart_and_response` creates a real
   pending approval, pauses as `handoff_input`, restarts the initiator while
   paused, sends exact `once` and `deny` in separate parameter cases, and proves
   one continuation.
9. `test_remote_handoff_follow_up_steer_and_lost_response` exercises correlated
   `message`, `steer`, and an injected lost response; successful commands are
   delivered and ambiguous ones are never resent.
10. `test_remote_handoff_stop_interrupted_and_cancellation_races` covers
    successful stop, already-interrupted status, cancel-versus-completion,
    cancel-versus-approval-response, and deadline-versus-input.
11. `test_remote_handoff_restart_cuts_are_convergent` parameterizes process
    cuts before bind, after submit journal, after keyed reservation, after Run ID
    persistence, after interaction persistence, and after response-command
    journal.

For lost-response injection, wrap the loopback response writer or close the
client connection after the real handler commits. Do not mock the peer
resolver, authentication, redirect handler, Runs handlers, idempotency store,
handoff service/store, Workflow store, or coordinator.

Run the new acceptance tests:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_peer_e2e.py \
  tests/plugins/workflow/test_remote_handoff_e2e.py -q
```

These tests are expected to pass after Tasks 1-9. If one exposes a production
defect, do not edit that production file under Task 10. Stop, identify the exact
owning seam, amend this plan with a narrow RED/GREEN remediation task and exact
owned paths, then resume this acceptance task.

## Task 10A: Route peer commands through the built-in handoff switch

**Live defect found by Task 10:** The real authenticated approval/restart case
records the exact Workflow response, but `_BuiltinHandoffChannels` delegates
only lifecycle operations. Its missing `deliver_command` delegation raises
before the peer channel can send any HTTP request, so the service conservatively
journals the command as `indeterminate` and the remote Run remains durably
`waiting_for_approval`.

**Owns:**

- `hermes_cli/handoff/service.py`
- `tests/hermes_cli/handoff/test_service.py`

**Consumes:** The existing fixed local/peer switch, peer command journal, and
peer channel's `deliver_command` implementation.

**Produces:** One fixed-switch delegation method. It does not add a registry,
change command semantics, or weaken ambiguity handling.

### RED

Add `test_builtin_channel_switch_delegates_peer_command_delivery`. Use the
existing fixed-switch test seam and prove a peer command reaches the selected
peer channel with the exact snapshot, command, and budget.

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_service.py \
  -k builtin_channel_switch_delegates_peer_command_delivery -q
```

### GREEN and commit

Delegate `deliver_command` through the same endpoint-kind selection already
used by lifecycle calls. Then run the service file and the real approval
acceptance case.

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_service.py -q

HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_remote_handoff_e2e.py \
  -k approval_pause_restart_and_response -q
```

Stage exactly the two owned files and commit:

```bash
git add hermes_cli/handoff/service.py tests/hermes_cli/handoff/test_service.py
git diff --cached --check
git commit -m "fix(handoff): route peer control through builtin channel"
```

## Task 10B: Keep overdue remote-input pauses coordinator-actionable

**Live defect found by Task 10:** A remote handoff can pause a Workflow with a
durable `handoff_input`, but `RunStore.coordinator_candidates()` indexes only
queued, running, and retry-waiting runs. The scheduler already handles paused
handoff deadlines correctly; the coordinator never gives it the overdue run.

**Owns:**

- `plugins/workflow/store.py`
- `plugins/workflow/coordinator.py`
- `tests/plugins/workflow/test_coordinator.py`

**Consumes:** The existing paused `handoff_input` projection, durable handoff
deadline, coordinator keyset scan, and `RunScheduler.advance_due_handoffs()`
deadline/cancel path.

**Produces:** Only overdue remote-input pauses enter the ordinary coordinator
scan. Other paused interactions and remote-input pauses before their deadline
remain dormant. The coordinator advances the overdue handoff without treating
the paused Workflow as ordinary runnable work.

### RED

Add `test_overdue_handoff_input_pause_remains_coordinator_actionable`. Prove
that an ordinary pause and a not-yet-due `handoff_input` are excluded, while an
overdue `handoff_input` is selected and passed only to
`advance_due_handoffs()`.

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_coordinator.py \
  -k overdue_handoff_input_pause_remains_coordinator_actionable -q
```

### GREEN and commit

Extend the existing candidate scan with the narrow paused/deadline predicate
and allow the coordinator to run only handoff maintenance for that paused
candidate. Do not add a second timer, queue, or supervisor.

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_coordinator.py \
  -k 'overdue_handoff_input_pause_remains_coordinator_actionable or handoff' -q

HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_remote_handoff_e2e.py \
  -k stop_interrupted_and_cancellation_races -q
```

Stage exactly the three owned files and commit:

```bash
git add plugins/workflow/store.py \
  plugins/workflow/coordinator.py \
  tests/plugins/workflow/test_coordinator.py
git diff --cached --check
git commit -m "fix(workflow): enforce paused handoff deadlines"
```

### GREEN

Do not introduce test-only production hooks. Run the complete Stage 2 focused
gate:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_models.py \
  tests/hermes_cli/handoff/test_store.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/handoff/test_local_runs.py \
  tests/hermes_cli/handoff/test_local_cli.py \
  tests/hermes_cli/handoff/test_runs_client.py \
  tests/hermes_cli/handoff/test_peer.py \
  tests/hermes_cli/handoff/test_peer_e2e.py \
  tests/hermes_cli/test_peers.py \
  tests/hermes_cli/test_peer_cmd.py \
  tests/hermes_cli/test_urllib_security.py \
  tests/gateway/test_api_server_run_idempotency.py \
  tests/gateway/test_api_server_runs.py \
  tests/gateway/test_api_server_multiplex_secret_scope.py \
  tests/gateway/test_api_server_profile_prefix_misdelivery.py \
  tests/gateway/test_session_api.py \
  tests/gateway/test_peer_dm_hidden_e2e.py \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_trust_policy.py \
  tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_handoff_executor.py \
  tests/plugins/workflow/test_store.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_approval_races.py \
  tests/plugins/workflow/test_cancel_node.py \
  tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_deadlines.py \
  tests/plugins/workflow/test_run_queries.py \
  tests/plugins/workflow/test_notifications.py \
  tests/plugins/workflow/test_notification_delivery.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_local_handoff_e2e.py \
  tests/plugins/workflow/test_remote_handoff_e2e.py -q
```

Run the cancellation-race case seven times with framework file retries still
disabled:

```bash
for run in 1 2 3 4 5 6 7; do
  HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
    tests/plugins/workflow/test_remote_handoff_e2e.py \
    -k stop_interrupted_and_cancellation_races -q || exit 1
done
```

### REFACTOR and commit

Keep fixture utilities inside the two test modules unless both modules truly
need one helper. Do not create a general gateway test framework.

Stage exactly the two new test files:

```bash
git add tests/hermes_cli/handoff/test_peer_e2e.py \
  tests/plugins/workflow/test_remote_handoff_e2e.py
git diff --cached --check
git commit -m "test(handoff): prove authenticated remote workflow path"
```

## Task 11: Verify the installed distribution and record the customization

**Owns:**

- `docs/upstream-customizations/agent-handoff.yaml`

**Consumes:** Completed task commits, installed-wheel test, customization
ledger schema, Stage 1 adversarial gate shape.

**Produces:** Fresh installed-distribution evidence and a precise Stage 2
customization record. This task makes no production change.

### Verification

Build/extract the real wheel through the existing test and verify Workflow
registration from a clean home:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  -m integration \
  -k extracted_wheel_registers_workflow_cli_from_a_clean_home -q
```

Run complete affected-file verification with retries disabled:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff \
  tests/hermes_cli/test_peers.py \
  tests/hermes_cli/test_peer_cmd.py \
  tests/hermes_cli/test_urllib_security.py \
  tests/gateway/test_api_server_run_idempotency.py \
  tests/gateway/test_api_server_runs.py \
  tests/gateway/test_api_server_multiplex_secret_scope.py \
  tests/gateway/test_api_server_profile_prefix_misdelivery.py \
  tests/gateway/test_session_api.py \
  tests/gateway/test_peer_dm_hidden_e2e.py \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_trust_policy.py \
  tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_handoff_executor.py \
  tests/plugins/workflow/test_store.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_approval_races.py \
  tests/plugins/workflow/test_cancel_node.py \
  tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_deadlines.py \
  tests/plugins/workflow/test_run_queries.py \
  tests/plugins/workflow/test_notifications.py \
  tests/plugins/workflow/test_notification_delivery.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_local_handoff_e2e.py \
  tests/plugins/workflow/test_remote_handoff_e2e.py \
  tests/plugins/workflow/test_installed_distribution_e2e.py -q
```

Run desktop closed-contract checks:

```bash
(cd apps/desktop && npm run test:ui -- \
  src/lib/workflow-public-codec.test.ts \
  src/app/workflows/adapter.test.ts)

(cd apps/desktop && npm run typecheck)
```

Run the whole-Workflow suite as a diagnostic, not as permission to absorb
unrelated lifecycle work:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow -q
```

Record exact pass/fail/skip counts. If the known macOS post-test bus error in
`test_scheduling_middleware_e2e.py` recurs unchanged, record it honestly and
compare against the Stage 2 merge base. Stop and fix only if Stage 2 depends on
or worsens it.

Update `agent-handoff.yaml` with the Stage 2 scope, exact commits, owned paths,
verification commands/counts, intentional exclusions, live protocol
clarifications, and upstream conflict surfaces. Validate the YAML using the
existing checker and its contract suite:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/scripts/test_check_upstream_customizations.py -q

.venv/bin/python scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/agent-handoff.yaml \
  --strict
```

```bash
git add docs/upstream-customizations/agent-handoff.yaml
git diff --cached --check
git commit -m "docs(handoff): record remote workflow customization"
```

## Task 11A: Keep installed-wheel handoff registration additive

**Live defect found by Task 11:** The extracted wheel exposes the Stage 2
`respond`, `steer`, and `message` commands correctly, but the installed-package
smoke test freezes the exact Stage 1 argparse choice string and rejects any
additive command. This is a stale change-detector assertion, not a packaging
failure.

**Owns:**

- `tests/plugins/workflow/test_installed_distribution_e2e.py`

**Consumes:** The real extracted-wheel CLI invocation already performed by the
test.

**Produces:** A behavioral registration assertion that requires every Stage 1
and Stage 2 handoff command while permitting future additive commands.

### RED

The Task 11 installed-distribution command must fail on the frozen Stage 1
choice string while showing the three Stage 2 commands in the real wheel help.

### GREEN and commit

Parse the argparse choice set from the real help output and assert that the
required commands are a subset.

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  -m integration \
  -k extracted_wheel_registers_workflow_cli_from_a_clean_home -q

git add tests/plugins/workflow/test_installed_distribution_e2e.py
git diff --cached --check
git commit -m "test(handoff): accept additive installed commands"
```

## Mandatory adversarial review gate

Do not declare Stage 2 complete after Task 11. Request an adversarial review of
the exact Stage 2 commit range. The reviewer must attempt to falsify:

- peer endpoint grammar and configured-registry-only resolution;
- initiating-profile credential isolation and destination-profile auth;
- proxy bypass and cross-origin credential stripping;
- durable keyed admission under lost response, duplicate key, conflicting
  payload, destination restart, registry retarget, and credential rotation;
- mechanism/destination immutability after submission may have occurred;
- polling-only correctness with no event dependency;
- command journal-before-I/O and no replay of ambiguous approval/steer/message;
- exact approval request/choice mapping and bounded `handoff_input` projection;
- Workflow coordinator fencing, fair maintenance, restart cuts, deadlines,
  semantic retry generations, structured output, and outward-effect
  reconciliation;
- cancel-versus-completion and cancel-versus-response races;
- peer DM, Bot Mode, `message_agent`, local fallback, and prompt-cache behavior
  remaining unchanged; and
- absence of credentials, headers, URLs, raw errors, prompts, or unrestricted
  results in databases, evidence, logs, notifications, API projections, and
  desktop state.

Classify every finding as confirmed, rejected with line-level evidence, or
deferred because it is explicitly outside Stage 2. Remediate confirmed Stage 2
findings with fresh RED/GREEN tests and one atomic commit per independent fix.
Rerun Task 10's focused/stress gates and Task 11's installed-distribution gate
after the final remediation commit.

## Completion checklist

- [ ] Tasks 1-9 recorded a genuine RED failure before their production changes.
- [ ] Every task has one atomic commit containing only its owned paths.
- [ ] Endpoint input contains no raw network or credential material.
- [ ] Peer resolution is registry-only and profile-scoped.
- [ ] Runs admission is always keyed and ambiguous submission reuses the same
      body/key.
- [ ] Polling by Run ID is sufficient without events.
- [ ] Non-idempotent command ambiguity never causes blind replay.
- [ ] `handoff_input` is approval-backed only and private routing stays private.
- [ ] Restart, destination restart, cancellation, deadline, and retry invariants
      pass real-boundary tests.
- [ ] Installed-wheel and desktop closed-contract checks pass with exact counts.
- [ ] Whole-Workflow diagnostic outcome and inherited macOS defect are recorded
      without scope expansion.
- [ ] Adversarial review has no unresolved Stage 2 correctness or security
      finding.
- [ ] Checkout is returned to `base`; unrelated worktree files remain untouched.

## Explicitly not implemented by this plan

Bot Mode/Desktop migration, model-visible `message_agent`, durable Bot return
delivery, peer-DM replacement, GitLab+ICM, repository channels, A2A, generic
third-party channel registration, Windows CLI destination locking, relay
retirement, interactive POSIX CLI fallback, per-profile keys inside one peer
entry, arbitrary non-approval questions, SSE-dependent correctness, and every
Stage 3-5 feature remain deferred.
