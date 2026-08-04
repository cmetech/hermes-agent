# Phase 3 Task 3 Independent Specification Rereview 2

**Reviewed commit:** `80a8cd806af4d0f963413d4449c3318e34b839a8`

**Reviewed tree:** `9359c687b5f85c567de54dd51e2e51ad44f2c9a4`

**Fix baseline:** `680b2df5659b4daa3f0cd33c9039a43d91fce900`

**Task baseline:** `9f33a94a15cf8190614c713ae03e4302afc73163`

**Verdict:** PASS

**Findings:** 0 Critical, 0 Important, 0 Minor

## Scope and evidence

I reread the original Task 3 reviews and first closure rereviews, then
inspected the complete final fix diff against the approved Phase 3 design and
Task 3 plan. The requested HEAD and tree match, and the worktree was clean
before this report.

The final fix is confined to the static v3 reference/schema authority and its
tests. It does not add the Task 4 runtime output resolver, Task 5 typed
condition evaluator, or later retry, Bash, persistence, recovery, API, or
Desktop behavior.

The complete focused and adjacent gate ran only through the repository wrapper
with flaky file retries disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
scripts/run_tests.sh \
  tests/plugins/workflow/test_strict_output_references.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_structured_output_language.py \
  tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_security_boundaries.py \
  tests/plugins/workflow/test_script_executor.py \
  tests/plugins/workflow/test_trust_policy.py \
  tests/plugins/workflow/test_resources.py \
  tests/plugins/workflow/test_language_snapshot.py \
  tests/plugins/workflow/test_runner_binding.py \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_compat_matrix.py \
  tests/plugins/workflow/test_doctor.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_performance_bounds.py

Result: 15 files, 1,126 tests passed, 0 failed, no retries.
```

Scoped Ruff, the final-fix `git diff --check`, and current-worktree
`git diff --check` all passed.

## Final closure disposition

- A canonical numeric path segment now has both contextual interpretations:
  exact mapping key on object-capable schemas and canonical index on
  array-capable schemas. Admission rejects only when every schema-permitted
  interpretation proves impossible.
- Real workflow-loading tests cover root and nested numeric mapping keys,
  object/array unions, `allOf` composition, closed-object misses, and an
  unaddressable dotted child below a numeric mapping key.
- Existing root/nested array, `items`, `prefixItems`, bounds, union,
  intersection, and local-ref cases remain green.
- The legacy Phase 2 `prove_output_path_impossible()` helper is unchanged;
  unversioned, `hermes-legacy`, and admitted Archon v1/v2 identifier,
  reference, command-byte, digest, failure-boundary, and sealed-resume behavior
  remains on its existing path.
- Hyphen-suffix rejection still uses the closed shared token boundary while
  complete internal hyphens remain accepted.
- Authenticated command bodies are inspected from the captured bytes before
  v3 promotion, invalid bytes produce bounded cataloged
  `invalid_command_resource`, and legacy/v1/v2 parsing remains gated out.
- Mixed malformed/non-UTF-8 named-script bytes cannot erase a recognized
  reference, and no generated mutable script copy is introduced.
- Quoted `when` RHS contents remain literals. The LHS reference iterator now
  works with absolute offsets, yields incrementally, retains late syntax
  errors, and has a relationship-based linear slice-volume bound.
- The v3 pre-promotion command check and explicit v2 sealed-byte resume test
  preserve the intended snapshot authority boundary.
- Every Task 3 static blocker remains registered additively with bounded,
  behavior-linked durable metadata.

## Review conclusion

The remaining numeric-context finding is closed, the condition-discovery
performance hardening preserves its grammar semantics, and every prior
specification/quality closure remains intact. Task 3 now satisfies its approved
static-admission scope with no remaining findings.
