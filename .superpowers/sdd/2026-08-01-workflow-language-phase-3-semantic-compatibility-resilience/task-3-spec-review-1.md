# Phase 3 Task 3 Independent Specification Review

**Reviewed commit:** `190626ae59e8a23319edd70ef39702a1881e1219`

**Reviewed tree:** `f38211f279fbb6f4e6532fc9f75d11f4decda5a2`

**Baseline:** `9f33a94a15cf8190614c713ae03e4302afc73163`

**Verdict:** CHANGES REQUIRED

**Findings:** 0 Critical, 3 Important, 0 Minor

## Scope and evidence

I read the root `AGENTS.md`, the complete approved Phase 3 design, the complete
Task 3 plan, and the complete baseline-to-target production/test diff. The
requested commit and tree match, and the worktree was clean before this report.
The production diff is bounded to static reference grammar/admission,
authenticated resource scanning, trust-time package inspection, and the
durable-code authority. It does not add the Task 4 typed runtime resolver.

Focused verification was run only through the repository wrapper with flaky
file retries disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
scripts/run_tests.sh \
  tests/plugins/workflow/test_strict_output_references.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_structured_output_language.py \
  tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_security_boundaries.py \
  tests/plugins/workflow/test_script_executor.py

Result: 6 files, 192 tests passed, 0 failed, no retries.
```

Additional legacy/schema/trust regression coverage was also run:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
scripts/run_tests.sh \
  tests/plugins/workflow/test_trust_policy.py \
  tests/plugins/workflow/test_language.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_compat_matrix.py

Result: 4 files, 662 tests passed, 0 failed, no retries.
```

The implementation range and pre-report worktree both pass `git diff --check`.
The implementation otherwise establishes the v3-safe node-ID schema, scans all
listed inline surfaces, checks authenticated command bodies before snapshot
publication, blocks recognized references in authenticated named scripts
without generating a script copy, preserves graph-cycle precedence, and adds
behavior-linked catalog coverage for the five Task 3 codes.

## Important findings

### I-1 — Static schema feasibility rejects every valid sequence index

The v3 grammar deliberately admits canonical numeric path segments, and the
approved design requires sequence segments to be canonical non-negative
indexes. However, `_validate_v3_static_output_references()` delegates path
feasibility to the Phase 2 object-only `prove_output_path_impossible()`
(`plugins/workflow/schema.py:878-909`). That helper immediately returns
impossible whenever the current schema type is not `object`
(`plugins/workflow/language.py:1127-1131`) and only knows `properties` /
`additionalProperties` traversal (`plugins/workflow/language.py:1166-1189`).

Consequently a valid declared output such as `{"type":"array","items":{"type":"string"}}`
cannot be referenced as `$producer.output.0`: the iterator accepts `0`, but
admission emits `structured_output_field_impossible`. Nested arrays have the
same failure. The added index test exercises only the token iterator, not an
admitted structured schema, so the required field/schema feasibility contract
remains unproved and incorrect.

This violates the design's one-reference grammar/runtime rules and the Task 3
requirement to cover field and index segments plus impossible schema paths.

**Required remediation:** Add v3 admission RED cases for root and nested array
indexes, including tuple/prefix item forms and composition/local-ref cases as
supported by the normalized schema contract. Implement a v3 path-feasibility
walker that distinguishes mapping-key and sequence-index traversal while
preserving the exact legacy Phase 2 helper behavior. Keep canonical index
syntax (`0` or non-zero decimal without leading zero) authoritative.

### I-2 — The token iterator accepts invalid hyphen-suffixed references by parsing a valid prefix

`iter_output_references()` uses a prefix match and then rejects `.`, brackets,
slash/backslash, underscore, alphanumeric, and non-ASCII continuations
(`plugins/workflow/language_schema.py:120-139`). It omits `-`, even though a
hyphen is part of the closed node/path identifier alphabet. Thus malformed
forms such as `$producer.output-field` and `$producer.output.1-child` are
accepted as the shorter references `$producer.output` and
`$producer.output.1`, leaving the hyphen suffix as ordinary text.

This is not the exact grammar in the approved design. It also defeats the
purpose of one shared admission token: a later renderer can substitute the
accepted prefix even though the authored token is not a valid `reference` or
`path_segment`. The rejection table covers underscore/alphanumeric and several
other suffixes but misses the hyphen boundary.

**Required remediation:** Make token boundaries part of the single closed
grammar rather than accepting a valid regex prefix. Add RED cases for invalid
hyphen continuations after whole-output and numeric-index references, plus
equivalent malformed reference-like candidates on inline and authenticated
command surfaces. Preserve legitimate hyphens inside a complete node ID or
named path segment.

### I-3 — Trust-time command parsing changes legacy and admitted v1/v2 behavior

`compute_package_digest()` now decodes and parses every command resource before
the profile/version gate (`plugins/workflow/trust.py:386-394`), and only later
does `validate_authenticated_resource_references()` return early for legacy or
choose v3 behavior (`plugins/workflow/trust.py:442-446`,
`plugins/workflow/schema.py:966-1008`). Before this commit, package digesting
for legacy and admitted Archon v1/v2 merely authenticated and hashed command
bytes; frontmatter parsing occurred at its existing execution/resource
boundary.

As a result, an unversioned or `hermes-legacy` package with non-UTF-8 command
bytes, unterminated frontmatter, or non-mapping frontmatter now fails during
trust/digest admission with `UnicodeDecodeError`/`ValueError` instead of
retaining its prior digest/admission behavior. Admitted Archon v1/v2 command
packages acquire the same drift. The green legacy tests use valid command
resources and therefore do not cover the changed boundary.

This violates the Phase 3 requirement that unversioned, `hermes-legacy`, and
recorded v1/v2 behavior remain exact. The named-script scan is correctly
needed only for newly normalized Archon v3; it does not require parsing legacy
command bodies.

**Required remediation:** Gate the new command-body parsing and named-script
text collection on effective Archon profile plus normalizer v3, while retaining
the pre-existing byte-authentication/hash path exactly for legacy and admitted
v1/v2. Add golden boundary tests proving malformed/non-UTF-8 legacy command
resources retain the baseline digest/admission outcome and the same v3 bytes
fail at the new authenticated pre-promotion boundary with a stable bounded
validation error.

## Review conclusion

The authenticated-surface inventory, direct-dependency enforcement, named
script blocker, topology ordering, and additive behavior-linked catalog are
substantively aligned and green. Task 3 is not specification-complete because
valid sequence paths are statically rejected, the supposedly exact token
iterator admits malformed hyphen continuations by prefix, and the new
trust-time parser changes legacy/v1/v2 command-package behavior. All three
Important findings should be fixed and independently reverified before Task 3
closes.
