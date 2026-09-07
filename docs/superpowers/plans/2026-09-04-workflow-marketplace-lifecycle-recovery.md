# Workflow Marketplace Lifecycle Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`. One implementation agent at a time; a fresh reviewer after every task. Use test-driven development and verification-before-completion. Steps use checkbox syntax. The user approved the lifecycle recovery design on 2026-09-04, strict-wire correction on 2026-09-05, supervisor identity-binding design on 2026-09-05, inspection digest parity amendment on 2026-09-06, and the inspection-admission design direction on 2026-09-07 for implementation in the existing Hermes worktree only.

**Goal:** Finish the existing marketplace branch with truthful, recoverable lifecycle operations and independently reviewed release evidence.

**Architecture:** Extend the backend operation registry with safe subjects, bounded admission receipts, strict outcomes, locked local-state reconciliation, and an epoch-scoped opaque actor binding. Electron owns a separate live native-route generation and enforces it before dispatch and response return. A feature-owned application supervisor binds both authorities, outlives Marketplace/Installed views, and retains no review secret. Shared cache barriers and identity-transition quarantine prevent stale or prior-actor projections from authorizing mutations or being disclosed. Inspection decoding preserves the backend's separate distribution and per-workflow trust digest domains; neither digest substitutes for supervisor-owned admission freshness. Read-only inspection is non-exclusive: an already-admitted inspection may finish across a later exact-package lifecycle operation, while an admitted lifecycle operation blocks every later inspection or lifecycle start until terminal.

**Tech Stack:** Existing Python/Pydantic/FastAPI/transaction locks and Git fixtures; React/TypeScript/Nanostores/TanStack Query/Vitest/Testing Library/Playwright. No new runtime dependency is planned.

**Spec:** [Lifecycle recovery amendment](../specs/2026-09-04-workflow-marketplace-lifecycle-recovery-amendment.md), [strict wire parity amendment](../specs/2026-09-05-workflow-marketplace-strict-wire-parity-amendment.md), [supervisor identity-binding amendment](../specs/2026-09-05-workflow-marketplace-supervisor-identity-binding-amendment.md), [inspection digest parity amendment](../specs/2026-09-06-workflow-marketplace-inspection-digest-parity-amendment.md), [inspection admission amendment](../specs/2026-09-07-workflow-marketplace-inspection-admission-amendment.md), plus the unaffected parts of the [original approved design](../specs/2026-09-03-workflow-package-marketplace-design.md).

## Global constraints

- Status: lifecycle recovery design approved on 2026-09-04; strict wire parity and supervisor identity-binding amendments approved on 2026-09-05; inspection digest parity approved on 2026-09-06. Task 14C2P is now authorized and still gates resumption of Task 14C2 and all dependent tasks. The smaller Task 14C0a/14C0b/revised-14C1 sequence below replaces the stopped original 14C1 attempt.
- Continue only in `/Users/coreyellis/Developer/personal/github.com/cmetech/hermes-agent/.worktrees/workflow-package-marketplace`, branch `feat/workflow-package-marketplace`.
- Baseline `c89f36c6b8b23c430432b947e3b4f8417eb974a5` is preserved. Do not revert or delete it. Tasks 1–13 are complete; their historical checkboxes are not a restart queue.
- This plan replaces remaining Task 14 and Task 15 execution in the September 3 plan. The work groups are contract, Desktop parity, supervisor identity authority, durable supervisor, package lifecycle, separate trust, accessibility, and Task 15. Contract and supervisor groups have smaller independent review gates below.
- Task 14B is review-clean at `eeba68e5c3521fa02a3cf6b5fb1cb56747c07b1f`. The stopped initial Task 14C1 attempt made no production/test change. Preserve its report and resume only through 14C0a and 14C0b.
- No Studio modifications, merge to `base`, push, publication, release, or worktree deletion without separate explicit approval.
- Backend owns admission, installed state, trust, and recovery evidence. Desktop never guesses operation identity or installed versions from timestamps, display names, or cached cards.
- Request IDs: `wmreq_<epoch32>_<issued_ms13>_<random32>`; first admission within five minutes, at most 30 seconds future skew; receipts retained at least 24 hours and while active; 4,096 unexpired receipts per profile; no early receipt eviction.
- Existing operation execution/result bounds remain; snapshot lists: at most 100 records/page, 1,088 records/snapshot, four snapshots/profile, 16 MiB aggregate, 30-second expiry.
- Visible polling: 500 ms, at most three simultaneous operation requests; pause hidden/disconnected; admission timeout 15 seconds. Remove cadence listeners and timers after every settlement.
- Tokens never enter shared state, query caches/keys, URLs, logs, DOM, or persistence. Token retrieval is explicit and actor/profile/review-bound.
- Python V2 public models are the sole wire-acceptance authority. Capabilities, evicted admissions, operation pages, and review tokens are strictly revalidated before HTTP publication; malformed values fail with a fixed safe 500 response, never malformed HTTP 200.
- V2 lifecycle relative paths are NFC, 1–1,024 code points, slash-separated, credential-free values with no control characters, U+2028/U+2029, leading/trailing slash, backslash, NUL, empty/dot/dot-dot/drive-prefix segment, or case-folded `.git` segment. V1 validators and package-contract bytes remain unchanged.
- Desktop request IDs use a scope/epoch-bound, memory-only server-clock observation. Monotonic elapsed time drives issuance; wall time only detects a negative, nonfinite, over-300-second, or greater-than-1-second divergent observation and forces capability revalidation before POST.
- Lifecycle supervision requires the exact memory-only binding `(connectionId|null, connectionGeneration, profile, principalBinding, registryEpoch)`. Electron generation proves a native descriptor; backend epoch/binding proves the authenticated marketplace actor/process. Neither substitutes for the other.
- Every lifecycle V2 IPC call carries a positive safe-integer `expectedConnectionGeneration`. Main rejects mismatch before network dispatch and before IPC return with exact local `marketplace_connection_generation_changed`; it never forwards generation over HTTP.
- Native generations are never reused within one Electron main-process lifetime. After issuing `Number.MAX_SAFE_INTEGER`, the next allocation atomically invalidates all bindings and permanently returns local `marketplace_connection_generation_exhausted`; no same-process reset/reprobe is allowed, and full application restart is the only recovery.
- Capabilities is the only V2 call without an expected principal. Every later V2 call carries a dedicated expected binding which Electron maps only to one native-owned `X-Hermes-Marketplace-Principal-Binding`. Every case-insensitive descriptor-header collision is rejected/stripped before injection. Backend exact-validates and constant-time compares it before access/admission; missing, malformed, duplicated, or unequal values return fixed `409 marketplace_principal_changed`.
- Reconnect, route reconfiguration, and either binding mismatch immediately quarantine retained Marketplace package/source/trust presentation. Same actor/epoch proof may restore it as last observed; changed actor/epoch atomically cancels and removes/resets the colliding `(connectionId, profile)` marketplace cache before new publication.
- Desktop retains only the amendment's closed backend lifecycle error-code set. Unknown, token-shaped, nested, or oversized error bodies become a fixed generic local error and never retain raw backend text.
- Keep package contract v1 artifacts byte-identical; preserve source sanitizer/U+FEFF parity, source CRUD/Refresh-all, and existing loose workflow/trust/run behavior.
- Keep package installation and trust separate. Destination requirements remain advisories; malformed/inconsistent package structures block.
- Use `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh` for Python evidence, never direct pytest. Run Desktop commands from `apps/desktop`; dependencies belong to the existing root workspace install.
- Every task requires observed RED, focused GREEN, fresh reviewer evidence, and ledger updates. No production fix is accepted only on implementer-authored tests. A new contract gap pauses dependent work for a recorded amendment.
- Inspection `package_digest` is the verified distribution digest; each `workflows[i].package_digest` is that workflow's effective marketplace trust digest and may differ from the root and siblings. Desktop preserves both domains exactly and never uses digest equality as freshness evidence.
- `inspect` is a non-exclusive exact-package observation. Earlier inspections may coexist with a later package lifecycle operation; an admitted lifecycle operation blocks new inspections and lifecycle starts until terminal. Backend and Desktop apply the same asymmetric matrix atomically. Late inspection results remain historical and cannot cross a newer package mutation generation.
- Update locales `ar`, `en`, `ja`, `zh`, and `zh-hant` before completion.

## Execution and review protocol

After approval, record its exact scope/date in the existing `.superpowers/sdd/2026-09-03-workflow-package-marketplace/progress.md`. Before each task, capture clean status, task-base SHA, and applicable instructions. Write `task-14a1-brief.md` / `task-14a1-report.md` (and corresponding task IDs below) in that directory; do not overwrite historical Task 13/14 reports.

Dispatch one implementation agent with the approved spec, task text, prior interface receipts, file ownership, and test commands. It writes failing behavior tests, observes the expected failure, implements the smallest change, observes passing checks, self-reviews, and commits. The controller then packages the exact base-to-head diff and sends it to a **fresh** reviewer. The reviewer checks spec compliance and code quality, independently reproduces at least one changed authority/race/failure boundary, and reports evidence. Reuse the implementer for verified fixes; use a fresh reviewer for acceptance. Do not begin a dependent task with open blocking findings. Record every finding, decision, RED/GREEN command, independent probe, changed SHA, and disposition in the ledger.

Documentation-only phase verification consists of scope/diff/link/consistency checks; no production pass is inferred from it. Implementation task commits below are new history. No agent is authorized to merge or publish.

## File and interface map

Existing implementations remain the starting point. New names below are deliberate plan interfaces, not claims that files already exist.

| File                                                                                                                                              | Responsibility / task                                                                                                                                                                                        |
| ------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `plugins/workflow/marketplace/lifecycle_models.py`                                                                                                | V2 Subject/Selection/Outcome/Operation/PackageState schemas and correlations, 14A1; V2-only lifecycle relative-path domain, 14B1                                                                             |
| `plugins/workflow/marketplace/admissions.py`                                                                                                      | Epoch/ID parsing, canonical private fingerprints, receipt lifetime/replay, 14A2; process-epoch principal-binding authority, 14C0a                                                                            |
| `plugins/workflow/marketplace/operations.py`                                                                                                      | Existing worker registry; strict publication/admission/listing, 14A1–A2; legacy terminal bridge and non-projecting active-package mutation query, 14A3b2; strict evicted/page/token envelopes, 14B1          |
| `plugins/workflow/marketplace/lifecycle_state.py`                                                                                                 | Locked package-state projection and domain outcome evidence, 14A3a                                                                                                                                           |
| `plugins/workflow/marketplace/service.py`, `transactions.py`, `plugins/workflow/trust.py`                                                         | Exact commit/rollback evidence, authoritative token metadata, full trust snapshot at the actual write boundary, 14A3a                                                                                        |
| `plugins/workflow/marketplace/catalog.py`, `source_store.py`, source portion of `lifecycle_state.py`                                              | Source-cache publication evidence, 14A3b1; preserve strict source diagnostics                                                                                                                                |
| `plugins/workflow/marketplace/lifecycle_api.py`                                                                                                   | V2 router, capability/token/admission/local-state endpoints, 14A3b3; strict capabilities and response-boundary revalidation, 14B1; expected-principal precondition on all post-capabilities V2 routes, 14C0a |
| `plugins/workflow/marketplace/api.py`                                                                                                             | Legacy read/source bridge and preview mutation retirement, 14A3b2; mount V2 and actual profile-context integration, 14A3b3                                                                                   |
| `plugins/workflow/marketplace/cli.py`                                                                                                             | Read-only package-state and explicit recovery adapters, 14A3c                                                                                                                                                |
| `scripts/generate_workflow_marketplace_lifecycle_fixtures.py`                                                                                     | Deterministic Python public-model/scenario corpus and check/write modes, 14B; generated domain/error descriptors, 14B1–B2                                                                                    |
| `tests/fixtures/workflow-marketplace-lifecycle-v2.json`                                                                                           | Token-free cross-language operation/state corpus, 14B; regenerated strict-wire corpus, 14B2                                                                                                                  |
| `tests/fixtures/workflow-marketplace-inspection-v1.json`                                                                                          | Generated real V1 admission/service/`get_legacy()` snake-case package-detail payload with distinct distribution/workflow digests, 14C2P                                                                      |
| `apps/desktop/src/types/workflow-marketplace-lifecycle.ts`                                                                                        | Strict generated V2 types/rules, 14B; regenerated parity, 14B2                                                                                                                                               |
| `apps/desktop/src/lib/workflow-marketplace-lifecycle-codec.ts`                                                                                    | Strict V2 decoding and cross-field checks, 14B; U+FEFF repository parity and closed error handling, 14B2                                                                                                     |
| `apps/desktop/src/lib/workflow-marketplace-codec.ts` / `.test.ts`                                                                                 | Existing V1 package-detail decoder; remove only the unsupported workflow-to-distribution digest equality and prove generated parity, 14C2P                                                                   |
| `apps/desktop/electron/main.ts`, `preload.ts`, native transport tests                                                                             | Process-global connection-generation allocation, invalidation-before-publication, and lifecycle IPC pre-dispatch/pre-return enforcement, 14C0b                                                               |
| `apps/desktop/src/global.d.ts`                                                                                                                    | Required descriptor generation plus dedicated lifecycle expected-generation/expected-binding IPC fields, 14C0b                                                                                               |
| `apps/desktop/src/api/workflow-marketplace-lifecycle.ts`                                                                                          | Scoped V2 API helpers and requested-ID enforcement, 14B; monotonic admission clock observation, 14B2; exact five-field binding and dedicated expected-binding transport, 14C0b                               |
| `apps/desktop/src/api/client.ts`, Marketplace query keys/cache adapter                                                                            | Binding-transition presentation quarantine and colliding-scope cancellation/purge seam, 14C0b; package mutation barriers remain 14C2                                                                         |
| `apps/desktop/src/store/workflow-marketplace-supervisor.ts`                                                                                       | Application-lifetime Nanostore records and public methods, 14C1                                                                                                                                              |
| `apps/desktop/src/lib/workflow-marketplace-supervision.ts`                                                                                        | Pure transitions, exact correlation, guard/poll scheduling, 14C1                                                                                                                                             |
| `apps/desktop/src/lib/workflow-marketplace-reconciliation.ts`                                                                                     | Query invalidation and generation/barrier policy, 14C2                                                                                                                                                       |
| `apps/desktop/src/main.tsx`                                                                                                                       | Initialize/dispose main-window supervisor alongside QueryClient, 14C1                                                                                                                                        |
| `apps/desktop/src/app/workflows/marketplace/use-package-lifecycle.tsx`                                                                            | Thin view/dialog adapter, 14D–E                                                                                                                                                                              |
| Existing Marketplace dialogs, `index.tsx`, `installed-packages.tsx`, `package-detail.tsx`, `query-keys.ts`, Workflows `index.tsx` / `catalog.tsx` | Shared supervisor/barrier consumers and keyboard/focus behavior, 14C2–F                                                                                                                                      |

Python public names: `LifecycleSubject`, `LifecycleSelection`, `LifecycleOutcome`, `LifecycleOperation`, `PackageState`, `TrustSnapshot`, `LifecycleAdmissionStore`, `read_package_state(service, identity)`, `create_lifecycle_router(context, verified_operator)`. Reuse existing `InstalledPackageIdentity`, result values, service signatures, and sanitized projections; V2 reviews omit token fields without changing CLI review objects.

Desktop names: `decodeLifecycleOperation(value)`, `decodePackageState(value)`, `startLifecycleOperation(intent, scope, requestId)`, `getLifecycleOperation(id, scope)`, `cancelLifecycleOperation(id, scope)`, `getLifecycleAdmission(requestId, scope)`, `listLifecycleOperations(cursor, scope)`, `getPackageState(identity, scope)`, `getLifecycleReviewToken(operation, scope)`. Lifecycle scope is `LifecycleConnectionBinding` with exact `connectionId: string|null`, positive safe-integer `connectionGeneration`, profile, 64-lowercase-hex `principalBinding`, and 32-lowercase-hex `registryEpoch`. All inputs/outputs use types in the lifecycle type module. Existing `@/hermes` barrel re-exports them.

Supervisor factory: `createMarketplaceSupervisor({api, queryClient, clock, visibility, connections})`. Public methods: `start(intent, scope)`, `retry(requestKey)`, `cancel(requestKey)`, `reconcileScope(scope)`, `reconcilePackage(scope, identity)`, `getPackageGate(scope, identity)`, `dispose()`, and `$records` read-only store. `intent` is a discriminated route-body union; sensitive confirm body exists only inside the admission transport closure. `PackageGate` is `{state: 'ready'|'busy'|'reconciling'|'unknown'|'recovery_required', packageState: PackageState|null}`. No token/DOM field is permitted on records/gates.

## Task 14A1 — Strict lifecycle schema and publication invariants

**Files:** Create `lifecycle_models.py` and `tests/plugins/workflow/test_marketplace_lifecycle_models.py`; modify `operations.py` and `test_marketplace_operations.py`. Keep HTTP route changes for 14A3.

**Consumes:** Existing result models/validators, the amendment §3 and §5 exact schema tables.

**Produces:** Strict V2 public types and a shared kind/result/phase table consumed by the registry and later API; no new persistence.

- [ ] Write parameterized acceptance/rejection tests for each kind, state, subject, selection, result, time/progress relation, and package-state invariant. Extend existing registry tests to reject mismatched results before publication.

```python
def test_confirm_cannot_publish_another_kind_result(operation_payloads):
    payload = operation_payloads.valid("update_confirm", "succeeded")
    payload["result"]["type"] = "installed_package"
    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)

def test_recovery_required_cannot_claim_verified_installed_state(state_payloads):
    payload = state_payloads.installed()
    payload["recovery"] = "required"
    with pytest.raises(ValidationError):
        PackageState.model_validate(payload)
```

Define test payload builders locally using existing valid review/package helpers; no loose casts. Include refresh source mismatch, one-workflow review mismatch, unknown/duplicate trust inventory, direct selector bounds, result/identity mismatch, and invalid outcome/state combinations.

- [ ] Run RED: `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_marketplace_lifecycle_models.py tests/plugins/workflow/test_marketplace_operations.py`. Expected new relationship tests fail for missing models or accepted invalid combinations.
- [ ] Implement frozen strict models, token-free V2 review projections, and exhaustive shared correlation table. Registry publication calls the validator; backend exceptions cannot publish an invalid terminal union. Preserve V1 public shape until 14A3 and existing refresh identity checks.

```python
def require_result_kind(kind, result):
    if result.type != RESULT_TYPE_BY_KIND[kind]:
        raise ValueError("operation kind/result mismatch")
```

- [ ] Run focused GREEN and existing operation/API tests; run Ruff check/format on touched Python files and `git diff --check`.
- [ ] Commit `feat(workflow): define lifecycle recovery contract`; fresh reviewer independently tests invalid state/result/subject combinations. Record acceptance before 14A2.

## Task 14A2 — Bounded idempotent admission and stable operation listing

**Files:** Create `admissions.py`, `tests/plugins/workflow/test_marketplace_admissions.py`; modify `operations.py`, `test_marketplace_operations.py`. Also modify `api.py`'s `_start` adapter and existing refresh/inspect/update-check callers, plus their `test_marketplace_api.py` coverage, solely to pass already-validated safe subject/body metadata for V1 receipt integration. New V2 routes and V1 mutation retirement remain in 14A3.

**Consumes:** 14A1 models and correlation table.

**Produces:** `LifecycleAdmissionStore.reserve(request_id, actor, profile_key, kind, canonical_body, subject, selection)` with found/new/conflict/expired outcomes; registry `start` integration, actor-scoped exact admission lookup, token-free snapshot pagination, and private token vault.

**Staged evidence boundary:** V2 workers supply an explicit typed result/outcome completion; the registry validates and publishes it but never invents mutation evidence from legacy V1 results. Vault insertion requires explicit authoritative expiry, review digest, subject/selection and unused-token validation; 14A3 supplies these from service/transaction internals. V1 read callers can pass safe subject/body and receive internal generated receipt IDs now, while their complete V2 terminal projection bridge belongs to 14A3. Until that bridge exists, a V2 list/get requiring unavailable legacy metadata/evidence must fail closed, not omit records, claim a complete scan, or synthesize success/unchanged. V2 routes are not exposed until 14A3. Test the mechanics through real registry workers supplying explicit typed completions; keep V1 routes functional in their existing shape.

- [ ] Write tests with injected clock/epoch/random and events controlling enqueue/worker execution. Verify concurrent replay, changed body/subject/kind/selection, replay before token revalidation, enqueue failure receipt, capacity, expiry after pruning, epoch change, cross-actor/profile isolation, and profile cache retirement.

```python
def test_replay_after_terminal_eviction_never_enqueues_again(admitted_confirm):
    first = admitted_confirm.start()
    admitted_confirm.finish_and_evict_result()
    replay = admitted_confirm.replay()
    assert replay.state == "evicted"
    assert replay.operation_id == first.id
    assert admitted_confirm.worker_invocations == 1
```

`admitted_confirm` is a new local fixture wrapping the real registry/store with a counted event-controlled worker; it does not replace transaction tests. Add snapshot-page tests where new operations finish/evict between pages and expiry/capacity do not silently truncate.

- [ ] Run RED: `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_marketplace_admissions.py tests/plugins/workflow/test_marketplace_operations.py`.
- [ ] Implement admission under the existing registry lock: receipt lookup → fingerprint comparison → new-request age/epoch/capacity check → reservation → one submission. Preserve unresolved receipt on scheduling failure. Retire full result to a tombstone, never re-admit. Keep epoch/admission data above disposable profile service caches. V1 read-oriented starts receive server-generated IDs internally.

```python
# Required ordering inside the admission lock:
receipt = admissions.lookup(actor, profile_key, request_id)
if receipt is not None:
    receipt.require_same_request(kind, canonical_body, subject, selection)
    return project_receipt(receipt)
admissions.require_new_request_window(request_id)
```

- [ ] Implement private review-token vault and immutable actor-scoped list snapshots with the amendment's budgets. Never persist HMACs, raw bodies, or tokens; existing transaction token hashes remain authoritative for consumption.
- [ ] Run GREEN plus operation/API regression tests, changed-file Ruff and diff check. Fresh reviewer independently races same-ID starts and eviction replay; inspect no token/private target in snapshots/errors. Commit `feat(workflow): correlate marketplace admissions` and record evidence.

## Backend integration checkpoints (replacement for monolithic 14A3)

14A3 is complete only after 14A3a, 14A3b, and 14A3c pass separate fresh review gates. This sequencing refinement does not change the approved public protocol, persistence, or recovery authority. Preserve the historical `task-14a3-brief.md` and inspection report. No new journal schema unless separately amended.

Complete the 14A2 staged interfaces before exposing V2 in 14A3b: authoritative typed worker outcomes, review-token expiry/digest/selection/unused checks, and V1 refresh/inspect/update-check terminal projections. Mixed legacy/V2 list coverage must be complete or fail closed; no missing-evidence placeholder may escape as truthful success. Read-only package knowledge cannot prove source-cache publication.

## Task 14A3a — Locked package state and domain evidence producers

**Files:** Create `plugins/workflow/marketplace/lifecycle_state.py` and `tests/plugins/workflow/test_marketplace_lifecycle_state.py`; modify `plugins/workflow/marketplace/service.py`, `plugins/workflow/marketplace/transactions.py`, `plugins/workflow/trust.py`, and existing `tests/plugins/workflow/test_marketplace_service.py`, `test_marketplace_transactions.py`, `test_marketplace_trust.py`, `test_trust_policy.py` as required by changed behavior. No route, registry, catalog/source-store, CLI, or Desktop edits in this checkpoint.

**Consumes:** 14A1 public models; 14A2 `LifecycleCompletion(state, result, error, outcome, review_token=None)` and `ReviewTokenMetadata(confirmation_token, expires_at, review_digest, subject, selection, validate_unused)` from `operations.py`; existing service methods and transaction/trust authorities.

**Produces:** These internal signatures in `lifecycle_state.py`, with existing service signatures/returns preserved:

```python
def read_package_state(service: WorkflowMarketplaceService,
                       identity: InstalledPackageIdentity) -> PackageState: ...

def complete_read(service: WorkflowMarketplaceService, *,
                  kind: Literal["inspect", "update_check", "install_prepare",
                                "update_prepare", "remove_prepare", "trust_prepare"],
                  subject: LifecycleSubject, selection: TrustSelection | None,
                  actor: str, call: Callable[[], object]) -> LifecycleCompletion: ...

def complete_mutation(service: WorkflowMarketplaceService, *,
                      kind: Literal["install_confirm", "update_confirm", "remove_confirm",
                                    "trust_confirm", "trust_revoke"],
                      subject: PackageSubject, selection: TrustSelection | None,
                      actor: str, call: Callable[[], object]) -> LifecycleCompletion: ...

def review_token_metadata(service: WorkflowMarketplaceService,
                          review: InstallReview | UpdateReview | RemoveReview | TrustReview,
                          *, actor: str, subject: LifecycleSubject,
                          selection: TrustSelection | None) -> ReviewTokenMetadata: ...
```

These are signature declarations, not implementation bodies. `read_package_state` owns strict bounded domain reads and lease evidence, without registry access; 14A3b composes intersecting active-registry work into public `busy`. A context-local evidence collector may preserve existing service returns, but must reset in `finally`, remain private, and accept evidence only at the actual domain boundary. No cross-worker evidence, registry callbacks inside domain locks, or generic-error-to-positive-outcome fallback.

- [ ] Extend real temporary-home/Git service fixtures into `lifecycle_domain`, a local test adapter over the actual service and these producers. The existing `test_post_trust_commit_failure_never_rolls_back_package_or_provenance` has already been reproduced at the inspection checkpoint; do not repeat it as claimed RED. Add failing outcome/state tests using its real `after_trust_revoke` fault seam:

```python
def test_cleanup_failure_has_no_verified_old_version(lifecycle_domain):
    lifecycle_domain.install_version("1.0.0")
    review = lifecycle_domain.prepare_update("2.0.0")
    completion = lifecycle_domain.confirm_with_fault(review, "after_trust_revoke")
    assert completion.outcome.type == "recovery_required"
    assert lifecycle_domain.read_installed_bytes().manifest.version == "2.0.0"
    state = read_package_state(lifecycle_domain.service, lifecycle_domain.identity)
    assert state.state == "unconfirmed"
    assert state.installed is None
```

- [ ] Add tests for verified rollback/absence, corrupt bytes/provenance/trust/journals, lease contention, bounded reads, update/remove failure, selected-A/full-A+B trust snapshots, concurrent manual trust writes and update/trust lock ordering. Test exact token actor/subject/selection/digest/expiry/unused authority and evidence isolation after exceptions across workers. Observe RED using `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_marketplace_lifecycle_state.py tests/plugins/workflow/test_marketplace_transactions.py tests/plugins/workflow/test_marketplace_service.py tests/plugins/workflow/test_marketplace_trust.py tests/plugins/workflow/test_trust_policy.py`.
- [ ] Implement locked state and explicit evidence emissions at transaction/service boundaries. `transaction_rollback_completed` requires verified bytes/provenance/full actual trust and clear recovery. `transaction_rollback_failed` and `transaction_recovery_ambiguous` always mean recovery-required, including read/preparation paths. Null proof cannot certify a cached version.
- [ ] Extend only the narrow trust read/write boundary needed to capture strict complete snapshots under the actual trust lock. Its file lock is not reentrant: never wrap existing locking methods in another acquisition. Service marketplace locking alone does not serialize manual trust writers. Corrupt trust must remain unconfirmed, not silently untrusted. Capture successful package mutation state before releasing transaction serialization; do not reconstruct it later from cached results.
- [ ] Implement token-free read/completion projection and metadata from real token authorities. Read-only outcomes apply only to the explicitly enumerated nonmutating package methods. Mutation completions consume typed evidence; missing evidence fails closed. Metadata cannot reissue/extend a token and must bind the exact review, actor, profile, subject, and selection.
- [ ] Run the RED command as GREEN, changed-file Ruff check/format, and `git diff --check`. Record exact producer/trust-boundary interface receipts. Commit `feat(workflow): capture authoritative lifecycle state`; fresh reviewer independently tests candidate-retained failure, full selected-trust map, and concurrent trust/state boundaries before 14A3b.

## Source/API integration checkpoints (replacement for monolithic 14A3b)

The unstarted 14A3b is split into three independently rejectable checkpoints after actual source/context inspection. This is implementation sequencing, not a protocol or persisted-format amendment. Preserve the historical broad b brief/report. 14A3b is accepted only when b1, b2 and b3 have separate independent receipts; V2 remains unmounted until b3 completes the accepted legacy bridge and actual context lifetime checks. No CLI or Desktop edits in these checkpoints.

## Task 14A3b1 — Source publication evidence and diagnostic regression closure

**Files:** Modify `plugins/workflow/marketplace/catalog.py`, `source_store.py`, and `lifecycle_state.py` only for source completion; corresponding `tests/plugins/workflow/test_marketplace_catalog.py`, `test_marketplace_sources.py`, and `test_marketplace_lifecycle_state.py`.

**Consumes:** Accepted strict V2 source result/outcome models and `LifecycleCompletion`; existing source-cache atomic replacement and strict persisted status projections.

**Produces:** `complete_source_refresh(service, *, source_name: str, call: Callable[[], SourceRefreshResult]) -> LifecycleCompletion`. The callable runs the actual existing synchronous service refresh. The producer binds exact source/store/execution identity, returns strict token-free source result and truthful outcome, and preserves existing service/catalog method signatures and returns. Private source publication evidence is captured at the real write boundary, never reconstructed from a returned state string or error code. Existing package/trust evidence is unchanged.

- [ ] Write real temporary source/Git/store tests for verified publication, disabled/cancelled-before-publication, retained stale catalog after fetch failure, failure before atomic replace, failure after possible replace, secondary status-write failure, and cancellation observed after cache publication. Assert persisted catalog/status facts and structured outcome, not callback counts.

```python
def test_uncertain_cache_publication_never_claims_unchanged(source_case):
    source_case.fail_after_catalog_replacement()
    completion = complete_source_refresh(
        source_case.service,
        source_name="company",
        call=lambda: source_case.service.refresh_source("company"),
    )
    assert completion.state == "failed"
    assert completion.outcome.type == "outcome_unknown"
    assert source_case.catalog_contains_candidate()
```

`source_case` uses the real source store with a temporary Git repository; inject faults only at the atomic filesystem boundary. The assertion requires uncertainty unless a strictly verified publication result can still be captured at the original serialization boundary. Do not infer rollback from failure of a later status write.

- [ ] Observe RED through `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_marketplace_catalog.py tests/plugins/workflow/test_marketplace_sources.py tests/plugins/workflow/test_marketplace_lifecycle_state.py --tb=short`. Keep pre-existing failures separate from new behavior RED.
- [ ] Implement the source completion producer and bounded private evidence capture, without holding a registry lock or transferring authority to another execution. Preserve uncertainty monotonically across fallback failure-status persistence; late cancellation cannot erase publication. Disabled/read-only or verified no-publication results use known-unchanged; success with verified cache publication uses committed/null package state. Invalid terminal projection fails closed.
- [ ] Independently check the two catalog regression assertions against the accepted canonical sanitizer. Resolve obsolete message expectations/raw SSH diagnostic fixtures in tests when supported by real persistence/projection behavior; never weaken production sanitization. Record exact baseline and final results.
- [ ] Run focused GREEN and the three-file gate, changed-file Ruff/format and diff check. Commit `feat(workflow): capture source publication evidence`; fresh reviewer independently injects before/after publication and fallback-status faults plus late cancellation, and verifies source-name correlation and diagnostic parity before b2.

## Task 14A3b2 — Legacy terminal bridge, preview mutation retirement and busy identity

**Files:** Modify `plugins/workflow/marketplace/api.py`, `operations.py`, and corresponding `tests/plugins/workflow/test_marketplace_api.py`, `test_marketplace_operations.py`. Accepted domain producers stay unchanged unless a separately reviewed defect requires repair.

**Consumes:** `complete_read` and accepted `complete_source_refresh`; strict legacy/V2 result models, admission receipts and snapshots. Preserve the public V1 source-refresh shape and safe source identity.

**Produces:** Complete dual V1/V2 projections for V1 refresh/inspect/update-check starts, V1 preview package/trust routes rejected before admission, and registry `intersects_active_mutation(identity: InstalledPackageIdentity) -> bool` (profile-wide and actor-nonprojecting). No V2 HTTP routes mounted yet.

- [ ] Write real worker/route tests for pending through terminal source refresh/inspect/update-check observed through both the existing V1 response and the internal strict V2 list/get. Include failed/cancelled outcomes and source publication followed by late cancellation. Test all preview mutation routes reject with bounded `409 marketplace_lifecycle_upgrade_required` before any receipt, operation, token consumption or domain write.

```python
def test_legacy_refresh_remains_visible_at_terminal(legacy_case):
    operation_id = legacy_case.post_refresh("company").json()["id"]
    legacy_case.wait_terminal(operation_id)
    legacy = legacy_case.get_operation(operation_id).json()
    lifecycle = legacy_case.registry.get_lifecycle(operation_id, actor=legacy_case.actor)
    assert legacy["source_name"] == "company"
    assert lifecycle.subject.source_name == "company"
    assert lifecycle.outcome.type == "committed"
```

`legacy_case` wraps the existing real authenticated API harness and temporary source fixtures. Use the actual registry's accepted get signature when adapting the harness; do not invent a V2 HTTP route early.

- [ ] Write event-controlled profile-wide busy tests: other-actor pending/running mutation for the same exact identity is busy; unrelated identity and read-only operations are not; no foreign operation identity appears in caller-visible lists. Observe RED with `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_marketplace_api.py tests/plugins/workflow/test_marketplace_operations.py --tb=short`.
- [ ] Adapt actual V1 workers to publish both strictly validated projections with authoritative outcomes; do not fake lifecycle mode, omit terminal records, or reinterpret post-publication cancellation. Preserve source CRUD/read and existing workflow/run behavior. Retire only unreleased V1 package/trust prepare/confirm/revoke routes before admission. Implement non-projecting active-mutation identity query under registry lock, with no domain callback there.
- [ ] Run focused GREEN and API/operation/source regression gates, changed-file Ruff/format and diff check. Commit `feat(workflow): bridge legacy lifecycle observations`; fresh reviewer independently verifies complete mixed terminal listing, pre-admission retirement/no writes and cross-actor busy isolation before b3.

## Task 14A3b3 — Authenticated V2 routes and actual context lifetime

**Approval recorded (2026-09-05):** The user approved the [HTTP version compatibility addendum](../specs/2026-09-05-workflow-marketplace-http-version-compatibility.md) after the pre-edit reverse V1→V2 policy pause. Task b3 may resume with the compatibility ownership and tests below. Accepted b1/b2 work is preserved.

**Approved compatibility ownership:** Also modify `operations.py` and `test_marketplace_operations.py` only for explicit versioned HTTP observations: `get_legacy(operation_id, *, actor) -> MarketplaceOperation`, `list_legacy(*, actor, offset, limit) -> tuple[MarketplaceOperation, ...]`, `cancel_legacy(operation_id, *, actor) -> MarketplaceOperation`, and `cancel_lifecycle(operation_id, *, actor) -> LifecycleOperation`. Reuse existing `get_lifecycle` and `list_snapshot`. Keep internal current methods compatible; share the existing cancellation primitive instead of duplicating state transitions. V1 lookup/version rejection must precede cancellation under the same registry lock. No new public/persisted fields.

- [ ] After addendum approval, write real HTTP mixed-version pagination/get/cancel tests. Interleave V2 records between V1 records; enumerate V1 pages and assert exact V1 IDs with no short-page omission. Assert V1 cancel against owned V2 work returns `409` with `detail.code == "marketplace_lifecycle_upgrade_required"` while its cancellation token and real profile bytes remain unchanged. Foreign IDs must remain 404. V2 cancel of a V1 read must decode as V2 with the exact requested ID.
- [ ] Observe compatibility RED through `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_marketplace_lifecycle_api.py tests/plugins/workflow/test_marketplace_api.py tests/plugins/workflow/test_marketplace_operations.py --tb=short`, then implement explicit endpoint-version selection and run focused GREEN. Include invalid eligible record fail-closed, token-free history, terminal truth, and independent reviewer probes in the existing b3 gate; do not add a lossy V2-to-V1 conversion.

**Files:** Create `plugins/workflow/marketplace/lifecycle_api.py`, `tests/plugins/workflow/test_marketplace_lifecycle_api.py`; modify marketplace `api.py` for router/context integration and `test_marketplace_api.py`. A narrow epoch-secret direct-selector helper may be added to `admissions.py` with `test_marketplace_admissions.py` if needed to avoid duplicating process identity authority; no persisted/public fields change.

**Consumes:** Accepted b1 source completion, b2 legacy bridge and `intersects_active_mutation`, a package/trust producers, A1 strict schemas and A2 replay/vault/snapshot authority.

**Produces:** `create_lifecycle_router(context, verified_operator)` and every amendment §§3–5 V2 route, exact direct-selector/request/token correlations, and real profile-context/service/vault/receipt lifetime integration.

- [ ] Build the V2 route test harness from actual temporary Git/service fixtures and authenticated context. Issue real route requests and wait for exact operation IDs. Add lost-response replay, wrong ID/subject/selection, expired/consumed token, cross-actor/profile, capability/authentication and complete snapshot listing with V1 and V2 records.

```python
def test_replay_after_lost_confirm_response_admits_only_once(lifecycle_api):
    request = lifecycle_api.reviewed_install_request()
    first_id = lifecycle_api.post_and_drop_response(request)
    replay = lifecycle_api.post(request)
    assert replay.operation.id == first_id
    assert lifecycle_api.count_admissions(request.request_id) == 1
    assert lifecycle_api.wait_for(first_id).outcome.type == "committed"

def test_selected_grant_returns_complete_map(lifecycle_api):
    operation = lifecycle_api.grant_selected("A", package_workflows=("A", "B"))
    states = {item.workflow_name: item.state
              for item in operation.outcome.package_state.trust.workflows}
    assert states == {"A": "trusted", "B": "untrusted"}
```

- [ ] Prove exact canonical repository/ref/path direct-selector binding to the returned review and resolved package identity through confirm. Prove same-epoch actual profile-context/service lifetime with live receipts, immutable issued-review fingerprints, trust selection and token-vault callbacks; refuse context retirement at capacity rather than lose live authority or grow unbounded state. Missing recreated-service proof fails closed without token reissue or expiry extension; preserve legacy persisted-token confirmation. Read current accepted producer receipts, not original optimistic expectations.
- [ ] Add route-level candidate-retained rollback-failed/recovery-ambiguous, full selected-workflow trust map, exact operation get/cancel and stale/evicted admission tests. Run RED: `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_marketplace_lifecycle_api.py tests/plugins/workflow/test_marketplace_api.py --tb=short`.
- [ ] Wire exact route-body unions, capability probe, token retrieval, get/cancel/admission/list routes, safe subject construction and preparation/confirmation metadata. Replay lookup precedes token authorization/consumption. Require the accepted complete b2 bridge before mounting V2. No generic-target parsing or latest-operation heuristics.
- [ ] Compose state-reader lease evidence with `intersects_active_mutation` without holding a registry lock while entering domain locks. Return only a validated `PackageState`; active work from another actor affects `busy`, not visibility of that actor's operation. Test lock ordering with event-controlled real workers.
- [ ] Run focused GREEN, lifecycle/API/admission/operation/source regressions, changed-file Ruff/format and diff checks. Commit `feat(workflow): expose correlated lifecycle API`; fresh reviewer independently reproduces lost admitted response replay, actual context retention/recreation, direct-selector secrecy/binding, candidate-retained API outcome, selected trust map and mixed legacy/V2 listing. Record all b1/b2/b3 acceptance receipts before 14A3c; b is not complete from route happy paths alone.

## Task 14A3c — Recovery CLI and complete backend gates

**Files:** Modify `plugins/workflow/marketplace/cli.py` and `tests/plugins/workflow/test_marketplace_cli.py`; narrow CLI registration in `plugins/workflow/cli.py` only if required. No unrelated production fixes inside this gate; route/domain findings return to their owner with a fresh fix review.

**Internal ownership refinement (2026-09-05):** Also modify `transactions.py` and `test_marketplace_transactions.py` for a bounded read-only `MarketplaceTransactionStore.inspect_recovery()` summary and a shared private owned-abandoned-staging candidate iterator. Existing `list_journals()` exposes paths and `recover_transactions()` returns only recovered IDs; neither can alone supply truthful confirmation scope or prove complete recovery. The summary carries validated identities, transaction IDs/classifications, and active consumed-writer-lease/completeness facts, never paths or tokens. Reuse existing strict journal/prepared parsers and exact marker checks; distinguish live unused preparations from consumed writer leases. An incomplete/oversized scan fails closed, not clear or partially actionable. Preserve existing recovery signature, ownership/deletion rules and persisted schemas. This is internal implementation of approved recovery behavior, not a public protocol expansion.

- [ ] Write failing real temporary-store inspection tests: owned/foreign or malformed markers, abandoned owned staging, live preparation versus consumed lease, incomplete bounded scan, and exact no-write bytes. Extract only the existing staging candidate validation so cleanup and inspection share the same authority; do not introduce another ownership parser.
- [ ] Keep interactive confirmation outside the lock. After confirmation, reacquire the existing reentrant marketplace lock, re-inspect and compare the exact affected scope; changed/incomplete scope or active writer lease refuses recovery with no recovery writes. Delegate existing `recover_transactions()` and post-inspect while retaining that lock; unresolved journals or incomplete inspection produce nonzero exit even if some transaction IDs were recovered. `--yes` supplies explicit profile-wide confirmation but does not bypass ownership/lease/completeness checks.
- [ ] Observe helper RED using `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_marketplace_transactions.py tests/plugins/workflow/test_marketplace_cli.py --tb=short`, then focused GREEN. Include both helper and unchanged cleanup ownership controls in the existing final backend gate and fresh independent review. No real-user profile recovery is authorized.

**Consumes:** Accepted `read_package_state`, existing `recover_transactions()` ownership/lease checks, and accepted V2 domain/API behavior.

**Produces:** `hermes workflow package-state SOURCE_KEY/PACKAGE_ID --json` and `hermes workflow recover-packages --yes --json`, plus confirmation/exit behavior from amendment §9.

- [ ] Write real temporary-home/journal CLI tests for installed/absent/unconfirmed state, ambiguous recovery, active leases, unrelated directories, and two profiles. Use the existing CLI test runner rather than a mocked service result:

```python
def test_read_state_does_not_recover_owned_journal(cli_home):
    cli_home.create_ambiguous_owned_journal()
    before = cli_home.journal_bytes()
    result = cli_home.run("package-state", cli_home.identity_text, "--json")
    assert result.json["state"] == "unconfirmed"
    assert cli_home.journal_bytes() == before

def test_recovery_requires_confirmation(cli_home):
    before = cli_home.journal_bytes()
    cli_home.run_noninteractive("recover-packages", "--json")
    assert cli_home.journal_bytes() == before
```

`cli_home` adapts the actual CLI runner and owned temporary transaction fixtures; it never targets the user's profile. Observe RED: `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_marketplace_cli.py tests/plugins/workflow/test_cli.py`.

- [ ] Implement read-only state rendering and bounded affected-identity confirmation around existing ownership-checked recovery. Without `--yes`, require interactive confirmation; noninteractive refusal performs no mutation. Refuse active writer leases; unresolved ambiguous journals yield nonzero exit. Never expose raw staging paths through Desktop or present workflow `doctor` as transaction recovery.
- [ ] Run focused GREEN, then `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_marketplace_*.py tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_cli.py tests/plugins/workflow/test_trust_policy.py`. Run changed-file Ruff/format and `git diff --check`. Report exact failures rather than claiming the whole slice green; known catalog debt remains a mandatory whole-branch gate.
- [ ] Commit `feat(workflow): add explicit package recovery tooling`; fresh reviewer independently executes no-write state/confirmation and owned/ambiguous recovery probes. Record all 14A3a/b/c acceptance receipts before 14B; overall 14A3 is not accepted from CLI tests alone.

## Task 14B — Desktop contract parity and backend-generated fixtures

**Historical status:** The initial implementation is preserved at `bf9db2e46d` and is not accepted. Its fresh review found five Important contract defects. Tasks 14B1 and 14B2 below are the approved correction and acceptance path; do not rewrite or squash the historical commit.

**Files:** Create the three V2 Desktop type/codec/API modules in the file map and corresponding `.test.ts` files; create fixture generator, `tests/fixtures/workflow-marketplace-lifecycle-v2.json`, and `tests/plugins/workflow/test_marketplace_lifecycle_fixtures.py`. Modify `apps/desktop/src/hermes.ts` re-exports and existing API tests for exact requested-ID correlation where shared.

**Consumes:** 14A public schemas and real service/API projections.

**Produces:** Strict V2 decoding, scoped helpers, contract fixture `--write`/`--check`; no lifecycle view changes yet.

- [ ] Generate the fixture corpus through actual model/projection functions and real temporary Git scenarios. Cover every table row in amendment §11, including invalid relationships derived from valid cases with Python expected acceptance. Fixed test tokens stay confined to token-endpoint tests and never enter the shared operation corpus. Include complete A/B grant and rollback-failed fixtures.
- [ ] Write differential and helper RED tests:

```typescript
it.each(corpus.operationCases)('$name follows Python acceptance', testCase => {
  expect(decodeLifecycleOperation(testCase.value) !== null).toBe(testCase.accepted)
})

it('rejects another operation returned by get', async () => {
  transport.reply(corpus.validOperationB)
  await expect(getLifecycleOperation(corpus.operationAId, scopeA)).rejects.toThrow()
})
```

Define `transport` using the existing Desktop API test transport mock; it returns raw fixture JSON to the real helper/decoder. Add cancel-ID mismatch, all exact scope/epoch/subject/request checks, selected workflow mismatch, capability 404 versus auth/network failure, duplicate JSON and byte limits, and token retrieval envelope mismatch.

- [ ] Run RED from Desktop: `npx vitest run --project ui src/lib/workflow-marketplace-lifecycle-codec.test.ts src/api/workflow-marketplace-lifecycle.test.ts`.
- [ ] Implement bounded decoders into fresh objects and exact requested-ID checks before returning operations. Implement client request-ID generation from backend epoch/time with cryptographic randomness. Expose explicit token helper outside QueryClient; preserve existing V1 browse/source behavior.

```typescript
const value = decodeLifecycleOperation(raw)
if (!value || value.id !== requestedId) throw invalidLifecycleResponse()
return value
```

- [ ] Run generator `--check` using the repository interpreter, Python fixture tests via wrapper, Desktop focused GREEN plus existing marketplace codec/API suites, typecheck and changed-file lint/format. Reviewer independently generates fixtures and verifies Python/TS acceptance plus requested-ID rejection. Commit `feat(desktop): validate lifecycle recovery protocol`.

## Task 14B1 — Backend strict-wire correction

**Files:** Modify `plugins/workflow/marketplace/lifecycle_models.py`, `plugins/workflow/marketplace/operations.py`, `plugins/workflow/marketplace/lifecycle_api.py`; modify `tests/plugins/workflow/test_marketplace_lifecycle_models.py`, `tests/plugins/workflow/test_marketplace_operations.py`, and `tests/plugins/workflow/test_marketplace_lifecycle_api.py`. Keep fixture generation and all Desktop files for 14B2.

**Interfaces:**

- **Consumes:** The approved strict wire parity amendment §§3–5 and §§8–11; existing `StrictLifecycleModel`, `LifecycleOperation`, admission store, registry list/token projections, and mounted V2 test app.
- **Produces:** One Python-authoritative V2 relative-path validator shared by all V2 workflow/package path fields; strict `_Capabilities`, `AdmissionEvicted`, `LifecycleOperationPage`, and `ReviewTokenResponse`; route-boundary response validation that either returns a valid public model or the fixed safe internal-error envelope. These models and their JSON schemas are the sole 14B2 generator inputs.

- [ ] **Step 1: Write failing V2 relative-path tests.** Add table-driven model tests for 1 and 1,024 code points; NFC; U+FEFF acceptance where Python clean text accepts it outside paths; and path rejection for Cc, U+2028/U+2029, leading/trailing slash, backslash, NUL, empty, dot, dot-dot, drive-prefix, and case-folded `.git` segments. Prove newline-terminated inspection `definition_path`, `package_path`, and `workflow_paths` values fail. Add a V1 regression that validates the existing V1 model fixtures and package-contract byte hashes unchanged.

```python
@pytest.mark.parametrize("path", ["workflows/a.yaml\n", "/workflows/a.yaml", "workflows/.GIT/a.yaml"])
def test_v2_lifecycle_paths_reject_noncanonical_values(path, valid_inspection_result):
    payload = valid_inspection_result(path=path)
    with pytest.raises(ValidationError):
        LifecycleOperation.model_validate(payload)
```

- [ ] **Step 2: Write failing strict-envelope tests.** Exercise exact fields and scalar domains for capabilities; exact operation/request/epoch/profile/kind/subject/selection correlations for evicted admissions; 0/1/100/101-item pages, duplicate operation/request IDs, common profile/epoch, cursor format, complete/cursor equivalence, and incomplete-nonempty pages; and exact review-token ID/request/subject/selection/digest/token/expiry fields. Use valid real projections first, then mutate one independent rule at a time.

```python
def test_operation_page_rejects_duplicate_request_ids(valid_page):
    payload = valid_page.model_dump(mode="json")
    payload["items"][1]["request_id"] = payload["items"][0]["request_id"]
    with pytest.raises(ValidationError):
        LifecycleOperationPage.model_validate(payload)
```

- [ ] **Step 3: Write failing real-route publication tests.** Substitute malformed capabilities/admission/page/token producer values behind the mounted V2 router. Assert the route never emits malformed HTTP 200 and returns status 500 with only `{"detail":{"code":"marketplace_internal_error"}}`. Also assert valid get/cancel/list/admission/token responses survive strict revalidation unchanged and contain no confirmation token outside the token route.

```python
def test_list_route_fails_safe_when_registry_page_is_malformed(client, malformed_registry):
    response = client.get("/api/workflow-marketplace/v2/operations")
    assert response.status_code == 500
    assert response.json() == {"detail": {"code": "marketplace_internal_error"}}
```

- [ ] **Step 4: Observe RED.** Run `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_marketplace_lifecycle_models.py tests/plugins/workflow/test_marketplace_operations.py tests/plugins/workflow/test_marketplace_lifecycle_api.py`. Record the new path/envelope/route tests failing because the current models accept malformed values or a real route publishes them as HTTP 200.

- [ ] **Step 5: Implement the strict backend authority.** Add the V2-only relative-path validator without changing V1 sanitation or package-contract artifacts. Make all four outer models frozen/extra-forbid with the amendment's exact bounds and cross-field validators. At each lifecycle HTTP publication boundary, revalidate through the declared public response model; catch validation failures and return only the fixed safe internal-error envelope. Do not expose a private registry target, raw body, token, exception, or repository secret.

```python
def _strict_public(model: type[StrictLifecycleModel], value):
    try:
        return model.model_validate_json(value.model_dump_json())
    except ValidationError as error:
        raise HTTPException(
            status_code=500,
            detail={"code": "marketplace_internal_error"},
        ) from error
```

- [ ] **Step 6: Verify GREEN and compatibility.** Re-run the focused command from Step 4, then `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_marketplace_lifecycle_fixtures.py tests/plugins/workflow/test_marketplace_api.py tests/plugins/workflow/test_marketplace_contract.py`. Run changed-file Ruff check/format and `git diff --check`. Preserve the existing generated files byte-for-byte in this backend-only unit. Sequencing ruling after observed compatibility execution: the corrected Python authority necessarily makes the stale lifecycle fixture check fail until 14B2 regenerates it. Accept only the exact newline `package_path` acceptance-label failure and generator `_Capabilities` construction missing required `schema_version`; all focused backend, V1 API, and package-contract checks must pass. Do not relax backend validation or modify generated/Desktop artifacts in 14B1.

- [ ] **Step 7: Commit and review.** Commit `fix(workflow): enforce strict lifecycle wire envelopes`. A fresh reviewer independently mutates each outer envelope, sends at least one malformed producer through the real route, probes the newline path mismatch, and verifies V1/package-contract bytes remain unchanged. Record acceptance before 14B2.

## Task 14B2 — Desktop strict-wire parity correction and Task 14B acceptance

**Files:** Modify `scripts/generate_workflow_marketplace_lifecycle_fixtures.py`, `tests/fixtures/workflow-marketplace-lifecycle-v2.json`, `tests/plugins/workflow/test_marketplace_lifecycle_fixtures.py`, `apps/desktop/src/types/workflow-marketplace-lifecycle.ts`, `apps/desktop/src/types/workflow-marketplace-lifecycle.test.ts`, `apps/desktop/src/lib/workflow-marketplace-lifecycle-codec.ts`, `apps/desktop/src/lib/workflow-marketplace-lifecycle-codec.test.ts`, `apps/desktop/src/api/workflow-marketplace-lifecycle.ts`, and `apps/desktop/src/api/workflow-marketplace-lifecycle.test.ts`. Modify shared transport tests only where the real helper boundary requires it.

**Interfaces:**

- **Consumes:** Review-clean 14B1 Python models/schemas and the strict wire parity amendment §§6–11; the unaccepted initial 14B implementation at `bf9db2e46d` remains the edit base for Desktop behavior.
- **Produces:** Deterministic generated types, schemas, fixtures, domain descriptors, and closed backend error-code set; exact Python/TypeScript wire acceptance; `LifecycleClockObservation` and monotonic `createLifecycleRequestId`; sanitized `LifecycleApiError`. Task 14C0a may extend the backend capabilities authority only after this task's fresh review accepts all five original Task 14B findings; Desktop consumes that addition in 14C0b.

- [ ] **Step 1: Write failing generated-contract and differential tests.** Extend the Python generator test so a fresh temporary-Git generation includes the V2 relative-path descriptor, canonical capability ordering, strict outer-envelope cases, repository U+FEFF positives, and the closed backend code set. Add isolated-rule mutation assertions so changing one rule changes generated output. In TypeScript, run every generated valid/invalid capabilities/admission/page/token/repository case through the real decoder and require exact agreement with Python.

```typescript
it.each(corpus.outerEnvelopeCases)('$name follows Python acceptance', testCase => {
  expect(decodeGeneratedEnvelope(testCase.model, testCase.value) !== null).toBe(testCase.accepted)
})
```

- [ ] **Step 2: Write failing request-clock tests.** Replace scalar receipt-time inputs in tests with `{registryEpoch, serverTimeMs, wallReceivedMs, monotonicReceivedMs}`. Prove stable elapsed time mints the exact 85-character request ID, while negative/nonfinite elapsed time, either elapsed value over 300,000 ms, absolute wall/monotonic divergence over 1,000 ms, +31-second wall adjustment, scope/epoch change, and application restart refuse before POST with `marketplace_clock_revalidation_required`. Stub only `crypto.getRandomValues`; never persist the observation.

```typescript
expect(() =>
  createLifecycleRequestId(observation, {
    wallNowMs: observation.wallReceivedMs + 31_000,
    monotonicNowMs: observation.monotonicReceivedMs + 1_000
  })
).toThrowError(expect.objectContaining({ code: 'marketplace_clock_revalidation_required' }))
```

- [ ] **Step 3: Write failing error-secrecy tests.** Cover every approved backend code and HTTP status. Feed unknown identifier-shaped, token-shaped, nested, duplicate-key, oversized, and non-JSON bodies through the actual transport helper; assert the result is the fixed generic local code and `JSON.stringify(error)` contains none of the supplied secret. Also preserve the local codes `marketplace_lifecycle_unsupported`, `marketplace_network_error`, `marketplace_invalid_response`, `marketplace_request_failed`, and `marketplace_clock_revalidation_required` only at their defined boundaries.

```typescript
expect(JSON.stringify(await captureError(tokenShapedBackendError))).not.toContain(secretToken)
expect((await captureError(tokenShapedBackendError)).code).toBe('marketplace_request_failed')
```

- [ ] **Step 4: Observe RED in both languages.** Run `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_marketplace_lifecycle_fixtures.py`, then from `apps/desktop` run `npx vitest run --project ui src/types/workflow-marketplace-lifecycle.test.ts src/lib/workflow-marketplace-lifecycle-codec.test.ts src/api/workflow-marketplace-lifecycle.test.ts`. Record the generator drift plus current U+FEFF, wall-clock, and arbitrary-error retention failures.

- [ ] **Step 5: Regenerate and implement exact parity.** Generate types/corpus from the review-clean Python models; do not hand-relax the generated schema. Make V2 repository validation match Python clean text byte-for-byte, including U+FEFF, without changing V1 repository/source validators. Mint request IDs from floored server time plus monotonic elapsed time and require a fresh capability observation on every discontinuity. Decode backend errors only through the generated closed set and construct fresh bounded safe errors without retaining raw response objects or text.

```typescript
const monotonicElapsed = now.monotonicNowMs - observation.monotonicReceivedMs
const wallElapsed = now.wallNowMs - observation.wallReceivedMs
if (!validElapsed(monotonicElapsed, wallElapsed)) throw clockRevalidationRequired()
const issuedMs = Math.floor(observation.serverTimeMs + monotonicElapsed)
```

- [ ] **Step 6: Verify GREEN, generation, and compatibility.** Run the fixture generator in `--write` and then `--check` modes using the repository interpreter. Re-run both Step 4 suites; from `apps/desktop` also run the existing workflow marketplace codec/API transport suites, `npx tsc --noEmit`, changed-file ESLint with zero warnings, and Prettier check. Run `git diff --check`. Confirm generated output has no secret, temporary path, nondeterministic time, or machine-specific content and V1 control cases remain unchanged.

- [ ] **Step 7: Commit and review Task 14B.** Commit `fix(desktop): align lifecycle strict wire parity`. A fresh reviewer regenerates artifacts, authors bounded Python-to-TypeScript mutations, tests a real U+FEFF get/cancel response, +31-second wall movement with stable monotonic time, and token-shaped backend error serialization. Acceptance requires all five original Task 14B findings addressed: V2 repository parity, V2 path authority, monotonic clock observation, closed safe errors, and strict outer-envelope publication. Only then record Task 14B complete and dispatch 14C0a.

## Task 14C0a — Backend principal-binding authority

**Files:** Modify `plugins/workflow/marketplace/admissions.py`, `lifecycle_models.py`, and `lifecycle_api.py`; modify `tests/plugins/workflow/test_marketplace_admissions.py`, `test_marketplace_lifecycle_models.py`, and `test_marketplace_lifecycle_api.py`. Modify no Desktop, Electron, generated fixture/type, transaction, package-contract, V1 route, or persistence file in this unit.

**Consumes:** Review-clean Task 14B Python authority at `eeba68e5c3`; the identity-binding amendment §§2–3 and §5; existing process `_PROCESS_EPOCH`/admission-secret lifetime and private `_actor(authority, profile_key)`.

**Produces:** A separate memory-only process-epoch principal-binding key and deterministic private test seam; `LifecycleAdmissionStore.principal_binding(actor)` (or an equivalently narrow process-authority function); required strict capabilities `principal_binding`; closed `marketplace_principal_changed`; and a shared route precondition applied to every V2 route except capabilities before any actor-scoped read, token access, cancellation, or admission.

- [ ] **Step 1: Write derivation RED tests.** Inject fixed epoch/key inputs. Prove same exact actor is stable across two admission stores and idle profile-context reconstruction in one process epoch; different actor/profile-derived actor, epoch, or key differs. Require exactly 64 lowercase hex. Inspect model/repr/error/log paths so no private key, actor, authority binding, username, token, or credential appears.

```python
def test_principal_binding_survives_profile_registry_recreation(process_binding):
    first = LifecycleAdmissionStore(profile_key=HOME, epoch=EPOCH)
    second = LifecycleAdmissionStore(profile_key=HOME, epoch=EPOCH)
    assert first.principal_binding(ACTOR) == second.principal_binding(ACTOR)
```

- [ ] **Step 2: Write strict capabilities and publication RED tests.** Require `principal_binding` on the public model and mounted authorized route. Mutate missing/extra/uppercase/short/long/non-string values through the real response boundary and assert fixed `500 marketplace_internal_error`, never malformed 200. Confirm 401/403 responses disclose no binding. Prove a retireable per-profile registry does not own or rotate the key.
- [ ] **Step 3: Write the all-route precondition RED matrix.** For list, exact get, cancel, admission lookup, review-token retrieval, package state, every registered start kind, refresh, and inspect, test correct, missing, malformed, uppercase, oversized, valid-but-unequal, duplicate-same-value, and duplicate-different-value `X-Hermes-Marketplace-Principal-Binding` wire headers. Every invalid case returns only fixed `409 marketplace_principal_changed` before producer/service/registry invocation. A correct single value preserves existing status/body behavior. Capabilities accepts no expected-binding requirement. Read the raw header collection and require cardinality exactly one before regex or HMAC comparison; a convenience `headers.get(...)` value is insufficient. Compare only the exact singleton using `hmac.compare_digest`; never echo either value.

```python
response = client.get(
    "/api/plugins/workflow/marketplace/lifecycle/v2/operations",
    headers={"X-Hermes-Marketplace-Principal-Binding": wrong_binding},
)
assert response.status_code == 409
assert response.json() == {"detail": {"code": "marketplace_principal_changed"}}
assert registry.list_calls == 0
```

- [ ] **Step 4: Observe RED.** Run `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_marketplace_admissions.py tests/plugins/workflow/test_marketplace_lifecycle_models.py tests/plugins/workflow/test_marketplace_lifecycle_api.py`. Record failures caused by the absent key/binding/model/header precondition before production edits.
- [ ] **Step 5: Implement the smallest backend authority.** Place principal-binding entropy beside process epoch authority, not inside `WorkflowMarketplaceOperationRegistry` or a profile context. Use the amendment's domain-separated HMAC over exact process epoch and already-private actor. Extend the strict model/error status table. Authenticate and resolve the actor normally; read all case-insensitive wire-header occurrences, require exactly one value before domain validation, compare that singleton in constant time, and stop before route-specific access on any missing/duplicate/malformed/mismatch. The header never authorizes or changes actor scope.
- [ ] **Step 6: Verify backend GREEN and bounded compatibility.** Re-run Step 4, then the focused V2 operation/API/auth suites and V1/package-contract controls through the wrapper. Run changed-file Ruff check/format and `git diff --check`. The Python model/error additions intentionally make generated lifecycle fixtures/types stale until 14C0b; record only that exact downstream drift and do not weaken Python or edit generated/Desktop files here.
- [ ] **Step 7: Commit and review.** Commit `feat(workflow): bind lifecycle requests to actor`. A fresh reviewer independently uses two actors and profile-context retirement, fault-injects malformed capability output, probes at least one read and one mutation per shared precondition path with malformed/unequal headers plus same-value and different-value duplicate wire fields, proves cardinality is checked before comparison with no producer call or secret disclosure, and verifies the only generator drift is the required binding/error addition. Do not dispatch 14C0b until all Critical/Important findings are closed.

## Task 14C0b — Electron and Desktop binding parity

**Files:** Create a small pure Electron generation allocator/module and tests if it keeps `main.ts` testable; modify `apps/desktop/electron/main.ts` and relevant API transport/connection apply/config/SSH/backend-state tests; modify `apps/desktop/src/global.d.ts`, `src/api/workflow-marketplace-lifecycle.ts` / `.test.ts`, generated lifecycle types/fixtures/generator tests, and add a feature-owned `lib/workflow-marketplace-connection-binding.ts` / `.test.ts` (or equivalently narrow adapter). Modify `preload.ts` only if the existing pass-through cannot carry the two dedicated IPC fields. Do not create the supervisor, change lifecycle dialogs, or implement package mutation barriers.

**Consumes:** Review-clean 14C0a capabilities/header/error authority; identity-binding amendment §§4–6 and §§8–10; existing connection registry/builders, connection change/apply events, backend dial claims, `handleHermesApiRequest`, and Task 14B private clock observation.

**Produces:** Required positive-safe-integer `HermesConnection.connectionGeneration`; process-global memory-only generation allocation and authoritative current-route lookup; dedicated `expectedConnectionGeneration` and `expectedMarketplacePrincipalBinding` IPC fields; native generation enforcement before HTTP dispatch and before IPC return; generated strict capability/error parity; exact `LifecycleConnectionBinding`; and a memory-only binding coordinator that quarantines/purges colliding marketplace cache roots for later supervisor consumption.

- [ ] **Step 1: Write allocator/descriptor RED tests.** Cover legacy primary/null-ID, registry local, token, OAuth/cloud, URL, SSH, shared routed backend, and per-profile descriptors. The same cached live descriptor retains one positive safe generation. Endpoint/header/token/auth mode, OAuth login/logout/account or native-session replacement, SSH host/user/port/key/path/profile/token/tunnel, backend descriptor replacement, connection apply/edit/delete/recreate, registry replacement, and legacy/registry switch allocate a new generation before change publication. Navigation, visibility, polling, A→B→A selection, and unchanged-descriptor transient reconnect do not. Prove a late generation-A resolution cannot become current after B. With private test injection near the limit, issue `Number.MAX_SAFE_INTEGER` once; the next allocation invalidates all authoritative generations exactly once, enters permanent exhaustion, returns exact `marketplace_connection_generation_exhausted` for every later allocation, never emits 1, and permits a fresh allocator/main-process instance to start at 1 only with no retained old state.
- [ ] **Step 2: Write native dispatch RED tests.** For both `hermes:api` and `hermes:api:structured`, require exact positive-safe `expectedConnectionGeneration` on lifecycle V2 calls. Change generation before resolved dispatch and while fetch is deferred; both reject exact `marketplace_connection_generation_changed`, return no backend body, and do not dispatch/return stale data. Under exhausted allocator state, both channels reject exact `marketplace_connection_generation_exhausted` before route/network work and never return a backend body; queued old generation 1 cannot match because no authoritative generation remains and no value is reused. Assert the generation field never reaches HTTP. Reject lifecycle-only fields on non-lifecycle paths. Map only exact-valid expected principal binding to `X-Hermes-Marketplace-Principal-Binding`; reject missing post-capabilities binding and arbitrary renderer headers. Treat the header name as case-insensitively reserved: exact, lowercase, and mixed-case collisions in saved descriptor/config headers fail validation and are stripped again before dispatch. Token and OAuth/cookie transport probes must observe exactly one native-owned header value after merge/sanitization.

```typescript
const pending = invokeLifecycle({ expectedConnectionGeneration: generationA })
advanceConnectionGeneration(connectionId)
resolveOldBackendResponse(secretBearingBody)
await expect(pending).rejects.toThrow('marketplace_connection_generation_changed')
expect(rendererReceivedBodies()).toEqual([])
```

- [ ] **Step 3: Write generated/Desktop API RED tests.** Regenerate in a scratch check to expose the 14C0a field/error drift, then require Python-to-TypeScript agreement for valid/invalid capabilities and the new closed 409 code. Replace placeholder string `connectionGeneration`/`principal` with exact numeric `connectionGeneration`/`principalBinding`. Capabilities sends generation only; every other helper sends both dedicated IPC fields. Native generation-change sentinel becomes the same closed local recoverable code; native generation-exhausted becomes a separate closed restart-required local code. Exact equal binding values remain valid; copied/deserialized branded clock observations, any mismatched current field, and stale native generations refuse before POST.
- [ ] **Step 4: Write binding-transition RED tests.** The pure coordinator captures descriptor generation before capabilities and rejects a response if native current generation changed. Disconnect, reconfiguration, native-generation error, and principal-change error synchronously enter presentation quarantine. No old package/source/trust query data is rendered while probing. Same ID/profile/principal/epoch under a new generation may restore settled values as “last observed” only after exact capability proof; a different principal/epoch cancels and removes/resets the entire colliding `(connectionId, profile)` marketplace query root before accepting new data. A late old response cannot purge, repopulate, announce, or focus the new scope. Exhaustion globally quarantines marketplace presentation, disables lifecycle, emits restart-required state, performs no same-process capabilities retry, and never clears an unresolved operation barrier or claims an outcome.
- [ ] **Step 5: Observe RED in native and renderer suites.** From `apps/desktop`, run `npx vitest run --project electron electron/connection-generation.test.ts electron/connection-apply.test.ts electron/connection-config-apply.test.ts electron/backend-connection-state.test.ts electron/api-transport.test.ts`, plus `npx tsx --test electron/structured-api-channel.test.ts` for the source-bounded IPC contract, then `npx vitest run --project ui src/types/workflow-marketplace-lifecycle.test.ts src/lib/workflow-marketplace-lifecycle-codec.test.ts src/api/workflow-marketplace-lifecycle.test.ts src/lib/workflow-marketplace-connection-binding.test.ts`. Run Python fixture generation check to record only expected 14C0a drift.
- [ ] **Step 6: Implement allocator and native enforcement.** Keep the monotonic counter, permanent exhaustion flag, and current route-generation table in main-process memory. Allocate/bump at the authoritative descriptor construction/invalidation seams, synchronously invalidate before change events, and validate the captured generation after route resolution/before fetch and after fetch/before return. Never reuse a number or reset in-process; after MAX_SAFE_INTEGER, invalidate once and reject all later allocation/lifecycle dispatch with the exact exhausted sentinel until main-process restart. Do not infer material changes in the renderer. Reserve the expected-principal header case-insensitively in connection validation, defensively delete all casing collisions from descriptor headers at dispatch, then inject exactly one dedicated value for exact lifecycle V2 paths after sanitization. Never expose arbitrary headers or treat the binding as authorization.
- [ ] **Step 7: Regenerate and implement Desktop parity/coordinator.** Run generator `--write`, update strict types/codecs/error set, and use the complete five-field binding for clock/API calls. Implement explicit coordinator transitions with injected connection/capabilities/query adapters. Binding values and observations remain memory-only and absent from query keys, persistence, analytics, URLs, logs, user errors, and operation subjects. Existing transport descriptors may retain their previously scoped credentials; the new binding/supervisor surfaces may not.
- [ ] **Step 8: Verify GREEN and compatibility.** Run generator `--check`, Python fixture tests through the wrapper, both Step 5 suites, existing Electron connection/apply/SSH/API transport tests affected by descriptor shape, existing marketplace API/codec controls, TypeScript compile/typecheck, zero-warning changed-file lint, Prettier, and `git diff --check`. Prove new Desktop + missing generation/binding disables V2 lifecycle without fallback while V1 browse/source remains unchanged. Prove exhaustion is memory-only, cannot be cleared by renderer reload/window recreation, and does not add persistence. Confirm no Workflow Studio/package-contract/persistence change.
- [ ] **Step 9: Commit and review.** Commit `feat(desktop): bind lifecycle transport identity`. A fresh reviewer independently races generation at both native checks, injects near-limit state and proves permanent fail-closed exhaustion/no numeric reuse/no queued-IPC alias, exercises OAuth same-actor versus changed-actor response using the real header/backend fixture, probes exact/lower/mixed-case descriptor collisions on token and OAuth/cookie transports and requires exactly one native-owned header, checks every connection mode/event classification, tests quarantine then same-binding restore/changed-binding purge and exhaustion restart guidance, regenerates fixtures, and searches all new serialized surfaces for secrets. Do not dispatch 14C1 until all Critical/Important findings are closed.

## Task 14C1 — Application operation supervisor

**Files:** Create `store/workflow-marketplace-supervisor.ts` / `.test.ts`, `lib/workflow-marketplace-supervision.ts` / `.test.ts`, and a small `app/workflows/marketplace/supervisor-provider.tsx` if React context is needed; modify `src/main.tsx` for main-window lifecycle and minimally attach the 14C0b binding coordinator above Workflows views.

**Consumes:** Review-clean 14C0b exact bindings, scoped V2 helpers/types, coordinator quarantine/purge transitions, existing connection/profile/visibility stores, and backend-generated operation fixtures.

**Produces:** The supervisor factory/public methods defined above, with memory-only records and injectable clock/transport. `getPackageGate` initially exposes busy/unknown states; 14C2 supplies mutation reconciliation. The original stopped 14C1 report is inspection evidence only and contributes no code.

- [ ] **Step 1: Write transition-table RED tests.** Cover close/tab/route survival, A→B→A, hidden/disconnect/reconnect, same-principal new-generation exact rebind, changed principal/epoch non-adoption, lost POST response, exact replay, evicted result, cancel racing commit, StrictMode, old generation settlement, update-check status-error guard release, cadence disposal, and concurrent request bounds. Require `401`, `403`, `marketplace_principal_changed`, and operation/admission not-found to suspend/quarantine and reprobe capabilities before eviction/outcome classification. Require `marketplace_connection_generation_exhausted` to quarantine globally, release network call guards, preserve unresolved barriers, avoid same-process reprobe, and expose restart-required state without terminal/outcome copy.

```typescript
it('retains admission correlation after view detachment', async () => {
  const harness = createSupervisorHarness(corpus)
  const pending = harness.supervisor.start(harness.confirmIntent, bindingA)
  harness.loseAdmissionResponse()
  harness.detachView()
  await pending
  await harness.supervisor.reconcileScope(bindingA)
  expect(harness.record().operationId).toBe(corpus.admittedConfirm.id)
  expect(harness.admittedWorkerCount()).toBe(1)
})
```

Create the harness using a real supervisor and fixture-decoding API fake, deterministic clock, connection-binding coordinator, and deferred responses. It simulates the server receipt table, not optimistic UI outcomes; backend one-worker proof remains in 14A2/A3.

- [ ] **Step 2: Observe RED.** Run `npx vitest run --project ui src/store/workflow-marketplace-supervisor.test.ts src/lib/workflow-marketplace-supervision.test.ts src/lib/workflow-marketplace-connection-binding.test.ts`.
- [ ] **Step 3: Implement pure transitions and application lifetime.** Use immutable records keyed by the complete binding plus request ID and exact operation ID once known. Individual call guards release in `finally`; barriers remain separate. Attach one application instance above Workflows routes, never inside Marketplace or Installed. Keep DOM focus/dialog state and tokens outside it. On disconnect, release transport closures and retain only token-free records/barriers.

```typescript
try {
  await recoverExactRequest(record)
} finally {
  releaseCallGuard(record.key, callGeneration)
}
```

- [ ] **Step 4: Implement exact reconciliation and bounded polling.** Scan actor-owned snapshot pages, directly look up every unresolved known request/operation ID, and accept only exact binding/request/operation/kind/subject/selection correlations. Same-principal/epoch reconfiguration may rebind records after exact lookup; changed principal/epoch never adopts old work or invalidates new cache. Unknown admission stays blocked until found or the admission window closes plus current-state reconciliation; never select “latest.” Clear sensitive replay closures after bounded admission lifetime.
- [ ] **Step 5: Implement scheduling/disposal.** At most three renderer operation calls and one 500-ms visible cadence per active operation; pause hidden/disconnected; remove each timer and abort listener on resolution, abort, error, or disposal. StrictMode double attachment creates one logical supervisor and no duplicate POST/poll. Every terminal or recoverable error path releases its action guard without prematurely releasing package barriers.
- [ ] **Step 6: Verify GREEN.** Re-run Step 2 plus V2 API/codec/binding suites, focused Workflows provider/navigation tests, TypeScript compile/typecheck, zero-warning changed-file lint, Prettier, and `git diff --check`. Confirm no secret/confirmation body enters records, query keys, URLs, logs, persistence, DOM, or errors.
- [ ] **Step 7: Commit and review.** Commit `feat(desktop): supervise marketplace operations across navigation`. A fresh reviewer independently races lost admission, view detachment, generation change, principal change, 401/403/not-found, and late completion; verifies exact origin-only reconciliation, quarantine, bounded calls, guard release, and listener disposal. No 14C2 work begins with an open Critical/Important finding.

## Task 14C2P — Inspection digest parity precursor

**Status:** Approved on 2026-09-06. This is a separate implementation/review gate before resuming Task 14C2 fix round 1.

**Files:** Modify `scripts/generate_workflow_marketplace_lifecycle_fixtures.py` and `tests/plugins/workflow/test_marketplace_lifecycle_fixtures.py`; add generated `tests/fixtures/workflow-marketplace-inspection-v1.json`; modify `apps/desktop/src/lib/workflow-marketplace-codec.ts` / `.test.ts`. Modify the C2 lifecycle harness only after this precursor is review-clean and resumed C2 begins. Do not change backend models/service/routes, V2 operation eligibility, package digest algorithms, trust semantics, native code, or Workflow Studio.

**Consumes:** Existing authoritative `PackageInspection`, real `WorkflowMarketplaceService.inspect()`, V1 registry/public serialization, V2 generated inspection, and the approved HTTP version-compatibility policy.

**Produces:** One strict backend-generated V1 package-detail fixture proving the separate distribution and per-workflow effective trust digest domains, plus a V1 Desktop decoder whose validation exactly matches that backend invariant. It does not establish lifecycle freshness; the resumed C2 fix owns admission-generation lineage.

- [ ] **Step 1: Preserve the stopped C2 RED.** Keep the three bounded uncommitted C2 test/harness files and record their diff before precursor implementation. Do not make the rendered historical-inspection test pass with a hand-shaped V2 downgrade, digest rewrite, or decoder bypass.
- [ ] **Step 2: Generate an authoritative V1 fixture.** Reuse the generator's real temporary A/B Git repository and marketplace service. Start `registry.start("package_detail", worker, actor=..., subject=..., canonical_body=...)` with no caller-supplied request ID. The worker follows `_legacy_completion(complete_read(..., kind="inspect", call=lambda: service.inspect(...)))`; poll the exact ID through `get_legacy()` and serialize the strict `MarketplaceOperation` with `model_dump(mode="json", by_alias=False)` so the artifact matches V1 HTTP snake-case output. Emit at least two workflows whose effective workflow digests differ from the root distribution digest. Add `tests/fixtures/workflow-marketplace-inspection-v1.json` to generator `--write`/`--check` ownership and its Python reproduction test. Do not use `_public`, wrap a V2 operation, or hand-rewrite digest values.
- [ ] **Step 3: Observe Desktop parity RED.** Feed that exact generated V1 payload to `decodeMarketplaceOperation` and assert preserved root/A/B digest values. It must fail only because the current decoder adds the unsupported equality. Add one-property negative mutations for malformed root/workflow digests, unexpected fields, identity/status, ordering/uniqueness, paths, and resource roles; those validations must already remain fail-closed.
- [ ] **Step 4: Implement the smallest correction.** Remove only `workflows.some(item => item.package_digest !== packageDigest)` from `decodeMarketplacePackageDetail`. Retain strict digest syntax and every closed-shape/correlation/bounds check. Do not rename public fields, recompute values, equate siblings, weaken the generated V2 decoder, or treat digest validity as candidate freshness.
- [ ] **Step 5: Verify the boundary.** From the worktree run `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_marketplace_lifecycle_fixtures.py tests/plugins/workflow/test_marketplace_service.py tests/plugins/workflow/test_marketplace_api.py tests/plugins/workflow/test_marketplace_lifecycle_models.py`, then the generator's `--check` through the interpreter selected by that wrapper. From `apps/desktop` run `npx vitest run --project ui src/lib/workflow-marketplace-codec.test.ts src/lib/workflow-marketplace-lifecycle-codec.test.ts`, typecheck, zero-warning changed-file ESLint, and Prettier. Run repository Ruff check/format-check for the changed Python generator/test and root `git diff --check`. Never invoke direct pytest.
- [ ] **Step 6: Commit and review.** Commit `fix(desktop): preserve inspection digest domains`. A fresh reviewer independently generates/observes one real V1 distinct-digest inspection, verifies exact Desktop preservation plus negative strictness, and confirms no V2-to-V1 downgrade, backend/schema/trust change, lifecycle-freshness heuristic, or unrelated scope. Reproduction of the still-open historical-inspection race is expected and does not block this precursor; passing R1/R2 belongs to resumed C2. Task 14C2 fix round 1 remains paused until this precursor is review-clean.

## Task 14C2 — Shared cache reconciliation barriers

**Files:** Create `lib/workflow-marketplace-reconciliation.ts` / `.test.ts`; modify supervisor, Marketplace `query-keys.ts`, `index.tsx`, `package-detail.tsx`, `installed-packages.tsx`, Workflows `catalog.tsx` / `index.tsx`, and their behavior tests.

**Consumes:** 14C1 records/gates and 14A3 `PackageState`.

**Produces:** Per-package query-generation barriers and action gating for all consumers, using the amendment §7 table.

**Transitional adapter gate:** The retained view-local `use-package-lifecycle.tsx` mutation callbacks do not participate in the application supervisor and therefore cannot prove that a barrier began before their POST. Task 14C2 must not label those callbacks fenced or allow a supervisor-derived `ready` gate to invoke them. Until Task 14D replaces install/update/remove preparation and confirmation with the supervisor, and Task 14E does the same for trust, the corresponding lifecycle controls are conservatively non-actionable; browsing and explicitly read-only metadata remain available and stale metadata is labeled last observed. Tests may drive the real V2 supervisor directly through `lifecycle-test-harness.tsx` to prove barrier semantics, but may not use that harness as evidence that the old callback path is safe. Direct/auxiliary renderers without an application supervisor use optional context only for read-only rendering and expose no lifecycle action fallback. Task 14D/E re-enable each control only through the supervisor-backed path after the exact post-barrier readiness requirements below pass. Existing behavior-test bodies whose sole purpose is exercising the temporarily unreachable callbacks must be preserved verbatim and may be skipped only individually with explicit 14D/14E ownership; record their exact names and skipped counts, add active no-fallback tests in C2, and re-enable every one before Task 15/final review.

**Stopped fix-round state:** Fresh review of `d5676aaa56` found two Important blockers: historical inspection retrieval can acquire a new query-fetch generation, and later authoritative busy/recovery-required/unconfirmed state can leave catalog actions ready. The R1 direct-state RED is preserved in three uncommitted test/harness files. During its rendered setup, the separate V1 inspection digest parity gap was confirmed. Do not resume either R1 production work or R2 until Task 14C2P is approved, implemented, and review-clean. After that gate, resumed C2 replaces the stopped harness's invalid V2-envelope wrapper with the exact generated V1 fixture and uses it in the rendered historical-inspection regression; continue to forbid operation downgrade and hand-shaped digest substitution.

- [ ] Write all four success-plus-failed-refetch cases, ambiguous outcome, verified rollback, stale in-flight response, detail aliases, multiple terminal histories, source deletion/orphaning, profile switch, absent package and unbound workflow-catalog cases.

```typescript
it('keeps Install disabled after success when refetch fails', async () => {
  const harness = renderLifecycleHarness(corpus.installScenario)
  await harness.installAndConfirm()
  harness.failOriginRefetches()
  await harness.closeAndReopenMarketplace()
  expect(screen.queryByRole('button', { name: 'Install package' })).toBeNull()
  expect(screen.getByText(/Refreshing package state/)).toBeVisible()
})
```

Define `renderLifecycleHarness` in `marketplace/lifecycle-test-harness.tsx` (test-only module) with real QueryClient/supervisor/providers and strict fixture decoding; reuse it in 14D–F. It controls network responses, not DOM claims.

- [ ] Run RED: `npx vitest run --project ui src/lib/workflow-marketplace-reconciliation.test.ts src/app/workflows/marketplace/index.test.tsx src/app/workflows/index.test.tsx`.
- [ ] Implement barrier generations before POST; cancel old query requests, invalidate exact origin roots, ignore pre-barrier completions for action readiness, and require fresh locked package state plus control-specific projections. Retained metadata is labeled last observed. Trust snapshots replace as a whole. Conservatively gate unbound catalog entry points while affected catalog truth is stale; never join by workflow name.

```typescript
if (responseGeneration !== gate.generation || gate.hasUnresolvedAdmission) return gate
if (state.state === 'unconfirmed' || state.busy || state.recovery !== 'clear') return gate
return markLocalStateVerified(gate, state)
```

- [ ] Run GREEN plus workflow UI, supervisor and API/codec suites, typecheck/lint/format. Reviewer independently delays an older query until after success and makes all refetches fail; verify stale install/remove/update/trust controls stay unusable. Commit `fix(desktop): fence stale marketplace mutation caches`.

For the stopped review fix, candidate freshness is admission-lineage evidence: a poll/get for an immutable operation carries the exact generation of its known inspection admission and can never acquire the generation at re-fetch time. Retrieval without a known exact admission establishes no candidate freshness. Catalog readiness must also inspect current authoritative package observations, including observations with no retained mutation record. A later busy, recovery-required, or unconfirmed observation fences the entire exact origin because the catalog has no package/workflow identity join; reopening requires a clear locked state and a catalog projection fetched after the contradictory observation. Add direct and rendered regressions for all three states, view remount, query removal/refetch, exact scope isolation, and failed replacement inspection admission before the fix commit and scoped re-review.

## Task 14D — Install, update and removal lifecycle adapters

**Files:** Refactor `use-package-lifecycle.tsx`, install/remove dialogs, Marketplace/Installed integration and corresponding tests. Extract `package-lifecycle-presentation.ts` / `.test.ts` for outcome-to-copy decisions. Keep shared review sections.

**Consumes:** Supervisor and package gates, exact V2 reviews/outcomes.

**Produces:** Thin prepare/review/token/confirm flow, truthful terminal presentation, explicit current/error/orphaned update-check results.

- [ ] Replace tests expecting unconditional “nothing installed/version remains” from generic failures with table-driven proof-based assertions. Cover update old_version differing from card version; removal current_version differing from card; lost admission; rollback failed/ambiguous; status/terminal ID mismatch; no-op update token absence; expired token; safe direct-subject handling without a new form.

```typescript
it.each(['rollbackFailed', 'recoveryAmbiguous', 'lostConfirmResponse'] as const)(
  '%s cannot claim the prior version remains',
  async scenario => {
    const harness = renderLifecycleHarness(corpus[scenario])
    await harness.updateAndConfirm()
    expect(await screen.findByText(/State could not be confirmed/)).toBeVisible()
    expect(screen.queryByText(/remains installed|Nothing new was installed/)).toBeNull()
  }
)
```

- [ ] Run RED: `npx vitest run --project ui src/app/workflows/marketplace/install-review-dialog.test.tsx src/app/workflows/marketplace/remove-review-dialog.test.tsx src/app/workflows/marketplace/index.test.tsx src/app/workflows/marketplace/package-lifecycle-presentation.test.ts`.
- [ ] Refactor the controller to subscribe to supervisor records; remove controller-owned polling/generic failure classifiers. Fetch token explicitly only for the reviewed operation; confirm sends exact subject/review/preparation binding under a new request ID. Clear secrets on all specified boundaries; supervision survives. Present commit history separately from current-state reconciliation.

```typescript
switch (outcome.type) {
  case 'recovery_required':
  case 'outcome_unknown':
    return { kind: 'state_unconfirmed', canPrepareAgain: false }
  case 'known_unchanged':
    return unchangedPresentation(outcome.package_state, authoritativeReview)
}
```

Implement exhaustive handling for committed/cancelled outcomes as defined in the spec, plus distinct check error/orphaned and exact safe Retry. Do not infer an installed version from `target.installed` captured on a card.

- [ ] Run GREEN and all Marketplace/workflow UI, typecheck/lint/format. Reviewer independently exercises a real backend-derived rollback-failed fixture and stale-card version mismatch. Commit `fix(desktop): render authoritative package lifecycle outcomes`.

## Task 14E — Separate trust lifecycle and complete package state

**Files:** Refactor trust portion of `use-package-lifecycle.tsx` into `use-package-trust-review.ts` if needed for a single responsibility; modify trust dialog, `review-sections.tsx`, post-install action gating, and tests.

**Consumes:** V2 trust selection/inventory/full-map contract and shared supervisor/barriers.

**Produces:** Exact all/one reviews, capability-gated independent trust, complete actual trust display.

- [ ] Write real A/B fixture tests: grant A → A trusted/B untrusted is success; all requires both; wrong selected review, unknown/duplicate/missing members, wrong digest, and post-write response failure produce unknown after possible admission. Verify full trust snapshot updates and failed-refetch behavior.

```typescript
it('accepts the complete package map after granting A', async () => {
  const harness = renderLifecycleHarness(corpus.grantAOnly)
  await harness.reviewAndGrantOne('A')
  expect(await screen.findByText('Trust granted for A')).toBeVisible()
  expect(harness.visibleTrustState('B')).toBe('untrusted')
})
```

- [ ] Run RED: `npx vitest run --project ui src/app/workflows/marketplace/trust-review-dialog.test.tsx src/app/workflows/marketplace/index.test.tsx`.
- [ ] Implement selection correlation against backend package inventory and exact preparation review; changing selection clears token and requests a new matching review. Render current per-workflow trust_state and all existing risks. Post-install Review trust checks trust capability and verified local state. Keep existing independent/manual grant semantics; do not force blanket untrusted flags or add a revoke UI.

```typescript
const selected = selection.type === 'all' ? packageMembers : [selection.workflow_name]
if (!isCompleteKnownMap(result.workflows, packageMembers)) return unknownOutcome()
if (!selected.every(name => stateByName.get(name) === 'trusted')) return unknownOutcome()
```

- [ ] Run GREEN plus V2 parity/supervisor/workflow UI, typecheck/lint/format. Fresh reviewer obtains an actual A/B backend grant fixture and verifies displayed truth under failed refetch. Commit `fix(desktop): reconcile full package trust results`.

## Task 14F — Keyboard, focus, responsive and retained-state integration

**Files:** Modify Marketplace `index.tsx`, lifecycle dialogs, shared dialog primitive only if necessary, and focused UI tests. Add `marketplace/lifecycle-navigation.test.tsx` and `lifecycle-accessibility.test.tsx`.

**Consumes:** Completed package/trust adapters and shared supervisor.

**Produces:** One-Escape behavior and deterministic foreground-only focus under every lifecycle transition.

- [ ] Write portal/document Escape tests in narrow layout, pending confirm admission timeout, close/tab/profile transitions, terminal success/cancel/failure focus, removed origin, disabled origin, and late background completion. Assert behavior rather than CSS class snapshots.

```typescript
it('one Escape closes the dialog and preserves package detail', async () => {
  const harness = renderLifecycleHarness(corpus.installScenario, { narrow: true })
  await harness.openInstallReview()
  await harness.user.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByRole('region', { name: /package details/ })).toBeVisible()
})
```

- [ ] Run RED: `npx vitest run --project ui src/app/workflows/marketplace/lifecycle-navigation.test.tsx src/app/workflows/marketplace/lifecycle-accessibility.test.tsx`.
- [ ] Implement modal key ownership and parent `defaultPrevented` guards, finite admission locking, focus origin/fallback policy and safe live regions. Confirm one cadence releases its abort listener even on ordinary timeout resolution. Migrate V2 source/inspect operation callers through shared helpers without reopening source CRUD design; preserve Refresh-all behavior and exact source identity.

```typescript
if (event.defaultPrevented || modalOwnsKeyboard) return
if (event.key === 'Escape' && narrow && selection) showPackageList()
```

- [ ] Run GREEN, full workflow UI, source/operation/ConfirmDialog regressions, typecheck and lint. Inspect 320px and 200% zoom, RTL, reduced motion, and keyboard-only navigation using the actual UI/browser test harness. Record screenshots only if useful to review; do not substitute screenshots for assertions.
- [ ] Fresh reviewer independently tests Escape during a deferred confirm and focus after successful removal/navigation. Commit `fix(desktop): preserve lifecycle keyboard ownership`. Mark original Task 14 complete only when 14A1–F are accepted with no blocking findings.

## Task 15A — Isolate marketplace transaction workspaces

**Spec:** `docs/superpowers/specs/2026-09-06-workflow-marketplace-transaction-workspace-isolation-amendment.md`

**Files:** Modify `plugins/workflow/marketplace/transactions.py`; modify `tests/plugins/workflow/test_marketplace_transactions.py`; create/modify `tests/plugins/workflow/test_marketplace_installed_distribution_e2e.py`. Do not modify `plugins/workflow/store.py` or its established paths.

**Consumes:** `WorkflowSourceStore.root == <profile-home>/marketplace/workflows`, the existing marketplace lock/private-root authority, identity-bound transaction markers, and existing strict journal path validation.

**Produces:** `MarketplaceTransactionStore.staging_root == self.root / ".staging"` and `MarketplaceTransactionStore.quarantine_root == self.root / ".quarantine"`, with existing private/no-follow/atomic ownership rules. Task 15B may initialize the real workflow runtime and marketplace service in either order without corrupting or rejecting a package transaction.

**Threat boundary:** The private profile directory plus the marketplace lock is the supported concurrency authority. Defend against cooperating-process concurrency, RunStore activity, pre-existing hostile paths/artifacts, crashes and replacements at observable transaction phases. Do not claim portable atomic protection against a hostile same-OS-user process replacing a directory inside the interval between one filesystem syscall returning and the next identity check. Such a change must still prevent durable success/installed mutation once detected; cleanup retains anything whose current identity/marker is not proven.

- [ ] Preserve the genuine integration REDs in `test_marketplace_installed_distribution_e2e.py`: initialize `RunStore` before install preparation and assert prepare/confirm commits; prepare first, initialize `RunStore`, then assert the exact transaction envelope remains and confirm commits. Before production changes, run through the repository wrapper and record the exact `transaction_destination_invalid` and `transaction_candidate_changed` failures.

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_marketplace_installed_distribution_e2e.py::test_real_workflow_backend_initialization_allows_marketplace_install \
  tests/plugins/workflow/test_marketplace_installed_distribution_e2e.py::test_workflow_runtime_recovery_preserves_live_marketplace_preparation
```

- [ ] Add focused transaction-store tests that assert the exact disjoint roots, `0700` marketplace modes where POSIX modes apply, and bidirectional non-enumeration: RunStore orphan cleanup cannot observe/move a live marketplace `owner.json` envelope, and marketplace abandoned-staging/recovery cannot observe/remove a RunStore `.snapshot-owner.json` directory.
- [ ] Add a persisted pre-release path test. A prepared record or journal naming `<profile-home>/workflows/.staging` or `.quarantine` must fail strict consistency before filesystem mutation. Assert the legacy envelope and installed/provenance/trust state are unchanged; do not migrate, chmod, delete, or reinterpret the entry.
- [ ] Add fault tests at named observable phases: before scratch/envelope validation, before prepared/journal state publication, before installed swap, and before cleanup. A changed identity fails before a committed result or installed mutation; cleanup never removes a path whose identity/marker is already mismatched. Inject between phases, not by replacing the result of `mkdir`, `open`, or conditional removal from inside the filesystem call. Record same-account syscall-boundary replacement as outside the approved portable threat model rather than converting it into an unimplementable release gate.
- [ ] Change only the marketplace transaction root derivation to the existing private marketplace state root. Keep `RunStore.staging_root`/`quarantine_root`, marker formats, installed destinations, journal schema/version, lock order, public error/result schemas, and recovery algorithms otherwise unchanged.

```python
self.staging_root = self.root / ".staging"
self.quarantine_root = self.root / ".quarantine"
```

- [ ] Run the two integration tests GREEN, then the complete marketplace transaction, operation, API, trust, CLI and RunStore recovery suites through `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh`. Include a restart/journal recovery case for install, update and remove using the new roots. Run `git diff --check`, changed-file formatting/lint, and a path audit proving marketplace production no longer constructs `<profile-home>/workflows/.staging` or `.quarantine`.
- [ ] Commit only Task 15A code/tests as `fix(workflow): isolate marketplace transaction workspaces`. A fresh reviewer independently runs both initialization orders, recovery restart, foreign-artifact refusal and named phase-boundary changes against the documented threat model. Same-user syscall-boundary probes may be recorded as a nonblocking platform limitation but must not be reported as a supported guarantee. Do not resume locale/docs/Desktop/gate implementation until 15A is review-clean.

## Task 15B0 — Align inspection and lifecycle admission

**Spec:** `docs/superpowers/specs/2026-09-07-workflow-marketplace-inspection-admission-amendment.md`

**Files:** Modify `plugins/workflow/marketplace/operations.py`, its focused registry tests, `plugins/workflow/marketplace/lifecycle_api.py` tests as needed, `apps/desktop/src/store/workflow-marketplace-supervisor.ts`, its focused tests, and the real API/Desktop lifecycle tests already preserved by Task 15B discovery. Do not change public/generated schemas.

**Consumes:** Exact package subjects, admission receipt lookup-before-conflict ordering, backend active-target locking, the application supervisor, PackageState mutation barriers, and Task 14F exact inspection supervision.

**Produces:** One backend/Desktop admission matrix: earlier inspection may coexist with a later lifecycle operation; active lifecycle work blocks later inspection/lifecycle starts; late inspection results cannot cross a newer mutation generation. Task 15B may then exercise the full real lifecycle without an enabled action failing before admission.

- [ ] Preserve the checked-in discovery REDs. In the registry/authenticated API, event-hold a real exact-package inspection and prove that an exact removal preparation is currently rejected while locked state is installed and `busy: false`. In the real supervisor, prove `getPackageGate()` is ready but `start(remove_prepare)` currently rejects locally. Record the exact `marketplace_operation_conflict` and `marketplace_request_conflict` failures.
- [ ] Add the complete asymmetric matrix before implementation: multiple inspections; inspection then lifecycle; lifecycle then inspection; lifecycle then lifecycle; terminal lifecycle reconciliation; different package/actor/profile; exact replay before conflict; capacity/cancellation/terminal release. Tests must use exact subjects and bounded synchronization, never timestamps or "latest operation."
- [ ] Change backend private target ownership under the existing registry admission lock. Inspection checks for an active lifecycle target but never reserves it; other package lifecycle operations reserve/check the existing target. Receipt replay remains first, all records still count toward capacity, and terminal cleanup releases only a target owned by that operation. Apply the behavior to existing V1/V2 registry callers without changing V1 replay promises.
- [ ] Mirror the matrix in Desktop `start()`: inspection records do not block lifecycle starts; nonterminal lifecycle barriers block new inspection and lifecycle starts; terminal lifecycle barriers retain their existing package-gate/reconciliation behavior. Preserve exact five-field binding, request/operation/subject/selection correlation and all action guard release paths.
- [ ] Add cache-order tests where an inspection admitted first completes after install, update, remove, and trust. The historical read-only operation result may remain visible in operation history, but it must not publish detail/installed/trust/action caches across the lifecycle mutation generation, clear its barrier, or reopen a contradictory action after failed refetch.
- [ ] Classify a genuine pre-admission conflict as known non-admission. Desktop reconciles exact package/scope and renders busy/conflict guidance, never unknown-outcome copy. Do not automatically retry, queue, cancel the earlier operation, or allocate another request until an explicit retry passes current eligibility.
- [ ] Run focused RED/GREEN through the Python wrapper and Desktop Vitest, then the real temporary-Git API and Playwright removal sequence. Run lifecycle registry/API/supervisor/reconciliation/navigation regressions, typecheck, lint/format and diff/scope audits. Commit `fix(workflow): align inspection admission`. A fresh reviewer independently exercises both admission orders, replay ordering and late-result suppression before Task 15B resumes.

## Task 15B — Localization, documentation, end-to-end proof and release gates

**Files:** Modify `apps/desktop/src/i18n/{types,en,ar,ja,zh,zh-hant}.ts`, `languages.test.ts`, `docs/workflow-orchestration.md`, `website/docs/user-guide/features/workflows.md`, `website/docs/reference/cli-commands.md`; create `website/docs/user-guide/features/workflow-packages.md`, `tests/plugins/workflow/test_marketplace_installed_distribution_e2e.py`, `apps/desktop/e2e/workflow-marketplace-lifecycle.spec.ts`; modify `scripts/test_workflow_merge_gate.sh`, related gate tests, and existing E2E fixtures only as needed.

**Consumes:** All accepted Task 14 slices, review-clean Task 15A workspace isolation, and review-clean Task 15B0 inspection admission.

**Produces:** Complete locale copy, operator/publisher guidance, real Git/API/trust/admission/UI proof, reproducible branch review gates.

- [ ] Write failing E2E tests in real temporary homes and Git repositories: source → inspect → prepare → explicit confirm → untrusted admission refusal → separate one/all trust → allowed admission → changed update and trust invalidation → remove. Include rollback success/failure/ambiguity, foreign/manual grants, two profiles, cancellation, lost POST response replay, and journal recovery. Test existing public/private credential seams without collecting credentials or using external repositories.

```python
def test_lost_confirm_response_installs_once_then_requires_trust(real_marketplace):
    review = real_marketplace.prepare_install()
    request_id = real_marketplace.new_request_id()
    real_marketplace.confirm_and_drop_response(review, request_id)
    operation = real_marketplace.recover_admission(request_id)
    assert operation.outcome.type == "committed"
    assert real_marketplace.install_commit_count == 1
    assert real_marketplace.admission_code() == "workflow_trust_required"
```

`real_marketplace` starts the actual authenticated API/service with existing safe temp Git fixtures and bounded event synchronization; instrumentation counts commits while the actual filesystem/provenance/trust writes occur. The Playwright fixture launches an isolated backend and renderer; response dropping happens at the test transport boundary after actual server admission, not by substituting success payloads. Cover tab-close-return and failed-refetch action gating in that real path.

The real lifecycle also holds an already-admitted inspection while starting Remove from verified locked state. Removal must reach backend admission and terminal truth exactly once; the late inspection result remains historical and cannot overwrite the removed state or reopen Remove/Update.

- [ ] Run RED with the repository wrapper for the new E2E file; from Desktop run `npx vitest run --project ui src/i18n/languages.test.ts` and focused Playwright lifecycle tests after building the renderer. Capture specific missing behavior, not unrelated environment failure.
- [ ] Translate all source/browse/lifecycle/recovery copy in all five locales. Locale tests assert usable keys and interpolation behavior; do not write source-text scans or fixed enumeration counts.
- [ ] Document manifest/index/digest publishing, user-managed Git push, private authentication setup, selected remote/profile authority, all lifecycle steps, install/trust separation, retained cache barriers, request replay/expiry/capacity, renderer versus backend restart, package-state/recover-packages commands, and current-state versus historical-outcome limits. Do not claim Doctor performs transaction recovery or that failed rollback preserves a version.
- [ ] Add fixture `--check`, lifecycle Python/UI/parity/supervisor/E2E tests to existing workflow gates. Keep all existing checks. Ensure gate tests use temporary repos and don't mutate the actual base/brand branches. Packaging/build commands must use publication-disabled paths; no release command is authorized.
- [ ] Run focused GREEN and the final gates below. A fresh reviewer independently exercises the real temporary Git/API/Desktop lifecycle and verifies locale/docs match implemented recovery behavior. Commit `test(workflow): verify marketplace lifecycle end to end` (separate docs commit permitted).

## Final verification and whole-branch review

Use the repository interpreter selected by `scripts/run_tests.sh`; if a generator needs it explicitly, set a task-specific interpreter path found through the same existing environment. Do not change user/global configuration or install a second dependency tree. The following commands are execution requirements after approval, not claims that they passed during drafting.

From the Hermes worktree:

```bash
uv run python scripts/generate_workflow_package_contract.py --check
uv run python scripts/generate_workflow_marketplace_lifecycle_fixtures.py --check
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/ tests/hermes_cli/test_git_source.py tests/hermes_cli/test_plugin_install_ref.py tests/hermes_cli/test_plugins_cmd.py tests/hermes_cli/test_workflow_dashboard_auth.py tests/test_project_metadata.py
npm run test:workflow-ui --workspace apps/desktop
npm run typecheck --workspace apps/desktop
npm run lint --workspace apps/desktop -- --quiet
scripts/test_workflow_merge_gate.sh --phase base
git diff --check
git status --short
```

From `apps/desktop`:

```bash
npx vitest run --project ui src/api/workflow-marketplace.test.ts src/api/workflow-marketplace-lifecycle.test.ts src/lib/workflow-marketplace-codec.test.ts src/lib/workflow-marketplace-lifecycle-codec.test.ts src/lib/workflow-marketplace-connection-binding.test.ts src/lib/workflow-marketplace-supervision.test.ts src/lib/workflow-marketplace-reconciliation.test.ts src/store/workflow-marketplace-supervisor.test.ts src/i18n/languages.test.ts
npx vitest run --project electron electron/connection-generation.test.ts electron/connection-apply.test.ts electron/connection-config-apply.test.ts electron/backend-connection-state.test.ts electron/api-transport.test.ts
npx tsx --test electron/structured-api-channel.test.ts
npm run test:ui
npm run build
npx playwright test e2e/workflow-marketplace-lifecycle.spec.ts
```

Read the current merge-gate script before invoking it; run only its local test/rehearsal paths. It must not integrate the feature branch into the real `base` checkout. If a gate needs an unavailable host/browser/runtime, record the exact missing evidence and leave the release gate open; do not silently skip or mark ready. Resolve existing Task 14 report's catalog-suite failures against the exact feature-start baseline before classifying them as pre-existing. A prior implementer report is not a waiver.

- [ ] Fresh whole-branch reviewer receives `git diff c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD`, commit list, amendment, plan, ledger and verification receipts. Also inspect `base...HEAD` read-only for integration drift; do not merge to resolve it without approval.
- [ ] Audit all prior deferred findings in the ledger, including package/digest test helper exclusion, changed-file formatting, portability/flakiness conventions, provenance timestamp, repeated package hashing, and backend union correlation. Close with evidence or explicitly report an accepted nonblocking limitation; do not silently omit earlier debt.
- [ ] Reviewer tests user-visible truth independently across Python/API/TS/cache boundaries. Verified fixes follow RED/GREEN and a fresh reviewer gate; if they expose another protocol gap, amend the design before dependent implementation.
- [ ] Run exact final required checks once more only if review fixes changed their inputs. Record clean status, exact tested HEAD, artifact SHA/byte hashes for later Studio pinning, review dispositions, test outcomes and remaining environmental limitations.
- [ ] Present branch for approval. Stop with the feature worktree intact. No merge, push, publication, release, worktree deletion, or Studio work until explicitly authorized.

## Spec coverage map

| Amendment requirement                                                   | Task gate                                             |
| ----------------------------------------------------------------------- | ----------------------------------------------------- |
| Supervisor identity: backend actor binding and per-request precondition | 14C0a, 14C0b, 14C1                                    |
| Supervisor identity: native generation, route races, quarantine/purge   | 14C0b, 14C1, 14C2, 14F                                |
| A public subject / G backend correlation                                | 14A1, 14A3, 14B                                       |
| B admission/replay / epoch / eviction                                   | 14A2, 14A3, 14B, 14C0a, 14C0b, 14C1                   |
| C stable supervisor and scope isolation                                 | 14C0a, 14C0b, 14C1, 14F                               |
| D explicit outcomes/current state/recovery tooling                      | 14A3, 14C2, 14D, 15B                                  |
| E mutation barriers                                                     | 14C2, 14D, 14E                                        |
| F full-package trust / exact selection                                  | 14A1, 14A3, 14B, 14E                                  |
| G exact get/cancel/prepare/confirm identity                             | 14A1–B, 14C0a, 14C0b, 14C1, 14D–E                     |
| H keyboard/focus/capabilities/timer disposal                            | 14C1, 14E, 14F                                        |
| I generated fixtures/differential/integration matrix                    | 14B, 14C0a, 14C0b, each task's independent review, 15B |
| Inspection distribution/effective-workflow digest domain parity         | 14C2P, resumed 14C2                                   |
| Compatibility/token privacy/list stability                              | 14A2–B, 14C0a, 14C0b, 14C1, 15B                       |
| Marketplace transaction workspace ownership isolation                   | 15A                                                    |
| Inspection versus lifecycle admission and stale read suppression         | 15B0                                                   |

Implementation mode is selected and approved by the user: subagent-driven, one implementation agent at a time and a fresh reviewer per task. No further mode-selection question is required. Integration and publication remain separately approval-gated.
