# Workflow Marketplace Supervisor Identity-Binding Amendment

**Date:** 2026-09-05

**Status:** Approved by the user on 2026-09-05 for implementation in the existing Hermes worktree. This approval does not authorize Workflow Studio changes, merge, push, publication, release, history rewrite, or worktree deletion.

**Baseline:** `eeba68e5c3521fa02a3cf6b5fb1cb56747c07b1f`, branch `feat/workflow-package-marketplace`.

**Amends:** [lifecycle recovery amendment](2026-09-04-workflow-marketplace-lifecycle-recovery-amendment.md), especially section 6, and [strict wire parity amendment](2026-09-05-workflow-marketplace-strict-wire-parity-amendment.md), especially its capabilities contract.

**Execution:** The [replacement remaining-work plan](../plans/2026-09-04-workflow-marketplace-lifecycle-recovery.md) defines the Task 14C0a, 14C0b, and revised 14C1 sequencing and review gates.

## 1. Decision and scope

The durable operation supervisor needs two independent proofs before it may attach a backend operation to a Desktop connection:

1. The backend issues a credential-free `principal_binding` proving that two observations belong to the same authenticated marketplace actor in the same live registry epoch.
2. Electron issues a nonsecret `connectionGeneration` proving that a renderer request and response use the same native route/configuration descriptor. The backend `registry_epoch`, not the Electron generation, distinguishes a restarted remote backend process.

Neither proof substitutes for the other. The backend cannot prove which native connection descriptor carried a response, and Electron must not inspect or reproduce the backend's private authorization identity. The full supervisor binding is:

```typescript
type LifecycleConnectionBinding = Readonly<{
  connectionId: string | null
  connectionGeneration: number
  profile: string
  principalBinding: string
  registryEpoch: string
}>
```

An exact binding lets Desktop resume known work after view navigation, reject delayed responses from an obsolete route, and recover a lost admission response without adopting another actor's operation. It does not prove a mutation's result. Only a correlated terminal operation plus authoritative package state can prove installed version, absence, recovery health, or trust.

This amendment changes Hermes backend, Electron, and Desktop contracts only. It adds no Workflow Studio change, persistent identity, public actor field, credential fingerprint, merge authority, or release authority. Task 14C1 remains stopped at the clean baseline; its initial inspection changed no production or test code.

## 2. Authority boundaries

| Fact | Owner | Lifetime | Permitted use |
| --- | --- | --- | --- |
| Authenticated actor and authorization scope | Backend request authority | One authenticated request/context | Private authorization and admission scoping only |
| `principal_binding` | Backend marketplace registry | Same actor within one live registry epoch | Safe equality test for supervisor reconciliation |
| `registry_epoch` | Backend marketplace registry | One backend registry/process lifetime | Separates operation, receipt, and binding generations |
| `connectionGeneration` | Electron main process | One live connection descriptor generation | Rejects stale native-route responses and observations |
| `connectionId` | Electron connection registry | Existing connection record, or null for legacy primary | Selects the native route; never proves credentials or actor |
| `profile` | Backend-resolved marketplace scope | Existing profile semantics | Exact operation/cache/admission isolation |
| Review token and replayable confirm body | Backend vault and dialog/transport closure | Bounded, ephemeral | Review/confirm only; never supervisor history |
| Installed bytes, provenance, trust, journal health | Backend filesystem/service | Durable according to existing stores | Authoritative user-visible package state |

The available SSH `remoteIdentity`, install ID, connection-registry schema version, gateway activation counter, URL, headers, token, and renderer-derived hashes are explicitly not lifecycle identity. The gateway activation counter changes with foreground navigation; SSH ownership identity is absent from other routes; registry version describes a storage schema; credentials and their hashes are secrets or stable tracking identifiers. None may fill a binding field.

## 3. Backend principal binding

### 3.1 Public schema

The strict lifecycle V2 capabilities response gains one required field:

```typescript
interface LifecycleCapabilitiesV2 {
  schema_version: 2
  profile: string
  registry_epoch: string // exactly 32 lowercase hex characters
  principal_binding: string // exactly 64 lowercase hex characters
  server_time: string
  capabilities: readonly LifecycleCapability[]
}
```

The object remains closed and exact. Capabilities publication validates this field at the Python producer boundary and again at the HTTP response boundary. A malformed internal value returns the existing safe fixed internal error rather than malformed HTTP 200. Generated Python-to-TypeScript fixtures and the strict Desktop decoder treat a missing, extra, uppercase, or malformed binding as invalid.

The field is safe equality material, not a display identifier. Desktop never shows it to the user and never sends it as authorization. Public operations continue to omit actor identity.

### 3.2 Derivation

The process-epoch authority in `marketplace/admissions.py` owns a private 32-byte cryptographically random principal-binding key alongside the existing process epoch and admission secret. It is outside retireable per-profile operation registries: retiring and recreating an idle profile context in the same process must not change the binding. The key remains memory-only, is excluded from representations, errors, logs, fixtures, and serialization, and can be deterministically injected only through a private test seam.

For the already-authorized capabilities request, the backend obtains the exact private marketplace actor through the existing `_actor(authority, profile)` path. It derives:

```text
HMAC-SHA256(
  registry_private_binding_key,
  "workflow-marketplace-lifecycle-principal-v2\0"
  || registry_epoch
  || "\0"
  || private_marketplace_actor
).hexdigest()
```

The derivation is domain-separated and length-unambiguous because the epoch has a fixed domain and the separators are fixed. The existing private actor already incorporates exact authority and profile identity. HMAC prevents a caller from testing guessed principals or recovering the underlying authority binding from the public value.

Within one process registry epoch, the same exact actor returns the same binding across profile-context retirement/recreation, and a different actor or profile returns a different binding. A new process binding key or epoch returns a different binding even if the human user and profile are unchanged. Backend restart therefore invalidates prior renderer observations consistently with the existing loss of process-local operations, admission receipts, and review-token vaults.

Capabilities use the same authenticated marketplace-read authority as operation list/get. A binding never broadens permissions, replaces request authentication, or allows cross-actor lookup. Operation and admission ownership remain enforced against the private actor.

## 4. Electron connection generation

### 4.1 Descriptor contract

Every newly produced `HermesConnection` descriptor gains required `connectionGeneration`, a positive JavaScript safe integer. Electron main owns a process-global monotonically increasing allocator. It is memory-only, nonsecret, never persisted, and never derived from connection contents.

Each cached live descriptor retains its generation. Any material descriptor reconstruction gets a fresh generation before the changed descriptor or a corresponding change event becomes observable to the renderer. A globally monotonic allocator prevents delete/recreate or connection-ID reuse from colliding with a still-retained supervisor record.

`connectionId` keeps its existing meaning:

- Registry-backed local, token, OAuth/cloud, URL, and SSH routes use their exact registry connection ID.
- A legacy primary route without a registry ID uses `null`; Desktop does not invent an ID from its URL, token, profile, or mode.
- `connectionGeneration` is required in new Electron descriptors for both cases.

Desktop's existing lifecycle scope changes from a required string connection ID and ambiguous `principal` field to the exact `LifecycleConnectionBinding` above. `principalBinding` is always the opaque capabilities value, never a username or credential.

### 4.2 What advances generation

Generation advances for every material route or authority change, including:

- endpoint/base URL, explicit headers, token, or authentication-mode change;
- OAuth login, logout, account/session replacement, or refreshed native session that reconstructs the connection descriptor;
- SSH host, user, port, key/path, remote profile, token, tunnel, or routed backend replacement;
- local or remote backend process replacement represented by a new native descriptor;
- connection apply/edit, deletion/recreation, registry replacement, or switch between legacy and registry routing.

The invalidation/change event must be published synchronously before asynchronous connection re-resolution can expose a changed descriptor. A late promise created under generation A cannot install or return itself as the current descriptor after generation B exists.

The renderer cannot enforce this boundary by notification timing alone. Each lifecycle V2 IPC request carries `expectedConnectionGeneration`; main validates it against its authoritative route generation after resolving the route and before network dispatch, then validates it again before returning the response. A mismatch rejects with the exact secret-free native sentinel `marketplace_connection_generation_changed` and returns no backend body. The Desktop helper maps only that sentinel to the closed local error code of the same name. It has no HTTP status and is a recoverable scope-transition signal: suspend the old binding, quarantine its presentation, and reprobe rather than classifying an operation terminal or its outcome unknown. This field is native routing metadata and is never forwarded to the backend. It is required for capabilities as well as every later lifecycle request.

Generation does not advance merely for:

- Marketplace dialog close, Workflows tab switch, route navigation, visibility change, or A-to-B-to-A foreground selection;
- polling cadence or QueryClient refetch;
- a transient WebSocket/network disconnect and reconnect that continues using the same unchanged descriptor.

A reconnect still requires a fresh authenticated capabilities probe. Descriptor equality alone never proves the backend actor or registry epoch stayed the same.

If the allocator reaches `Number.MAX_SAFE_INTEGER`, Electron synchronously invalidates all current descriptors and renderer lifecycle bindings, clears its descriptor cache, and restarts allocation at 1. No pre-rotation descriptor may remain current across that barrier. This is testable defensive behavior, not persistent wraparound.

## 5. Observation and supervisor binding

Desktop establishes a lifecycle binding in this order:

1. Resolve a `HermesConnection` and capture its `connectionId` and `connectionGeneration`.
2. Request lifecycle capabilities through that exact descriptor, supplying its generation as the native dispatch precondition.
3. Strictly decode the exact `profile`, `registry_epoch`, and `principal_binding`.
4. Before accepting the response, verify that the current native descriptor still has the captured ID and generation.
5. Create the existing module-private, identity-branded, memory-only clock observation under the complete binding.
6. Admit or reconcile operations only while every field remains equal.

`observeLifecycleClock` takes the full binding. `createLifecycleRequestId` requires the same current binding and invalidates its observation after any mismatch or failed use, preserving the already-approved monotonic-clock rules. Copying, serializing, reconstructing, logging, or persisting the observation remains forbidden and fails its private identity check.

Every API request captures the binding it started under. Get/cancel/list/admission responses must pass their existing exact operation/request/kind/subject/profile/epoch checks and the native pre-dispatch/pre-return generation checks. A delayed response from an old generation is discarded; it cannot update records, barriers, caches, dialogs, announcements, or focus in the new scope.

Capabilities is the only lifecycle V2 request that does not yet have an expected principal binding. Every subsequent V2 request carries `expectedMarketplacePrincipalBinding` over IPC. Electron validates the 64-lowercase-hex domain and maps it only for the exact lifecycle V2 route prefix to the `X-Hermes-Marketplace-Principal-Binding` request header; it never accepts arbitrary renderer-supplied headers. That header name is reserved case-insensitively: connection/configuration descriptor headers containing any casing of it are rejected at validation and stripped fail-closed at dispatch defense-in-depth. Electron injects exactly one native-owned value after sanitizing descriptor headers for token and OAuth/cookie transports, so merge order cannot override or duplicate it. The backend independently requires and validates that header as exactly 64 lowercase hexadecimal characters, authenticates the request normally, derives the actual binding from that request's exact authority/profile/process epoch, and compares it in constant time before list/get/state access, review-token retrieval, admission lookup, cancellation, or operation admission. Missing, malformed, uppercase, oversized, duplicated, or unequal values all return the same fixed `409 marketplace_principal_changed` with neither expected nor actual value. The header is a non-authorizing precondition, not a credential.

This per-request check closes native OAuth bearer refresh and cookie-session races: a request may proceed when refreshed credentials resolve to the same backend actor, but it cannot silently run as a different actor under an older capabilities observation. Main still applies the generation checks independently because the same actor does not prove the same native route.

## 6. Navigation, reconnect, and reconfiguration

### 6.1 Normal navigation

Dialog close, tab changes, route changes, and A-to-B-to-A navigation do not destroy supervisor records or advance the connection generation. The application-lifetime supervisor keeps token-free records and barriers. Dialog tokens and DOM/focus references remain ephemeral and are cleared according to the lifecycle recovery amendment.

### 6.2 Disconnect and unchanged-route reconnect

On disconnect, Desktop stops renderer polling and releases obsolete transport closures while the backend may continue. It retains only token-free records and barriers. On reconnect it immediately quarantines presentation of retained marketplace package/source/trust data for that scope, resolves the descriptor again, and performs a fresh capabilities probe. Quarantine means old data is not rendered or actionable during the probe window; it is not merely labeled stale.

If every binding field is unchanged, the supervisor may resume exact known request/operation polling and actor-scoped operation-list reconciliation. It never chooses an operation by recency, kind alone, package alone, or timestamp.

### 6.3 Material reconfiguration

Electron advances generation and Desktop immediately suspends the old binding before publishing changed route data. It cancels in-flight marketplace queries for the colliding `(connectionId, profile)` scope, quarantines settled marketplace package/source/trust data from presentation, and marks that scope unavailable/reconciling before the new descriptor can publish data. `marketplace_connection_generation_changed` or `marketplace_principal_changed` applies the same quarantine before reprobe. The new descriptor performs a fresh capabilities probe.

- If `connectionId`, `profile`, `principalBinding`, and `registryEpoch` are unchanged, the supervisor may rebind its exact known request/operation records to the new generation only after exact admission/list/get reconciliation. The same-principal/epoch proof may lift presentation quarantine and show settled cache as “last observed,” but actions remain gated until post-binding reconciliation succeeds. This supports a harmless descriptor reconstruction without duplicating work.
- If `principalBinding` or `registryEpoch` differs, Desktop never adopts old records into the new authority. Before accepting or publishing new-scope query data, it atomically cancels and removes/resets every marketplace query under the colliding `(connectionId, profile)` cache root, clears secrets, and keeps the old historical outcome unresolved. Any user-visible current package fact comes from a fresh new-scope package-state read. This privacy purge is binding-transition handling; a late old operation or response has no authority to invalidate or purge the new actor's cache.
- If capabilities cannot be authenticated or validated, lifecycle mutation controls remain disabled. A transport failure is not evidence that the old operation failed or never started.

An unexpected `401`, `403`, `marketplace_principal_changed`, or operation/admission not-found while supervising active or possibly admitted work triggers a capabilities reprobe before Desktop classifies eviction or terminal loss. The route may have changed actor or epoch. A generation change while capabilities or a request is in flight discards that response and retries only from a fresh binding when replay rules make retry safe.

```mermaid
sequenceDiagram
  participant E as Electron
  participant S as Application supervisor
  participant O as Old backend/actor
  participant N as New backend/actor
  S->>E: Resolve connection ID C, generation 41
  S->>O: GET capabilities, expected generation 41
  E->>E: Apply auth/config change; allocate generation 42
  E-->>S: Invalidate 41 before publishing descriptor 42
  O-->>E: Late capabilities/operation response
  E-->>S: Reject before IPC return because 41 is obsolete
  S->>E: Resolve C, generation 42
  S->>N: GET capabilities, expected generation 42
  N-->>S: profile + epoch + principal binding
  alt Same principal binding and epoch
    S->>N: Reconcile exact IDs with expected principal binding
  else Principal or epoch changed
    S->>S: Do not adopt old operations or invalidate new-scope data
    S->>N: Read authoritative current package state
  end
```

```mermaid
sequenceDiagram
  participant V as Marketplace view
  participant S as Application supervisor
  participant B as Backend
  V->>S: Start exact request R under binding K
  S->>B: POST R
  B--xS: Response lost after possible admission
  V->>V: Navigate away; clear dialog token
  S->>S: Retain token-free R and barrier under K
  V->>S: Return after unchanged-route reconnect
  S->>B: Fresh capabilities probe
  B-->>S: Same principal binding and epoch K
  S->>B: GET admission R, then exact operation O
  B-->>S: Correlated terminal operation
  S->>B: GET authoritative package state
  B-->>S: Installed version/trust/recovery health
  S-->>V: Truthful result; release barrier when reconciliation succeeds
```

## 7. Lifetime and user-visible truth

| Boundary | Binding/supervisor behavior | User-visible consequence |
| --- | --- | --- |
| Dialog or view navigation | Full binding and token-free records survive | Work continues without keeping a dialog open |
| Temporary disconnect, same descriptor | Polling pauses and old presentation is quarantined; fresh capabilities must reproduce the binding | Old data stays hidden until exact authority confirmation; then work may resume |
| Same ID, material configuration/auth change | Generation advances; old binding suspends, presentation quarantines, and colliding queries cancel immediately | Late old-route results cannot affect the new scope or reveal prior-actor data |
| Same actor/epoch after descriptor reconstruction | Exact records may rebind after exact lookup | No duplicate admission and no “latest” guess |
| Different actor or epoch | No old-record adoption; colliding cache is privacy-purged before new publication | State is shown as unconfirmed until the new scope is read |
| Renderer/application restart | Desktop binding, records, observations, barriers, and tokens are lost | Remote backend work may continue; fresh actor-scoped list/state rebuilds only what backend still retains |
| Backend registry/process restart | Epoch, binding key, process operations, receipts, and vault change/disappear | Historical operation outcome can be unknown; durable package/journal state is re-read |

“Outcome unknown” means Desktop cannot safely tell whether a requested mutation committed. It does not mean failure or rollback. The UI disables contradictory actions, states that package state could not be confirmed, and requests reconciliation or recovery. It may claim a version is installed, absent, trusted, or unchanged only when a correlated terminal result and/or the authoritative current package-state endpoint proves that exact fact under the active binding, as specified by the lifecycle outcome and cache-barrier tables.

## 8. Security and data handling

`principal_binding` and `connectionGeneration` are safe, nonsecret correlation values. They may exist in decoded capabilities, the dedicated expected-binding IPC fields/header, transient API request context, and memory-only supervisor keys. They must not enter URL path/query data, analytics, telemetry, user-facing errors, local storage, persisted Nanostores, QueryClient keys, workflow/package files, source records, or operation public subjects.

The private registry HMAC key, private actor string, authority binding, usernames, credentials, tokens, headers, SSH material, confirmation bodies, and derived credential fingerprints never enter the public binding. Logs may identify only existing safe connection/profile labels and operation/request IDs according to current sanitization rules; they must not add either private derivation input.

Changing a public binding value grants no authority. Every backend request is independently authenticated and actor-scoped. Equality only decides whether Desktop may correlate two already-authorized observations.

## 9. Compatibility and migration

Lifecycle V2 is unreleased on this isolated feature branch, so this amendment selects a coordinated strict V2 change rather than adding a V3 or an optional ambiguous field. The Python capability model, generated fixtures/types, Electron descriptor, renderer global types, and Desktop decoder ship together.

- New Desktop with a legacy Electron descriptor lacking `connectionGeneration` disables V2 mutation/supervision with upgrade guidance. It may retain existing browsing and source-management behavior.
- New Desktop with a backend capability response lacking `principal_binding` treats strict V2 lifecycle as unsupported/invalid and disables mutation. It must not synthesize a binding. Existing V1 browse/source behavior remains subject to its current feature detection.
- Old Desktop's strict V2 decoder may reject the new required capability field. Because V2 has not shipped, the branch requires a coordinated Hermes release rather than reverse compatibility for this internal preview contract.
- No installed-package layout, source cache, trust store, transaction journal, connection-registry persistence schema, or Workflow Studio authoring/package contract changes.

If implementation discovers a supported deployment mode that cannot produce an exact native generation or backend binding, it stops and amends this design. It must not downgrade to a credential hash, SSH identity, gateway activation count, timestamp, or latest-operation heuristic.

## 10. Test matrix

Tests use behavior assertions, backend-generated fixtures where applicable, deterministic injected entropy/clocks, real route adapters, and existing temporary Git repositories. User-visible tests assert truthful copy and action safety, not merely callback counts.

| Boundary | Required RED/GREEN proof |
| --- | --- |
| Backend derivation | Same actor/profile/epoch/key is stable; different actor, profile, epoch, or key differs; exact 64-hex domain; no derivation inputs in output/repr/log/error |
| Backend capabilities | Authorized real route publishes exact binding; malformed producer cannot emit 200; auth failures disclose no binding; list/get ownership agrees with binding equivalence |
| Generated parity | Python-generated valid/invalid capabilities flow through the real TypeScript decoder with exact agreement; closed-object and missing/uppercase/length mutations |
| Every connection mode | Local, legacy, token, OAuth/cloud, URL, SSH, and routed variants include the correct ID/null and a positive safe generation |
| Generation stability | Cached unchanged descriptor is stable; navigation, visibility, polling, and transient unchanged-route reconnect do not advance it |
| Generation invalidation | Every listed endpoint/auth/OAuth/SSH/backend/edit/delete/recreate change advances before renderer publication; late promises cannot become current |
| Native dispatch race | Capabilities and later V2 calls reject generation mismatch with exact local `marketplace_connection_generation_changed` both before fetch and before IPC return; expected generation never reaches HTTP; supervisor reprobes without terminal/outcome claims |
| Defensive rollover | Global invalidation barrier clears all current descriptors before allocator restarts; no retained supervisor binding collides |
| Capabilities race | Generation change before response acceptance rejects the response and branded clock observation; no POST occurs from stale observation |
| Per-request actor precondition | Missing, malformed, uppercase, oversized, duplicated, or unequal binding returns fixed `409 marketplace_principal_changed` before read/admission/cancel/token access; OAuth bearer/cookie change to same actor succeeds and different actor cannot act under the old observation |
| Reserved header ownership | Exact/lower/mixed-case descriptor collisions are rejected and stripped; token and OAuth/cookie dispatch each send exactly one native-owned expected-binding header after sanitization |
| Compatibility | Missing generation or principal binding disables V2 lifecycle without synthetic fallback; browse/source controls retain their documented compatibility |
| Supervisor rebind | Same ID/profile/principal/epoch under a new generation reattaches only exact known request/operation IDs after lookup |
| Authority isolation | Changed principal or epoch never adopts old operations, announces old completion, releases new-scope barriers, or invalidates new-scope cache |
| Cache privacy transition | Reconfiguration cancels old requests; changed principal/epoch removes/resets the colliding marketplace cache before new publication; a late old result cannot purge or repopulate it |
| Presentation quarantine | Reconnect, reconfiguration, native generation mismatch, and principal mismatch render no old package/source/trust data during capabilities probe; same binding restores last-observed display, changed binding purges before render |
| Reauthentication | Token rotation, OAuth logout/login/account change, saved config apply, SSH change, and local backend restart exercise the exact rules above |
| Reconnect/status loss | Disconnect pauses; reconnect reprobes; `401`, `403`, principal-changed, and not-found reprobe before eviction/outcome classification |
| Navigation/concurrency | Close, tab switch, A-to-B-to-A, delayed old-generation response, and concurrent exact operations preserve origin scope and bounded polling |
| Secret handling | New binding fields, supervisor records, generated fixtures, logs, errors, queries, URLs, and persistence contain no token/header/credential/private actor/HMAC key; existing transport descriptors may continue to hold their already-scoped credentials |
| User-visible truth | Unknown authority/outcome disables contradictory actions and never claims rollback or a version; verified current state permits exact copy and barrier release |

Python evidence runs through `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh`. Desktop tasks run focused Vitest/transport suites, TypeScript compilation, zero-warning lint, formatting, generated-artifact checks, and `git diff --check`, followed by the plan's broader gates. Each implementation task receives a fresh independent reviewer; no production fix is accepted solely on its implementer's tests.

## 11. Revised implementation boundary

After this written amendment is approved, the plan will split the stopped Task 14C1 before resuming it:

1. **Backend principal binding:** process-epoch binding authority, strict model/capabilities derivation, per-request expected-binding precondition, and Python route/security tests. Generated Desktop parity is an explicit downstream handoff, not silently accepted drift.
2. **Electron and Desktop binding parity:** connection-generation allocator and invalidation events, native pre-dispatch/pre-return checks, dedicated expected-binding IPC/header mapping, renderer descriptor/type changes, regenerated capability artifacts, strict decoder/clock binding, cache privacy transition, compatibility behavior, and native/TypeScript tests.
3. **Application operation supervisor:** restart Task 14C1 using only the two reviewed authorities, then proceed to cache barriers and the remaining lifecycle tasks in the approved sequence.

Each unit starts from a clean accepted base, follows test-driven development, records its evidence in the existing SDD ledger, and receives a fresh reviewer before the next dependent unit. Finding another missing authority or correlation fact stops dependent implementation and returns to design amendment rather than adding a local heuristic.

## 12. Rejected alternatives

| Alternative | Why it is not selected |
| --- | --- |
| Signed per-session handshake carried on every request | Could bind the route, but adds a new stateful header/session protocol, rotation rules, and authorization surface when backend actor equality plus native route generation is sufficient |
| Public hash of username, authority binding, token, headers, URL, or credential bundle | Leaks stable correlation or enables guessing, mishandles rotation/equivalence, and makes Desktop reproduce backend authorization identity |
| SSH `remoteIdentity`, install ID, registry schema version, or gateway activation epoch | Incomplete across modes or represents ownership/schema/navigation rather than authenticated route generation |
| Renderer-computed connection fingerprint | Lets a less-authoritative layer decide which fields are material and risks secrets, normalization differences, and missed native session changes |
| Always discard work on any reconnect | Safe but loses deterministic same-actor navigation/recovery and does not solve lost-response admission correlation |
| Adopt the latest operation with matching kind/package | Can attach another tab, client, request, profile, or actor's work and is forbidden by the lifecycle recovery contract |
