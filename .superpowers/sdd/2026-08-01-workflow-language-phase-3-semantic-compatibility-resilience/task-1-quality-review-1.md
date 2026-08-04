# Phase 3 Task 1 Quality Review 1

**Reviewed commit:** `b677f770a182492ea7e3723733d278600309f3e7`

**Reviewed tree:** `8b57bbbea5edf975ae8bcceefd7a0b7d6f8ad6e6`

**Scope:** Diff from the approved planning commit
`cffc23cecd801d3aed08ba66d596bec4a365a43a` through Task 1, with surrounding
normalization, schema, trust, compatibility, snapshot, and test behavior.

**Verdict:** CHANGES REQUIRED

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 4 |
| Minor | 0 |

## Findings

### Important 1 — Null retry authoring bypasses stable validation or crashes normalization

**Evidence:** `plugins/workflow/language.py:417-490`

The v3 path uses `options.get("retry")`, so it cannot distinguish an omitted
field from an explicitly authored `retry: null`. The explicit null form is
silently accepted as if no retry block existed and receives the AI defaults.
Likewise, `_normalize_v3_retry()` permits `max_attempts: null` because it only
validates a non-`None` value, after which `_normalize_v3()` evaluates
`requested_retries + 1` and raises a raw `TypeError`.

Direct parser repro at the reviewed commit:

```text
retry-null ACCEPTED {'retry': {'explicit': False, 'requested_retries': 2, ...}}
max-attempts-null TypeError None unsupported operand type(s) for +: 'NoneType' and 'int'
```

This violates the approved exact retry-object shape and the requirement that
malformed requests fail with a stable bounded code rather than silently
defaulting or escaping as an implementation exception.

**Remediation:** Test key presence separately from value (`"retry" in
options`), require every authored retry value to be a mapping, and require an
authored `max_attempts` value to be an integer even when its parsed value is
null. Convert both cases to `WorkflowSemanticNormalizationError` with
`archon_retry_invalid` (or the more specific required-field code for an
actually absent deterministic `max_attempts`). Add parser-level tests for
`retry: null`, `max_attempts: null`, `delay_ms: null`, and `on_error: null` on
AI and deterministic nodes.

### Important 2 — The trust change invalidates every legacy v2 trust record

**Evidence:** `plugins/workflow/trust.py:505-539`

`build_risk_summary()` adds `language_identity` to `risk_fields`
unconditionally. Since `risk_fields` is the hashed document, every existing
unversioned and `hermes-legacy` v2 package now receives a different
`risk_digest` even when source bytes, profile, normalizer version, normalized
definition, compatibility, and all risk-bearing capabilities are unchanged.
The new test only compares default-v2 and explicit-v2 under the new algorithm;
it does not compare against the pre-Phase-3 digest and therefore cannot detect
this migration regression.

This contradicts both the global exact-legacy constraint and the approved
design statement that legacy remains on v2 and does not acquire a trust-digest
change from Phase 3. Users' previously trusted legacy packages would become
untrusted after upgrading.

**Remediation:** Preserve the historical risk document exactly for legacy
v1/v2. Bind the new language identity only where it is needed to distinguish a
new Archon v3 normalization from its prior Archon semantics (for example, a
version-gated Archon-v3 risk component). Add a golden/compatibility test that
computes the pre-Phase-3 legacy risk document and proves its digest is
unchanged, plus the existing Archon v2-to-v3 retrust invariant.

### Important 3 — An affected existing CLI test file has five failures

**Evidence:** `tests/plugins/workflow/test_cli.py:176-213`,
`tests/plugins/workflow/test_cli.py:260-383`

Task 1 intentionally removes the Phase 2 timeout/retry compatibility blockers,
but it leaves the CLI behavior tests asserting those fields still fail
validation, trust, run, and doctor. The repository test runner reports five
failures in this file:

```text
scripts/run_tests.sh tests/plugins/workflow/test_cli.py
=== Summary: 1 file, 72 tests passed, 5 failed ===
```

The failures cover both JSON and text CLI paths and the packaged module
entrypoint. This means the commit does not currently preserve the repository's
green baseline and its focused verification set is too narrow for the behavior
it changed.

**Remediation:** Update the timeout/retry cases to assert the new supported-v3
CLI behavior while retaining blocker coverage with Phase 5 fields such as
`maxBudgetUsd`/`sandbox`, and retain doctor rendering coverage using a still
active compatibility finding. Run the updated CLI test file together with the
Task 1 focused suite before handoff. Search the remaining suite for old
`archon_*_semantics_unavailable` behavioral assumptions and update only those
made obsolete by v3.

### Important 4 — The new catalog completeness tests are planned-extension change detectors

**Evidence:** `tests/plugins/workflow/test_phase3_code_catalog.py:10-24`,
`tests/plugins/workflow/test_phase3_code_catalog.py:89-96`

The tests assert that every entry in the phase-wide `PHASE3_DURABLE_CODES`
collection has area `normalization`, is a runtime failure, is not evidence, and
is exactly represented by Task 1's fixed seven-case table. The approved plan
requires later tasks to register condition, reference, Bash, session, and
evidence codes in this same authority. The first legitimate non-normalization
or evidence entry will therefore fail Task 1's tests even when the catalog is
correct.

This is the repository's prohibited change-detector pattern and conflicts with
the plan's additive code-registration protocol.

**Remediation:** Give the Task 1 normalization cases an explicit subset or
area/version filter and assert their relationships without equating them to the
entire phase registry. Build aggregate completeness from additive behavior-link
registrations owned by each later task, so adding a correctly linked code does
not require rewriting unrelated Task 1 expectations.

## Verification evidence

The planned Task 1 focused suite passed cleanly with flaky retries disabled:

```text
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

9 files, 846 tests passed, 0 failed; no retries.
```

The additional affected CLI check failed as described above. `git diff
--check` passed for the reviewed range. The worktree was clean before this
review report was written. No production or test files were modified by this
review.
