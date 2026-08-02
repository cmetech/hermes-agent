# Phase 3 Task 1 Quality Rereview 1

**Reviewed commit:** `473ea197b5e2f7322a136ad9b877edd3fbaf5cac`

**Reviewed tree:** `4408182f1f48585f624e880649e54f3dd717ebd4`

**Fix baseline:** `b677f770a182492ea7e3723733d278600309f3e7`

**Verdict:** CHANGES REQUIRED

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 1 |
| Minor | 0 |

## Closure of the original quality findings

1. **Null retry validation — closed.** The normalizer now distinguishes field
   presence from an omitted block, rejects non-mappings, and validates an
   authored null `max_attempts` before arithmetic. Behavior tests cover null
   block/max-attempt/delay/on-error values on both AI and deterministic nodes.
2. **Legacy trust drift — closed.** The new language-risk component is gated to
   Archon normalizer v3. Legacy and Archon v1/v2 retain the Phase 2 risk
   document. Tests include an independently constructed Phase 2 hash and a
   fixed legacy fixture digest while preserving the Archon v2-to-v3 retrust
   boundary.
3. **CLI regressions — closed.** Timeout and retry now have supported-v3 CLI
   assertions, while blocker/doctor/entrypoint coverage uses still-deferred
   Phase 5 fields. The complete CLI file passes.
4. **Catalog change detectors — closed for the original concern.** Task 1 now
   asserts its normalization behavior codes are a subset of the additive
   phase registry and no longer requires every future code to be a
   normalization-only runtime failure.

The overlapping specification finding about incomplete migration guidance is
substantially addressed: direct dependencies, `output_format`, typed scalar
conditions, the 32,768-byte boundary, pathname removal, deterministic/AI
one-attempt distinctions, and stale Phase 2 prose now have backend-authored
tests. One applicability defect remains below.

## Finding

### Important — Migration advice authors fields that Archon v3 rejects on the current node kind

**Evidence:** `plugins/workflow/language.py:580-623`

The new migration branching is based on attempt count for Bash/script and AI
nodes but has no branch for valid legacy `approval`/`cancel` retry fields.
Those fields are explicitly unsupported by Archon v3. An approval with one
legacy total attempt therefore receives the factually inapplicable message
"For legacy total attempts N >= 2, author Archon max_attempts as N - 1"; a
cancel node with two attempts is likewise told to author a retry block that
v3 rejects with `archon_retry_node_unsupported`.

The same problem exists for legacy `idle_timeout`: the legacy schema accepts it
on all node kinds, but the migration text always says to multiply and author
Archon milliseconds even though v3 permits `idle_timeout` only on command and
prompt nodes. For example, a valid legacy loop receives that advice and then
fails Archon admission with `archon_idle_timeout_node_unsupported`.

Observed at the reviewed commit:

```text
approval-retry-1 => For legacy total attempts N >= 2, author Archon max_attempts as N - 1 ...
cancel-retry-2   => For legacy total attempts N >= 2, author Archon max_attempts as N - 1 ...
loop-idle        => Multiply idle_timeout seconds by 1,000 to author Archon milliseconds.
```

This violates the approved normalization/admission applicability boundary and
would guide a user from a valid legacy workflow directly into a blocking v3
definition.

**Required remediation:** Make legacy migration guidance node-kind aware.
Approval/cancel retry guidance must say the retry block cannot migrate under
v3 and must be removed or redesigned; non-command/prompt `idle_timeout`
guidance must similarly state that the field is unsupported rather than
suggesting unit conversion. Preserve the existing conversion/N-1/one-attempt
guidance only for node kinds where Archon v3 admits the field. Add behavior
tests for approval and cancel retry (including total one and greater than one)
and for an inapplicable legacy idle timeout such as loop or Bash.

## Verification evidence

The exact Task 1 gate plus the affected CLI suite passed through the repository
runner with file retries disabled:

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
  tests/plugins/workflow/test_doctor.py \
  tests/plugins/workflow/test_cli.py

10 files, 934 tests passed, 0 failed; no retries.
```

`git diff --check cffc23cecd801d3aed08ba66d596bec4a365a43a..HEAD`
passed. The worktree was clean before this review report was written. No
production or test files were modified by the rereview.
