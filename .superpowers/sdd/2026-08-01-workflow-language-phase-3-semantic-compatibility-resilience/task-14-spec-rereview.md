# Task 14 Specification Rereview

## Review identity

- Baseline: `f4f4216651d0ab4830f232df8a4d15ee1981022e`
- Original Task 14 implementation: `85234ba4c8bdb048f0b41b8f45907b20311cf32c`
- Rereview candidate: `f6dc95200fad782c4a34cd33ed97f7a17a9db698`
- Candidate tree: `018720e3d24e5eaca3dd4b1ac8d9dace00d995b9`
- Verdict: **APPROVED**
- Findings: **0 Critical / 0 Important / 0 Minor**

This rereview rechecked the sole prior Important finding and the complete Task
14 specification boundary. It modified no production code or tests. Per the
user's restriction, no threat-model, security-focused, exploit, adversarial,
or security-boundary validation was performed or invoked.

## Prior finding closure

The prior finding, "Closed Phase 3 retry evidence drops
`additional_provider_attempts`," is genuinely closed at every required layer:

1. **Python closed allowlist:** `_PHASE3_RETRY_FIELDS` now includes
   `additional_provider_attempts` at `plugins/workflow/evidence.py:33-40`.
   `_attempt_evidence_item()` continues to serialize only that explicit tuple
   plus the bounded attempt identity/state/error envelope at
   `plugins/workflow/evidence.py:385-406`.
2. **Desktop type:** `WorkflowAttemptEvidence.retry` requires
   `additional_provider_attempts: number` at
   `apps/desktop/src/types/hermes.ts:341-353`.
3. **Exact synthetic projection:** the focused mapping includes the producer
   field and asserts it survives in the exact closed response at
   `tests/plugins/workflow/test_evidence_api.py:865-909`.
4. **Real producer-to-route path:** an admitted Archon v3 workflow executes
   through `RunScheduler`, then the existing authenticated evidence route is
   queried with `kind=attempts`; the exact response retains
   `additional_provider_attempts: 0` at
   `tests/plugins/workflow/test_workflow_language_desktop_e2e.py:355-395`.

The real route assertion is exact, not a subset assertion. Therefore it also
proves that producer-only `provider_attempts_exact`, primary-output metadata,
and unrelated provider fields remain outside the public projection. This
matches the approved design's evidence field set rather than widening it to the
entire internal retry ledger.

## Closure invariants

- The sanctioned requested/effective fields, `retry_consumed`,
  `remaining_attempts`, `additional_provider_attempts`, and `capped` are all
  retained.
- Error code/message remain in their separate bounded object.
- Persistent-session recovery remains the same closed projection with the two
  sanctioned SHA-256 digests and without exact session identity, registry
  authority, pending obligations, histories, or provider payloads.
- The fix added no endpoint, route parameter, filesystem input, renderer
  parser/calculator/probe, or alternate workflow authority.
- Existing evidence-route authorization and publication-ID artifact lookup are
  unchanged.
- Compatibility/migration guidance still derives from the shared versioned
  durable-code catalog.
- Legacy attempt behavior is unchanged: the pre-existing fallback at
  `plugins/workflow/evidence.py:390-394` still returns the established legacy
  shape unless the complete Phase 3 metadata contract is present. The fix made
  that discriminator stricter by adding the newly required field; it did not
  broaden Phase 3 projection over legacy records.
- Old/new Desktop additive compatibility and generic recovery rendering are
  unchanged by the fix.

## Independent focused verification

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_evidence_api.py \
  tests/plugins/workflow/test_workflow_language_desktop_e2e.py
```

Result: **32 passed / 0 failed** across two files, with no flaky retry.

`git diff --check 85234ba4c..f6dc95200` was clean, and the worktree remained
clean apart from ignored retained review reports.

## Final disposition

The sole prior Important finding is closed. Task 14 now satisfies the approved
Phase 3 API/Desktop projection contract with no remaining specification
findings.
