# Workflow Language Phase 5: Provider Portability Design

**Status:** Review-ready draft; implementation is not authorized

**Date:** 2026-08-06

**Branch:** `feat/workflow-language-phase-5-provider-portability`

**Baseline:** `cff7875049a7f369c2eae758503c63b6467c4433`

## Purpose

Phase 5 makes provider-dependent Archon workflow behavior explicit, portable,
and fail-closed. One backend authority will resolve a workflow's concrete model
routes and classify every requested provider feature as exactly one of:

- `native`;
- `hermes_adapter`;
- `degraded_with_explicit_semantics`;
- `unsupported`.

An `unsupported` decision is blocking for `archon-2026-07`. No Phase 5 field
may remain advisory, disappear during translation, or reach a provider before
its resolution has been pinned and rechecked.

This phase extends the provider profiles, runtime-provider resolver,
workflow runner binding, sealed run snapshot, and generic plugin-agent runner
already present. It does not add a core model tool, a second scheduler, an OS
sandbox, telemetry, dynamic workflow children, or a Desktop-side resolver.

## Baseline and invariants

The implementation starts from the verified Phase 4 closure:

- newly admitted Archon workflows use normalizer v4 and snapshot format 2;
- legacy workflows use normalizer v2;
- admitted v1-v4 snapshots retain their recorded behavior;
- the compiled full dependency closure is the unit of package identity, trust,
  risk, sealing, and scheduled revalidation;
- structured-output decisions already use `ExecutionRuntimeCapabilities`,
  `ConfiguredExecutionRoute`, `ExecutionCapabilityContext`, execution identity,
  and bounded run metadata;
- `PluginAgentRunRequest` already carries provider/model, exact tool policy,
  hook, MCP, inline-agent, reasoning, fallback, budget, sandbox, timeout,
  process-tree, cancellation, and sealed provider-attempt data;
- request MCP runs in the isolated plugin-agent worker, resolves secrets only
  after IPC, uses authenticated private materialization, and tears down in the
  worker's `finally` path;
- node and inline-agent skill bodies are fully snapshotted and prepended to the
  current user turn, never the system prompt;
- `allowed_tools: []` already crosses the request boundary as an empty tuple
  and produces an empty model-visible tool set;
- API, CLI, Gateway, evidence, notifications, catalog/detail, and Desktop use
  backend-authored compatibility and action projections.

Phase 5 preserves strict role alternation and a byte-stable system prompt for
the life of a conversation. A fingerprint-changing route or extension change
never mutates a cached prefix: it selects a fresh node session before the next
provider call.

## Existing infrastructure to extend

Phase 5 does not create parallel authorities.

| Existing authority | Phase 5 extension |
| --- | --- |
| `providers.base.ProviderProfile` | Add code-owned, model-aware workflow capability declarations. |
| `hermes_cli.runtime_provider` | Resolve credential-free configured routes and feed the central capability resolver. |
| `ExecutionCapabilityContext` / `WorkflowRunnerBinding` | Resolve every AI node and inline agent, construct one immutable manifest, and derive compatibility/evidence projections. |
| `plugins.workflow.compat` | Consume decisions rather than its current optional string-set provider map. |
| Normalizer and language snapshot | Add v5 bounded hook/MCP semantics while keeping v1-v4 readers exact. |
| Snapshot format 2 | Seal a provider-resolution manifest as another authenticated closure member. |
| `PluginAgentRunner` and its authenticated provider-attempt broker | Add the smallest generic shared-cost settlement seam and propagate remaining child limits. |
| `agent.usage_pricing` | Distinguish authoritative billed cost from estimates; estimates remain display-only. |
| Node-session fingerprint | Combine sealed intended authority with the exact runtime model-visible prefix and select fresh context on mismatch. |
| Existing workflow public projections | Add bounded summaries; preserve URLs and action vocabulary. |

The current process-global `DIRECT_ALIASES` cache is not suitable as admission
authority: it swallows malformed configuration, is lazily mutable, and was
designed for interactive model switching. Phase 5 reuses its accepted config
shapes through a pure parser but does not read that global cache.

## Version and activation boundary

### Normalizer v5 is required

Phase 5 changes the admitted meaning of existing fields:

- hook entry/response shapes become a bounded canonical form with per-field,
  per-event support decisions;
- the accepted MCP wrapper shapes become one canonical server map;
- model references gain tier and `@alias` syntax;
- provider-dependent options become pinned capability obligations rather than
  an optional compatibility lookup;
- node semantic identity gains the digest of these normalized obligations.

Applying those changes to already-admitted v4 definitions would change replay
and cache behavior. Therefore the implementation adds normalizer v5, makes it
current for newly admitted `archon-2026-07` packages only after all Phase 5
gates pass, and leaves legacy at v2. The supported reader set becomes
`{1, 2, 3, 4, 5}`. A v1-v4 snapshot is decoded and executed by its recorded
version without v5 normalization or reinterpretation.

### Snapshot format remains 2

No snapshot format 3 is needed. Format 2 already authenticates an exact,
arbitrary sealed path set for the complete compiled closure, and its
`resources.json` embeds a versioned language snapshot. A v5 run adds:

- `provider-resolution.json` to `sealed_paths`;
- `provider_resolution_sha256` to `resources.json`;
- v5 node semantic material to the language snapshot.

The format-2 reader requires those members only when the recorded Archon
normalizer is v5, forbids contradictory/missing material, and retains the
unchanged v4 contract otherwise. Thus the file-format envelope remains the
same while the language-version boundary tells the exact reader which
versioned members are required. Scheduled revalidation hashes the same sealed
closure and execution identity.

Activation is one final commit after v5 normalization, resolution, runtime,
surface, installed-distribution, merge-rehearsal, and brand-regression gates
are green. Before that commit, current Archon admission remains v4 and all new
Phase 5 code is dormant for ordinary users.

## Central capability authority

### Types

The central resolver exposes immutable, JSON-safe values equivalent to:

```python
class CapabilityDisposition(str, Enum):
    NATIVE = "native"
    HERMES_ADAPTER = "hermes_adapter"
    DEGRADED = "degraded_with_explicit_semantics"
    UNSUPPORTED = "unsupported"

class WorkflowProviderFeature(str, Enum):
    STRUCTURED_OUTPUT = "structured_output"
    SESSION_RESUMPTION = "session_resumption"
    TOOL_RESTRICTIONS = "tool_restrictions"
    HOOKS = "hooks"
    MCP = "mcp"
    SKILLS_INLINE_AGENTS = "skills_inline_agents"
    EFFORT_THINKING = "effort_thinking"
    FALLBACK_MODELS = "fallback_models"
    WEB_EXECUTION = "web_execution"
    COST_BUDGETS = "cost_budgets"
    PROVIDER_NATIVE_SANDBOX = "provider_native_sandbox"

@dataclass(frozen=True, slots=True)
class ProviderCapabilityDecision:
    feature: WorkflowProviderFeature
    disposition: CapabilityDisposition
    provider: str
    model: str
    option: str | None
    adapter_version: int | None
    declaration_source: str
    registration_provenance_digest: str
    code: str
    rationale: str
```

The exact implementation may use names consistent with the existing
structured-output types, but it must preserve these semantics. Text fields and
collection sizes have explicit byte/count ceilings. Decisions never contain
credentials, raw provider payloads, prompts, commands, headers, environment
values, base URLs, or filesystem paths.

### Declaration precedence

The resolver evaluates one already-resolved credential-free route:

1. explicit code-owned provider-profile declaration for the exact option and
   model;
2. trusted direct-route facts such as API mode and Hermes-managed tool-loop
   ownership;
3. a named, versioned Hermes adapter whose preconditions all hold;
4. otherwise `unsupported`.

A hostname, OpenAI-compatible wire shape, user-entered custom-provider field,
or successful best-effort request never promotes a security or billing
capability. Custom and aggregator routes receive only generic Hermes adapter
decisions whose enforcement happens wholly inside Hermes. Native structured
output, authoritative billed cost, and provider-native sandboxing require a
code-owned declaration and the required trusted route class.

Provider profiles may implement a pure model-aware declaration method because
effort/thinking and native formats vary by model. The method cannot perform
network I/O, read credentials, mutate global state, or inspect workflow
contents. Provider plugins are loaded through the existing registry. The
resolver remains the only component that combines declarations with actual
workflow requests.

Declaration provenance is assigned by the provider loader, not supplied by a
profile object. The immutable registration record identifies bundled
distribution code, legacy compatibility code, or a user-installed plugin and
binds the distribution/plugin identifier and version. For v5 capability
declarations, the complete
declaration/encoder code closure digest is mandatory: the loader hashes every
owning distribution file and imported local helper that can contribute to the
declaration or its request translation, plus the central resolver/adapter
version. A manifest version or entry-module hash alone is insufficient. If the
loader cannot produce that complete closure digest, the route may use only
Hermes-enforced generic adapters; native, degraded provider translation,
authoritative billing, and sandbox claims are `unsupported`. That provenance
is part of the route fingerprint and sealed authority. A later same-name or
same-version code registration necessarily changes its provenance identity.

For Phase 5, user-installed provider plugins may declare functional option
support such as effort translation, but they cannot assert authoritative
billing or provider-native sandbox guarantees. Those two security-sensitive
facts require a distribution-owned reviewed adapter. An overriding user plugin
named `openrouter` therefore loses the bundled OpenRouter billing candidate; it
does not inherit guarantees by provider name or endpoint.

### Decision rules

| Feature | Accepted decision | Blocking conditions |
| --- | --- | --- |
| Structured output | Existing native JSON-schema strategy is `native`; prompt-schema validation is `hermes_adapter`; any explicit provider prohibition is `unsupported`. | No stable schema strategy or runtime drift. |
| Session resumption | `hermes_adapter` when SessionDB history, the same complete cache fingerprint, and a Hermes-owned conversation loop are available. | Missing session, changed fingerprint, external loop without an exact contract. Missing sessions retain Phase 3 recovery. |
| Tool restrictions | `hermes_adapter` only when Hermes owns schema selection and dispatch. | External/provider-owned tool loops without exact filtering. Empty allow lists remain empty. |
| Hooks | `hermes_adapter` per normalized event/response operation. | Any unsupported event or response operation, invalid matcher, or unavailable MCP/agent lifecycle. |
| MCP | `hermes_adapter` when the sealed definition/closure, worker lifecycle, tool loop, dependency, and teardown contracts hold. | Mutable/uncontained executable resource, unavailable dependency, external loop, startup or teardown failure. |
| Skills and inline agents | Skills are `hermes_adapter` as current-turn content; inline agents are `hermes_adapter` only with the declared worker tool and shared remaining authorities. | Ambient delegation, missing snapshots, unavailable tool loop, or inability to share remaining limits. |
| Effort/thinking | `native` for an exact provider/model option; `degraded_with_explicit_semantics` only for a documented, pinned translation. | Provider silently ignores or ambiguously translates the option. |
| Fallback models | `hermes_adapter` only for a fully resolved fallback route that starts fresh context and shares the parent authorities. | In-place provider/model mutation, unresolved fallback, or provider-owned opaque fallback that violates pinned options. |
| Web execution | `native` for an exact provider-native option; `degraded_with_explicit_semantics` for the explicitly selected Hermes web-tool mapping. | Implicit web access, unavailable/gated web service, or an unrecognized mode. |
| Cost budgets | `hermes_adapter` only when every billable response carries authoritative settled cost. | Estimated/unknown/included-with-unproven-fees cost, missing settlement, or a route that can bill outside the observed response. |
| Provider-native sandbox | `native` only for a code-owned declaration plus a trusted direct route and exact request translation. | Every current resource-limit/process-isolation claim and any custom/aggregated route without provider guarantee. |

`degraded_with_explicit_semantics` is runnable only with a stable warning code,
the exact alternate semantics in doctor/detail output, and the translated
option in the sealed manifest. It is not a synonym for “best effort.”

### Single machine-authoritative matrix

The final matrix is generated from the feature enum, generic adapter rules,
and provider-profile declarations. There is no hand-maintained workflow table
beside it. The same resolver result drives:

- schema-aware validation diagnostics;
- `doctor` findings and remediation;
- trust/run admission;
- scheduled revalidation;
- executor request construction and pre-transport drift checks;
- evidence and notification summaries;
- catalog list/detail projections;
- Desktop rendering and Run enablement.

The website reference is generated or contract-tested against the same feature
inventory. Desktop TypeScript contains display types and exhaustive rendering
only; it never maps provider/model names to capabilities.

## Portable model references

### Configuration

Phase 5 formalizes the existing optional top-level `model_aliases` shape and
adds an optional top-level `model_tiers` shape to `config.yaml`:

```yaml
model_tiers:
  small:
    provider: openai
    model: gpt-5.6-luna
    options:
      effort: low
  medium:
    provider: anthropic
    model: claude-sonnet-5
  large:
    provider: openai
    model: gpt-5.6-sol

model_aliases:
  code-review:
    provider: openrouter
    model: anthropic/claude-opus-4.8
    options:
      effort: high
```

Both roots are registered as known optional config roots and validated by one
pure parser. The parser also accepts the existing `model.aliases` string form,
with the existing precedence that a rich top-level `model_aliases` entry wins;
the documented Phase 5 authoring form is the rich top-level mapping. No default
tier is invented: a referenced but unconfigured tier blocks. No config version
bump is required because the keys are additive and optional; no user file is
rewritten. Neither root is read from `.env`, and no new user-facing
`HERMES_*` variable is introduced.

Phase 5 has exactly two configuration provenance scopes because those are the
authorities the current loader implements: the active profile's `config.yaml`
and the optional managed `config.yaml` overlay. Existing managed leaf
overrides win over the profile file. There is no project `cli-config.yaml`,
repository-local, package-local, or process-global alias authority in Phase 5.
The immutable admission snapshot records whether an effective entry came from
`profile` or `managed` and fingerprints the exact safe effective entry. A
project/showcase package that references a profile-local tier or alias receives
`model_reference_not_globally_portable`; catalog source decides only that
diagnostic and is never treated as a config scope.

`model_tiers` has exactly `small`, `medium`, and `large` keys. Tier and alias
entries require nonempty `provider` and `model`, accept only a bounded allowlist
of provider options, reject secret-like keys, and preserve the existing
credential lookup outside the package. Existing direct-alias `base_url`
behavior remains available to interactive model switching, but workflow
evidence records only the resolved provider/model, route trust class, and a
credential-free route fingerprint.

### Reference grammar and precedence

An AI node, inline agent, or fallback model reference resolves as follows:

- exact `small`, `medium`, or `large` means a configured tier;
- `@name` means an exact configured alias named `name`;
- any other nonempty string is a literal model ID and is never rejected by a
  static model allowlist.

The workflow `provider` selects the route for a literal model. A tier or alias
contains its own provider. Per the approved umbrella contract, a tier/alias
provider wins over a conflicting explicit workflow provider and emits the
stable visible warning `model_reference_provider_overridden`. This precedence
is included in identity and is the same in validation, doctor, admission, and
execution.

For a literal, provider precedence is node `provider`, workflow `provider`,
then the active provider from the same immutable config snapshot. An absent or
`auto` provider must be reducible by the pure configured-route resolver to one
concrete provider; otherwise admission blocks with
`model_provider_unresolved`. It never waits for credential-driven runtime
auto-detection.

For provider options, explicit node fields override workflow fields, which
override tier/alias option defaults. The tier/alias still supplies the route's
provider and model. Every override is revalidated against that concrete route;
an unsupported override blocks rather than falling back to the configured
default. The effective option set and safe override warning codes are sealed.

Package-distributed use of a profile-local alias or tier emits
`model_reference_not_globally_portable`. It is not silently rewritten to a
literal in the package. The sealed run always contains the literal resolved
provider/model/options, so later alias edits do not reinterpret an admitted
run.

### Resolution manifest

`provider-resolution.json` is canonical JSON with a schema version, resolver
version, credential-free config fingerprint, and a sorted entry for every AI
node and declared inline/fallback route. Each entry contains:

- logical node and optional inline-agent identifier;
- authored reference kind (`tier`, `alias`, or `literal`) and a digest of the
  authored reference, not secret config;
- concrete provider and model;
- exact supported option set after precedence;
- route trust/API-mode/tool-loop facts;
- all requested capability decisions;
- safe warning codes;
- cache fingerprint material;
- budget settlement strategy and sandbox decision where requested.

The manifest has explicit node, option, decision, string, and total-byte
limits. It contains no alias bodies beyond the resolved safe fields, no
credentials, no raw provider configuration, and no endpoint URL. Its digest is
part of run start identity, node-session fingerprinting, scheduled
revalidation, and evidence correlation.

Admission resolves exactly once from one immutable config snapshot. Execution
uses the sealed resolution, reloads only current credentials for its concrete
route, and reclassifies the credential-free runtime immediately before any MCP
spawn or provider transport. A mismatch fails with
`provider_capability_drift`, zero provider attempts where observable, and no
fallback to live re-resolution.

The provider-authority digest is part of the risk digest stored beside package
trust. Changing an alias, tier, provider declaration, or supported option set
therefore makes the old package-plus-risk trust decision stale; it does not let
an operator's earlier trust silently authorize materially different execution.

## Hook normalization

Normalizer v5 converts the authored event-object shape into a sorted bounded
tuple of canonical hook obligations. Each obligation has event, Hermes event,
matcher source, timeout seconds, and normalized response operations. Regex
compilation occurs from the authenticated source in the isolated worker; v5
accepts only a string or null matcher, caps its length/complexity, and rejects
invalid expressions before admission.

Every response field is classified for its event. A value is accepted only if
the mapped Hermes event actually fires on that path and the worker consumes its
response with the documented semantics. The activation set is deliberately
limited to the lifecycle paths proven by the isolated worker:

- `PostToolUse` and `PostToolUseFailure` stay status-disjoint;
- `PreToolUse` may use exact allow/block/ask and tool-input replacement
  operations, and `UserPromptSubmit` may add bounded current-turn context;
- `PostToolUse`, `PostToolUseFailure`, `SessionStart`, and `SessionEnd`
  normalize into explicit obligations but block because their current runtime
  callbacks do not consume authored response values;
- `SubagentStart`, `SubagentStop`, `TaskCompleted`, `Elicitation`,
  `ElicitationResult`, `PermissionRequest`, `Setup`, and
  `InstructionsLoaded` normalize into explicit obligations but block because
  the current worker does not provide an exact event-and-response contract;
- hardline approval policy remains authoritative over hook requests;
- `systemMessage`/`additionalContext` become bounded current-turn/tool-result
  content, never a system-message mutation;
- an unsupported field such as output suppression on an event without exact
  suppression semantics blocks instead of being dropped.

The other published unsupported events remain blocking: `Notification`,
`Stop`, `PreCompact`, `TeammateIdle`, `ConfigChange`, `WorktreeCreate`, and
`WorktreeRemove`. Structurally recognizing and sealing an event never implies
runtime support; the provider authority is the machine-readable activation
decision.

The worker must stop editing plugin-manager private dictionaries directly.
Phase 5 adds one generic scoped hook-registration token/context manager to the
existing plugin lifecycle. It restores hooks and middleware deterministically
on every exit and is ledgered/tested outside workflow-specific policy. This is
a concrete consumer-backed extension, not a speculative hook API.

## MCP normalization and lifecycle

V5 accepts the documented package-local wrapper spellings `mcp_servers` and
`mcpServers`, the existing bare server map, and the existing single-server
`command`/`url` form. Supplying conflicting wrappers is invalid. They normalize
to one sorted server map with an explicit `stdio`, `streamable_http`, or `sse`
transport and bounded launch/connect/teardown settings.

The dependency compiler resolves the referenced definition and every declared
runtime file from the owning package origin. Phase 5 initially accepts only a
package-contained Python entry script launched by the exact trusted Hermes
interpreter identity under isolated `-I -S` semantics and a worker-enforced
import policy that permits the standard library and sealed package roots only.

The interpreter family/version/build identity and import-policy version are
sealed safe facts. PATH-selected commands, `python -m`, `python -c`, ambient
`site-packages`, Node/npm/module resolution, registry downloads, mutable
`npx -y` specs, standalone binaries, shebang scripts, ELF/Mach-O/PE dynamic
executables, ambient scripts, path escapes, symlinks, and any import outside
the permitted roots block v5. Runtime import enforcement remains active for
the server lifetime so a delayed tool-call import cannot bypass admission.
This is dependency immutability, not an OS sandbox or a claim that trusted MCP
code is non-malicious. V4 behavior remains unchanged for admitted snapshots.

Normalizer v5 recognizes and canonicalizes remote HTTP/SSE definitions, but
they are `unsupported` for Archon execution in Phase 5. A remote implementation
and its discovered schemas are mutable outside the sealed package closure, and
the current runtime has no version-pinned remote-adapter contract. This avoids
pretending that endpoint/config identity authenticates executable behavior.
Future support requires a separate explicit design for adapter/version trust,
schema drift, cancellation, and session reuse. Credentials remain placeholders
outside packages/evidence even for a blocked definition.

The existing worker-scoped MCP lifecycle remains authoritative:

1. verify and privately materialize the exact authenticated closure;
2. resolve secret placeholders without returning values to the parent;
3. classify the runtime before discovery;
4. start only the node's declared servers within process-tree and deadline
   limits;
5. expose only admitted MCP tool names through the exact tool policy;
6. cancel and tear down sessions/processes on success, failure, pause,
   cancellation, timeout, or parent death;
7. remove private material and restore registry/loaders even when shutdown
   reports failure.

A startup or teardown failure is visible and terminal for the node. Resource
limits are availability/containment controls, never described as a sandbox.
MCP stderr and tool output are treated as untrusted secret-bearing data:
private diagnostic logs are bounded/redacted and public evidence receives only
typed lifecycle status and safe digests, never raw stderr or tool payloads.

## Tools, skills, and inline agents

Tool aliases normalize before identity. Unknown aliases block. Deny still
wins, ambient `delegate_task` remains denied, and `workflow_agent` is exposed
only when the sealed node declares inline agents. `allowed_tools: []` remains
an explicit empty allow set through normalization, manifest, IPC, schema
filtering, Tool Search disablement, and dispatch.

Node and inline-agent skill files are fully read during snapshot creation,
digest-bound, and stored in the complete closure. Execution adds their exact
content to the new user turn. No lazy pagination, system-prompt append, hidden
assistant/user message, or live skill reread is allowed.

An inline-agent manifest entry pins its provider, model, option set, tool/skill
scope, and maximum iterations. At spawn it inherits:

- the authenticated remaining provider-attempt grant;
- the same shared authoritative cost authority and remaining settled budget;
- remaining wall/provider/idle deadlines rather than fresh full durations;
- the enclosing process-tree RSS, CPU, and descendant authority;
- the exact contained workdir;
- parent cancellation and parent-death lifeline;
- the parent sandbox decision (which cannot be widened);
- a fresh context unless an independently fingerprinted declared session is
  explicitly supported.

Child results remain bounded final text, usage totals, status, and safe audit
facts. No prompt, child command, provider payload, credential, or private path
is returned. Nested raw delegation remains unavailable.

## Fallback and cache semantics

A fallback model is resolved like any other model reference and appears as a
separate route in the sealed manifest. The existing in-place fallback path is
not valid for Archon when it would change provider/model/options under a cached
prefix. The Phase 5 adapter starts the fallback as a fresh isolated context
under the same node attempt, remaining deadline, attempt authority, cost
authority, tool policy, and immutable user-turn material. It never injects a
synthetic message or copies a provider-specific cached prefix.

The node-session fingerprint includes at least:

- concrete provider/model/API mode and supported option set;
- schema strategy/version and schema digest;
- exact allowed/denied tool set;
- hook semantic digest;
- MCP definition/closure/tool-name digest;
- node and inline-agent skill digests;
- inline-agent definition/resolution digest;
- initial system prompt digest;
- fallback route digest;
- budget/sandbox decision and adapter versions.

That sealed digest is the **intended authority identity**, not by itself the
model-visible cache identity. Phase 5 adds a second runtime handshake after
private MCP discovery and final tool filtering but before session selection or
provider transport. It renders the exact final system-prompt bytes once and
canonicalizes the complete final model-visible tool definitions, including
names, descriptions, parameter schemas, and provider-visible ordering. Their
digest is `model_visible_prefix_digest`.

The rendered bytes include every active system-prompt contributor, including
context files, memory/context-engine material, plugin prompt contributions,
and renderer/version identity. The digest contains no prompt text or tool
schema bodies in public evidence. The same bytes and schemas are then reused
unchanged for the conversation; they are not regenerated mid-loop.

A node session stores both the sealed intended-authority digest and runtime
`model_visible_prefix_digest`. Resume requires equality of both. A same-name
MCP schema/description change, built-in/plugin tool-schema change, context-file
or memory change, prompt-renderer change, or any intended-authority change
selects fresh context before transport. If the sealed policy expected a tool
or MCP capability that final discovery cannot supply, execution blocks rather
than starting a fresh but weakened context. Existing v1-v4 session fingerprints
and recovery behavior are not recomputed.

## Enforceable `maxBudgetUsd`

### Explicit enforcement semantics

Phase 5 defines `maxBudgetUsd` as a **settled-call hard stop**, not a forecast:

- the first request may start only while remaining settled budget is positive;
- after every completed billable provider transport, Hermes atomically settles
  the authoritative billed amount against the shared node authority;
- while a hard budget is active, the authority permits only one unsettled
  billable provider transport across the parent/repair/fallback/inline tree;
  concurrent callers wait within their inherited deadline or fail terminally;
- once cumulative settled cost is greater than or equal to the declared
  maximum, no later parent, child, repair, retry, or fallback transport may
  start;
- the already in-flight request is allowed to finish and may make settled cost
  exceed the threshold; the bounded evidence reports the overage;
- exhaustion or missing/contradictory authoritative settlement is terminal,
  has no grace call, and is never workflow- or provider-retried.

This matches the enforceable boundary available to a client-side orchestrator:
Hermes can stop subsequent calls but cannot retroactively cancel a charge
settled for an already-dispatched request. It is not represented as an
absolute preauthorization cap. Approval of Phase 5 includes approval of this
specific product meaning.

### Initial candidate authority

The first candidate route is trusted direct OpenRouter billing, whose usage
accounting documentation describes response `usage.cost` as the total amount
charged to the account. Route enablement requires either (a) an authoritative
contractual provider guarantee covering every billable terminal outcome plus
adapter tests for every Hermes transport path, or (b) authoritative per-attempt
post-hoc reconciliation before the lease can be released. Recorded transport
fixtures alone prove only Hermes parsing and poisoning behavior. Current
OpenRouter documentation does not establish charged error, disconnect,
timeout, and cancellation completeness, so shared-credit OpenRouter remains
`unsupported` at Phase 5 activation unless stronger evidence or reconciliation
is added. Hermes extracts only a finite, nonnegative decimal and never retains
the raw usage object. OpenRouter BYOK is explicitly unsupported because the
returned OpenRouter account charge does not prove the separate upstream bill.

Official-doc/model-catalog price tables, token multiplication, cached pricing,
“included” subscription guesses, and provider model APIs remain estimated
display accounting. They cannot enable `maxBudgetUsd`. Other providers remain
blocking until a code-owned adapter proves authoritative complete billed cost
for every billable attempt.

Primary references: [OpenRouter Usage Accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting)
and [OpenRouter Workspace Budgets](https://openrouter.ai/docs/guides/features/workspaces/workspace-budgets),
[OpenRouter BYOK](https://openrouter.ai/docs/guides/overview/auth/byok), and
[OpenRouter Errors](https://openrouter.ai/docs/api/reference/errors-and-debugging).
The latter likewise documents that an already-running request can complete
after a limit is reached, so this design does not promise preauthorization or
zero overrun.

### Shared authority

The generic plugin-agent seam is modeled after the existing authenticated
provider-attempt broker. A parent-owned request-local authority stores the
limit and cumulative settled cost as canonical bounded decimal/fixed-point
values, plus settlement count, exhaustion, and terminal error. Provider
decimals are never converted through binary float and are never rounded down;
an unsupported precision blocks rather than weakening the limit. Parent
execution, inline workers, structured repair, and fallback all share its
authenticated descriptor. Retries cannot construct a new authority.

The authority issues one authenticated in-flight settlement lease. It releases
that lease only after an authoritative settlement or a code-owned proof that
the transport did not start and cannot bill. Cancellation, timeout, or a
missing settlement with an ambiguous billing outcome terminally poisons the
authority; it does not open a second transport. This serialization is the
mechanism that bounds overrun to one already-started provider call even when
inline-agent work could otherwise overlap.

The agent core receives only a generic per-response settlement callback. It
does not import workflow code and does not expose credentials or provider
responses. The provider profile/usage normalizer returns either one
authoritative bounded cost fact or no authoritative cost. The callback runs
after usage is available and before another tool-loop provider transport can
begin. Existing display estimates continue independently and cannot be passed
to the hard authority.

Structured repair receives the same authority and remaining budget; the
current `max_budget_usd=None` repair request is replaced by the shared private
authority, not by resetting the public numeric limit. Inline children likewise
share it instead of copying the full maximum.

### Evidence

Budget evidence contains only:

- declared limit and settled cost as bounded decimal strings;
- remaining/overage amount;
- settlement count;
- `authoritative`, `exhausted`, and terminal status;
- safe provider/model and settlement-strategy identifiers;
- stable failure code.

It excludes raw usage, generation IDs, account/workspace IDs, headers,
credentials, provider responses, prompts, and pricing payloads.

## Truthful `sandbox`

The resolver accepts a `sandbox` object only when a code-owned provider profile
declares an exact provider-native sandbox schema, the resolved route is trusted
direct, and the request adapter proves the option is sent and enforced by that
provider. The sealed manifest pins the accepted keys and adapter version;
runtime drift blocks before transport.

No currently proven profile is promoted merely to make the field runnable.
Until a provider meets the contract, `sandbox` remains `unsupported` with
`provider_native_sandbox_unavailable`. Doctor recommends the existing
`execution_environment: isolated_backend_required` companion policy when the
operator's intent is isolated execution. That policy and plugin-agent process
limits are not relabeled as a provider sandbox or security boundary.

## Admission, execution, and recovery flow

```text
read one config snapshot + compiled package closure
  -> normalize with recorded/current language version
  -> resolve model/tier/alias/fallback/inline routes
  -> classify every requested feature
  -> build compatibility + risk from those decisions
  -> block unsupported obligations
  -> seal provider-resolution.json in format-2 closure
  -> persist execution/cache identity
  -> before MCP/provider start, compare runtime to sealed facts
  -> execute only sealed requests under shared authorities
  -> publish bounded evidence/projections derived from the same decisions
```

Trust does not override unsupported capability findings. Direct internal store
callers cannot bypass the gate: v5 snapshot preparation requires a valid
resolution manifest, and v5 run start verifies its digest/identity.

For scheduled runs, admission records the manifest and live credential-free
execution identity. Scheduled revalidation uses the same resolver against a
fresh bounded config snapshot. A changed tier/alias/profile declaration or
route blocks as execution-capability change; it never rewrites the sealed run.

For an already-running or resumed v5 run, the sealed concrete route remains
authoritative. Missing current credentials or incompatible runtime capability
fails visibly without substituting a different model. V1-v4 resumes use their
recorded behavior and do not require a Phase 5 manifest.

## Public projections and Desktop

Catalog list responses add only a bounded capability summary: runnable level,
resolved-route count, mixed-provider flag, unsupported/degraded counts, and
safe warning codes. Detail responses may add bounded per-node provider/model
and feature decisions. Run evidence includes the sealed manifest digest and
bounded outcomes, not the manifest's private operational material.

All projections pass through the existing sanitization and size ceilings.
They exclude prompts, commands, secret placeholders/values, credentials, raw
provider responses, hook feedback bodies, temporary-root absolute paths, MCP
private paths, and unnecessary local paths.

Raw resolved provider/model strings remain private transport values and are
not automatically safe merely because their keys are named `provider` or
`model`. Public projections use a separate bounded display-identifier
function. It permits ordinary provider/model slug characters but rejects
controls, URI schemes/userinfo/query/fragment material, absolute or traversal
paths, credential-like prefixes, and overlong/high-entropy values. Unsafe
values become a stable `redacted:<digest-prefix>` label; no substring of the
raw value is returned. The same closed projection is used by evidence,
notifications, REST, catalog/detail, and Desktop.

Desktop extends existing workflow types and views to render backend resolutions
and diagnostics. It fails Run closed while authoritative detail is absent,
malformed, unsupported, or stale. It does not parse YAML, read `config.yaml`,
resolve aliases/tiers, infer provider capability, or add action names. New
backend fields are additive; an old Desktop continues using compatibility and
existing actions, while a new Desktop treats missing Phase 5 fields from an old
backend as unavailable presentation rather than inventing a decision.

## Compatibility and failure contracts

Stable blocking/failure codes include at least:

- `provider_capability_unsupported` with feature-specific detail;
- `provider_capability_drift`;
- `model_tier_unconfigured`;
- `model_alias_unknown`;
- `model_provider_unresolved`;
- `model_reference_provider_overridden` (warning);
- `model_reference_not_globally_portable` (warning);
- `hook_event_unsupported` and `hook_operation_unsupported`;
- `package_mcp_unavailable` and `package_mcp_closure_unproven`;
- `authoritative_cost_unavailable`;
- `authoritative_cost_missing`;
- `cost_budget_exhausted`;
- `provider_native_sandbox_unavailable`;
- `cache_fingerprint_changed` where a fresh context is selected.

Capability, budget, sandbox, validation, cancellation, integrity, and
credential failures are terminal/non-retryable unless an existing recovery
contract explicitly says otherwise. Budget exhaustion never receives the
agent loop's grace call. Provider attempts and model calls remain exact and
shared with Phase 3 retry accounting.

## Security and privacy

- Config parsing is local, bounded, and credential-free.
- Secrets stay in `.env`/credential stores and are resolved only at the
  existing provider or isolated MCP boundary.
- Capability declarations cannot include callbacks from workflow YAML.
- Custom configuration cannot self-assert sandbox or authoritative billing.
- Provider/MCP/hook payloads are not persisted as evidence.
- All public text is bounded, sanitized, and redacted.
- Absolute private materialization roots never cross IPC or public APIs.
- MCP child processes remain inside the existing process-tree ownership and
  parent-death cleanup path.
- No telemetry, usage attribution, or third-party analytics is added.
- No synthetic conversation message or mid-conversation system prompt change
  is added.

## Documentation and installed distribution

The schema/website inventory documents model reference grammar, config shapes,
the four dispositions, the exact hook/MCP canonicalization, settled-call budget
semantics, sandbox blocking, and remediation codes. Examples never put
non-secret settings in `.env`.

Installed-distribution tests create a temporary `HERMES_HOME`, install/import
through the shipped package layout, load provider plugins, parse optional
config roots, run doctor/catalog resolution offline, admit a v5 closure, and
resume v1-v4 fixtures. They verify that source-checkout imports are not masking
missing package data or provider declarations.

## Upstream and brand discipline

Generic changes to provider profiles, runtime classification, usage
settlement, plugin-agent IPC, worker lifecycle, and config validation are
recorded as separate entries in the same atomic commit as each generic change in
`docs/upstream-customizations/workflow-orchestration.yaml`. Each entry names
the upstream/base ownership boundary and invariant tests. Workflow-specific
policy remains in `plugins/workflow`.

`last_verified_upstream` is not advanced by Phase 5. Before activation, the
implementation runs the upstream replay checker and temporary upstream merge
rehearsal. It then runs the existing base and brand regression gates without
checking out, merging, pushing, tagging, releasing, or modifying `otto`,
`loop24`, or literal `main`. Phase 4 Desktop v5.3.0 releases are untouched.
Production brands are discovered from and validated through every real
`brands/*.json` descriptor, never a hardcoded tuple. Before/after snapshots of
refs, worktrees, branch/status, tags, brand-repository refs, and release
metadata must compare equal around the read-only rehearsals.

## Verification requirements

The implementation is strict TDD and must prove:

1. pure config parsing, route resolution, precedence, and exact model pinning;
2. one exhaustive feature inventory and four-way decision classification;
3. normalizer v5 canonical hook/MCP semantics and exact v1-v4 replay;
4. sealed format-2 provider manifest integrity and scheduled revalidation;
5. validation/doctor/admission/execution parity and zero-call fail-closed drift;
6. empty tool allow lists, skill current-turn injection, and cache-freshness;
7. package-contained MCP closure, isolated startup, cancellation, and teardown;
8. inline-agent remaining attempt/cost/resource/deadline/workdir/cancellation
   inheritance;
9. authoritative OpenRouter settlement, shared repair/child/retry budget,
   terminal exhaustion, missing-cost failure, and estimated-provider blocking;
10. sandbox rejection unless an exact native declaration exists;
11. bounded/redacted evidence, catalog/detail, Gateway, notifications, and
    Desktop projections;
12. installed-distribution, full workflow, customization-ledger,
    upstream-rehearsal, and brand-regression gates.

Mutation tests must show that changing any provider/model/option/capability,
hook operation, MCP closure, skill/agent digest, budget strategy, or sandbox
decision changes identity and prevents shared-context reuse. Tests assert
relationships and invariants rather than provider counts, model lists, or
config-version literals.

## Out of scope

- Phase 6 `loop_group`;
- runtime child workflows or unrestricted delegation;
- dynamic includes, `include.with`, child input mapping, or deep child-output
  navigation;
- a new model tool or a new OS sandbox;
- provider capability probing by Desktop;
- live mutation of admitted resolutions;
- preauthorization/absolute no-overage dollar caps;
- telemetry, provider attribution tags, or public raw usage payloads;
- new user-facing non-secret environment variables;
- changes to REST mutation URLs or old-client action vocabulary.

## Decisions requiring approval

Implementation should begin only after explicit approval of these decisions:

1. Activate normalizer v5 for new Archon admissions while retaining snapshot
   format 2 with a v5-required sealed provider manifest.
2. Use optional top-level `model_tiers` and the existing `model_aliases` roots;
   tier/alias provider wins conflicts with a visible warning.
3. Define `maxBudgetUsd` as a settled-call hard stop that can overrun by one
   already in-flight request, and initially enable it only for routes with a
   complete authoritative returned billed-cost field (trusted direct
   OpenRouter is the first candidate, not presumed supported). Budgeted
   provider transports are serialized across the parent/repair/fallback/inline
   tree to keep that one-call bound truthful.
4. Keep `sandbox` blocking for all providers until a code-owned exact native
   guarantee is added; process/resource limits do not satisfy it.
5. For Archon fallback, start fresh isolated context instead of mutating a
   provider/model under an existing cached prefix.
6. Tighten v5 local MCP to provably package-contained executable closure,
   initially allowing only isolated sealed Python scripts, and block standalone
   binaries, shebang/dynamic executables, remote MCP, and mutable/ambient launch
   forms; v1-v4 behavior remains unchanged.
7. Resolve literal-provider and option precedence as node > workflow > the
   immutable configured route/options, while tier/alias provider+model remain
   authoritative and any unsupported override blocks.
8. Restrict Phase 5 tier/alias configuration to the actual profile and managed
   `config.yaml` scopes; do not invent project-local alias files.
9. Let user provider plugins declare functional option support but reserve
   authoritative billing and provider-native sandbox guarantees for
   loader-provenanced distribution-owned adapters.

## Resolved ambiguities

- A literal model ID is not catalog-validated; capability is checked against
  the resolved route and requested options.
- Trust never converts unsupported into runnable.
- Aliases/tiers are operator config, not package content and not secrets.
- Provider change after admission causes drift/revalidation failure, not live
  rewriting.
- Hook context is current-turn/tool-result content, never a system prompt.
- `allowed_tools: []` means no tools, including no implicit `workflow_agent`.
  An inline-agent declaration that cannot be invoked under that exact allow
  list blocks rather than silently adding the tool.
- Budget estimates remain UI/accounting information only.
- Provider-native sandbox and isolated execution environment are distinct
  capabilities and may be recommended together without being equated.

## Self-review

- No Phase 6, dynamic workflow-child, input-mapping, deep-output, core-tool,
  telemetry, release, or OS-sandbox work is included.
- Every accepted provider-dependent feature has one of the four required
  dispositions; unsupported is blocking.
- The version boundary preserves v1-v4 snapshots and explains why v5 is needed
  and format 3 is not.
- Model, hook, MCP, skill, child, budget, sandbox, cache, evidence, Desktop,
  installed-distribution, upstream, and brand boundaries are explicit.
- The unsettled product choices are listed for user approval rather than left
  as implementation assumptions.
