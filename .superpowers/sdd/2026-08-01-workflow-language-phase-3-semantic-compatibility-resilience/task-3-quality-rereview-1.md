# Phase 3 Task 3 Quality Rereview 1

**Reviewed commit:** `680b2df5659b4daa3f0cd33c9039a43d91fce900`

**Reviewed tree:** `5ac89a4535de0af24b037f92dececae2bf69a446`

**Fix baseline:** `190626ae59e8a23319edd70ef39702a1881e1219`

**Task baseline:** `9f33a94a15cf8190614c713ae03e4302afc73163`

**Scope:** Closure rereview of all original Task 3 specification and quality
findings, the complete fix diff, static parser/security boundaries, legacy and
sealed-snapshot compatibility, catalog/test quality, and focused/adjacent
regression evidence.

**Verdict:** CHANGES REQUIRED

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 2 |
| Minor | 0 |

## Closure disposition

- **Named-script mixed-token bypass:** closed. `contains_output_reference()`
  retains detection across malformed/non-UTF-8 neighbors, and real
  authenticated-resource cases cover valid tokens both before and after them.
- **General sequence feasibility:** substantially closed for root/nested
  arrays, `items`, `prefixItems`, bounds, unions, intersections, and local
  refs. The numeric mapping-key edge remains incorrect and keeps this finding
  open in narrowed form below.
- **Quoted condition literals:** functional false positives are closed; only
  clause LHS operands are emitted and single/double-quoted RHS text remains
  literal. The new iterator's complexity is not bounded linearly, producing a
  separate quality/security finding below.
- **Legacy/admitted-v1/v2 resource drift:** closed. Command parsing and named
  script decoding are gated to effective Archon v3; raw byte hashing remains
  unchanged for legacy and recorded v1/v2, including invalid UTF-8 and malformed
  frontmatter.
- **Affected snapshot regression:** closed. The v3 case now asserts
  pre-promotion failure, the explicit v2 case retains resume-time sealed-byte
  authority, and the complete snapshot test file is green.
- **Hyphen token-boundary specification finding:** closed on inline and
  authenticated command paths while legitimate internal hyphens remain valid.
- **Stable command-resource failure/catalog:** closed with bounded
  `invalid_command_resource` metadata and a real behavior-linked emitter.
- **Later-task scope:** preserved. The fix does not add the Task 4 runtime
  resolver, Task 5 evaluator, Bash rendering, retries, or recovery behavior.

## Findings

### Important 1 — Numeric path spelling still forces array semantics and rejects exact mapping keys

**Evidence:** `plugins/workflow/schema.py:890-916`,
`plugins/workflow/schema.py:938-991`

The new v3 feasibility walker chooses the expected container solely from the
segment's spelling: every canonical decimal segment becomes an array index and
requires an array-capable schema. It therefore never tests the same segment as
an exact key on an object-capable schema.

A real loader probe at the reviewed commit used a declared object schema with
`properties: {"0": {"type": "string"}}` and consumer reference
`$producer.output.0`. Admission failed with:

```text
[('nodes[1].prompt', 'structured_output_field_impossible')]
```

The approved resolver contract is contextual: mapping segments are exact keys,
while sequence segments are canonical indexes. Numeric spelling does not make
an exact JSON object key disappear. The same assumption affects unions where
only an object branch permits `"0"`, nested numeric mapping keys, and dotted-key
detection beneath numeric object keys.

**Required remediation:** Branch feasibility on every container type allowed by
the schema. For a numeric segment, test exact-key traversal on object-capable
branches and index traversal on array-capable branches, rejecting only if every
possible interpretation is proven impossible. Preserve the exact legacy helper.
Add real admission tests for root/nested `"0"` keys, object/array unions and
compositions, closed-object misses, and a dotted key below a numeric mapping
segment.

### Important 2 — Condition reference discovery is quadratic near the admitted document bound

**Evidence:** `plugins/workflow/language_schema.py:182-209`,
`plugins/workflow/schema.py:651-659`,
`plugins/workflow/schema.py:1016-1036`

Each condition clause passes `expression[position:]` into
`iter_output_references()`. That suffix slice copies all remaining text, so a
long valid expression performs successively smaller full-tail copies. The
parser also accumulates all tokens before yielding them. Workflow YAML permits
up to 2 MiB and `when` has no Task 3 length/token bound, and this iterator runs
once during node normalization and again during v3 static-reference
validation.

A direct scaling probe on the reviewed commit showed super-linear growth:

```text
clauses     expression bytes     one iterator pass
  16,000             287,996                0.167 s
  32,000             575,996                0.575 s
  64,000           1,151,996                2.017 s
 100,000           1,799,996                4.785 s
```

Near the admitted document ceiling, ordinary loading pays this cost twice,
making untrusted catalog/admission parsing a practical CPU exhaustion path.
The existing performance suite passes because it has no condition-iterator
case, and the new correctness tests use only one clause.

**Required remediation:** Parse against the original string with absolute
offsets, without repeated suffix copies or rescans, and yield incrementally (or
return one bounded immutable result) in O(expression bytes). Add relationship-
based scaling/bound tests that exercise many clauses without brittle wall-clock
thresholds, and ensure malformed late clauses, quoted RHS literals, and token
offsets remain correct. Task 5 may add stricter expression/token limits, but
Task 3 admission must not depend on future work to avoid quadratic behavior.

## Verification evidence

The exact Task 3 gate passed through the repository wrapper with flaky retries
disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_strict_output_references.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_structured_output_language.py \
  tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_security_boundaries.py \
  tests/plugins/workflow/test_script_executor.py

6 files, 221 tests passed, 0 failed; no retries.
```

Adjacent legacy, trust, snapshot, schema, compatibility, and generated-contract
coverage passed:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_trust_policy.py \
  tests/plugins/workflow/test_resources.py \
  tests/plugins/workflow/test_language_snapshot.py \
  tests/plugins/workflow/test_runner_binding.py \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_compat_matrix.py \
  tests/plugins/workflow/test_doctor.py \
  tests/plugins/workflow/test_language.py \
  tests/plugins/workflow/test_language_schema.py

9 files, 904 tests passed, 0 failed; no retries.
```

The existing performance-bound suite also passed but does not cover the new
condition iterator:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh tests/plugins/workflow/test_performance_bounds.py

1 file, 5 tests passed, 0 failed; no retries.
```

Ruff passed on every fix production/test file, and `git diff --check` passed
for the fix range. The worktree was clean before independent rereview reports
were written. This rereview modified no production or test files.
