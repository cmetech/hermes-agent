# Workflow Marketplace Lifecycle Recovery Amendment

**Date:** 2026-09-04

**Status:** Approved by the user on 2026-09-04 for implementation in the existing Hermes worktree. This approval does not authorize merge, push, publication, release, worktree deletion, or Workflow Studio changes.

**Baseline:** `c89f36c6b8b23c430432b947e3b4f8417eb974a5`, branch `feat/workflow-package-marketplace`.

**Amends:** [approved marketplace design](2026-09-03-workflow-package-marketplace-design.md), sections 12–20.

**Execution:** [replacement remaining-work plan](../plans/2026-09-04-workflow-marketplace-lifecycle-recovery.md).

## 1. Decision and authority

Backend admission, transaction evidence, installed bytes/provenance, and trust are authoritative. Desktop owns observation, navigation, ephemeral review secrets, and barriers against using obsolete cached facts. A successful POST proves admission only. A failed request does not prove non-admission. An operation failure does not prove rollback.

This draft replaces the original unconditional statements that failed installation leaves the prior state unchanged, that replacement failure always restores it, and that the UI can always identify the installed version after failure. The backend must attempt recovery, but failed recovery is a supported outcome. The old Task 14 brief's unconditional “nothing installed,” “version remains,” and invalidation-only instructions are superseded on approval.

Tasks 1–13 remain completed. Preserve `c89f36c6b8`, including its independently reviewed Task 13 U+FEFF fix. Refactor its lifecycle code through subsequent commits. Task 14 is not review-clean; Task 15 has not started. No Studio edits, merge, push, publication, or worktree deletion are authorized.

### Alternatives considered

| Approach | Benefit | Cost / decision |
| --- | --- | --- |
| Bounded backend admission receipts, application supervisor, explicit unknown outcomes | Extends existing operations and journals; deterministic navigation and lost-response recovery | Selected; bounded restart/history guarantees must be visible |
| Durable operation database and resumable workers | Could preserve operation history across backend restart | Broader job infrastructure and secret/recovery migration; unnecessary for this release |
| View-local polling plus refetch | Smallest edit | Cannot correlate lost admissions or enforce navigation-wide action barriers; insufficient |

## 2. Evidence and additional contract gaps

The archived Task 14 diff and current implementation show:

- `use-package-lifecycle.tsx`: `operationFailureKind`, `admit`, and dialog projections collapse uncertain mutations into failure; `acceptOperation` lacks requested-ID equality; controller cleanup destroys supervision; update-check guards can remain set; single-workflow grants expect a subset result.
- Install/removal dialogs append unchanged-version copy to status loss, eviction, and generic failure. `invalidateOriginTruth` is foreground-generation-gated and invalidates without a mutation barrier.
- `transactions.py::atomic_install` can raise `transaction_rollback_failed` after committing candidate provenance/trust revocation. `test_marketplace_transactions.py::test_post_trust_commit_failure_never_rolls_back_package_or_provenance` asserts that candidate 2.0.0 remains installed while the journal requires recovery.
- `service.py::grant_trust` returns `workflow_trust(identity)` for the entire package after applying selected grants; this final read currently occurs outside the mutation lock. A post-write projection error or competing mutation can obscure a committed grant.
- The registry retains bounded process-local results (default 128 terminal results, one hour); no admission ID exists. Only refresh exposes safe subject identity. Backend kind/result/phase constraints are weaker than Desktop's union.
- Marketplace's outer Escape handler ignores `defaultPrevented`; trust review omits the provided `trust_state`; post-install trust is not capability-gated; each resolved poll cadence retains an abort listener.

Additional design issues recorded here: token-bearing preparation results must not become shared supervisor/query history; trust commit evidence must be captured inside its serialization boundary; installed provenance alone cannot certify package/recovery health; offset pagination can skip operations while admissions/evictions change the list; and the existing transaction recovery primitive lacks a marketplace CLI entry point. These receive explicit decisions below, not local UI inference.

## 3. Versioned public operation contract

Add `/api/plugins/workflow/marketplace/lifecycle/v2`. Its capabilities endpoint is a separate probe, leaving the existing strict capabilities response shape intact. It returns `schema_version: 2`, exact `profile`, `registry_epoch`, canonical UTC `server_time`, and capabilities `operations`, `admission_replay`, `package_state`, `transactions`, `updates`, `trust`, `sources`, `inspect` as supported. No lifecycle action is enabled until all capabilities needed by that action are available. Authentication/network errors are not interpreted as an old backend.

V2 uses snake_case wire keys consistently. Existing manifest/index/digest artifacts remain version one and unchanged. Every new object rejects extra keys, booleans masquerading as integers, noncanonical strings, duplicate JSON keys, nonfinite numbers, and oversized collections. Reuse existing package/source/workflow/diagnostic validators and budgets.

```typescript
type Subject =
  | { type: 'source'; source_name: SourceName }
  | { type: 'package'; identity: PackageIdentity }
  | { type: 'all_packages' }
  | { type: 'direct_install'; source_key: SourceKey; selector_id: Hex64 }

interface PackageIdentity { source_key: SourceKey; package_id: PackageId }
type TrustSelection = { type: 'all' } | { type: 'one'; workflow_name: WorkflowName }
```

`SourceName`, `SourceKey`, `PackageId`, and `WorkflowName` use the existing exact backend domains, not display names. `Hex64` is exactly 64 lowercase hexadecimal characters. Direct-install `selector_id` is backend HMAC-SHA256 under an epoch-local secret over the validated canonical repository/ref/path tuple; it exposes no URL, path, token, or reversible internal target. `source_key` uses the existing credential-free source identity. Direct preparation is available to API clients, but this release adds no Desktop direct-install form. The resolved package identity appears in its review and is used by confirm; an unresolved direct subject never authorizes a package mutation.

The generic registry `target` remains private. Construct `subject` from validated route inputs or confirmed token metadata, never by splitting an arbitrary internal target string. Confirmation must reject invalid/expired/foreign tokens before creating an operation with a fabricated subject.

| Kind | Allowed subject | Successful result discriminator | Allowed running phases |
| --- | --- | --- | --- |
| `refresh` | source | `source_refresh` | running, fetching |
| `inspect` | package | `package_detail` | running, fetching, reviewing |
| `update_check` | package or all_packages | `update_checks` | running, fetching, reviewing |
| `install_prepare` | package or direct_install | `install_review` | running, fetching, reviewing |
| `update_prepare` | package | `update_review` | running, fetching, reviewing |
| `remove_prepare` | package | `remove_review` | running, reviewing |
| `trust_prepare` | package | `trust_review` | running, reviewing |
| `install_confirm` | package | `installed_package` | running, validating, committing, recovering |
| `update_confirm` | package | `updated_package` | running, validating, committing, recovering |
| `remove_confirm` | package | `removed_package` | running, validating, committing, recovering |
| `trust_confirm` | package | `trust_grant` | running, validating, committing |
| `trust_revoke` | package | `trust_revoke` | running, validating, committing |

Every operation has exactly: `schema_version`, `id`, `registry_epoch`, `request_id`, `kind`, `subject`, `selection`, `profile`, `state`, `phase`, `progress`, `created_at`, `started_at`, `updated_at`, `finished_at`, `result`, `error`, `outcome`. Selection is required for trust kinds and null otherwise. Preserve the existing valid operation-ID domain. `registry_epoch` is 32 lowercase hex characters, random once per backend API process; it must not change on profile cache retirement. Request IDs embed that epoch (§4). Actor identity is enforced server-side and never projected.

Pending uses `queued`, progress 0, no start/finish/result/error/outcome. Running has a start time, progress 0–99, a phase allowed for its kind, and no finish/result/error/outcome. Success uses `completed`, progress 100, a matching result and outcome, and no error. Failure uses `failed`, no result, a safe error and an outcome. Cancellation uses `cancelled`, no result/error, and `cancelled_before_commit`. Existing canonical UTC and chronological timestamp checks apply. Terminal states never regress. `outcome` is null only while pending/running.

Successful package/trust mutations require a non-null verified package state matching their result; source refresh is the only committed outcome with null package state. Refresh results that did not publish a verified cache use known-unchanged. A mutation whose success cannot be projected safely is failed/outcome-unknown even if its side effects committed. Failed operations cannot carry committed outcomes; cancellation cannot be published after atomic entry merely because cancellation was requested. A trust-revoke success includes the complete actual trust snapshot and revoked count; independent grants can keep some entries trusted.

Result/subject checks apply at registry publication, API serialization, Desktop decoding, and supervisor acceptance. Package reviews/results must match both identity fields; refresh result source matches its subject (and legacy `source_name` where emitted); one-package update checks contain exactly that identity, all-package checks contain unique identities. Trust review/grant must match selection as well as identity. No kind, timestamp, sequence position, or “latest” matching substitutes for equality.

Get and cancel responses must match the requested operation ID in both the API helper and supervisor. The supervisor additionally requires captured connection generation, profile, epoch, request ID, kind, and subject. A mismatch cannot replace a watch; after a possibly mutating admission it produces `outcome_unknown`.

### Token-free history

V2 preparation results omit `confirmation_token`; use `confirmation_available: boolean` and `expires_at: UTC | null`. A successful unchanged update has false/null. Review facts and their digests remain immutable after publication; availability at retrieval is checked again.

`POST /operations/{id}/review-token` requires admin authority, exact actor/profile/epoch, a successful matching preparation, and a still-valid unused token. Body: `{review_digest, subject, selection}`. Response: `{operation_id, request_id, subject, selection, review_digest, confirmation_token, expires_at}`. It returns the same token while available, never extends its expiry or reauthorizes consumed bytes; unavailable/evicted tokens return `410 marketplace_review_unavailable`. Keep raw token only in a private backend in-memory vault bounded by retained preparations and existing expiry. Existing persisted stores continue keeping token hashes only. Never reconstruct a raw token from persisted state.

Explicit Review/Resume review may fetch this token; list/get/replay/supervisor never do so automatically. Desktop stores it in a dialog-owned secret ref, excludes it from stores, QueryClient data/keys, URLs, logs, analytics, DOM, persistence, and error objects, and clears it on close, selection/scope change, expiry, or terminal use. During confirm admission a transport closure may temporarily retain the exact submitted body for replay; it is discarded on admission/rejection/timeout or disconnect. Navigation clears the dialog token without losing the token-free admission record.

## 4. Admission and idempotency

Every V2 operation-starting request carries a client-generated `request_id` in its JSON envelope. Existing route-specific payloads are nested under `body`; confirm additionally supplies expected package `subject`, `selection`, `review_digest`, and `prepare_operation_id` beside its token. These are verified against backend authorization metadata. All starts, including source refresh and inspection, become POSTs under V2. Cancel and token retrieval create no operation and are intrinsically idempotent.

Request ID format: `wmreq_<epoch32>_<issued_ms13>_<random32>` (85 ASCII characters). `issued_ms13` is Unix milliseconds calculated using the capabilities server-time offset; `random32` is cryptographically random. IDs contain no user data or confirmation material. The embedded time is a replay-expiry bound, never an operation-matching heuristic.

Receipt key is `(backend epoch, canonical profile-home identity, authenticated actor, request_id)`. Never use only the public profile display name. Bound one receipt to the HTTP action/kind, canonical request body, expected subject and trust selection. Canonicalize validated JSON key order, not arbitrary string content. Fingerprint the bounded body with backend HMAC-SHA256; confirmation tokens may participate in this private fingerprint but neither they nor fingerprints enter public projections or disk. An exact replay is a lookup, not another token consumption or worker launch.

1. Authenticate and resolve profile; validate envelope/ID epoch and request shape.
2. Under the registry admission lock, look up the receipt **before** checking a consumed/expired token, active-target conflicts, or new-work capacity.
3. Existing matching receipt returns its original operation; changed kind/subject/body returns `409 marketplace_request_conflict` without revealing the original body. Cross-actor/profile lookups never expose a receipt.
4. With no receipt, accept first admission only within five minutes of the ID's embedded server-adjusted time (at most 30 seconds future skew). Otherwise return `409 marketplace_request_expired`. An old epoch returns `409 marketplace_epoch_changed` and never starts work.
5. Validate authorization metadata, reserve receipt and operation and existing conflict guards atomically, then enqueue exactly once. If submission fails, retain a terminal `known_unchanged` receipt; do not delete a reservation that a replay could re-admit.
6. Duplicate simultaneous requests share the reservation and return the same ID. A reused token with a different request ID cannot authorize a second commit; token consumption and transaction revalidation remain single-use.

Keep receipts for at least 24 hours from the embedded time and throughout any longer-running operation. Maximum 4,096 unexpired receipts per profile, shared across actors. At capacity reject new admission with `503 marketplace_admission_capacity`; exact replay still works. Never evict an unexpired receipt to make room. Full result retention keeps existing bounds (128 terminal results / one hour by default); eviction leaves a compact receipt tombstone with operation ID/kind/subject/selection and `outcome_unknown`, not a fictional failure. No raw body/token in tombstones. At 24-hour expiry a delayed replay cannot become new work because its embedded admission time is already too old. Document capacity and expiry; do not suggest restarting solely to bypass a barrier.

Admission retention remains inside the existing bounded profile-context capacity. A profile with unexpired receipts cannot be silently retired and reconstructed without them; reject new profile-context allocation when necessary until eligible expiry. Snapshot/token vault allocations also belong to that bounded context. Never preserve replay by introducing an unbounded map of formerly selected profiles.

`GET /admissions/{request_id}` returns a discriminated response: `{state:'found', operation}`, `{state:'evicted', operation_id, request_id, registry_epoch, profile, kind, subject, selection}`, or a strict scoped `404 marketplace_admission_not_found`. Expired/old-epoch requests use the errors above. A 404 is not proof that a delayed POST will never arrive. Retry the exact POST if its ephemeral body is still available; otherwise look up until admitted or its five-minute admission window has closed, then reconcile package state. Never create a fresh confirm request to discover whether the old one worked.

Backend restart loses operation workers, receipts, vault, and epoch. Old-epoch requests cannot replay into the new process. Persisted package/provenance/trust and existing ownership-checked journals survive. A new process must honor existing transaction locks/leases and report busy/unconfirmed until prior writers are quiescent and journals recovered. Epoch change alone does not prove an old remote process has stopped or that a mutation rolled back.

## 5. Explicit outcomes and authoritative local state

```typescript
type Outcome =
  | { type: 'committed'; package_state: PackageState | null }
  | { type: 'known_unchanged'; evidence: 'read_only' | 'before_mutation' | 'rollback_verified'; package_state: PackageState | null }
  | { type: 'cancelled_before_commit'; package_state: PackageState | null }
  | { type: 'recovery_required'; reason: 'rollback_failed' | 'recovery_ambiguous' | 'state_unverified' }
  | { type: 'outcome_unknown'; reason: 'terminal_invalid' | 'status_unavailable' | 'response_lost' | 'evicted' | 'backend_restarted' }
```

`committed` is allowed only for successful package/trust mutation (and source cache publication, with null package state). Successful inspection/check/preparation uses `known_unchanged/read_only`; its result is still meaningful (review/current/update_available/error/orphaned). A request admission response/error is separate from this terminal outcome. Transport-only unknown reasons are Desktop observations; the backend emits terminal-invalid unknown or a tombstone, not assertions about whether the renderer received a response.

Add read-only `GET /packages/{source_key}/{package_id}/state` under V2. It performs no Git or recovery writes. Under existing profile/transaction/trust lock order, read provenance, verify package bytes against provenance, classify full trust membership, and inspect relevant owned recovery journals. Return:

```typescript
interface PackageState {
  profile: string
  identity: PackageIdentity
  observed_at: UTC
  state: 'installed' | 'absent' | 'unconfirmed'
  installed: InstalledPackage | null
  trust: TrustSnapshot | null
  recovery: 'clear' | 'required' | 'unconfirmed'
  busy: boolean
}
interface TrustSnapshot {
  identity: PackageIdentity
  distribution_digest: Hex64
  workflows: Array<{ workflow_name: WorkflowName; definition_path: CanonicalPath; state: 'trusted' | 'untrusted' }>
}
```

Installed requires verified bytes, exact provenance, matching trust snapshot membership (up to 512 unique workflows), and recovery clear. Absent requires both destination and provenance absent, no outstanding recovery, and null installed/trust. Unconfirmed has null installed/trust and cannot assert a version; keep any cached version explicitly labeled last observed. `busy` reports intersecting pending/running work or a held writer lease without disclosing another actor's operation identity. Unverifiable locks/journals/bytes return unconfirmed, not empty installed state. Schema validation correlates all fields; `observed_at` is display evidence, not an ordering authority.

Successful package mutations capture their exact resulting state under the transaction boundary. Trust grant/revoke captures the full package trust snapshot inside the same lock as its write. A later failure to project/serialize cannot become “trust was not granted.” If commit evidence is unavailable, retain recovery-required/unknown truth and reconcile. `removed_package` describes what was removed; only a verified absent package state proves present absence.

### What proves unchanged

No existing diagnostic code **alone** proves that the previously displayed version still exists. The backend emits evidence at the point where it knows mutation did not begin or rollback completed; Desktop never creates evidence by classifying a catch-all exception.

| Codes / condition | Permitted evidence and limits |
| --- | --- |
| `confirmation_token_invalid`, `transaction_candidate_changed`, `transaction_review_changed`, `transaction_review_invalid`, `trust_review_changed`, `installed_package_changed`, `installed_package_not_found`, `installed_workflow_not_found` | `before_mutation` only when raised on the validated pre-write path; proves this attempt made no mutation, not that a cached prior version remains |
| `transaction_lock_timeout`, `trust_confirmation_lock_timeout`, rejected admission/capacity/conflict | No mutation by this new attempt when the backend proves no write occurred; another operation may be active; do not claim a version |
| Successful prepare/check/inspect, or their failure before mutation | `read_only`; preparing staging/token metadata does not install or grant trust |
| New `transaction_rollback_completed` | `rollback_verified` only after bytes, provenance, and actual trust state are re-read and recovery cleanup verified; include package state; retain sanitized original diagnostic internally |
| `transaction_rollback_failed`, `transaction_recovery_ambiguous` | Always `recovery_required`; never unchanged, even when candidate bytes appear installed |
| `transaction_state_write_failed`, `provenance_state_write_failed`, `marketplace_operation_failed`, unclassified exceptions | No code-wide unchanged allowance; instrument stage and proof, otherwise recovery-required after entering mutation or outcome-unknown |
| Cancel requested or network abort | No evidence. Only backend checkpoint before mutation permits `cancelled_before_commit`; cancellation during atomic work waits for its real terminal outcome |

“Version X remains installed” requires a verified package state matching the review's `old_version` (update) or `current_version` (removal). A different verified version uses “Currently installed: X.” Null proof uses “This attempt made no package changes,” with no version. Rollback of package bytes does not imply restoration of trust grants; show the actual trust snapshot. Existing generic failure and stale-card snapshots never supply proof.

### Outcome-to-copy table

| Evidence | User-visible copy | Available next action |
| --- | --- | --- |
| Committed install/update | “Installed version X” / “Updated to version X”; installation does not grant trust | Reconcile; separate Review trust only with capability and fresh state |
| Committed remove | “Package removal completed” | Reconcile absence; return to surviving package/list |
| Committed selected trust | “Trust granted for A”; display full actual trust map | Reconcile; no claim B was granted |
| Verified unchanged, matching review version | “Version X remains installed” | Fresh review, subject to barrier clearance |
| Known no mutation without current state proof | “This attempt made no package changes” / “This attempt granted no trust” | Refresh state |
| Cancelled before commit | “Cancelled before changes were committed” | Refresh state; no cached-version assertion |
| Verified rollback | “Changes were rolled back”; actual installed version and trust state | Refresh state |
| Recovery required | “State could not be confirmed. Package recovery is required” | Recovery guidance and Refresh state |
| Lost response/status/invalid response/eviction/restart | “State could not be confirmed. The operation may have completed” | Retry status / Recover request / Refresh state as applicable |
| Update check `error` | “Could not check for updates” | Retry check after guard release |
| Update check `orphaned` | “The installed package's source is unavailable” | Manage Sources; no automatic preparation |

### Lifecycle state-transition table

| Current observation | Event | Next state / required effect |
| --- | --- | --- |
| Idle | Explicit prepare/check | `admitting`; reserve synchronous intent guard and request ID |
| Admitting | Exact pending/running response | `watching`; bind immutable operation ID; release POST guard |
| Admitting | Lost/invalid POST response | `admission_unknown`; retain request correlation, release POST guard, keep mutation barrier |
| Admission unknown | Exact lookup or replay finds operation | `watching` or terminal; never launch another worker |
| Admission unknown | Not found, admission window still open | Stay unknown; offer exact recovery; no new confirm |
| Watching | Read/status error | `status_unknown`; release action guards, retain immutable watch/barrier; explicit Retry status |
| Watching | Exact terminal preparation | `review_available`, `current`, `check_error`, or `orphaned`; no mutation success |
| Review available | Explicit resume | Fetch ephemeral token; `review_ready` only after exact correlation |
| Review ready | Confirm | New request ID, exact review body; `admitting`; mutation barrier begins before POST |
| Any observed operation | Exact terminal mutation | Store outcome; release all action guards; invalidate origin and reconcile |
| Unknown | Eviction / changed epoch | Keep `outcome_unknown`; fence old admission, then read authoritative state |
| Reconciling | Failed or obsolete refetch | Remain blocked; show last observed metadata and Retry |
| Reconciling | Fresh verified state, no unresolved admission/active mutation | Actions use that state; terminal history stays historical |
| Any | Close/tab/route change | Detach view/token/focus references only; supervisor continues |
| Any | Disconnect/profile switch | Suspend unavailable scope polling, clear secrets; retain token-free records and barriers |
| Suspended | Reconnect / return to scope | Probe epoch, reconcile list and exact known IDs before actions |

Action guards cover individual network calls and are always released in `finally`. Operation barriers are separate explicit state, so releasing a guard cannot re-enable a contradictory mutation. An update-check polling error cannot strand Prepare again behind a boolean from the initial POST.

## 6. Application supervisor and navigation

Create a feature-owned, memory-only Nanostore supervisor at the application QueryClient lifetime, initialized once from `src/main.tsx`, above all Workflows views. Keep effect subscriptions idempotent under StrictMode. It owns scoped immutable records, bounded polling, admission lookup, cancellation, terminal observation, and reconciliation barriers. It does not own dialog tokens or DOM nodes. `use-package-lifecycle.tsx` becomes a thin dialog adapter; Installed and Marketplace consume the same supervisor.

Record key: `(connectionId, connection configuration generation, profile, registry_epoch, request_id)` plus exact operation ID once known. Generation is renderer routing protection, not a query parameter or backend profile identity. Reconfiguring the same connection ID invalidates its old transport binding and caches before any adoption. Requests always use captured explicit connection/profile; never fall back to current ambient scope. An old completion may invalidate only its origin cache, quietly, if that connection binding is still valid. It never announces, changes selection, or focuses the new foreground.

An authentication-principal change also advances the connection binding generation and clears prior actor observations. Reconnecting with the same principal preserves correlation; if that cannot be established through the existing connection/auth lifecycle, invalidate and perform a fresh actor-scoped scan. The renderer never invents an actor ID from a credential.

V2 `GET /operations` uses a bounded snapshot cursor, not live offset pagination. First page freezes the actor/profile-visible list (maximum existing registry bound: 1,088 records); page size at most 100; cursor opaque, random, actor/profile/epoch-bound, expires after 30 seconds. Retain at most four snapshots per profile and at most 16 MiB total snapshot data; reject capacity rather than truncate. Snapshot entries are token-free. Expired snapshot returns `410 marketplace_list_expired`; restart reconciliation once, then show Retry. Page metadata supplies `next_cursor` and `complete`; duplicate IDs or inconsistent epoch invalidate the scan. List failure/absence never means a known operation completed. Look up known request/operation IDs directly even if omitted from an expired scan.

On attach/reconnect, supervise every exact actor-owned active operation from the list. Unsolicited operations are observable work, not the user's current dialog intent: never auto-open a review or claim a “latest” result. For each package, active/uncertain mutations block conflicting actions. Multiple terminal operations are history; fetch current state rather than choosing a winner by time. Unknown direct preparation is shown as a direct preparation until its result supplies package identity.

Poll at most once per 500 ms per active visible operation, with at most three simultaneous renderer operation requests. Pause while the document is hidden or its connection is disconnected. Resume via list/get reconciliation on visibility/connection return. Background Workflows tab navigation alone does not stop application supervision. Stop automatic polling after a recoverable transport/status error; Retry is explicit. Clear the cadence timer AND abort listener on resolution, abort, error, and disposal. Release obsolete transport closures on disconnect. Limit renderer retained history to the backend result bound; never evict an unresolved barrier merely to meet the limit—disable new action admission and require reconciliation if capacity is exhausted.

```mermaid
sequenceDiagram
  participant V as Marketplace dialog
  participant S as Application supervisor
  participant B as Backend
  V->>S: Confirm reviewed package (ephemeral token)
  S->>S: Reserve request ID and package barrier
  S->>B: POST confirm(request ID, exact review)
  B->>B: Atomically reserve receipt and operation
  B--xS: Admission response lost
  S->>S: Admission unknown; retain safe correlation
  V->>V: Close or switch tab; discard dialog token
  S->>B: GET admissions/exact request ID
  B-->>S: Original operation and safe subject
  S->>B: GET operations/exact operation ID
  B-->>S: Terminal outcome
  S->>B: GET exact package state; refetch origin data
  B-->>S: Verified installed/absent/trust state
  V->>S: Return to Marketplace
  S-->>V: Current state or explicit unresolved barrier
```

```mermaid
sequenceDiagram
  participant U as User
  participant S as Application supervisor
  participant A as Origin connection/profile A
  participant B as Foreground connection/profile B
  S->>A: Start request R, operation O
  U->>S: Switch A to B
  S->>S: Clear A dialog secrets, retain A record/barrier
  S->>B: Fetch B in explicit scope
  A-->>S: O completes (if transport remains available)
  S->>S: Reconcile only A cache; no B announcement/focus
  U->>S: Return to A
  S->>A: Probe epoch; list + exact R/O lookup
  alt Same epoch, result available
    A-->>S: Correlated outcome
  else Restart or eviction
    A-->>S: Epoch changed / evicted
    S->>A: Verify current package state and journal health
    S->>S: Historical outcome stays unknown
  end
```

| Lifetime boundary | Survives | Does not survive |
| --- | --- | --- |
| Dialog close / Workflows tab or route navigation | Supervisor records, origin barriers, backend operations | Dialog tokens, selection-specific focus references |
| Profile/connection switch in one renderer | Safe origin records/barriers; backend work | Old foreground dialog/token; polling over unavailable transport |
| Renderer/application restart with remote backend alive | Backend retained operations/receipts; installed state/journals | Renderer intent history and all tokens; rebuild by exact backend list plus fresh state |
| App close with app-owned local serve backend | Installed state/journals | No guarantee workers survive local backend termination |
| Backend restart / receipt eviction | Installed state, provenance, trust, recoverable journals | Complete operation history and replay beyond documented bounds; historical result may remain unknown |

On fresh renderer startup, no lifecycle control is actionable until the current scope's operation scan and required local-state reconciliation succeed. This prevents a lost renderer barrier from reviving a stale cached action.

## 7. Cache mutation barrier

Choose one strategy: **package reconciling barrier**, shared by every lifecycle action consumer. Keep descriptive cache content, label it “Last observed” while blocked, and derive all actions/trust/version badges from verified post-barrier package state. Do not optimistically patch guessed membership or trust. A terminal outcome is immutable historical evidence and may be announced while the current package state is still reconciling.

Start the barrier before a potentially mutating POST, on discovery of an active mutation, or on uncertain outcome. Cancel affected in-flight queries and increment a local reconciliation generation. A result requested before that generation cannot clear the barrier or restore actionability, even if QueryClient accepts it. Invalidate affected origin queries. Release only after a fresh local-state read started after the last relevant terminal observation, no unresolved admissible request/active mutation, and successful refresh of each projection needed by the particular control. A failed Git detail refresh need not block Remove if verified local state is sufficient; it does keep Update-from-candidate disabled. Source search metadata can remain stale for browsing but never overrides local install/trust facts.

| Operation / outcome | Invalidate in originating scope | Barrier release and display |
| --- | --- | --- |
| Install success | Installed packages, all matching detail aliases, search pages, workflow catalog, trust snapshots, package state | Verified installed digest/version and actual trust; Install remains disabled during failure; no automatic trust |
| Update success | Same set | Verified new bytes/version/trust; old candidate/review becomes unusable; actual manual/other-origin trust may survive |
| Remove success | Same set | Verified absence; stale Remove/Update/Trust disabled; matching cached group is a non-actionable last-observed row until refreshed |
| Trust grant/revoke success | Same set, including entire package trust snapshot | Fresh complete trust state; do not display old untrusted/trusted as current; no subset patch |
| Rollback verified / cancelled / pre-write rejection after mutation intent | Same set | Fresh current state; no assumption earlier card or trust survived |
| Rollback failed / recovery ambiguous / unknown mutation outcome | Same set regardless of error/result validity | State unconfirmed; all package mutations disabled until admission is resolved/fenced and recovery clear |
| Read-only check/prepare failure or eviction | Review/check data and relevant local-state read | Release request guard; no mutation claim; safe re-prepare only after resolving a possibly admitted predecessor |
| Refresh source | Existing source/search invalidation | Persisted source status remains authority; refresh never rewrites installed state |

All matching detail aliases are found through exact decoded package identity, not display-name matching. Unknown aliases can be invalidated through that scope's detail prefix. Unbound loose workflow catalog rows cannot be heuristically joined to a package; when catalog/trust is being reconciled, gate catalog run/review entry points conservatively for that scope until fresh catalog truth returns. Already running workflows are not stopped. Existing backend runtime admission remains the final execution/trust authority.

Fresh package state can resolve **current** state after eviction without recovering historical success. Preserve copy such as “Earlier operation outcome is unavailable. Currently installed: X.” An admission not-found while its window is still open cannot be cleared by a read: the original POST may still arrive. After its window closes (or epoch changes), ensure no admitted work/lease remains, then use the locked local-state endpoint. Recovery-required journals never clear on a successful ordinary installed-list response alone.

## 8. Trust semantics

Trust selection is all workflows or exactly one canonical workflow name. The token binds that selection and the exact reviewed installed distribution/risk digests. A one-workflow review contains exactly the selected workflow's review, while an additional `package_workflows` inventory supplies all known `{workflow_name, definition_path}` pairs for result validation. An all-workflow review must match that entire inventory. Both are backend-derived from the same verified distribution; arbitrary subsets are unsupported.

V2 grant result value becomes `{identity, selection, distribution_digest, workflows}`. `workflows` is the **complete package trust map**, including definition paths, from the commit boundary. Required: unique exact known members, all members present, no unknown names/paths, selected A present and trusted. All selection requires all members trusted. Other known workflows may be trusted or untrusted. Replace the whole displayed snapshot after authoritative reconciliation; never infer successful grant solely from array length or “every entry trusted” for one selection.

Example package with workflows A and B (illustrative fixed digest):

```json
{
  "identity": {"source_key": "company", "package_id": "support"},
  "selection": {"type": "one", "workflow_name": "A"},
  "distribution_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "workflows": [
    {"workflow_name": "A", "definition_path": "workflows/a.yaml", "state": "trusted"},
    {"workflow_name": "B", "definition_path": "workflows/b.yaml", "state": "untrusted"}
  ]
}
```

This is successful. An all grant returns both trusted. A missing A, duplicate A, unknown C, wrong digest, wrong package, review for B instead of requested A, or an omitted B in the claimed complete map is invalid. After possible mutation invalid means outcome unknown, not “Trust was not granted.” Trust review renders each provided `trust_state`. Trust availability is independently capability-gated, including the post-install action. No revoke UI is added; backend revoke remains supported, receives the same outcome/barrier rules, and returns actual full trust state alongside its revoked count.

Installation/update never grants trust. Do not force every workflow to untrusted when independent matching grants remain valid; show “Installation does not grant trust,” and “Trust required” only for actual untrusted workflows.

## 9. Accessibility and recovery actions

Reuse the existing Desktop design-system primitives and tokens. The existing rich package review dialogs are retained as multi-step review surfaces; this amendment does not add a second generic confirmation primitive. Source deletion continues using the shared ConfirmDialog. The explicit lifecycle focus/cancel rules below specialize the review surface; ordinary ConfirmDialog behavior remains unchanged.

- One Escape performs one action. Dialog Escape always claims the event (`preventDefault` and appropriate propagation control); it dismisses once if allowed. Outer Marketplace/detail handlers return immediately for `defaultPrevented` or an owned modal event, including document/portal paths.
- During bounded confirm admission, Escape, outside click, and parent keyboard navigation cannot bypass the dialog lock. Use a finite 15-second admission transport timeout: timeout releases the UI lock into admission-unknown, never cancellation. Explicit global profile/navigation transitions detach presentation but retain supervision.
- Preparation remains closable; after admission the progress view remains closable. Close never means backend cancellation. Cancel waits for correlated terminal truth and stays truthful if commit wins.
- Focus starts on safe Close/Cancel, stays trapped while modal, and remains visibly indicated. On terminal changes, retain focus if its control survives; otherwise focus the dialog status heading. Closing returns to the captured origin only if connected, enabled, in the same scope/view, and the user has not navigated elsewhere. Fallback: surviving package action, package-list/search, then Workflows heading. Removal/package disappearance uses that fallback; background completion never focuses or auto-opens a dialog.
- Use polite status announcements for progress/terminal truth and a single alert for recoverable failure. Honor reduced motion, keyboard operation, RTL, 320px narrow layout, and 200% zoom. Read-only stale metadata remains readable with an explicit uncertainty label and a working Retry.

Refresh state invokes only the local-state read. Recovery guidance offers the new backend-side commands `hermes workflow package-state SOURCE_KEY/PACKAGE_ID --json` (read-only) and `hermes workflow recover-packages --yes --json` (explicit profile-wide ownership-checked recovery). Run them on the originating backend/profile using existing profile selection, not locally for a remote target. Recovery refuses active writer leases, never guesses ownership, and delegates to `recover_transactions()`; it does not expose raw staging paths through Desktop. Without `--yes`, show bounded affected identities and require interactive confirmation. Exit nonzero while any journal remains ambiguous. Do not label existing workflow `doctor NAME` as transaction recovery.

## 10. Migration and compatibility

**2026-09-05 approved clarification:** The user approved the [HTTP version compatibility addendum](2026-09-05-workflow-marketplace-http-version-compatibility.md), specifying reverse V1 observation of V2 operations and endpoint-version cancellation responses. Task 14A3b3 may resume. Accepted predecessor tasks and the rest of this approved amendment remain unchanged.

V2 is an API lifecycle revision, not a package-language revision. No changes to the Studio-consumed package contract bytes, installed layout, source cache schema, or legacy manual trust grants are needed for admission receipts. Receipts and raw review tokens are memory-only. Existing journals remain the durable recovery authority; any implementation need for a new persisted journal field requires an explicit versioned read-compatible amendment before coding it.

Keep existing read/source CRUD routes and older workflow catalog/run APIs. V1 refresh/inspect/update-check remain for read-oriented compatibility, backed by the tightened shared registry and safe subject construction; they receive server-generated receipt IDs and make no client replay promise. V2 list may observe them by exact operation identity but never attach them to a new Desktop intent. Existing source-refresh `source_name`/result correlation remains enforced.

The marketplace feature is unreleased on this isolated branch. Retire V1 install/update/remove/trust prepare/confirm/revoke routes with a bounded `409 marketplace_lifecycle_upgrade_required` **before admission**; do not keep an unsafe mutation fallback merely to preserve preview clients. New Desktop uses V2 for all operation starts; against V1-only backend it preserves browsing/source management but disables package/trust lifecycle controls with upgrade guidance. Existing supported CLI/service signatures keep their shape; their errors become truthfully structured without auto-replay. No existing loose workflow execution route is removed.

Backend restart loses V1 and V2 operation history alike. Source status still survives through persisted source records. Package state is reconstructed from bytes/provenance/trust/journals, never inferred from missing history. App restart reconnects to an already-running remote backend where available; local app-owned backend lifetime remains unchanged.

## 11. Test matrix and acceptance

Generate token-free fixtures from real Python public models and actual service/API scenarios in temporary Git repositories. Freeze clock/random test inputs, sanitize through the real projection, and include positive and deliberately invalid relationship fixtures with expected Python acceptance. TypeScript differential tests execute strict decoders on those exact JSON bytes. UI tests consume decoded fixtures; hand-written state transitions may select fixtures but must not bypass codecs with casts or fake subset trust maps.

| Requirement | Backend proof | Desktop / user-visible proof |
| --- | --- | --- |
| Subjects and strict union | Every kind × valid/invalid subject/result/phase; direct selector secrecy | Differential acceptance, exact requested-ID/subject/selection rejection |
| Admission replay | Concurrent same ID one worker/one token consumption; changed body/kind/subject rejected; cross actor/profile isolation | Lost POST response, lookup/replay, no duplicate prepare/confirm |
| Retention/restart | Receipt survives full-result eviction; expiry cannot re-admit; capacity; profile retirement; epoch rejection; lock/lease recovery | Evicted/unseen result and restart never become rollback copy |
| Supervision | Actor-scoped snapshot list with concurrent admissions/evictions and expired cursors | Close/reopen, tab/route switch, A→B→A, connection reconfiguration, hidden/resume, renderer restart |
| Outcome truth | Faults before write, after swap, after provenance, during rollback, after trust commit/cleanup | Transition/copy table tests assert absence of unsupported version/unchanged claims |
| Cache barrier | Coherent local-state bytes/provenance/trust/recovery classification | Each mutation success + failed refetch; old in-flight response; late old-scope terminal; stale rows non-actionable |
| Full trust map | Real A/B package; grant one/all; unknown/duplicate/missing member rejected; concurrent update and post-write failure | A trusted/B untrusted accepted; wrong selected review rejected; actual per-workflow state visible |
| Guards/cancel | Cancel before/during commit and idempotent cancel | Update-check error/orphaned, Retry after status loss, same-tick activation; no deadlocked booleans |
| Tokens | List/get/replay/log/error/persisted state exclude secrets; explicit token retrieval cannot extend/reissue | No token in DOM/query/store/URLs; close and scope switch clear references |
| Accessibility | No backend assertion needed | Portal/document/narrow Escape, confirm admission lock, focus success/failure/cancel/disappearance/navigation, RTL/reduced motion |
| Recovery tooling | Real temporary repos/homes/journals, refusal of active/foreign ownership | Guidance targets origin backend/profile; fresh state restores actions only after recovery |
| Regression | Existing contract, source sanitizer including U+FEFF, CLI/trust/discovery/admission, plugin Git behavior | Tasks 12–13 browsing, source CRUD/refresh-all, old backend feature detection |

Production acceptance requires RED/GREEN evidence plus a fresh reviewer after each revised task. Reviewers independently exercise the boundary the task changes. No fix is accepted only on its implementer's tests. Run the repository Python wrapper with retries disabled for evidence; broad gates and whole-branch review remain mandatory. Record unresolved gaps in the existing SDD ledger, amend this design, and stop dependent implementation instead of inventing correlation or recovery heuristics.
