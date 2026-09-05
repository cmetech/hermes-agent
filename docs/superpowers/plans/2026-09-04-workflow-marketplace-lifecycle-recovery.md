# Workflow Marketplace Lifecycle Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`. One implementation agent at a time; a fresh reviewer after every task. Use test-driven development and verification-before-completion. Steps use checkbox syntax. The user approved this plan and its amendment on 2026-09-04 for implementation in the existing Hermes worktree only.

**Goal:** Finish the existing marketplace branch with truthful, recoverable lifecycle operations and independently reviewed release evidence.

**Architecture:** Extend the backend operation registry with safe subjects, bounded admission receipts, strict outcomes, and locked local-state reconciliation. A feature-owned application supervisor outlives Marketplace/Installed views; thin dialog adapters retain only ephemeral review secrets. Shared cache barriers prevent retained stale projections from authorizing mutations.

**Tech Stack:** Existing Python/Pydantic/FastAPI/transaction locks and Git fixtures; React/TypeScript/Nanostores/TanStack Query/Vitest/Testing Library/Playwright. No new runtime dependency is planned.

**Spec:** [Lifecycle recovery amendment](../specs/2026-09-04-workflow-marketplace-lifecycle-recovery-amendment.md), plus the unaffected parts of the [original approved design](../specs/2026-09-03-workflow-package-marketplace-design.md).

## Global constraints

- Status: approved by the user on 2026-09-04; implementation may proceed through the task review gates below.
- Continue only in `/Users/coreyellis/Developer/personal/github.com/cmetech/hermes-agent/.worktrees/workflow-package-marketplace`, branch `feat/workflow-package-marketplace`.
- Baseline `c89f36c6b8b23c430432b947e3b4f8417eb974a5` is preserved. Do not revert or delete it. Tasks 1–13 are complete; their historical checkboxes are not a restart queue.
- This plan replaces remaining Task 14 and Task 15 execution in the September 3 plan. The seven work groups are contract, Desktop parity, supervisor, package lifecycle, separate trust, accessibility, and Task 15. Contract and supervisor groups have smaller independent review gates below.
- No production/test edits until design approval. No Studio modifications, merge to `base`, push, publication, release, or worktree deletion without separate explicit approval.
- Backend owns admission, installed state, trust, and recovery evidence. Desktop never guesses operation identity or installed versions from timestamps, display names, or cached cards.
- Request IDs: `wmreq_<epoch32>_<issued_ms13>_<random32>`; first admission within five minutes, at most 30 seconds future skew; receipts retained at least 24 hours and while active; 4,096 unexpired receipts per profile; no early receipt eviction.
- Existing operation execution/result bounds remain; snapshot lists: at most 100 records/page, 1,088 records/snapshot, four snapshots/profile, 16 MiB aggregate, 30-second expiry.
- Visible polling: 500 ms, at most three simultaneous operation requests; pause hidden/disconnected; admission timeout 15 seconds. Remove cadence listeners and timers after every settlement.
- Tokens never enter shared state, query caches/keys, URLs, logs, DOM, or persistence. Token retrieval is explicit and actor/profile/review-bound.
- Keep package contract v1 artifacts byte-identical; preserve source sanitizer/U+FEFF parity, source CRUD/Refresh-all, and existing loose workflow/trust/run behavior.
- Keep package installation and trust separate. Destination requirements remain advisories; malformed/inconsistent package structures block.
- Use `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh` for Python evidence, never direct pytest. Run Desktop commands from `apps/desktop`; dependencies belong to the existing root workspace install.
- Every task requires observed RED, focused GREEN, fresh reviewer evidence, and ledger updates. No production fix is accepted only on implementer-authored tests. A new contract gap pauses dependent work for a recorded amendment.
- Update locales `ar`, `en`, `ja`, `zh`, and `zh-hant` before completion.

## Execution and review protocol

After approval, record its exact scope/date in the existing `.superpowers/sdd/2026-09-03-workflow-package-marketplace/progress.md`. Before each task, capture clean status, task-base SHA, and applicable instructions. Write `task-14a1-brief.md` / `task-14a1-report.md` (and corresponding task IDs below) in that directory; do not overwrite historical Task 13/14 reports.

Dispatch one implementation agent with the approved spec, task text, prior interface receipts, file ownership, and test commands. It writes failing behavior tests, observes the expected failure, implements the smallest change, observes passing checks, self-reviews, and commits. The controller then packages the exact base-to-head diff and sends it to a **fresh** reviewer. The reviewer checks spec compliance and code quality, independently reproduces at least one changed authority/race/failure boundary, and reports evidence. Reuse the implementer for verified fixes; use a fresh reviewer for acceptance. Do not begin a dependent task with open blocking findings. Record every finding, decision, RED/GREEN command, independent probe, changed SHA, and disposition in the ledger.

Documentation-only phase verification consists of scope/diff/link/consistency checks; no production pass is inferred from it. Implementation task commits below are new history. No agent is authorized to merge or publish.

## File and interface map

Existing implementations remain the starting point. New names below are deliberate plan interfaces, not claims that files already exist.

| File | Responsibility / task |
| --- | --- |
| `plugins/workflow/marketplace/lifecycle_models.py` | V2 Subject/Selection/Outcome/Operation/PackageState schemas and correlations, 14A1 |
| `plugins/workflow/marketplace/admissions.py` | Epoch/ID parsing, canonical private fingerprints, receipt lifetime/replay, 14A2 |
| `plugins/workflow/marketplace/operations.py` | Existing worker registry; strict publication/admission/listing, 14A1–A2; legacy terminal bridge and non-projecting active-package mutation query, 14A3b2 |
| `plugins/workflow/marketplace/lifecycle_state.py` | Locked package-state projection and domain outcome evidence, 14A3a |
| `plugins/workflow/marketplace/service.py`, `transactions.py`, `plugins/workflow/trust.py` | Exact commit/rollback evidence, authoritative token metadata, full trust snapshot at the actual write boundary, 14A3a |
| `plugins/workflow/marketplace/catalog.py`, `source_store.py`, source portion of `lifecycle_state.py` | Source-cache publication evidence, 14A3b1; preserve strict source diagnostics |
| `plugins/workflow/marketplace/lifecycle_api.py` | V2 router, capability/token/admission/local-state endpoints, 14A3b3 |
| `plugins/workflow/marketplace/api.py` | Legacy read/source bridge and preview mutation retirement, 14A3b2; mount V2 and actual profile-context integration, 14A3b3 |
| `plugins/workflow/marketplace/cli.py` | Read-only package-state and explicit recovery adapters, 14A3c |
| `scripts/generate_workflow_marketplace_lifecycle_fixtures.py` | Deterministic Python public-model/scenario corpus and check/write modes, 14B |
| `tests/fixtures/workflow-marketplace-lifecycle-v2.json` | Token-free cross-language operation/state corpus, 14B |
| `apps/desktop/src/types/workflow-marketplace-lifecycle.ts` | Strict V2 types, 14B |
| `apps/desktop/src/lib/workflow-marketplace-lifecycle-codec.ts` | Strict V2 decoding and cross-field checks, 14B |
| `apps/desktop/src/api/workflow-marketplace-lifecycle.ts` | Scoped V2 API helpers and requested-ID enforcement, 14B |
| `apps/desktop/src/store/workflow-marketplace-supervisor.ts` | Application-lifetime Nanostore records and public methods, 14C1 |
| `apps/desktop/src/lib/workflow-marketplace-supervision.ts` | Pure transitions, exact correlation, guard/poll scheduling, 14C1 |
| `apps/desktop/src/lib/workflow-marketplace-reconciliation.ts` | Query invalidation and generation/barrier policy, 14C2 |
| `apps/desktop/src/main.tsx` | Initialize/dispose main-window supervisor alongside QueryClient, 14C1 |
| `apps/desktop/src/app/workflows/marketplace/use-package-lifecycle.tsx` | Thin view/dialog adapter, 14D–E |
| Existing Marketplace dialogs, `index.tsx`, `installed-packages.tsx`, `package-detail.tsx`, `query-keys.ts`, Workflows `index.tsx` / `catalog.tsx` | Shared supervisor/barrier consumers and keyboard/focus behavior, 14C2–F |

Python public names: `LifecycleSubject`, `LifecycleSelection`, `LifecycleOutcome`, `LifecycleOperation`, `PackageState`, `TrustSnapshot`, `LifecycleAdmissionStore`, `read_package_state(service, identity)`, `create_lifecycle_router(context, verified_operator)`. Reuse existing `InstalledPackageIdentity`, result values, service signatures, and sanitized projections; V2 reviews omit token fields without changing CLI review objects.

Desktop names: `decodeLifecycleOperation(value)`, `decodePackageState(value)`, `startLifecycleOperation(intent, scope, requestId)`, `getLifecycleOperation(id, scope)`, `cancelLifecycleOperation(id, scope)`, `getLifecycleAdmission(requestId, scope)`, `listLifecycleOperations(cursor, scope)`, `getPackageState(identity, scope)`, `getLifecycleReviewToken(operation, scope)`. All inputs/outputs use types in the new lifecycle type module. Existing `@/hermes` barrel re-exports them.

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

## Task 14C1 — Application operation supervisor

**Files:** Create `store/workflow-marketplace-supervisor.ts` / `.test.ts`, `lib/workflow-marketplace-supervision.ts` / `.test.ts`, and a small `app/workflows/marketplace/supervisor-provider.tsx` if React context is needed; modify `src/main.tsx` for main-window lifecycle only.

**Consumes:** 14B helpers/types, existing connection/profile/visibility stores.

**Produces:** The supervisor factory/public methods defined above, with memory-only records and injectable clock/transport. `getPackageGate` initially exposes busy/unknown states; 14C2 supplies reconciliation.

- [ ] Write transition-table tests and deterministic event tests for close/tab/route survival, A→B→A, disconnect/reconnect, same connection ID reconfiguration, lost POST response, exact replay, evicted result, cancel racing commit, StrictMode, old generation settlement, update-check status-error guard release, hidden cadence cleanup and concurrent request bounds.

```typescript
it('retains admission correlation after view detachment', async () => {
  const harness = createSupervisorHarness(corpus)
  const pending = harness.supervisor.start(harness.confirmIntent, scopeA)
  harness.loseAdmissionResponse()
  harness.detachView()
  await pending
  await harness.supervisor.reconcileScope(scopeA)
  expect(harness.record().operationId).toBe(corpus.admittedConfirm.id)
  expect(harness.admittedWorkerCount()).toBe(1)
})
```

Create the test harness in this suite using a real supervisor and fixture-decoding API fake, deterministic clock and deferred responses. It simulates the server receipt table, not optimistic UI outcomes; backend one-worker proof remains in 14A2/A3.
- [ ] Run RED: `npx vitest run --project ui src/store/workflow-marketplace-supervisor.test.ts src/lib/workflow-marketplace-supervision.test.ts`.
- [ ] Implement pure transitions, immutable request/operation correlation and synchronous call guards. Individual call guards release in `finally`; barriers remain separate. Attach one application instance above Workflows routes, not inside Marketplace or Installed. Keep DOM focus and dialog state outside it.

```typescript
try {
  await recoverExactRequest(record)
} finally {
  releaseCallGuard(record.key, callGeneration)
}
```

- [ ] Implement actor-owned snapshot-list reconciliation, direct lookup for every unresolved known request/ID, bounded poll queue and disposal. Unknown admission stays blocked until found or admission window closes plus state reconciliation; no “latest” recovery. Clear sensitive replay closures after bounded admission lifetime.
- [ ] Run GREEN, V2 API/codec suites, typecheck/lint/format. Fresh reviewer independently races timeout, navigation and a late admission; verifies exact scope invalidation cannot target foreground B. Commit `feat(desktop): supervise marketplace operations across navigation`.

## Task 14C2 — Shared cache reconciliation barriers

**Files:** Create `lib/workflow-marketplace-reconciliation.ts` / `.test.ts`; modify supervisor, Marketplace `query-keys.ts`, `index.tsx`, `package-detail.tsx`, `installed-packages.tsx`, Workflows `catalog.tsx` / `index.tsx`, and their behavior tests.

**Consumes:** 14C1 records/gates and 14A3 `PackageState`.

**Produces:** Per-package query-generation barriers and action gating for all consumers, using the amendment §7 table.

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

## Task 14D — Install, update and removal lifecycle adapters

**Files:** Refactor `use-package-lifecycle.tsx`, install/remove dialogs, Marketplace/Installed integration and corresponding tests. Extract `package-lifecycle-presentation.ts` / `.test.ts` for outcome-to-copy decisions. Keep shared review sections.

**Consumes:** Supervisor and package gates, exact V2 reviews/outcomes.

**Produces:** Thin prepare/review/token/confirm flow, truthful terminal presentation, explicit current/error/orphaned update-check results.

- [ ] Replace tests expecting unconditional “nothing installed/version remains” from generic failures with table-driven proof-based assertions. Cover update old_version differing from card version; removal current_version differing from card; lost admission; rollback failed/ambiguous; status/terminal ID mismatch; no-op update token absence; expired token; safe direct-subject handling without a new form.

```typescript
it.each(['rollbackFailed', 'recoveryAmbiguous', 'lostConfirmResponse'] as const)(
  '%s cannot claim the prior version remains', async scenario => {
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

## Task 15 — Localization, documentation, end-to-end proof and release gates

**Files:** Modify `apps/desktop/src/i18n/{types,en,ar,ja,zh,zh-hant}.ts`, `languages.test.ts`, `docs/workflow-orchestration.md`, `website/docs/user-guide/features/workflows.md`, `website/docs/reference/cli-commands.md`; create `website/docs/user-guide/features/workflow-packages.md`, `tests/plugins/workflow/test_marketplace_installed_distribution_e2e.py`, `apps/desktop/e2e/workflow-marketplace-lifecycle.spec.ts`; modify `scripts/test_workflow_merge_gate.sh`, related gate tests, and existing E2E fixtures only as needed.

**Consumes:** All accepted Task 14 slices.

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
npx vitest run --project ui src/api/workflow-marketplace.test.ts src/api/workflow-marketplace-lifecycle.test.ts src/lib/workflow-marketplace-codec.test.ts src/lib/workflow-marketplace-lifecycle-codec.test.ts src/lib/workflow-marketplace-supervision.test.ts src/lib/workflow-marketplace-reconciliation.test.ts src/store/workflow-marketplace-supervisor.test.ts src/i18n/languages.test.ts
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

| Amendment requirement | Task gate |
| --- | --- |
| A public subject / G backend correlation | 14A1, 14A3, 14B |
| B admission/replay / epoch / eviction | 14A2, 14A3, 14B, 14C1 |
| C stable supervisor and scope isolation | 14C1, 14F |
| D explicit outcomes/current state/recovery tooling | 14A3, 14C2, 14D, 15 |
| E mutation barriers | 14C2, 14D, 14E |
| F full-package trust / exact selection | 14A1, 14A3, 14B, 14E |
| G exact get/cancel/prepare/confirm identity | 14A1–B, 14C1, 14D–E |
| H keyboard/focus/capabilities/timer disposal | 14C1, 14E, 14F |
| I generated fixtures/differential/integration matrix | 14B, each task's independent review, 15 |
| Compatibility/token privacy/list stability | 14A2–B, 14C1, 15 |

Implementation mode is selected and approved by the user: subagent-driven, one implementation agent at a time and a fresh reviewer per task. No further mode-selection question is required. Integration and publication remain separately approval-gated.
