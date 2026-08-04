# Phase 3 Task 3 Quality Rereview 2

**Reviewed commit:** `80a8cd806af4d0f963413d4449c3318e34b839a8`

**Reviewed tree:** `9359c687b5f85c567de54dd51e2e51ad44f2c9a4`

**Fix baseline:** `680b2df5659b4daa3f0cd33c9039a43d91fce900`

**Task baseline:** `9f33a94a15cf8190614c713ae03e4302afc73163`

**Scope:** Final quality/security closure of contextual numeric reference
semantics, condition-reference scanning complexity and offsets, performance
test strength, malformed/quoted-token behavior, and every prior Task 3
specification and quality finding.

**Verdict:** PASS

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 0 |
| Minor | 0 |

## Final closure assessment

- Numeric path segments now receive both schema-permitted interpretations:
  exact mapping key for object-capable values and canonical sequence index for
  array-capable values. Admission rejects only when every possible
  interpretation is proven impossible.
- Real loader tests cover root/nested numeric mapping keys, object/array union,
  `allOf`, a closed object without the key, and an unaddressable dotted child
  below a numeric mapping key. Existing array, prefix/items, bounds,
  composition, and local-ref cases remain green.
- `iter_when_output_references()` now parses the original expression with
  absolute offsets. It no longer creates a suffix slice for each clause, does
  not retain an internal token list, and yields each validated clause operand
  before advancing.
- Late malformed syntax still raises after earlier tokens have been yielded;
  offsets slice back to the exact original tokens. Single- and double-quoted
  RHS contents remain literal, including reference-like and malformed-looking
  dollar text.
- The performance test instruments actual slice volume through a `str`
  subclass and asserts relationships between 512- and 1,024-clause inputs. It
  is not a brittle wall-clock test or source-shape check, and the prior
  quadratic suffix-copy implementation would violate both relationships.
- A direct scaling probe corroborated linear behavior: 16k/32k/64k/100k
  clauses completed in approximately 0.023/0.046/0.092/0.146 seconds for one
  iterator pass, while preserving the expected reference count.
- The shared output-reference lexer retains exact ASCII and hyphen boundary
  behavior. Mixed malformed/non-UTF-8 named-script bytes cannot erase a valid
  reference, and authenticated command scanning remains byte-authoritative.
- Legacy and admitted Archon v1/v2 command bytes, digest timing, identifier
  acceptance, reference adapters, and sealed-resume behavior remain on the
  unchanged pre-v3 paths. Invalid v3 commands retain bounded cataloged
  `invalid_command_resource` evidence.
- The snapshot suite is green with v3 pre-promotion validation and explicit v2
  sealed-byte resume coverage. Catalog tests remain additive and
  behavior-linked without whole-catalog counts or source inspection.
- No Task 4 runtime resolver, Task 5 condition evaluator, Bash renderer,
  retry, persistence, recovery, API, or Desktop behavior leaked into Task 3.

## Verification evidence

The complete focused, adjacent, and performance gate passed through the
repository wrapper with flaky retries disabled:

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
  tests/plugins/workflow/test_language.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_performance_bounds.py

16 files, 1,138 tests passed, 0 failed; no retries.
```

Ruff passed on every final-fix production/test file. `git diff --check` passed
for the final fix range and the current worktree. The worktree was clean before
independent final-rereview reports were written. This rereview modified no
production or test files.

## Conclusion

All original and closure-round Task 3 quality findings are resolved. The
static v3 reference admission boundary is correct, bounded, compatibility-safe,
and ready to proceed.
