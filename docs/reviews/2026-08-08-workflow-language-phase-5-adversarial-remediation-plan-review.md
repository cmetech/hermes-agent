# Workflow Language Phase 5 Adversarial Remediation Plan Review

**Date:** 2026-08-08

**Verdict:** GO

**Reviewed artifact:**
`docs/superpowers/plans/2026-08-08-workflow-language-phase-5-adversarial-remediation.md`

**Authority:**
`docs/superpowers/specs/2026-08-08-workflow-language-phase-5-adversarial-remediation-design.md`

## Review scope

An independent reviewer checked the remediation plan for:

- compliance with the approved F-1 through F-4 remediation design;
- strict RED-before-GREEN sequencing and executable commands;
- exact repository file and test paths;
- atomic commit and upstream-customization-ledger boundaries;
- v1-v4, normalizer, snapshot, cache, recovery, redaction, and public-wire compatibility;
- installed-distribution, Desktop, upstream-rehearsal, and branded regression gates; and
- Git/worktree/release safety and preservation constraints.

The reviewer did not edit the plan or production code.

## Initial findings and disposition

The first bounded review returned BLOCK with seven Important findings and no
Critical findings.

| Finding | Disposition |
| --- | --- |
| Endpoint identity did not sufficiently distinguish provider defaults, genuinely endpointless contracts, and malformed/unresolvable routes | Resolved. The plan now resolves explicit routes then provider-profile defaults, permits a versioned sentinel only for the code-owned endpointless API-mode set, and blocks every other empty/malformed/unclassified route. The RED matrix covers default, structural-query, malformed, sentinel-to-concrete, and absent-to-concrete behavior. |
| Endpoint drift used the wrong failure code | Already resolved before the reviewed snapshot was read. Both primary and structured-repair drift tests require `provider_capability_drift`; `context_incompatible` remains limited to shared-context semantics. |
| Mismatch diagnostics could disclose endpoint or registration digests | Resolved. Diagnostics may report only stable codes and mismatched field names, never identity values. Defensive canaries cover diagnostics, evidence, notifications, catalog/detail, REST, Desktop payload/render output, and logs. |
| Structured-repair drift tests appeared after production implementation | Resolved. Authority, drift, no-side-effect, and budget terminality tests are all introduced RED in Task 2 Step 1; implementation follows in Step 2; Step 3 is a GREEN rerun. |
| Shared-context projection stripped semantic `role`/inline identity | Resolved. The structural exclusion set is limited to node/route/source-location fields authorized by the design. Role and inline-agent identity remain semantic input. |
| Tasks 2 and 3 deferred generic-seam ledger ownership | Resolved. Both tasks update and test `docs/upstream-customizations/workflow-orchestration.yaml` in their own atomic commits without advancing `last_verified_upstream`. |
| Nested release/brand scripts could use retry defaults | Resolved. The exact upstream, base, and brand rehearsal command blocks export `HERMES_TEST_FILE_RETRIES=0` before invoking scripts that launch the test wrapper internally. |

A convergence pass found one remaining Important issue: the endpoint-digest
canary covered diagnostics/evidence/logs but not every prohibited client
surface. The plan was updated to exercise notifications, catalog/detail, REST,
and Desktop payload/render output as well.

## Final verdict

The final blocker-only convergence review returned **GO** with zero unresolved
Critical or Important findings.

This is a plan-quality verdict, not implementation authorization and not a
claim that F-1 through F-4 are fixed. The candidate remains blocked until the
plan is implemented, all gates pass, and a fresh adversarial code review
returns GO.
