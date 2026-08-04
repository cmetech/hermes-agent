# Phase 3 Task 4 Independent Quality Review 1

**Review date:** 2026-08-02  
**Baseline:** `707307b6e`  
**Implementation:** `27af2ff350c8ddd9443c623790c81cf11e01ae82`  
**Isolated scheduler test-contract repair:** `dbd1a3c8572e9e7034b7582a51cf696012b6f39e`  
**Verdict:** CHANGES REQUIRED

## Severity summary

- Critical: 0
- Important: 3
- Minor: 0

## Scope and evidence reviewed

I read the repository `AGENTS.md`, the complete approved Phase 3 design, the
global implementation constraints and Task 4 plan, both commits' full diffs,
the publication/store verification path, all resolver callers in the scheduler
and executors, cache accounting and eviction, and the new and adjacent tests.
The review was read-only apart from this retained report.

The following commands were run through the required wrapper with flaky file
retries disabled:

1. Task 4 focused gate:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_performance_bounds.py`
   — 6 files, 196 tests passed, 0 failed.
2. Complete scheduler surface:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_parallel_scheduler.py`
   — 2 files, 43 tests passed, 0 failed.
3. Adjacent resolver consumers:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_resources.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_approval.py`
   — 4 files, 166 tests passed, 0 failed.
4. `git diff --check 707307b6e..dbd1a3c8572e9e7034b7582a51cf696012b6f39e`
   — clean.
5. Ruff on all changed Python files — clean.

The scheduler repair is a valid test-contract repair, not a masked regression.
New v3 admission now rejects the impossible authenticated reference before a
snapshot exists, while the original scheduler revalidation behavior is still
tested against an explicitly reconstructed admitted Archon v2 snapshot. Both
public scheduler entry points remain covered, and the complete scheduler suite
is green.

## Important findings

### I-1 — AI prompt substitution swallows strict reference failures before the scheduler boundary

`WorkflowOutputReferenceError` inherits `ArchonOutputIntegrityError`, which in
turn inherits `RuntimeError` (`plugins/workflow/output_resolution.py:53`). The
AI executor renders the command/prompt while constructing
`PluginAgentRunRequest` (`plugins/workflow/executors/ai.py:892-894`) inside a
broad `try`, then catches every `RuntimeError` and returns
`agent_execution_failed` with a conservatively charged provider-attempt count
(`plugins/workflow/executors/ai.py:987-997`). Consequently a missing field,
schema mismatch, or integrity sentinel in a command/prompt never reaches the
new scheduler catch at `plugins/workflow/scheduler.py:2604-2613`.

This violates the required stable reference code, zero additional-provider
attempt accounting, and terminal/non-retry behavior. With `on_error: all`, the
generic failure is also eligible for the scheduler's fatal-to-transient
override, so a pre-provider resolution failure can consume the grant and be
retried. None of the Task 4 tests executes a failing v3 reference through the
AI executor and scheduler, which is why all focused and adjacent suites remain
green.

**Required remediation:** Preserve `WorkflowOutputReferenceError` across the AI
executor boundary (for example, re-raise it before the generic `RuntimeError`
handler, while preserving materializer cleanup). Add an end-to-end scheduler
test for prompt and authenticated-command substitution, including
`on_error: all`, that proves the exact reference code, terminal metadata, zero
additional provider attempts, no provider invocation, and no retry.

### I-2 — Strict Bash/script whole outputs still use the Phase 2 JSON-reparse adapter

In `_resolve_node_output`, strict schemaless text is preserved only when a
`PrimaryOutputCandidate` exists (`plugins/workflow/output_resolution.py:675-676`).
When `strict=True` and `candidate is None`, execution falls through to the
legacy `json.loads(text)` adapter (`plugins/workflow/output_resolution.py:677-689`).
Bash outputs have no primary candidate unless a declared `output_type` causes
one to be attached, and ordinary script stdout likewise reaches this branch;
the script executor even labels JSON-looking stdout as `application/json`.

Thus a newly admitted v3 Bash/script whole output such as `{"answer":42}` or
`42` acquires a mapping or numeric `typed_value` instead of the required exact
schemaless string. Field access still fails because there is no schema
fingerprint, but Task 5 conditions would receive the wrong type and the Phase 2
reparsing adapter has not actually been removed. The new schemaless test always
supplies a candidate (`tests/plugins/workflow/test_strict_output_references.py:1061-1099`),
so it does not cover the deterministic producer path.

**Required remediation:** In strict mode, decide structuredness solely from the
declared/verified schema identity. When no schema fingerprint exists, retain
the exact UTF-8 text regardless of candidate presence or media type. Add real
v3 Bash and script scheduler tests for JSON-looking and numeric whole outputs,
and retain an explicit v2 assertion showing the legacy adapter is unchanged.

### I-3 — Publication descriptor identity is optional and the cache key omits fields that validation depends on

The strict resolver checks `publication_id` only when that field happens to be
present (`plugins/workflow/output_resolution.py:597-602`). Candidate comparisons
for `schema_fingerprint`, `canonicalization_version`, and `output_type` are also
conditional on each descriptor key being present
(`plugins/workflow/output_resolution.py:626-638`). A strict call with a valid
publication bundle and a descriptor missing those identity fields can
therefore resolve successfully. The test named
`test_v3_resolver_requires_publication_path_and_full_schema_identity` only
omits the publication file; its descriptor actually contains every identity
field (`tests/plugins/workflow/test_strict_output_references.py:1176-1218`), so
it does not prove its stated full-identity contract.

The reusable resolved-output cache compounds this gap. Its key includes the
basic artifact tuple and candidate identity but omits descriptor
`content_name`, `schema_fingerprint`, `canonicalization_version`, and
`output_type` (`plugins/workflow/scheduler.py:806-829`). After a successful
cache fill, changing one of those descriptor fields can hit the prior cache
entry and bypass the very comparisons intended to produce
`output_reference_integrity`. In addition, a retained command/prompt candidate
with no matching descriptor simply falls through as absent
(`plugins/workflow/scheduler.py:761-788`), later becoming
`output_reference_missing` rather than integrity drift.

The Phase 2 store's normal load path strongly corroborates publication
descriptors, which limits exploitability, but Task 4 explicitly promises one
resolver whose descriptor/schema/attempt identity drift is closed and whose
reusable cache cannot conceal or poison that result. The current resolver and
cache do not independently satisfy that boundary.

**Required remediation:** Require the complete applicable strict publication
identity rather than treating fields as optional; classify a retained winning
candidate whose descriptor disappears as `output_reference_integrity`; and
key reusable successes by every descriptor field that strict validation
consumes (or by one canonical verified descriptor identity). Add mutation tests
that fill a cache, alter each identity field one at a time, and prove integrity
failure, plus a bad-first/good-second test proving integrity failures are never
cached.

## Positive findings

- `ResolvedOutputReference` and `ResolvedNodeOutput` freeze nested canonical
  values rather than exposing mutable publication state.
- Structured strings render without JSON quotes, while non-string values use
  deterministic finite JSON rendering.
- Mapping keys and canonical sequence indexes have distinct, stable failure
  behavior; JSON-looking text does not gain field semantics.
- Resolved and candidate caches remain lock-protected, LRU/weight-bounded, and
  separated by run-directory/run/node/attempt identity; transient read errors
  and strict integrity failures are not stored as negative cache entries.
- Legacy entry points remain explicit and the reviewed diff does not introduce
  Phase 4/5 behavior, a core tool, raw provider data, or unbounded evidence.
- Durable-code catalog tests are behavior-linked and additive rather than a
  whole-catalog snapshot/change detector.

## Final assessment

Task 4 is not ready to hand off. The implementation has a sound immutable
resolver shape and preserves the existing cache bounds, but the three Important
issues above break the core v3 promises at real consumer, deterministic-output,
and publication-identity boundaries. A bounded fix round with new RED tests and
fresh focused verification is required.
