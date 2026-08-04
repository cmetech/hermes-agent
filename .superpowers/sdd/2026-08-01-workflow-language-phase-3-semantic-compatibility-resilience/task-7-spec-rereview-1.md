# Phase 3 Task 7 Specification Rereview 1

**Original implementation:** `47d0aa7413407ed3ca66643d38ee619cc473da6f`

**Fix commit:** `e400cc4102b9201287471268ed0bc07d76ee363c`

**Reviewed tree:** `eeb5d748bfd3d8a59305a87b21d3d3a1334d0692`

**Verdict:** WITH FIXES

**Findings:** 0 Critical, 2 Important, 0 Minor

## Scope and evidence

I reread both original Task 7 reports and reviewed the full fix diff plus the
current resource renderer, scheduler preflight and both claim-selection paths,
AI/script/approval/loop callers, output resolver, and all changed and adjacent
tests. Production and test sources were not edited; this report is the only
file created.

Fresh verification used the required wrapper with file retries disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_strict_output_references.py \
  tests/plugins/workflow/test_phase3_resolution_waits.py \
  tests/plugins/workflow/test_ai_executor.py \
  tests/plugins/workflow/test_script_executor.py \
  tests/plugins/workflow/test_approval.py \
  tests/plugins/workflow/test_loop_executor.py \
  tests/plugins/workflow/test_node_mcp.py \
  tests/plugins/workflow/test_node_skills.py \
  tests/plugins/workflow/test_node_hooks.py \
  tests/plugins/workflow/test_ai_extensions_middleware_e2e.py \
  tests/plugins/workflow/test_resources.py \
  tests/plugins/workflow/test_parallel_scheduler.py
```

The wrapper discovered 12 files and reported **555 passed, 0 failed**, with no
retry/flaky section. `git diff --check` over the production/test fix diff also
passed.

## Closed original findings

- **Preclaim authority/cache dependence — closed.**
  `_preflight_strict_node_references()` now returns one frozen
  `_StrictReferenceSnapshot` containing both immutable producer outputs and
  exact `ResolvedOutputReference` facets. Both bounded `advance()` and fair
  `advance_all()` retain the per-node snapshot and pass that same object into
  `_execute_claim()`. `_variables()` consumes its output mapping and
  `NodeExecutionContext.output_resolver` consumes its exact facet mapping, so
  execution no longer rereads output storage or depends on the evictable
  process cache after claim. The new identity test purges the cache after the
  one authoritative read and proves the executor receives the identical
  preflight facet.
- **Malformed/partial v3 reference grammar — closed for the original cases.**
  The strict renderer now obtains output-token spans only from canonical
  `iter_output_references(..., normalizer_version=3)`. The prior permissive
  output branch of `_VARIABLE` is confined to legacy `VariableContext` paths,
  and malformed hyphen, suffix, empty-segment, and bracket candidates fail as
  `output_reference_path_unsupported` without partial output substitution.
- **Legacy ordering — closed.** AI performs early prompt resolution only when
  both the effective profile and variable snapshot identify Archon v3; v1/v2
  retain request-time prompt resolution after the existing entitlement,
  structured-output, and shared-session decisions. Script planning is likewise
  early only for v3; legacy again creates its attempt/artifact tree before
  resource planning. Golden regressions cover unavailable/invalid entitlement,
  shared-context precedence, guarded missing command behavior, and both legacy
  named-script runtimes.
- **`until_bash` failure timing — closed.** The loop obtains all prompt and
  `until_bash` output facets through `resolve_outputs()` before the first
  provider iteration. The later Bash render uses a frozen resolver while
  `$LOOP_PREV_OUTPUT` remains iteration-local. Undeclared and missing-field
  failures now prove zero provider requests, zero Bash calls, and no spill or
  attempt side effects.
- **Other Task 7 boundaries remain closed.** Authenticated command bytes remain
  template authority, named scripts remain uninterpolated, prompt changes stay
  confined to the initial isolated request, MCP/skills/hooks/tool/system
  configuration remains unchanged, and the fix adds no Task 8 timeout or Phase
  4 loop behavior.

## Important findings

### I-1 — Valid references to uppercase scalar-named producers overlap scalar substitution

**File:** `plugins/workflow/resources.py:790-847`

The renderer correctly sources output spans from the canonical iterator, but
it independently scans `_SCALAR_VARIABLE` across the entire same template and
appends both substitution sets before sorting only by start offset. Archon v3
permits uppercase node IDs, including names already used by scalar variables
such as `CONTEXT`, `ARGUMENTS`, `WORKFLOW_ID`, and `LOOP_PREV_OUTPUT`.

For a valid direct reference such as `$CONTEXT.output.answer`, the canonical
iterator contributes the full output-reference span while `_SCALAR_VARIABLE`
also contributes the overlapping `$CONTEXT` prefix because `_scalar()` returns
a non-`None` value for that name. `_replace()` then processes both overlapping
spans and can append the scalar value plus the `.output.answer` suffix after
the already-rendered output. The exact canonical v3 token therefore does not
have one meaning at runtime, and the valid output value is corrupted.

**Required fix:** Exclude scalar matches whose spans overlap a canonical output
reference (output tokens must own the full span), or use one non-overlapping
token stream for both families. Add RED relationship tests for every built-in
uppercase scalar name used as a valid direct producer ID, covering whole and
field references alongside ordinary scalar usage. Assert each canonical
reference renders exactly once and the neighboring real scalar still renders
normally.

### I-2 — Loop prompt pre-rendering rescans inserted output text in the child request

**Files:**

- `plugins/workflow/executors/loop.py:111-137`
- `plugins/workflow/executors/loop.py:165-189`

The loop correctly resolves immutable output facets before the provider, but
then calls `strict_renderer.render_outputs(prompt)` and stores the substituted
text as the synthetic child prompt. `AgentNodeExecutor._prompt()` subsequently
runs `render_prompt()` over that new string to insert loop-local scalar values.
This is a second scan of output-derived text.

If a canonical output string is `$ARGUMENTS`, the first pass inserts that exact
data and the child pass changes it to the workflow argument. If it contains
`$other.output.value`, the child pass treats data as a new reference and may
raise a dependency/integrity failure. Both violate the resolver contract that
`rendered_text` is the value and the Task 7 invariant that replacements are
not recursively rescanned.

**Required fix:** Keep the early `resolve_outputs(prompt, until_bash)` call for
zero-side-effect validation, but do not pre-render the loop prompt into another
template. Pass the frozen loop resolver into the child context and let the
child renderer perform output and loop-local scalar substitution together in
one span-based pass. Keep `until_bash` on its current frozen-facet plus dynamic
`$LOOP_PREV_OUTPUT` path. Add RED loop tests with output strings containing a
built-in scalar token and a reference-looking token, asserting exact literal
prompt bytes, one provider call only after successful preflight, and unchanged
per-iteration loop-local values.

## Assessment

All five original findings are materially addressed, and the scheduler,
legacy, side-effect, prompt-cache, and scope corrections are sound. Two
composition bugs in the revised renderer remain: overlapping canonical/scalar
spans and a two-pass loop prompt render. They must be fixed and independently
verified before Task 7 can close.
