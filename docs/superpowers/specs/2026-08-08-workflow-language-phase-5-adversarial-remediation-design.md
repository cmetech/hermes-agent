# Workflow Language Phase 5: Adversarial Remediation Design

**Status:** Approved architecture; implementation is not yet authorized

**Date:** 2026-08-08

**Branch:** `feat/workflow-language-phase-5-provider-portability`

**Product-code baseline:** `1373c306061d2f4a2cf1dd313df16f6453fa1939`

**Evidence HEAD before this document:** `fe9c712fa57466de33e8b44cb5f12b8f23947dac`

## Purpose

This design resolves the four release-blocking findings in
`docs/reviews/2026-08-07-workflow-language-phase-5-adversarial-review-fable.md`
without weakening the approved Phase 5 provider-portability contract or any
Phase 1–4 compatibility, sealing, recovery, redaction, prompt-cache, or public
wire guarantee.

The remediation is deliberately not four local conditionals. It restores
correct authority boundaries:

- a node-bound execution identity remains distinct from cross-node shared-
  context compatibility;
- structured repair becomes an explicit narrowed child of the sealed primary
  request;
- canonical provider names and provider aliases share one deterministic token
  namespace; and
- the actual normalized provider endpoint becomes part of the pre-transport
  runtime identity.

No Phase 5 implementation change begins until the executable remediation plan
is separately approved.

## Verified findings

All four findings were independently rechecked against exact candidate
`1373c306...` by the primary reviewer and two fresh high-reasoning Sol
reviewers. Existing focused suites remained green, confirming coverage gaps
rather than already-detected failures.

| Finding | Verdict | Root cause | Severity |
| --- | --- | --- | --- |
| F-1 | Confirmed and broader than reported | Cross-node sharing compares node-bound identities, while the scheduler also omits Phase 5 predecessor identities entirely | HIGH |
| F-2 | Confirmed | Structured repair omits sealed runtime identity and provider-option transport, so it live-resolves before transport | HIGH |
| F-3 | Confirmed | Canonical collision precedence does not govern aliases, while lookup resolves aliases before canonical names | MEDIUM |
| F-4 | Confirmed | Runtime identity binds endpoint trust class but not the normalized endpoint | MEDIUM |

The current BLOCK verdict remains correct. Every finding is integration-
blocking even where its standalone severity is MEDIUM because each contradicts
the machine-authoritative provider or cache contract.

## Baseline invariants

The remediation preserves all approved Phase 5 and inherited guarantees:

1. New Archon admissions use normalizer v5; legacy remains v2; recorded v1-v4
   workflows and snapshots retain their exact recorded meaning.
2. Snapshot format remains 2. The provider-authority member remains part of the
   authenticated full closure.
3. Provider/model/options resolve once from one immutable config snapshot.
   Runtime reloads credentials only and fails before side effects on
   credential-free drift.
4. Unsupported Archon behavior blocks before trust mutation, run creation,
   extension startup, agent construction, or provider transport.
5. The system prompt and complete model-visible prefix remain byte-stable for
   the conversation. Identity changes select fresh context.
6. Structured repair, fallback, retry, approval, and inline agents cannot mint
   provider attempts, budget, time, resource, workdir, cancellation, sandbox,
   or route authority.
7. `allowed_tools: []`, current-turn skill injection, bounded MCP teardown,
   backend-authored projections, and closed redaction remain exact.
8. No core model tool, synthetic conversation message, telemetry, new OS
   sandbox, unrestricted delegation, or user-facing nonsecret `HERMES_*`
   variable is added.
9. REST mutation URLs and old-client action vocabulary do not change.
10. Every generic upstream-owned change is invariant-tested and recorded in
    `docs/upstream-customizations/workflow-orchestration.yaml` without
    advancing `last_verified_upstream`.

## Version and activation boundary

Normalizer selection remains unchanged: current `archon-2026-07` is v5 and
legacy is v2. No normalizer v6 is needed because the authored language meaning
does not change; this remediation corrects execution and sealed authority for
the already-approved v5 meaning.

Snapshot format remains 2. The internal provider-authority schema advances
from version 1 to version 2 because a v5 route gains an exact endpoint identity.
The resolver/identity version advances with it. Version-2 authority is required
for all new v5 admissions after remediation.

V1-v4 snapshots never read the new field and remain byte-compatible. The
unmerged candidate's experimental v5 schema-1 authorities are not silently
upgraded or completed from live config. They fail closed with a stable
provider-authority version/integrity diagnostic and require re-admission. This
is acceptable because Phase 5 has not been integrated, pushed, or released;
inferring an endpoint from current config would recreate F-4.

Activation remains atomic. The remediation commit that advances the provider-
authority schema cannot land independently of its reader, execution identity,
installed-distribution tests, and historical snapshot gates.

## Remediation 1: Restore truthful v5 shared context

### Root cause

`phase5_node_intended_authority_digest()` correctly creates a node-bound
execution identity, but `AgentNodeExecutor` incorrectly uses equality of that
identity as the cross-node compatibility predicate. Distinct nodes differ in
explicit `node_id`, route `route_id`/`node_id`, and obligation `path`/`route_id`,
even when their cache-affecting semantics are otherwise identical.

There is an independent durable-handoff failure. Successful attempts retain
`intended_authority_digest` and `model_visible_prefix_digest` in attempt
metadata, but `RunScheduler._predecessor_results()` reconstructs only legacy
`session_id` and `cache_fingerprint`. Real v5 shared runs therefore receive no
predecessor Phase 5 identities. The existing executor test injects identical
fake digests and bypasses both failures.

### Separate identities

The node-bound `intended_authority_digest` remains unchanged and continues to
bind:

- one logical node's sealed route and obligations;
- execution evidence;
- persistent same-node session records;
- retry and recovery identity; and
- the node's final `phase5_session_cache_fingerprint()`.

A new `shared_context_compatibility_digest` answers a different question:
whether two distinct nodes are permitted to reuse the same conversation
prefix before the worker verifies the exact rendered bytes.

The two digests must never be substituted for each other.

### Shared-context compatibility material

The new digest is domain-separated, versioned canonical JSON derived only from
authenticated admission and snapshot material. It contains:

- the complete sealed-closure digest;
- a semantic projection of the concrete primary route, including requested-
  reference digest, provider selector, effective provider, model, API mode,
  route fingerprint, endpoint digest, registration provenance, supported
  provider options, config scope, and trust class;
- the sorted capability-decision projections for that primary route, excluding
  only structural location fields (`path` and `route_id`);
- the structured-output strategy, adapter version, and schema fingerprint;
- exact normalized allowed and denied tool policy;
- hook semantic obligations and adapter version;
- MCP definition/closure/tool-name and import-policy identity;
- skill resource bindings;
- inline-agent definitions, resolutions, skills, tools, and adapter identity;
- initial system-prompt configuration identity;
- fallback route and strategy identity;
- budget and sandbox decisions plus adapter versions; and
- every other code-owned value documented as cache-fingerprint-changing.

It excludes only facts that identify graph location or the new user turn rather
than the reusable prefix contract:

- logical node ID, route ID, inline source path, source index, and source line;
- dependency names and graph position;
- the node prompt/value that becomes the next user turn;
- `context` selection itself; and
- retry, timeout, persistence, and scheduling controls that do not alter the
  provider route, extensions, prompt prefix, or model-visible tool contract.

The implementation must build this projection from normalized/sealed objects,
not from a second ad hoc reading of YAML or live resource paths. Prompt, skill,
hook, MCP, inline-agent, or command bodies do not enter public evidence; only
the final digest may be retained.

### Durable data flow

For each v5 AI node:

```text
sealed package + provider authority + closure identity
  -> node-bound intended_authority_digest
  -> cross-node shared_context_compatibility_digest
  -> NodeExecutionContext
  -> worker computes exact model_visible_prefix_digest
  -> successful attempt stores all three digests in authenticated metadata
  -> scheduler reconstructs predecessor identity from the winning attempt
```

The new digest is retained in successful attempt metadata, not promoted into a
new public top-level node authority. `RunScheduler._predecessor_results()` reads
the winning completed attempt's authenticated metadata and validates lowercase
SHA-256 shape before handing it to the executor. It does not trust an arbitrary
client projection or reconstruct the value from current package/config state.

### Sharing predicate

Node B may request Node A's same-run session only when all of the following are
true:

1. B has exactly one completed predecessor, preserving existing semantics.
2. A supplies a nonempty session ID.
3. A's shared-context compatibility digest equals B's independently derived
   compatibility digest.
4. A's cache fingerprint exactly recomputes from A's own node-bound intended
   digest and A's own model-visible prefix digest.
5. The worker for B computes a model-visible prefix digest equal to A's prefix
   before it reads session history or starts provider transport.

B still sends its own node-bound intended digest to the worker. A's prefix
digest is only the expected prefix for the shared session. On success B records
its own intended digest, its verified prefix, and its own cache fingerprint.

Any missing, malformed, stale, or mismatched component fails with the existing
bounded `context_incompatible`, `cache_fingerprint_changed`, or missing-session
contract and zero provider attempts where observable.

### Compatibility and recovery

V1-v4 continue through their existing fingerprint paths. Same-node persistent
session recovery retains the node-bound intended/prefix pair and is not changed
to use the shared-context digest. Crash/restart reconstruction must produce the
same predecessor handoff from authenticated attempt metadata without replaying
the provider call or consulting live config.

## Remediation 2: Seal the structured-repair envelope

### Root cause

The structured-repair builder predates Phase 5. It deliberately strips tools,
hooks, MCP, skills, inline agents, fallback, and system-prompt overrides, but it
also unintentionally strips the Phase 5 runtime identity and exact provider-
option transport. The worker consequently skips the pre-transport drift check,
live-resolves a route, and constructs `AIAgent` with default reasoning/options.

Post-call structured evidence validation is too late: it cannot undo transport
to a non-admitted endpoint or restore an ignored option.

### Explicit repair envelope

One code-owned `build_phase5_structured_repair_request()`-equivalent builder
creates the repair request from the already-validated primary request and the
sealed structured-output decision. It applies only when the request carries a
v5 intended authority and exact runtime identity. Legacy repair construction
remains unchanged.

The v5 repair preserves:

- provider selector, effective provider expectation, model, API mode,
  registration provenance, trust class, and endpoint digest;
- the parent node's intended authority digest;
- exact `reasoning_config` and `request_overrides` produced by the sealed
  provider-option encoder;
- structured strategy, adapter version, schema fingerprint, and canonical
  schema;
- shared provider-attempt and authoritative-cost authorities;
- remaining absolute wall, idle, provider, and resource limits;
- exact workdir, parent cancellation, parent-death/process-tree authority; and
- accepted provider-native sandbox policy when one is eventually supported.

The v5 repair deliberately narrows:

- `context_mode` to `fresh` and `session_id` to null;
- expected shared-prefix identity to null;
- tools, toolsets, hooks, MCP, skills, inline agents, and delegation to empty;
- fallback to null;
- approval/action identity to null; and
- node/system-prompt overrides to null.

Because MCP is deliberately absent, the repair does not mechanically copy
`expected_mcp_runtime_identity_digest`. The generic provider runtime identity,
including endpoint digest, is still mandatory and is checked before agent
construction or transport.

Before launching repair, the builder verifies that the structured-output
decision's effective provider, model, API mode, strategy, adapter, and schema
still agree with the primary sealed request. Any contradiction returns a
terminal zero-new-attempt capability/integrity failure. Repair never receives a
special live-resolution exception.

The repair remains one fresh model iteration, action-free, extension-free,
bounded, and nonrecursive. No repair session is persisted.

## Remediation 3: Unify canonical and alias registration precedence

### Root cause

Provider registrations and aliases occupy one lookup-token namespace, but only
canonical-name collisions use loader-authored origin precedence and bounded
collision diagnostics. Alias assignment is unconditional and lookup consults
aliases before canonical registrations.

This allows a user profile alias to shadow a bundled canonical provider, or an
old bundled alias to intercept a later user canonical registration, without a
diagnostic. Privileged billing/sandbox facts remain protected by origin/trust
checks, so this is not a native-authority forgery, but provider selection and
provenance become nondeterministic.

### Token policy

The registry applies one explicit policy:

1. A canonical provider name always outranks an alias token. An alias cannot
   shadow an existing canonical name at any origin precedence.
2. Registering a new canonical name displaces an existing alias claim for that
   token and records a bounded diagnostic.
3. Two canonical registrations retain the existing origin precedence:
   `bundled < legacy_compatible < user_plugin`; equal precedence preserves
   last-writer compatibility with a diagnostic.
4. Two alias claims use the same origin precedence; equal precedence preserves
   last-writer compatibility with a diagnostic.
5. A user plugin that intentionally replaces a bundled provider continues to
   register the same canonical name. An alias is not an override mechanism.

Alias claims retain enough loader-authored provenance to compare origin ranks.
They do not accept profile-authored provenance. Lookup checks canonical names
first, then the winning alias claim. List/dedup behavior remains based on
canonical registrations.

Stable bounded diagnostics distinguish:

- alias rejected because a canonical name owns the token;
- canonical registration displaced an alias;
- lower-precedence alias ignored;
- higher-precedence alias replaced; and
- same-precedence alias replaced.

Diagnostics remain path-free, credential-free, capped by the existing bound,
and do not grant capability. Registry/list caches are invalidated on every
winning token change.

## Remediation 4: Bind the normalized endpoint into runtime identity

### Root cause

Portable model resolution already hashes configured `base_url` inside its
composite route fingerprint, but execution cannot independently recheck that
component. The sealed and live runtime identities compare only provider,
model, API mode, endpoint trust class, and registration provenance. Two
different endpoints in the same broad trust class therefore compare equal.

### Credential-free endpoint identity

The existing credential-free route normalization becomes the single source for
endpoint identity. It:

- accepts only supported HTTP(S) route shapes;
- normalizes scheme, host casing, default ports, path, structural query keys,
  and trailing separators;
- removes URL userinfo and fragments;
- retains approved nonsecret structural query values;
- replaces credential-query values with key-only presence; and
- fails closed on unclassified query parameters rather than hashing raw
  credentials or ambiguous route material.

A domain-separated SHA-256 of that normalized value is the endpoint digest.
Provider profile defaults are resolved before hashing so admission and runtime
classify the same effective endpoint even when the user did not explicitly
write `base_url`. Empty/unresolvable endpoint identity is represented by a
versioned deterministic sentinel only where the provider contract genuinely
has no endpoint; it cannot compare equal to a later concrete endpoint.

Raw endpoints and their digests remain private operational authority. They are
not added to evidence, notifications, catalog/detail, REST, Desktop, or logs.

### Central runtime identity type

A frozen generic runtime identity in `hermes_cli.runtime_provider` contains:

- effective provider;
- model;
- API mode;
- base-URL trust class;
- endpoint digest; and
- registration provenance digest.

It has one exact `to_dict()`/bounded reader contract. Primary AI, fallback,
approval primary/fallback, inline-agent definitions, and structured repair all
construct expected identity through this type. The worker constructs actual
identity through the same type from the freshly resolved runtime. Handwritten
five-field dictionaries are removed.

The generic plugin-agent wire validates the closed six-field shape when a
sealed expected runtime identity is present. It does not expose a workflow-
specific dependency in the agent core.

### Sealed authority

`WorkflowResolvedProviderRoute` gains the endpoint digest and includes it in
canonical `provider-resolution.json`. The schema-2 reader requires exactly the
new field, validates lowercase SHA-256, and rejects missing, extra, malformed,
or contradictory material. The authority digest, package risk identity,
scheduled revalidation, execution identity, and node cache material therefore
all change when the effective endpoint changes.

Immediately before any MCP startup, agent construction, or provider transport,
the worker re-resolves credentials/current runtime, computes the six-field
identity, and requires exact equality. A same-trust endpoint change returns
`provider_capability_drift`, zero provider attempts, and no fallback to live
re-resolution.

Normalization-equivalent endpoint spellings compare equal. Different hosts,
ports, paths, structural query values, or transitions between absent and
concrete endpoints compare unequal.

## Combined execution flow

```text
immutable config + compiled closure
  -> resolve routes and normalized endpoint digests
  -> classify capabilities and provider options
  -> build provider-authority schema 2
  -> derive node-bound intended identity
  -> derive cross-node shared compatibility identity
  -> seal snapshot-format-2 closure
  -> reconstruct authenticated predecessor identities when requested
  -> build primary or explicit narrowed repair request
  -> resolve credentials/current runtime
  -> compare centralized six-field runtime identity
  -> start MCP/agent/provider only after equality
  -> compute exact model-visible prefix
  -> reuse session only after shared/prefix checks
  -> record bounded authenticated identities and outcomes
```

## Failure and compatibility contracts

- Shared-context semantic mismatch remains `context_incompatible` and performs
  zero provider calls.
- Runtime/prefix/endpoint drift remains `provider_capability_drift` or
  `cache_fingerprint_changed` at its existing boundary, with zero provider
  attempts where observable.
- Malformed provider-authority schema/version remains an integrity/recovery
  failure and never consults current config.
- Alias collisions emit bounded diagnostics but do not crash discovery unless
  the canonical provider registration itself is malformed.
- Repair contradiction is terminal and not provider- or workflow-retried.
- Budget exhaustion, ambiguous settlement, and unsupported sandbox behavior
  remain terminal and unchanged.
- No new public action, mutation URL, evidence body, or Desktop resolver is
  introduced.

## Security and privacy

- Endpoint identity is derived from credential-free normalized route material;
  raw endpoints and credentials never enter the sealed public projection.
- Shared-context metadata contains digests only, never prompts, hooks, skills,
  MCP definitions, tool schemas, inline-agent prompts, or paths.
- Structured repair retains the existing bounded prompt containing only the
  canonical schema, invalid response, and bounded validation diagnostics. It
  receives no original user prompt, system prompt, extensions, or action
  surface.
- Alias diagnostics contain only bounded provider tokens and stable codes.
- No fix widens the provider, tool, MCP, workdir, resource, budget, sandbox, or
  cancellation authority of a parent or child.

## Testing requirements

Implementation must use strict RED/GREEN TDD through `scripts/run_tests.sh`.
At minimum, the remediation proves:

### Shared context

- Node-bound intended digests remain different across distinct nodes.
- Shared compatibility is node-ID/path invariant for otherwise identical
  cache contracts.
- Mutating each cache-affecting route, tool, hook, MCP, skill, inline-agent,
  structured-output, fallback, budget, sandbox, or prompt-prefix input changes
  compatibility.
- Honest different node authorities can share after exact prefix equality.
- Winning attempt identities survive store projection, scheduler handoff,
  restart, and crash recovery.
- Missing/tampered predecessor identity blocks before the provider.

### Structured repair

- V5 repair inherits the exact six-field runtime identity, reasoning config,
  request overrides, shared limits, budget, cancellation, and sandbox policy.
- Repair remains fresh and extension/action/fallback free.
- Same-trust endpoint drift or provider/plugin drift blocks before `AIAgent`
  construction and records zero attempts.
- Legacy repair retains its established behavior.

### Provider registry

- Alias cannot shadow an existing canonical token in either import order.
- A new canonical token displaces an existing alias deterministically.
- Alias-versus-alias collisions use origin precedence in both import orders.
- Existing canonical user-over-bundled override remains supported.
- Collision diagnostics remain bounded and path-free.

### Endpoint identity and sealing

- Normalization-equivalent endpoints produce equal digests.
- Same-trust different endpoints produce different identities.
- Primary, fallback, approval, repair, and inline routes carry their own exact
  endpoint digest.
- Provider-authority omission, mutation, malformed digest, or schema-1 v5
  input fails closed.
- V1-v4 snapshot fixtures remain byte-compatible and executable.

### Closure gates

- Focused Phase 5 and v1-v4 compatibility suite.
- Provider/config and generic plugin-agent invariants.
- Workflow scheduler/store/crash/recovery suite.
- Installed-wheel integration outside the checkout.
- Customization ledger and upstream merge gates.
- Desktop typecheck, Vitest, and lint.
- Complete workflow plugin and no-retry Python suites.
- Clean exact-candidate base release gate and externally audited dynamic brand
  rehearsals without publication or ref mutation.
- Fresh independent code review and adversarial re-review with zero Critical or
  Important findings before integration.

## Documentation and ownership

The runtime docs do not require new user syntax. If any current text describes
the five-field runtime identity or v5 shared-session behavior, update it to the
six-field endpoint-bound and dual-identity contract. Do not expose internal
digests as user configuration.

Update symbol-level ledger ownership for every changed generic seam, including
provider registration, runtime endpoint normalization/identity, plugin-agent
wire validation, and worker drift comparison. Workflow-owned compiler,
scheduler, executor, authority, and tests remain under the existing workflow
customization family.

## Explicit non-goals

This remediation does not:

- block or remove `context: shared` from v5;
- make the node-bound intended digest node-invariant;
- reinterpret v1-v4 snapshots;
- preserve unmerged experimental v5 schema-1 authorities by consulting live
  config;
- enable authoritative budgets or provider-native sandbox on a provider that
  remains unsupported;
- enable repair tools, hooks, MCP, skills, inline agents, fallback, approval,
  or session persistence;
- permit provider aliases to override canonical provider tokens;
- expose endpoint digests or raw routes publicly;
- add a new core model tool, OS sandbox, telemetry, synthetic message,
  environment variable, action, or Desktop-side resolver; or
- implement any Phase 6 or runtime child-workflow feature.

## Decisions resolved by approval

The user approved the following product decisions on 2026-08-08:

1. Restore functional v5 `context: shared`; do not block the inherited
   capability.
2. Keep node-bound and cross-node compatibility identities separate.
3. Use an explicit narrowed repair envelope rather than copying every primary
   field mechanically.
4. Give canonical provider names priority over aliases while retaining
   canonical user-plugin override through same-name registration.
5. Advance only the internal v5 provider-authority schema and fail closed on
   unmerged experimental schema-1 v5 authorities.
6. Preserve snapshot format 2, normalizer v5, v1-v4 compatibility, and all
   public wire contracts.

## Self-review

- **Finding coverage:** F-1 includes both semantic identity and durable
  scheduler handoff; F-2 includes route identity, option transport, and the
  deliberately reduced repair surface; F-3 covers canonical/alias and
  alias/alias collisions; F-4 covers normalized endpoint sealing and every
  execution route.
- **Compatibility:** v1-v4 and snapshot format 2 remain unchanged; only
  unmerged v5 provider-authority schema advances.
- **Authority:** no client, worker child, alias, repair, or live config path can
  create authority absent from admission.
- **Privacy:** only credential-free digests cross the new boundaries; no public
  projection grows.
- **Scope:** no unrelated refactor, feature, provider enablement, or Phase 6
  work is included.
- **Ambiguity:** identity purposes, alias precedence, repair inheritance,
  endpoint normalization, schema migration, and failure behavior are explicit.
- **Placeholders:** none.
