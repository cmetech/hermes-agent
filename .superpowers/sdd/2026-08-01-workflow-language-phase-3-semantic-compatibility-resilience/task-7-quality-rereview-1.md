# Phase 3 Task 7 Independent Quality Rereview 1

**Review date:** 2026-08-02  
**Original implementation:** `47d0aa7413407ed3ca66643d38ee619cc473da6f`  
**Review-fix baseline:** `72b0dd4c0dd74b4aa5333b1d9b4210fdccb5212f`  
**Fix commit:** `e400cc4102b9201287471268ed0bc07d76ee363c`  
**Reviewed tree:** `eeb5d748bfd3d8a59305a87b21d3d3a1334d0692`  
**Verdict:** CHANGES REQUIRED

## Severity summary

- Critical: 0
- Important: 2
- Minor: 1

## Scope and verification evidence

I reread the original Task 7 specification and quality reports, reviewed the
complete `72b0dd4c0..e400cc410` fix diff, and traced the current resource
renderer, scheduler preflight, bounded `advance()` and fair `advance_all()`
claim paths, AI/script/approval/loop consumers, output cache, immutable output
types, and all changed tests. I specifically rechecked immutable facet identity,
post-claim storage/cache independence, claim/action-grant/heartbeat ordering,
canonical grammar ownership, malformed reference behavior, exact legacy error
precedence and filesystem effects, loop pre-provider validation, dynamic
`$LOOP_PREV_OUTPUT`, prompt non-rescanning, and Task 8/Phase 4 scope. No
production or test file was edited; this report is the only file I created.

Fresh verification used only the required wrapper with retries disabled:

1. Task 7 plus resolution-snapshot gate:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_loop_executor.py tests/plugins/workflow/test_phase3_resolution_waits.py tests/plugins/workflow/test_node_mcp.py tests/plugins/workflow/test_node_skills.py tests/plugins/workflow/test_node_hooks.py tests/plugins/workflow/test_ai_extensions_middleware_e2e.py`
   — 10 files, 499 tests passed, 0 failed.
2. Adjacent resource, bounded/fair scheduler, portable compatibility, snapshot,
   and Bash gate:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_resources.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_portable_compatibility_e2e.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_bash_e2e.py`
   — 6 files, 198 tests passed, 0 failed.
3. Ruff on every production/test file changed by the fix — clean.
4. `git diff --check 72b0dd4c0..e400cc4102b9201287471268ed0bc07d76ee363c`
   — clean.

I also ran three read-only facade probes. Two demonstrate the Important
composition failures below; the third shows the malformed-reference attribution
defect in M-1.

## Original finding closure

- **Preclaim facet authority and post-claim reread — closed.**
  `_preflight_strict_node_references()` now returns one frozen
  `_StrictReferenceSnapshot` containing immutable producer outputs and exact
  `ResolvedOutputReference` instances (`plugins/workflow/scheduler.py:1466-1595`).
  Both bounded `advance()` and fair `advance_all()` retain the per-node snapshot
  before claim and pass it into `_execute_claim()`
  (`plugins/workflow/scheduler.py:3430-3523` and
  `plugins/workflow/scheduler.py:3626-3796`). `_variables()` consumes the
  snapshot's output mapping, and `NodeExecutionContext.output_resolver` consumes
  its facet mapping (`plugins/workflow/scheduler.py:2911-2968`). There is no
  post-claim output-storage/cache read, so a claim, action grant, and heartbeat
  cannot precede the authoritative reference read. The new test purges the
  cache and proves executor facet object identity.
- **Canonical grammar for the originally reported malformed forms — closed.**
  Output spans now come from `iter_output_references()` and the tested invalid
  hyphen, suffix, empty-segment, and bracket forms fail closed without partial
  output substitution.
- **Exact legacy ordering — closed.** AI performs early prompt resolution only
  for effective Archon v3, while legacy resource loading remains at request
  construction after entitlement/structured/session decisions. Script planning
  is early only for v3; legacy again creates its attempt/artifact directories
  first. New golden tests cover runner, entitlement, shared-context, missing
  command, and both named-script runtime cases.
- **`until_bash` pre-provider validation — closed.** Loop prompt and
  `until_bash` output facets are resolved before the first provider call and
  reused from a frozen map. Strict failures prove zero provider/Bash calls and
  no spill/attempt side effects. Later `until_bash` rendering still obtains
  iteration-local `$LOOP_PREV_OUTPUT`.
- **Command/cache/scope boundaries remain closed.** Authenticated command bytes
  remain template authority; named scripts remain uninterpolated; tools,
  system prompt, MCP, skills, hooks, and history are unchanged; no Task 8,
  Phase 4, core-tool, API, or evidence surface was introduced.

## Important findings

### I-1 — Canonical output spans overlap scalar spans for valid uppercase producer IDs

`StrictSubstitutionRenderer._substitutions()` first records full canonical
output-reference spans, then independently scans `_SCALAR_VARIABLE` across the
same template and appends every recognized scalar span
(`plugins/workflow/resources.py:825-846`). It sorts only by start offset and
`_replace()` assumes spans never overlap (`plugins/workflow/resources.py:848-859`).
Archon v3 permits uppercase node IDs, including `ARGUMENTS`, `CONTEXT`,
`WORKFLOW_ID`, `LOOP_PREV_OUTPUT`, and the other built-in scalar names.

For a valid direct reference `$ARGUMENTS.output`, the output token owns the full
span while the scalar scanner also owns its `$ARGUMENTS` prefix. The real facade
probe produced:

```text
render_prompt('$ARGUMENTS.output')
=> frozen-outputdynamic-argument.output
```

`render_outputs()` returns the correct `frozen-output`, proving the corruption
comes from the overlapping scalar pass rather than the canonical resolver. The
same defect affects prompt/command, inline script, approval, and Bash/loop
surfaces and can append workflow arguments or other dynamic context to an
otherwise valid canonical output value.

**Required remediation:** Build one non-overlapping token stream, or exclude
every scalar match whose span intersects a canonical output-reference span.
Output tokens must own their complete span. Add RED relationship coverage for
every built-in uppercase scalar name as a valid producer ID, both whole and
field references, neighboring genuine scalar variables, and Bash quoting. Prove
each canonical output token renders exactly once and ordinary scalar behavior
is unchanged.

### I-2 — The loop prompt's two rendering passes rescan output-derived text

The loop correctly resolves prompt and `until_bash` facets before the provider,
but it then substitutes outputs into `prompt` with `render_outputs()`
(`plugins/workflow/executors/loop.py:108-126`). The synthetic prompt child later
enters `AgentNodeExecutor._prompt()`, which calls `render_prompt()` again over
that already substituted string (`plugins/workflow/executors/loop.py:165-189`
and `plugins/workflow/executors/ai.py:618-636`). Thus provider output becomes a
new interpolation template.

The review probe used the immutable output string `$LOOP_PREV_OUTPUT`. It
observed:

```text
after frozen output pass: $LOOP_PREV_OUTPUT
after child prompt pass:  dynamic-loop-value
```

An output containing `$ARGUMENTS` is likewise rewritten, while reference-looking
output such as `$other.output.value` can trigger a second resolution or failure.
This violates command/value authority and the explicit no-rescan invariant; it
also creates an output-to-prompt interpolation injection path.

**Required remediation:** Keep the early `resolve_outputs(prompt,
until_bash)` validation, but retain the original prompt template. Pass the
frozen loop resolver into `child_context.output_resolver` and render output plus
iteration-local scalar spans together exactly once inside the child request.
Keep `until_bash` on its existing one-pass frozen-output/dynamic-scalar path.
Add RED tests whose output strings contain every scalar-looking token, a valid
reference-looking token, and malformed reference-like text, asserting literal
output bytes, one provider call after successful preflight, no recursive
resolution, and still-correct authored `$LOOP_PREV_OUTPUT` changes per iteration.

## Minor finding

### M-1 — A malformed later reference is attributed to the first reference-like node

When canonical iteration raises `WorkflowReferenceSyntaxError`, `_references()`
searches the entire template from the beginning with `_REFERENCE_NODE_CANDIDATE`
and uses that first match as the runtime error's `node_id`
(`plugins/workflow/resources.py:765-774`). For:

```text
$good.output then $bad.output-field
```

the real facade reports `output_reference_path_unsupported` for node `good`,
even though `bad` owns the malformed token. Stable code and bounds are intact,
but durable error attribution violates the exact-node diagnostic contract and
can misdirect remediation.

**Required remediation:** Carry the malformed candidate's start/node identity
from the canonical parser exception, or identify the candidate at the parser's
failure position rather than rescanning from offset zero. Add a mixed-template
test asserting the malformed producer and bounded path identity, not only the
stable code.

## Final assessment

The fix closes every original Task 7 finding at its scheduler, side-effect,
legacy, and `until_bash` authority boundaries, and 697 focused/adjacent tests
pass without retry. Task 7 still cannot close because the revised compositor
corrupts valid uppercase producer references and loop prompt output is rescanned
as interpolation syntax. The smaller malformed-token attribution defect should
be corrected in the same bounded round. Fresh RED coverage and independent
verification are required.
