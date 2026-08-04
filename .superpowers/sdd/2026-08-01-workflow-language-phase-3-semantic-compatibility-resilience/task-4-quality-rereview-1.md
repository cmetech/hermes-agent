# Phase 3 Task 4 Independent Quality Rereview 1

**Review date:** 2026-08-02

**Reviewed fix commit:** `11c802d5c6a31752d78db2ee2668d5c03c677d67`

**Reviewed tree:** `c2ee6f9bcacf8d5a5e69c758e565d11b6ecafa4e`

**Fix baseline:** `19665407c`

**Task baseline:** `707307b6e3d8f2c73f650e69c0323d458900dcbc`

**Verdict:** PASS

## Severity summary

- Critical: 0
- Important: 0
- Minor: 0

## Scope and verification

I reread both original Task 4 reviews and inspected the complete closure-fix
diff. I retraced resolver construction and immutable facets, candidate and
publication descriptor authority, approval-style candidate-less publication,
winning attempt selection, all strict identity dimensions, reusable cache keys
and weights, locking and eviction, negative-cache behavior, AI/approval/script/
Bash consumer boundaries, retry accounting, cleanup, legacy adapters, and the
new behavior tests. No production or test files were changed by this review.

The combined Task 4 and adjacent suite ran only through the repository wrapper
with flaky file retries disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
scripts/run_tests.sh \
  tests/plugins/workflow/test_strict_output_references.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_typed_publication.py \
  tests/plugins/workflow/test_typed_publication_recovery.py \
  tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_performance_bounds.py \
  tests/plugins/workflow/test_scheduler.py \
  tests/plugins/workflow/test_parallel_scheduler.py \
  tests/plugins/workflow/test_resources.py \
  tests/plugins/workflow/test_ai_executor.py \
  tests/plugins/workflow/test_script_executor.py \
  tests/plugins/workflow/test_approval.py \
  tests/plugins/workflow/test_approval_races.py \
  tests/plugins/workflow/test_bash_e2e.py

Result: 14 files, 451 tests passed, 0 failed, no retries.
```

The fix commit and production/test range pass `git diff --check`. Ruff passes
on every changed production and test module. The complete range beginning at
the pre-review implementation commit has four `git diff --check` notices only
for intentional Markdown hard-break spaces in the retained original quality
review; there are no source or test whitespace errors.

## Original quality finding closure

### QI-1 — AI prompt substitution swallowed typed reference failures: closed

`AgentNodeExecutor` now catches and re-raises
`WorkflowOutputReferenceError` before its generic `RuntimeError` adapter. The
raise remains inside the existing `try/finally`, so any authenticated MCP
materializer is still cleaned before control reaches the scheduler.

Unit coverage proves prompt rendering raises before the provider. End-to-end
scheduler coverage exercises both inline prompt and authenticated command
surfaces with `on_error: all` and proves:

- only the producer provider request occurs;
- the consumer retains the exact `output_reference_field_missing` code;
- `additional_provider_attempts` is zero;
- exactly one claimed workflow attempt is charged;
- no retry is scheduled; and
- terminal metadata and bounded run error projection are preserved.

Approval gate and rejection-prompt rendering already occur outside their
provider exception adapter and continue to reach the same scheduler boundary.
The approval suites remain green.

### QI-2 — Candidate-less deterministic outputs reparsed JSON text: closed

Strict resolution now uses the verified schema fingerprint as the sole
structuredness authority. When no schema fingerprint exists, strict mode
retains exact UTF-8 text regardless of candidate presence, media type, or
JSON-looking contents. It no longer enters the Phase 2 `json.loads` adapter.

Direct resolver tests cover object, array, number, boolean, and null-looking
text without a candidate. Real scheduler tests run both Bash and script
producers for all five shapes, pass their rendered text through an AI consumer,
and inspect the resolved `typed_value`. The same five cases explicitly prove
that non-strict v2 retains the legacy JSON-reparse behavior.

### QI-3 — Publication identity and cache closure were incomplete: closed

Strict publication resolution now requires a complete applicable identity:
publication ID, content name, explicit schema fingerprint or null,
canonicalization version, output type, node, attempt, relative source path,
media type, byte size, and digest. Candidate-backed publications must match the
candidate in every candidate-owned dimension. Candidate-less schemaless
publication remains intentionally valid for typed approval output, and the
existing real approval restart/publication/consumer flow remains green.

A retained command/prompt candidate without a matching descriptor now becomes
`output_reference_integrity`, rather than degrading to a missing output.

The cache key now contains every descriptor field consumed by strict
validation in addition to root/run/node/winning-attempt and candidate identity.
Node and attempt are represented by the enclosing key, while path, media,
size, digest, publication ID, content name, schema fingerprint,
canonicalization version, and output type are explicit. This prevents
cross-descriptor collisions and stale warm-cache success.

Warm-cache mutation tests prove content-name, schema, canonicalization, and
output-type drift each produce integrity failure. Existing keys already cover
path, media, size, digest, publication, node, and attempt drift. A bad-first,
good-second test proves integrity failures are not cached. Transient read
failures likewise remain uncached for Task 6's later durable wait handling.

Cache insertion and lookup remain under the same reentrant lock. The shared
byte-weighted LRU dynamically accounts for the enlarged tuple keys, evicts
overweight entries, purges failed durable completions and terminal runs, and
retains no separate unbounded side index. Existing concurrency, stale
projection, candidate-registration fencing, weight, and proxy-graph tests all
remain green.

## Additional quality assessment

- Typed and rendered facets remain deeply immutable through frozen dataclasses,
  mapping proxies, and tuples.
- Structured strings render as raw text; null, booleans, finite numbers,
  arrays, and mappings render as deterministic canonical JSON.
- Schemaless JSON-looking text never gains field semantics; exact mapping and
  canonical-index traversal retain distinct bounded error codes.
- Winning-attempt, content, digest, media, schema, canonicalization, and
  publication identities remain checked before a reusable success is stored.
- Durable-code catalog tests remain additive and behavior-linked rather than
  count or whole-catalog change detectors.
- The stale scheduler test repair remains valid: new v3 rejects the impossible
  reference at admission, while an explicitly sealed v2 snapshot still proves
  scheduler-time revalidation without weakening the runtime contract.
- The fix is confined to Task 4 resolver plumbing and tests. It adds no Task 5
  condition evaluator, Task 6 durable wait machinery, Phase 4 loop/include
  behavior, Phase 5 portability behavior, core tool, raw provider response,
  path-taking endpoint, or unbounded evidence.

## Conclusion

All three original quality findings and both original specification findings
are closed. Task 4 now satisfies the typed/rendered resolver, candidate and
publication integrity, cache collision/nonpoisoning/bounds/concurrency,
terminal consumer conversion, deterministic schemaless, approval publication,
legacy compatibility, test-strength, and scope contracts with no remaining
quality findings.
