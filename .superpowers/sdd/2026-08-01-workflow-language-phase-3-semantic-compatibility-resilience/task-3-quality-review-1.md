# Phase 3 Task 3 Quality Review 1

**Reviewed commit:** `190626ae59e8a23319edd70ef39702a1881e1219`

**Reviewed tree:** `f38211f279fbb6f4e6532fc9f75d11f4decda5a2`

**Baseline:** `9f33a94a15cf8190614c713ae03e4302afc73163`

**Scope:** Task 3's closed v3 reference grammar, static admission across inline
and authenticated resource surfaces, direct-dependency and structured-schema
checks, trust/digest ordering, durable-code registration, and focused/adjacent
regression tests.

**Verdict:** CHANGES REQUIRED

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 5 |
| Minor | 0 |

## Strengths

- The ASCII node/reference grammar is centralized in the dependency-neutral
  language inventory and projected into the generated JSON/editor contract.
- Inline prompt, Bash, script, approval, loop, and authenticated command bodies
  converge on one direct-dependency validator with bounded stable diagnostics.
- Command body scanning uses bytes returned by the same contained resource read
  that contributes to the package digest; it does not reopen a mutable command
  pathname for the new check.
- New catalog entries are additive and behavior-linked rather than asserting a
  brittle total catalog count.
- The exact Task 3 gate, schema/doctor/compatibility checks, Ruff, and diff
  hygiene pass. One directly affected existing snapshot suite is red, as
  detailed below.

## Findings

### Important 1 — A later malformed named-script token erases an earlier forbidden reference

**Evidence:** `plugins/workflow/schema.py:981-992`

Named-script scanning first materializes the entire iterator as a tuple. If a
later reference-like token raises `WorkflowReferenceSyntaxError`, the handler
replaces the whole result with `()`. That discards every valid reference the
iterator yielded before the error and admits bytes which must block.

A direct behavior probe at the reviewed commit passed the authenticated named
script body `$producer.output $bad.output.` to
`validate_authenticated_resource_references()`; it printed `ADMITTED`. The
first token is a recognized output reference, so the approved contract requires
`named_script_output_reference_unsupported` regardless of what follows.

This is an admission bypass for the named-script rule. The script still receives
the text literally rather than through interpolation, but the silent literal
behavior is exactly what Task 3 was required to eliminate.

**Required remediation:** Scan incrementally and retain the fact that any valid
reference was seen. A syntax error must not clear prior tokens. Define the
stable result for malformed reference-like syntax in named bytes without ever
admitting a body that contains a recognized reference. Add tests with valid
references before and after malformed candidates, including multiple tokens
and non-UTF-8 bytes surrounding an ASCII reference.

### Important 2 — Every structured array/index reference is rejected at admission

**Evidence:** `plugins/workflow/schema.py:892-911`,
`plugins/workflow/language.py:1107-1131`

The v3 grammar deliberately accepts canonical numeric path segments, and the
design requires field/index traversal. Static admission delegates possibility
checking to the Phase 2 object-only `prove_output_path_impossible()` helper.
That helper returns impossible for every schema whose type is not `object`, so
both a root array reference such as `$producer.output.0` and a nested array
reference such as `$producer.output.items.0` fail as
`structured_output_field_impossible` even when the schema permits them.

Direct probes returned `True` for both:

```text
{"type":"array","items":{"type":"string"}} + ("0",)
{"type":"object","properties":{"items":{"type":"array",...)}} + ("items","0")
```

The new tests exercise index tokenization but never exercise an index against a
declared structured-output schema, so the feature-shaped regression remains
green.

**Required remediation:** Extend the schema proof to distinguish object keys
from canonical sequence indexes and conservatively handle `items`,
`prefixItems`, tuple bounds, `minItems`/`maxItems`, unions, intersections, and
local refs. Reject only schema-proven-impossible indexes. Add root/nested array
acceptance and impossible-index tests through real workflow loading.

### Important 3 — Quoted condition literals are misclassified as references

**Evidence:** `plugins/workflow/schema.py:651-659`,
`plugins/workflow/schema.py:923-925`,
`plugins/workflow/language_schema.py:107-148`

The approved condition grammar permits quoted string literals on the right
hand side, but static scanning runs the context-free reference iterator across
the entire `when` string. For the valid expression
`$producer.output == '$literal.output'`, the iterator reports both `producer`
and `literal`. Admission therefore requires a direct dependency and structured
schema for text that is syntactically a literal, or rejects malformed
reference-like text inside the literal as `output_reference_path_unsupported`.

A direct probe at the reviewed commit returned:

```text
[('producer', ()), ('literal', ())]
```

This is a false positive in the newly closed grammar and will reject legitimate
typed string comparisons. It also makes static admission disagree with the
Task 5 parser that must treat the RHS as a literal.

**Required remediation:** Make `when` reference discovery grammar-aware so only
the clause's output-reference operand is emitted; quoted literal contents must
remain literal. Reuse the same token authority in the later typed parser rather
than layering a second interpretation. Add direct-dependency and malformed-token
tests with dollar/output text in both single- and double-quoted RHS literals.

### Important 4 — Authenticated command parsing changes legacy and admitted-v2 digest behavior

**Evidence:** `plugins/workflow/trust.py:386-407`,
`plugins/workflow/schema.py:966-975`

`compute_package_digest()` now decodes and parses every command resource before
the profile/version-gated validator is called. The validator's early return is
too late to preserve legacy behavior. An unversioned or `hermes-legacy`
package containing non-UTF-8 command bytes previously hashed those exact bytes;
the reviewed code raises a raw `UnicodeDecodeError`. Malformed command
frontmatter similarly moves from its existing command-resolution boundary into
package digest/trust computation. Admitted Archon v1/v2 packages take the same
new parsing path.

A direct legacy-v2 probe with a one-byte `commands/bad.md` containing `0xff`
raised:

```text
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff ...
```

Valid command digests remain unchanged, but exact legacy and sealed-v1/v2
behavior includes failure type and boundary, not only the successful digest.

**Required remediation:** Collect/parse command and named-script bodies only for
new Archon v3 static admission. All other profiles/versions should retain the
pre-Task-3 raw-resource hashing path exactly. Add golden legacy and explicit
Archon-v2 tests for non-UTF-8 bytes and malformed frontmatter, plus v3 tests for
the intended bounded validation error.

### Important 5 — A directly affected existing snapshot test is red

**Evidence:** `tests/plugins/workflow/test_language_snapshot.py:428-469`

`test_verified_load_checks_command_references_from_sealed_bytes_only` still
expects an impossible authenticated command reference to be admitted and fail
only during verified resume. Task 3 correctly moves this check before
promotion, so `RunStore.prepare_run_snapshot()` now raises
`structured_output_field_impossible` at line 461 and the existing test fails.

The no-retry adjacent run produced 94 passes and 1 failure in that file. This
means the commit does not leave the affected repository suite green, and the
sealed-byte authority invariant no longer has a compatible test at its new
admission boundary.

**Required remediation:** Update the test to assert the new pre-promotion v3
failure, and retain resume-time sealed-byte authority coverage using either an
explicit admitted v2 snapshot or a v3 package that is valid at admission and a
controlled authenticated-snapshot tamper/reseal fixture. Run the whole file,
not only the new Task 3 test module.

## Verification evidence

The exact Task 3 gate passed through the repository runner with flaky retries
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

6 files, 192 tests passed, 0 failed; no retries.
```

Additional trust/resource/runner/snapshot coverage found one real regression:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_trust_policy.py \
  tests/plugins/workflow/test_resources.py \
  tests/plugins/workflow/test_language_snapshot.py \
  tests/plugins/workflow/test_runner_binding.py

4 files, 200 tests passed, 1 failed; no retries.
Failure: test_verified_load_checks_command_references_from_sealed_bytes_only
```

Additional schema/compatibility/doctor coverage passed:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_compat_matrix.py \
  tests/plugins/workflow/test_doctor.py

3 files, 95 tests passed, 0 failed; no retries.
```

`git diff --check` passed for the reviewed range, and Ruff passed on every Task
3 production and test file. The worktree was clean before this review report
was written. This review modified no production or test files.
