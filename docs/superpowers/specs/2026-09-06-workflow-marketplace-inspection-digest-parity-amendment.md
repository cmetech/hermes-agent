# Workflow Marketplace Inspection Digest Parity Amendment

**Date:** 2026-09-06

**Status:** Approved by the user on 2026-09-06 for implementation in the existing isolated Hermes worktree. This approval authorizes Task 14C2P and, once it is review-clean, resumption of the stopped Task 14C2 fix. It does not authorize Workflow Studio changes, merge, push, publication, release, or worktree deletion. The bounded Task 14C2 failing tests remain preserved at `d5676aaa56d643ef0b075b09c2e9f3908852f479` plus three uncommitted test/harness files.

**Amends:** [workflow package marketplace design](2026-09-03-workflow-package-marketplace-design.md) §14, [lifecycle recovery amendment](2026-09-04-workflow-marketplace-lifecycle-recovery-amendment.md) §§3, 7, and 10, and the [strict wire parity amendment](2026-09-05-workflow-marketplace-strict-wire-parity-amendment.md). The [HTTP version compatibility addendum](2026-09-05-workflow-marketplace-http-version-compatibility.md) remains unchanged.

**Execution:** [lifecycle recovery implementation plan](../plans/2026-09-04-workflow-marketplace-lifecycle-recovery.md), Task 14C2P before resuming the stopped Task 14C2 fix round.

## 1. Decision and authority boundary

The backend `PackageInspection` model and marketplace service own digest meaning. Desktop strictly validates the public shape and each digest domain, but it must not add a cross-field equality rule that the backend contract does not define.

An inspection intentionally contains two different digest layers:

| Field                                           | Backend meaning                                                                                                                                                         | Valid relationship                                                                               |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| inspection `packageDigest` / `package_digest`   | Verified composite digest of the fetched distribution: all package-owned bytes covered by `digests.json`                                                                | One digest for the inspected distribution; this is the candidate/installed distribution identity |
| `workflows[i].packageDigest` / `package_digest` | Effective trust digest for workflow `i`, derived from the distribution digest, canonical workflow-relative path, and that workflow's executable-resource closure digest | May differ from the distribution digest and from every sibling workflow digest                   |
| `workflows[i].riskDigest` / `risk_digest`       | Risk projection digest for workflow `i`                                                                                                                                 | Correlates only with that workflow's effective trust digest and reviewed risk facts              |

The existing public field names remain unchanged for compatibility even though both levels historically use “package digest.” Consumers distinguish them by structural position. No digest is inferred from another.

The backend continues to own fetched bytes, distribution verification, effective workflow trust calculation, and trust lookup. Desktop may keep inspection data in its query cache, but candidate action readiness additionally requires the application supervisor's exact post-barrier inspection-admission evidence. Neither digest value proves freshness by itself.

## 2. Evidence for the amendment

The V1 backend inspection route calls `WorkflowMarketplaceService.inspect()`. That service publishes the fetched distribution digest at the inspection root and publishes each workflow assessment's effective marketplace trust digest inside `workflows`. `PackageInspection` validates identity, ordering, status, and resource relationships without equating those digest fields. V2 `SnakePackageInspection` is a snake-case projection of the same model and preserves the same values.

The backend-generated `service inspect` fixture demonstrates the valid distinction:

```json
{
  "package_digest": "a430672aca08ad77c25f03e1c57bf39bef255c3a14d59a6f89cce9268c404395",
  "workflows": [
    {
      "workflow_name": "A",
      "package_digest": "5fc71713174f222672ccce00f572ce3f23dcd83e7134cbaff9c01129968c6191"
    },
    {
      "workflow_name": "B",
      "package_digest": "bade48acb1bf58ab2a1640940b007218007cb82ff4216bfb10d5e9280dae819d"
    }
  ]
}
```

All three values are lowercase SHA-256 strings and all are authoritative in their own domains. The current Desktop V1 decoder rejects this shape solely because it requires every workflow digest to equal the root distribution digest. That extra equality is a Desktop defect, not a backend ambiguity.

## 3. V1 and V2 wire behavior

V1 and V2 inspection payloads share digest semantics. V1 retains its existing operation eligibility and response shape. V2 retains snake-case keys and strict lifecycle envelopes. This amendment does not permit a V2 operation to be downgraded through a V1 endpoint.

For both versions:

- Require the root and every workflow digest to be exactly 64 lowercase hexadecimal characters.
- Require unique, sorted workflow names and all existing identity, status, resource-role, path, diagnostic, and bounds checks.
- Do not require a workflow digest to equal the root distribution digest.
- Do not require sibling workflow digests to be equal or different; content determines them.
- Preserve every digest byte exactly. Do not rewrite, substitute, normalize, or recompute a public digest in Desktop.
- Use the root distribution digest for candidate/installed distribution correlation.
- Use each workflow's effective package digest together with its risk digest for workflow trust review/state correlation.

Install and update confirmation continue binding the reviewed distribution digest. Trust preparation/grant continues binding the installed distribution plus exact per-workflow effective/risk digests. A consumer must never use the root distribution digest where an effective workflow trust digest is required, or vice versa.

## 4. Desktop correction and freshness separation

The Desktop V1 `decodeMarketplacePackageDetail` correction removes only the unsupported workflow-to-root digest equality condition. It does not relax closed-object fields, scalar domains, sorting/uniqueness, identity/status correlations, repository/path safety, resource-role validation, or operation kind/result checks. The generated V2 decoder remains unchanged unless differential tests expose a separate mismatch.

Digest parity and lifecycle freshness are separate gates:

```text
backend inspection admission
  -> immutable operation ID + candidate projection
Desktop supervisor records the current mutation-barrier generation
for that exact admitted inspection ID and captured five-field binding
  -> strict V1/V2 digest-domain decoding
  -> current package-state reconciliation
  -> candidate is actionable only when its Desktop-recorded admission generation
     is at or after the latest package mutation barrier
```

Re-fetching an old operation can revalidate its bytes and shape, but cannot assign it a newer admission generation. A historical inspection with valid distinct digests remains “last observed” after a later mutation. A fresh candidate requires a newly admitted, exactly correlated post-barrier inspection. No timestamp, latest-operation, kind-only, digest-equality, or result-rewrite heuristic is allowed.

## 5. Generated compatibility fixture

Extend `scripts/generate_workflow_marketplace_lifecycle_fixtures.py` to own a deterministic `tests/fixtures/workflow-marketplace-inspection-v1.json` compatibility corpus. Its V1 inspection case must use the generator's real temporary Git repository and service, then start a genuine V1 registry operation with no caller-supplied request ID. The worker follows `_legacy_completion(complete_read(... service.inspect(...)))`; retrieve the exact operation through `get_legacy()` and serialize the strict returned `MarketplaceOperation` with `model_dump(mode="json", by_alias=False)`, matching the V1 HTTP routes' snake-case response. Do not use the internal camel-case `_public` helper, wrap a V2 operation, or manually replace digest values.

The fixture must contain at least two workflows whose effective workflow digests differ from the root distribution digest. The generator's `--check` mode owns byte-for-byte drift detection, with `tests/plugins/workflow/test_marketplace_lifecycle_fixtures.py` covering generation/reproduction behavior. Desktop `workflow-marketplace-codec.test.ts` consumes that exact generated V1 payload. The existing backend-generated V2 inspection case remains the differential oracle for shared model semantics.

Negative Desktop tests mutate one independent property at a time: malformed root digest, malformed workflow digest, duplicate/unsorted workflow name, resource mismatch, identity mismatch, and unexpected field. They must continue failing. An equality-only mutation is not a negative case because equality is allowed when content happens to produce it; inequality is likewise valid.

## 6. Migration and compatibility impact

- No backend route, model, serializer, operation registry, package-contract artifact, digest algorithm, installed layout, provenance, transaction journal, trust record, or API version changes.
- No persisted Desktop data migration. Reopening/refetching a previously rejected valid V1 inspection makes it readable, subject to the existing supervisor freshness barrier.
- Existing hand-shaped fixtures with equal digest values remain valid but cannot prove parity. Backend-generated distinct-digest fixtures become required coverage.
- No Workflow Studio change. The package contract consumed by Studio remains byte-identical.
- No new secret, token, digest, actor identity, or operation metadata enters URLs, logs, query keys, DOM, or persistence.
- Security becomes stricter in meaning: Desktop stops conflating distribution identity with per-workflow trust identity while retaining every actual structural and cryptographic-format check.

## 7. Acceptance matrix

| Gate          | Boundary              | Required evidence                                                                                                                                                            |
| ------------- | --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 14C2P         | Backend V1 generation | Real temporary Git/service/V1 operation produces root distribution digest plus distinct A/B effective workflow digests                                                       |
| 14C2P         | Backend V2 parity     | Existing generated V2 operation preserves the same inspection model semantics                                                                                                |
| 14C2P         | Desktop V1 positive   | Exact generated V1 payload decodes without rewriting any digest                                                                                                              |
| 14C2P         | Desktop V1 negatives  | Closed shape, digest syntax, identity/status, ordering, resource-role and safe-path mutations still reject                                                                   |
| 14C2P         | Version compatibility | V1 fixture originates from a V1 admission; no V2-to-V1 operation downgrade is introduced                                                                                     |
| resumed 14C2  | Lifecycle freshness   | Old pre-barrier inspection with valid distinct digests remains last-observed after mutation, failed replacement admission, polling, query removal/refetch, and view remount  |
| resumed 14C2  | Exact correlation     | Unknown/unadmitted operation GET cannot establish candidate freshness; wrong ID/subject/result remains rejected                                                              |
| resumed 14C2  | Scope isolation       | Connection/profile/principal/epoch changes cannot reuse inspection admission evidence across scopes                                                                          |
| resumed 14C2  | Catalog truth         | Later busy, recovery-required, or unconfirmed locked package state fences catalog actions and requires appropriate fresh reconciliation before reopening                     |
| staged review | Review independence   | The 14C2P reviewer proves fixture/decoder/version boundaries and may reproduce the known race; resumed 14C2 review requires the historical-inspection and catalog races pass |

Task 14C2P does not fix or accept the stopped lifecycle races. Its fresh reviewer may reproduce the known historical-operation failure without blocking digest-parity acceptance. Only after 14C2P is review-clean may resumed 14C2 replace the stopped harness's invalid V2-envelope wrapper with the generated V1 fixture and make the admission-lineage and catalog-authority regressions pass.

## 8. Rejected alternatives

| Alternative                                                        | Decision                                                                                                                      |
| ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| Rewrite workflow digests to the root digest in fixtures or Desktop | Rejected: fabricates trust identity and hides a valid backend result                                                          |
| Make the backend equate both digest layers                         | Rejected: destroys per-workflow closure binding and changes trust semantics                                                   |
| Move browsing wholesale to V2 during Task 14C2                     | Rejected for this correction: broadens compatibility and adapter scope; later lifecycle adapters already use V2 where planned |
| Accept any loose package-detail object                             | Rejected: only the unsupported equality is removed; all strict validation remains                                             |
| Treat a freshly fetched old operation as a fresh inspection        | Rejected: fetch time is not admission time and cannot cross a mutation barrier                                                |
