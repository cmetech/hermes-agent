# Phase 3 Task 4 Independent Specification Rereview 1

**Reviewed commit:** `11c802d5c6a31752d78db2ee2668d5c03c677d67`

**Reviewed tree:** `c2ee6f9bcacf8d5a5e69c758e565d11b6ecafa4e`

**Fix baseline:** `19665407c`

**Task baseline:** `707307b6e3d8f2c73f650e69c0323d458900dcbc`

**Verdict:** PASS

**Findings:** 0 Critical, 0 Important, 0 Minor

## Scope and evidence

I reread the original Task 4 specification and quality reports, the complete
approved Phase 3 design and Task 4 plan, and the full closure-fix diff. I
reinspected the resolver, publication/store authority, scheduler selection and
cache, AI executor exception/cleanup boundary, legacy adapters, and the new
behavior tests. The requested HEAD and tree match, and the worktree was clean
before this report.

The closure fix is confined to the Task 4 output resolver, scheduler, AI
executor boundary, and focused tests. It does not implement the Task 5 typed
condition parser/store transition or Task 6 durable resolution waits,
backoffs, counters, or transient/unavailable codes. The earlier isolated
scheduler test repair remains valid: v3 rejects the impossible command before
publication, while an explicitly sealed v2 package retains the scheduler-time
snapshot contract.

The combined focused and adjacent gate ran only through the repository wrapper
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
  tests/plugins/workflow/test_approval.py

Result: 12 files, 440 tests passed, 0 failed, no retries.
```

The production/test fix diff passes `git diff --check`.

## Closure disposition

- Strict v3 resolution now retains exact UTF-8 schemaless text whenever there
  is no verified schema fingerprint, independent of candidate presence or
  JSON-looking media/content. Direct and real scheduler tests cover Bash and
  script object, array, number, boolean, and null shapes.
- The non-strict v2 adapter still reparses those same five JSON shapes exactly
  as before; the new branch is gated solely by `strict`.
- Applicable publication identity is now complete: publication ID and content
  name, schema fingerprint (including explicit null), canonicalization
  version, output type, media, size, digest, node, and attempt are checked
  against the winning candidate/descriptor authority.
- A retained winning command/prompt candidate with no matching descriptor now
  becomes `output_reference_integrity`, not a later missing-output result.
- Complete candidate-less schemaless publication identity remains accepted,
  preserving the legitimate approval-style publication case without falsely
  demanding a structured candidate.
- Every descriptor dimension consumed by strict validation is included in the
  reusable resolution-cache key. Warm-cache mutation tests cover content name,
  schema fingerprint, canonicalization version, and output type, while a
  bad-first/good-second test proves integrity failures do not poison the cache.
  Existing count/weight eviction and transient non-caching bounds remain green.
- AI prompt and authenticated-command rendering now re-raise
  `WorkflowOutputReferenceError` ahead of the generic `RuntimeError` adapter.
  The existing `finally` still cleans any authenticated execution
  materializer before the scheduler emits the exact terminal reference code.
- End-to-end scheduler tests with `on_error: all` prove no consumer provider
  request, zero additional provider attempts, one terminal claimed workflow
  attempt, no retry, exact durable error code, and terminal metadata.
- Immutable typed/rendered facets, exact missing/not-structured/field/path
  errors, winning-attempt selection, `NodeExecutionContext` threading, explicit
  legacy entry points, and additive behavior-linked durable-code metadata all
  remain intact.

## Review conclusion

All two specification findings and all three quality findings are closed by
the fix and behavior-level regression coverage. Task 4 now satisfies the
approved typed/rendered resolver, publication integrity, cache, consumer
boundary, legacy compatibility, and scope contracts with no remaining
findings.
