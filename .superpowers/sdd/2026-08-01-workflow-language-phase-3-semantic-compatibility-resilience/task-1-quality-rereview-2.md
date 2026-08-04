# Phase 3 Task 1 Quality Rereview 2

**Reviewed commit:** `896afabd407696e1fc9fc3d06f0b97458be716fe`

**Reviewed tree:** `1d36ad36595e3201a891d3cec4f11bceef1e3fca`

**Fix baseline:** `473ea197b5e2f7322a136ad9b877edd3fbaf5cac`

**Verdict:** APPROVED

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 0 |
| Minor | 0 |

## Closure of the remaining Important finding

The migration guidance is now node-kind aware at the legacy-to-Archon v3
boundary.

- Legacy `approval` and `cancel` retry blocks report that retry cannot migrate
  under Archon v3 and must be removed or redesigned. This branch is selected
  before attempt-count guidance, so it is correct for both one total attempt
  and totals greater than one. The new table-driven behavior test covers both
  node kinds at totals `1` and `2` and proves that neither `N - 1` nor
  deterministic `omit retry` advice leaks into an unsupported node kind.
- Legacy `idle_timeout` retains seconds-to-milliseconds advice only for
  command and prompt nodes, the kinds admitted by Archon v3. Other node kinds
  report that the field cannot migrate and must be removed or redesigned. The
  new behavior test exercises Bash and loop representatives and proves the
  multiplication advice is absent.

The fix changes only compatibility guidance selection and its focused tests.
Supported command/prompt idle-timeout guidance and supported deterministic/AI
retry guidance remain on their existing branches. The tests exercise public
workflow-loading behavior, are table-driven over the semantic boundaries, do
not inspect source text, and do not freeze catalog sizes or unrelated values.
No brittle change-detector test was introduced.

All four findings from the original quality review remained closed; this fix
does not alter normalization, trust-risk identity, CLI behavior, or catalog
registration.

## Verification evidence

The exact Task 1 gate passed through the repository wrapper with file retries
disabled:

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

9 files, 863 tests passed, 0 failed; no retries.
```

Both `git diff --check cffc23cecd801d3aed08ba66d596bec4a365a43a..HEAD`
and `git diff --check 473ea197b5e2f7322a136ad9b877edd3fbaf5cac..HEAD`
passed. The reviewed production/test state was clean before concurrent review
reports were written. No production or test files were modified by this
rereview.
