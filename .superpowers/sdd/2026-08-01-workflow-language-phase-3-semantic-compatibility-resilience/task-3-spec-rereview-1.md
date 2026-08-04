# Phase 3 Task 3 Independent Specification Rereview 1

**Reviewed commit:** `680b2df5659b4daa3f0cd33c9039a43d91fce900`

**Reviewed tree:** `5ac89a4535de0af24b037f92dececae2bf69a446`

**Fix baseline:** `190626ae59e8a23319edd70ef39702a1881e1219`

**Task baseline:** `9f33a94a15cf8190614c713ae03e4302afc73163`

**Verdict:** CHANGES REQUIRED

**Findings:** 0 Critical, 1 Important, 0 Minor

## Scope and evidence

I reread both original Task 3 reviews and inspected the complete fix diff. I
checked the fix against the approved design and complete Task 3 plan, including
the overlap findings for array/index feasibility, closed token boundaries,
legacy/v1/v2 behavior, mixed named-script bytes, quoted condition literals,
authenticated command and snapshot ordering, stable bounded catalog metadata,
and later-task scope. The requested HEAD and tree match, and the worktree was
clean before this report.

The combined exact and adjacent verification gate ran only through the
repository wrapper with flaky file retries disabled:

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
  tests/plugins/workflow/test_language_schema.py

Result: 14 files, 1,113 tests passed, 0 failed, no retries.
```

Scoped Ruff, the fix-range `git diff --check`, and the current-worktree
`git diff --check` all passed.

## Closed findings

- The hyphen continuation hole is closed at the shared token boundary, with
  inline and authenticated-command behavior coverage.
- Command body parsing and named-script text collection are now gated to
  effective Archon normalizer v3. Legacy and explicit Archon v1/v2 retain raw
  byte hashing, including non-UTF-8 and malformed-frontmatter boundaries.
- Invalid v3 authenticated command bytes now emit bounded stable
  `invalid_command_resource`, registered additively and exercised through a
  real behavior path.
- Named-script scanning no longer loses a recognized reference when malformed
  or non-UTF-8 bytes occur before or after it.
- V3 `when` discovery now emits only clause operands and leaves dollar/output
  text inside single- and double-quoted RHS literals untouched.
- Root/nested arrays, `items`, `prefixItems`, bounds, unions, intersections,
  and local refs now have real admission-path acceptance/impossibility tests.
- The affected snapshot test now asserts v3 pre-promotion failure and retains
  the sealed-byte resume authority invariant with an explicit admitted-v2 run.
- The fix changes no Task 4 runtime resolver, Task 5 condition evaluator, or
  other later-phase execution surface.

## Important finding

### I-1 — Numeric path segments are still forced to arrays, so exact numeric mapping keys are rejected

The new feasibility walker decides the expected container type solely from
the segment's spelling:

```python
index = int(segment) if segment.isascii() and segment.isdigit() else None
expected_type = "array" if index is not None else "object"
```

(`plugins/workflow/schema.py:890-897`). For every numeric segment it then calls
only `_v3_array_path_impossible()` (`plugins/workflow/schema.py:910-916`).
It never considers an object property's exact numeric key.

That rejects a valid structured contract such as:

```json
{
  "type": "object",
  "properties": {"0": {"type": "string"}},
  "additionalProperties": false
}
```

when the consumer uses `$producer.output.0`. It also rejects an
object/array union when only the object branch makes exact key `"0"`
possible. The approved resolver contract is contextual: mapping segments are
exact keys, while sequence segments are canonical non-negative decimal indexes
(`design.md:524-528`). The grammar's numeric spelling does not erase a mapping
key with that exact spelling.

The new array tests cover only array-valued schemas, so this false-impossibility
case remains green. The same numeric-is-array assumption also appears in the
dotted-key feasibility helper (`plugins/workflow/schema.py:837-853`), which can
miss an unaddressable dotted key below an exact numeric mapping key.

**Required remediation:** Make feasibility branch on the schema's possible
container types, not on segment spelling alone. For a canonical numeric
segment, test exact mapping-key traversal on every object-capable branch and
index traversal on every array-capable branch; reject only when all possible
branches prove impossible. Preserve named-segment rejection for array-only
schemas and the exact legacy Phase 2 helper. Add real admission tests for an
object property `"0"`, a nested numeric mapping key, object/array unions and
compositions, impossible closed-object numeric keys, and dotted keys below a
numeric mapping segment.

## Review conclusion

The fix closes the original grammar-boundary, trust-gating, mixed named-script,
quoted-literal, snapshot, stable-code, and array-index findings, and all exact
and adjacent gates are green. Task 3 is not yet specification-complete because
the static schema authority still conflates canonical numeric spelling with an
array container and therefore rejects exact numeric mapping keys required by
the approved contextual resolver contract. This Important finding should be
fixed and independently reverified before Task 3 closes.
