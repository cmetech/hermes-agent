# Phase 3 Task 1 Independent Specification Review

**Reviewed commit:** `b677f770a182492ea7e3723733d278600309f3e7`

**Reviewed tree:** `8b57bbbea5edf975ae8bcceefd7a0b7d6f8ad6e6`

**Baseline:** `cffc23cecd801d3aed08ba66d596bec4a365a43a`

**Verdict:** CHANGES REQUIRED

**Findings:** 0 Critical, 2 Important, 0 Minor

## Scope and evidence

I read the root `AGENTS.md`, the complete approved Phase 3 design, the plan's global protocol and Task 1, and inspected the complete baseline-to-target diff. The commit is atomic, the requested identity matches, the worktree was clean before this report, and the production changes remain bounded to Task 1 language, schema, compatibility, and trust surfaces. I found no Phase 2 execution adapters or later Phase 3 scheduler/resolver/Bash/session implementation in the diff.

Focused verification was run only through the repository wrapper, with flaky file retries disabled:

```text
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python \
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_language.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_language.py \
  tests/plugins/workflow/test_language_snapshot.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_trust_policy.py \
  tests/plugins/workflow/test_compat_matrix.py \
  tests/plugins/workflow/test_doctor.py

Result: 9 files, 846 tests passed, 0 failed, no retries.
```

The controller also supplied retained RED evidence, all produced through `scripts/run_tests.sh` with retries disabled: profile/version 110 pass/1 fail; semantics/schema 653 pass/4 fail, then strengthened to 657 pass/7 fail; trust/migration 61 pass/4 fail; catalog 600 pass/10 fail; strict snapshot 17 pass/2 fail; parser audit 29 pass/1 fail. The new tests exercise runtime behavior rather than source-text matching, and the catalog completeness test relates every Task 1 durable code to a real loader emission.

The implementation otherwise meets the core Task 1 goals: new legacy packages remain on v2, new Archon packages select v3, explicit v1/v2 snapshots reload without v3 fields, requested millisecond and retry semantics are bounded and round-trip through the v3 snapshot, the v3 digest/fingerprint bind both structured outputs and node semantics, and the durable-code metadata has behavior-linked uniqueness/applicability coverage.

## Important findings

### I-1 — Language identity changes every legacy/v1/v2 trust digest

`plugins/workflow/trust.py:505-529` inserts `language_identity` into the hash document unconditionally. That changes the computed `risk_digest` for every existing legacy v2 package, and also for admitted Archon v1/v2 packages, relative to the Phase 2 hash document even when their source, profile, normalizer, and semantics are unchanged. Existing trust records are keyed to the old risk digest, so this creates an unintended retrust boundary for precisely the versions whose behavior and identity must remain stable.

This contradicts the approved design at `...design.md:216-220` (legacy remains on v2 and acquires no trust-digest change) and Task 1 at `...plan.md:147-150` (legacy v2 must not drift). The new assertions at `tests/plugins/workflow/test_phase3_language.py:325-363` compare two legacy packages both hashed by the new implementation, so they prove default-v2 equals explicit-v2 but do not compare either result to the Phase 2 risk digest. Likewise, the Archon test proves new v2 differs from new v3 but does not prove v2 retained its pre-Phase-3 identity.

**Required remediation:** Preserve the exact Phase 2 risk-hash document for legacy and admitted v1/v2 semantics. Add the effective-profile/normalizer/normalized-digest discriminator only at the new Archon-v3 trust boundary (or use another versioned construction that is byte-identical for all older versions). Add a regression fixture or independently calculated Phase 2 hash assertion proving unchanged legacy v2 and Archon v1/v2 risk digests remain exact, while the same Archon package under v3 requires retrust.

### I-2 — Required migration guidance is incomplete and includes stale Phase 2 instructions

Task 1 explicitly requires doctor migration behavior to explain seconds-to-milliseconds, retries-after-initial, direct dependencies, typed comparisons, and the Bash byte boundary (`...plan.md:147-155`). The approved design is more exact at `...design.md:952-968`: it also requires deterministic one-attempt and AI one-attempt caveats, `output_format` before field references, structured scalar guidance, and the 32,768-byte/no-pathname boundary.

The implementation updates only timeout conversion and the `N - 1` retry case (`plugins/workflow/language.py:568-591`). It does not surface the direct-dependency, typed-condition, structured-output-field, deterministic one-attempt, AI one-attempt, or 32,768-byte Bash migration guidance. It also retains obsolete text telling users to wait for Phase 2 output-format/output-type support (`plugins/workflow/language.py:593-607`), even though Phase 2 is the reviewed baseline. The new migration test checks only two substrings (`tests/plugins/workflow/test_phase3_language.py:401-407`), so the missing contract remains green.

**Required remediation:** Extend the backend-authored doctor/compatibility migration projection owned by Task 1 to cover every item in the approved design, and remove the stale “wait for Phase 2” instructions. Add behavior-level doctor/compatibility tests for the complete guidance, including the deterministic `N=1` and AI-one-attempt distinction, direct `depends_on`, `output_format` before `.field`, typed scalar conditions, and the 32,768-byte content boundary with no pathname assumption. Preserve legacy execution and trust identity while improving diagnostic text.

## Review conclusion

The v3 normalization and durable-code scaffold are substantively aligned and fully green under the focused gate, but the trust-digest drift violates the central legacy compatibility guarantee and the planned migration contract is not complete. Task 1 should not advance to quality review until both Important findings are fixed and independently reverified.
