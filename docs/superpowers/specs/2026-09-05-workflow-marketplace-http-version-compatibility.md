# Workflow Marketplace HTTP Version Compatibility Addendum

**Status:** Proposed; user approval required before Task 14A3b3 production or test changes. The September 4 lifecycle design remains approved; this new migration decision is not yet approved.

**Scope:** Clarifies section 10 of the [lifecycle recovery amendment](2026-09-04-workflow-marketplace-lifecycle-recovery-amendment.md). Execution stays in [Task 14A3b3](../plans/2026-09-04-workflow-marketplace-lifecycle-recovery.md#task-14a3b3--authenticated-v2-routes-and-actual-context-lifetime), not a restart of accepted tasks.

## Evidence and decision needed

At `cd7558d3c2c1cca14ee48ce40bd1564e177fc21e`, V1 operation list/get/cancel routes declare strict `MarketplaceOperation` response models (`api.py:1282–1339`), while registry `get`, `list` and `cancel` select the model by the operation's admission version (`operations.py:1250`, `1451`, `1483`). Mounting V2 would allow a V2 record into a V1 response. Cancellation can already change the record before response validation fails. Conversely, V2 cancellation of a V1 record would receive a V1 projection from the existing method.

The approved amendment requires V1 read compatibility and V2 observation of V1 work, but does not specify reverse V1 observation of V2 work. This is a public compatibility policy, not merely a serializer fix. Task 14A3b2 remains accepted: it deliberately did not mount V2 and independently verified complete internal dual observations for surviving V1 starts.

## Alternatives

| Approach | Consequence |
| --- | --- |
| Version-scoped V1 observation; complete V2 observation (recommended) | Preserves exact legacy response shapes, avoids lossy outcome conversion, and keeps V2 the recovery-aware interface. Old clients do not monitor V2-started work. |
| Convert every V2 record into V1 | Cannot preserve explicit outcomes and token-free review semantics faithfully; introduces downgrade policy and possible misleading legacy interpretation. Rejected. |
| Retire V1 operation reads/cancel entirely | Simpler routing, but breaks the approved surviving refresh/inspect/update-check workflow. Rejected. |

## Proposed public behavior

The requested HTTP version chooses the response schema. Immutable admission version chooses eligibility for V1, not the receipt's existence: surviving V1 starts also have server-generated receipts.

| Request | Own V1-started record | Own V2-started record | Foreign actor/profile, missing or evicted record |
| --- | --- | --- | --- |
| V1 list | Included in original V1 shape | Outside this version-scoped collection | Excluded by existing ownership/retention rules |
| V1 get exact ID | Original V1 shape | `409 marketplace_lifecycle_upgrade_required` | Existing bounded 404 |
| V1 cancel exact ID | Existing cancellation semantics, V1 response | Same bounded 409 **before cancellation** | Existing bounded 404, no cancellation |
| V2 list/get | Strict token-free V2 projection | Strict token-free V2 projection | Existing scoped list/get rules |
| V2 cancel exact ID | Existing cancellation semantics, strict V2 response | Existing cancellation semantics, strict V2 response | Existing bounded 404, no cancellation |

The upgrade response is exactly `{"detail":{"code":"marketplace_lifecycle_upgrade_required"}}`. Authentication/permission checks happen first (read for list/get, admin for cancel). Actor/profile ownership lookup precedes any version-disclosing rejection. Malformed IDs retain the existing bounded 404. An unauthorized or foreign caller cannot use upgrade errors to discover another actor's work.

V1 list filters by exact actor and admission version **before** offset/limit pagination, preserving existing ordering and bounds. Interleaved V2 records cannot create a short page that hides remaining V1 records. This is deliberate collection membership, not skipping invalid records: an eligible V1 record that cannot be strictly projected fails the request rather than disappearing. V2 snapshot listing still includes every eligible actor-owned V1/V2 record or fails closed; its bounds and completeness rules do not change.

Version eligibility, actor lookup and the decision to cancel are checked at the same registry synchronization boundary. Do not perform cancellation and then discover a response-version mismatch. Do not inspect a public result discriminator or parse the private generic target to infer admission version. Existing internal `get/list/cancel` consumers keep their behavior; HTTP adapters select an explicit versioned observation method.

## Authority and lifetime

No new operation, admission, token, outcome, capability, or persisted schema is introduced. Request replay, epoch/profile retention, exact subject correlation and accepted cancellation evidence remain unchanged. V1 cannot cancel V2 work; an authorized V2 client can observe and request cancellation of either version, subject to the same backend atomic boundary.

An empty V1 list says nothing about V2 work or current installed state. Legacy source/read compatibility does not authorize legacy package mutations; those endpoints remain retired. V2 Desktop uses complete V2 observations and authoritative package-state reconciliation, never the V1 list to clear a barrier. Missing/evicted history never proves rollback or absence.

Navigation, application restart and backend restart retain the September 4 guarantees. This policy does not add durable history or raw-token recovery. All list/get/cancel/replay history remains token-free on V2; no preparation token is reconstructed or downgraded into V1 history.

## Implementation and acceptance

After approval, Task 14A3b3 may add narrow version-specific observation methods in `operations.py` and matching operation tests, alongside its existing API/context ownership. No accepted domain producer or source publication rule changes.

Required independent behavior tests:

- Interleave more than one page of own V1/V2 records plus foreign records; V1 pagination returns every V1 ID once, with unchanged shape, while V2 snapshot contains every own record of both versions.
- Exercise both versions against pending/running/succeeded/failed/cancelled records; response schema follows the endpoint, not the admitted version.
- V1 get/cancel of own V2 ID returns exact bounded 409; cancel leaves cancellation token, future, operation state, receipt and real domain bytes unchanged.
- Foreign/missing/malformed IDs keep bounded 404 after normal authentication, with no version leak or side effect; read-only credentials cannot cancel.
- V2 cancellation of a V1 read returns the exact requested ID in strict V2 shape and preserves committed/unknown evidence on late cancellation.
- Invalid eligible projections fail closed rather than disappearing; no list or response leaks tokens, private targets, bodies or fingerprint material.

Use the existing required Python wrapper with retries disabled, real HTTP/registry workers and temporary Git/profile fixtures. Observe RED before production changes, then focused GREEN and the b3 final regression gate. A fresh reviewer must independently execute the compatibility boundary before V2 route acceptance. Backend/CLI/Desktop and whole-branch gates remain mandatory; no merge/push/publication/Studio changes are authorized.
