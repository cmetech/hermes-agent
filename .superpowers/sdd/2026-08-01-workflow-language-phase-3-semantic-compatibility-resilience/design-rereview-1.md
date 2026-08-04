# Phase 3 Semantic Compatibility and Resilience — Design Rereview 1

**Reviewed HEAD:** `9b2fd01b7a5b7e56d0529853072290a5dfc2a01d`

**Verdict:** Changes required before implementation planning. All nine findings from `design-review-1.md` are closed, but one new Important internal contradiction remains.

**Severity count:** 0 Critical, 1 Important, 0 Minor.

## Original finding disposition

| Finding | Status | Revised contract |
|---|---|---|
| I-1 — Effective-semantics snapshot and limit authority | Closed | Lines 224–299 define the exact versioned projection, all five sealed limit fields, admission authority across entry points, canonical sealing, changed-config resume verification, and direct execution without current-config resolution. |
| I-2 — Omitted Bash/script timeout default | Closed | Lines 307–323 normalize omission to requested `120.0` seconds and cap it with the sealed subprocess ceiling. |
| I-3 — Deadline durability | Closed | Lines 330–339 correctly define timeout as an upstream-compatible per-workflow-attempt contract. Each workflow retry receives a new attempt deadline; active-claim crash recovery prevents an in-flight attempt from being silently extended. No invented cross-retry total deadline is required. |
| I-4 — Reference grammar and named script coverage | Closed | Lines 446–491 define one v3 ASCII grammar across all consumers, reject incompatible Archon node IDs and unsupported paths, and explicitly block recognized references in sealed named script resources. |
| I-5 — Typed versus rendered output facets | Closed | Lines 496–527 and 626–630 separate `typed_value` from `rendered_text`, assign consumers to the correct facet, and specify structured root-scalar behavior. |
| I-6 — Transient resolver outcome and hot-loop prevention | Closed | Lines 531–559 exclude transient reads from terminal conversion and define a durable, fenced, bounded resolution-wake protocol with exhaustion, restart, and multiprocess behavior. |
| I-7 — Spill pathname race | Closed | Lines 651–682 keep verified read-only descriptors open through launch and consume them through explicitly inherited handles without reopening a pathname. |
| I-8 — Unsupported shell lexical contexts | Closed | Lines 684–704 bound interpolation to lexer-proven simple-token contexts and reject heredocs, substitutions, expansions, and ambiguous states. |
| I-9 — Session CAS crash ordering | Closed | Lines 770–845 and 847–885 atomically journal the winning result and private update obligation before CAS, make observation idempotent, block finalization while pending, and define crash/cancellation/operational-failure recovery without provider replay. |

## Important finding

### N-I-1 — The exact snapshot rejects a valid requested total of six

**Design references:** lines 248–271 define the exact `phase3_execution_semantics` retry fields and state that “every attempt count is an integer from 1 through 5.” Lines 359–370 accept authored `max_attempts` values 1 through 5 as retries after the initial execution and explicitly require `max_attempts: 5` to record six requested total attempts before capping the effective total to five.

These requirements cannot both hold. A valid Archon `max_attempts: 5` must serialize `requested_retries: 5` and `requested_total_attempts: 6`, but the exact snapshot validator as written permits no attempt count above five. Implementing the 1–5 rule would reject or corrupt the documented boundary case; implementing the retry math would violate the exact snapshot contract and its resume validation.

**Remediation:** Specify field-specific ranges: `requested_retries` is 0 through 5, `requested_total_attempts` is 1 through 6, and `effective_total_attempts` plus `combined_total_attempts` are 1 through 5. Keep `max_attempts: 5` represented as requested total 6, effective total 5, and `capped: true`. Add an exact snapshot round-trip/resume test for this boundary.

## Required disposition

Resolve N-I-1 in the exact snapshot contract and its tests. No other Critical, Important, or Minor findings remain from this rereview.
