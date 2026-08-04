# Phase 3 Task 4 Independent Specification Review

**Reviewed implementation commit:** `27af2ff350c8ddd9443c623790c81cf11e01ae82`

**Implementation tree:** `f971f1da80c968077ada0068abea98415a6c2ab7`

**Reviewed stale-test repair:** `dbd1a3c8572e9e7034b7582a51cf696012b6f39e`

**Final reviewed tree:** `234d23f01c343b5a98f80c3b6cd73876f9c65ea5`

**Task baseline:** `707307b6e3d8f2c73f650e69c0323d458900dcbc`

**Verdict:** CHANGES REQUIRED

**Findings:** 0 Critical, 2 Important, 0 Minor

## Scope and evidence

I read the complete root `AGENTS.md`, the complete approved Phase 3 design,
the complete Task 4 plan, both requested commit diffs, and the relevant output
resolver, resource facade, scheduler cache, executor context, publication, and
recovery authorities. The requested commit and tree identities match, and the
worktree was clean before this report.

The implementation is bounded to Task 4 resolver/runtime plumbing and its
tests. It adds immutable typed/rendered reference results, typed stable
failures, v3 scheduler selection of winning outputs, bounded cache accounting,
`NodeExecutionContext.output_resolver`, explicit legacy adapters, and additive
behavior-linked durable-code entries. It does not add the Task 5 condition
parser/evaluator or the Task 6 durable resolution-wait state/codes.

The isolated scheduler test repair is specification-preserving. New v3 input
is now rejected before snapshot publication as required, while the retained
scheduler contract is exercised by explicitly loading normalizer v2 bytes,
sealing them, mutating the live command path, and proving both scheduler entry
points fail from the admitted snapshot without claiming or executing. It does
not weaken a Task 4 assertion or disguise a runtime resolver failure.

Focused verification was run only through the repository wrapper with flaky
file retries disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
scripts/run_tests.sh \
  tests/plugins/workflow/test_strict_output_references.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_typed_publication.py \
  tests/plugins/workflow/test_typed_publication_recovery.py \
  tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_performance_bounds.py \
  tests/plugins/workflow/test_scheduler.py

Result: 7 files, 220 tests passed, 0 failed, no retries.
```

The implementation range also passes `git diff --check`.

## Important findings

### I-1 — Candidate-less v3 Bash/script output still takes the Phase 2 JSON-reparse adapter

The strict resolver preserves schemaless JSON-looking text only when a
`PrimaryOutputCandidate` is present: the v3 text branch is guarded by
`strict and candidate is not None` (`plugins/workflow/output_resolution.py:675-676`).
When `candidate` is absent, strict execution falls through to the Phase 2
`json.loads(text)` adapter (`plugins/workflow/output_resolution.py:677-689`).

That is the normal whole-output path for v3 Bash and undeclared-output script
nodes: the scheduler deliberately resolves Bash/script descriptors without
requiring a candidate (`plugins/workflow/scheduler.py:783-784`). A Bash stdout
of `{"answer":42}`, or script output with no declared structured contract,
therefore gets a mapping as `typed_value` rather than the exact schemaless
string. Numeric, boolean, null, and array-looking text are similarly
reclassified. The added JSON-looking-text test covers only an AI-style
candidate and cannot expose the deterministic-node path
(`tests/plugins/workflow/test_strict_output_references.py:1061-1099`).

This violates the design rule that schemaless whole output has the same exact
string in both facets and that JSON-looking schemaless text never acquires
typed structured semantics. It would also give Task 5 the wrong condition LHS
authority.

**Required remediation:** Add RED runtime/scheduler cases for candidate-less
v3 Bash and script outputs containing JSON object, array, number, boolean, and
null text. In strict mode, derive structured status from the sealed
`structured_outputs`/schema identity, not from JSON-looking bytes or candidate
presence; a schemaless whole output must remain an exact string. Preserve the
existing non-strict v1/v2 reparse adapter exactly.

### I-2 — The reusable cache key omits descriptor identity fields that strict resolution validates

On a cache miss, strict resolution compares descriptor
`schema_fingerprint`, `canonicalization_version`, and `output_type` with the
winning candidate (`plugins/workflow/output_resolution.py:621-639`), and it
also uses `content_name` to select the publication bytes
(`plugins/workflow/output_resolution.py:604-620`). However, the scheduler's
reusable resolution key includes only descriptor path/media/size/digest and
publication ID, then candidate identity fields
(`plugins/workflow/scheduler.py:807-829`). It omits the descriptor's
`content_name`, `schema_fingerprint`, `canonicalization_version`, and
`output_type`.

After one valid resolution is cached, a later projection carrying drift in any
of those descriptor dimensions hits the old cache entry and bypasses the
strict comparisons entirely (`plugins/workflow/scheduler.py:831-847`). The
resolver therefore returns prior typed/rendered data instead of the required
`output_reference_integrity`. Existing tests mutate content/digest identities,
which do change the key, but do not exercise these omitted dimensions across a
warm cache.

This violates the Task 4 requirement that publication identity drift be an
integrity failure and never poison/reuse a cache entry. Store recovery normally
rejects many corrupt durable projections earlier, but the resolver cache is an
independent bounded authority and must key every identity dimension it relies
on rather than depending on a caller to have repeated unrelated validation.

**Required remediation:** Add warm-cache RED cases for each strict descriptor
dimension, including `content_name`, schema fingerprint, canonicalization
version, and output type. Include all validated descriptor identity fields in
the cache key (or revalidate them before a hit), while retaining existing
count/weight eviction bounds and the rule that transient/integrity misses are
not cached.

## Review conclusion

The immutable result facets, stable missing/not-structured/field/path errors,
winning-attempt selection, publication byte reads, bounded caches, context
threading, legacy gates, durable catalog entries, and isolated v2 scheduler
repair are otherwise aligned. Task 4 is not specification-complete because
candidate-less deterministic outputs still reparse schemaless text and the
cache can hide drift in strict descriptor identity fields. Both Important
findings should be fixed and independently reverified before Task 4 closes.
