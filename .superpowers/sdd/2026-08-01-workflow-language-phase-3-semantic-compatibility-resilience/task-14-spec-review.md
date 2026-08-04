# Task 14 Specification Review

## Review identity

- Baseline: `f4f4216651d0ab4830f232df8a4d15ee1981022e`
- Candidate: `85234ba4c8bdb048f0b41b8f45907b20311cf32c`
- Candidate tree: `7f801aabecb9ca4de938f7111488f5dac95aa57e`
- Scope: Task 14 specification compliance only
- Verdict: **CHANGES REQUESTED**
- Findings: **0 Critical / 1 Important / 0 Minor**

The review was read-only apart from this retained report. It inspected the
complete Task 14 diff, the governing repository and Desktop instructions, the
full approved Phase 3 design and implementation plan, the Task 14 brief and
implementation report, and the relevant retry/recovery producer contracts.
Per the user's restriction, no threat-model, security-focused, exploit, or
adversarial validation was performed or invoked.

## Finding

### Important — Closed Phase 3 retry evidence drops `additional_provider_attempts`

**Evidence**

- The approved design defines the durable charge as one workflow attempt plus
  the exact `additional_provider_attempts`
  (`docs/superpowers/specs/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience-design.md:398-409`).
- The same design explicitly requires retry evidence to contain
  `additional_provider_attempts`
  (`docs/superpowers/specs/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience-design.md:441-444`).
- The existing authoritative producer already records the field in
  `RetryLedgerGrant.evidence()` (`plugins/workflow/models.py:382-394`).
- Task 14's allowlist omits it from `_PHASE3_RETRY_FIELDS`
  (`plugins/workflow/evidence.py:33-40`), and the closed projection serializes
  only that allowlist (`plugins/workflow/evidence.py:385-405`).
- The new test fixture also omits the producer field and therefore encodes the
  incomplete shape as expected behavior
  (`tests/plugins/workflow/test_evidence_api.py:865-907`).
- The Desktop retry interface repeats the omission
  (`apps/desktop/src/types/hermes.ts:341-353`).

**Why this is specification-significant**

The requested/effective ceilings and `retry_consumed` show the aggregate
ledger, but without `additional_provider_attempts` an operator cannot tell how
much of a workflow attempt's charge came from provider retries. That removes
the evidence needed to verify the central Phase 3 non-multiplication contract
for provider-only, workflow-only, mixed, repair, and fallback paths. The field
is already bounded, durable, and backend-authored, so projecting it does not
require a new authority or expose raw provider data.

**Exact fix contract**

1. Add `additional_provider_attempts` to `_PHASE3_RETRY_FIELDS` so the closed
   attempt projection retains the producer-authored integer.
2. Add `additional_provider_attempts: number` to the Desktop
   `WorkflowAttemptEvidence.retry` shape.
3. Update the Task 14 evidence test so its metadata includes the field and its
   exact expected API projection asserts it.
4. Add or extend a real Phase 3 attempt-path assertion proving the value emitted
   by `RetryLedgerGrant.evidence()` survives the authenticated evidence route;
   do not satisfy this only with a synthetic mapping.
5. Keep the projection closed: do not restore unrelated metadata or raw
   provider payloads.

## Requirement disposition

| Task 14 requirement | Result | Evidence |
|---|---|---|
| Normalizer v3 and additive compatibility | PASS | `WorkflowDetailLanguageStatus.normalizer_version` accepts 1–3 at `plugins/workflow/dashboard/plugin_api.py:299-319`; Desktop language fields remain optional/additive at `apps/desktop/src/types/hermes.ts:147-153`. |
| Bounded compatibility and migration projection | PASS | Detail-only migration is optional and bounded at `plugins/workflow/dashboard/plugin_api.py:421-437`; `_finding_migration()` derives it from `compatibility_code_catalog()` at `:465-481`; catalog entries retain their existing summary/full rules. |
| Requested/effective retry and error projection | **FAIL** | Error is separated and the requested/effective aggregate fields are closed, but `additional_provider_attempts` is lost as described above. |
| Closed persistent-session recovery projection | PASS | The allowlist retains only the two sanctioned digests and bounded recovery facts at `plugins/workflow/evidence.py:41-51,407-420`; it excludes pending obligations, raw session identity, registry keys, histories, and provider payloads. |
| Existing authenticated routes and publication-ID lookup only | PASS | Evidence continues through the existing read-authorized route at `plugins/workflow/dashboard/plugin_api.py:1692-1713`; artifact preview/download remain publication-ID based at `:1816-1869`; the apparent decorator addition in the diff is only reformatting of the existing workflow-detail route. |
| Versioned durable-code authority | PASS | API migration guidance resolves through `compatibility_code_catalog()` rather than a duplicate list at `plugins/workflow/dashboard/plugin_api.py:465-481`, with the new relationship test at `tests/plugins/workflow/test_phase3_code_catalog.py:38-47`. |
| Desktop generic recovery rendering | PASS | `RunInspector` uses the existing evidence query and a generic deterministic formatter at `apps/desktop/src/app/workflows/run-inspector.tsx:45-75,89-99`; the recovery-tab test requests `kind=recovery` and renders `recovery_kind=persistent_session`. |
| Old/new Desktop compatibility | PASS | Additive language/evidence members are optional or remain covered by the generic record fallback; the new older-backend test verifies omitted normalizer/digest fields leave the detail usable. |
| No renderer-side workflow authority | PASS | The Desktop production diff adds only interfaces plus generic evidence formatting. It adds no condition parser, retry computation, output resolver, session probe, or filesystem access. |

## Conclusion

Task 14 is otherwise aligned with the approved backend and Desktop boundaries,
but it is not spec-complete until the already-durable
`additional_provider_attempts` evidence survives the new closed API projection
and corresponding Desktop type.
