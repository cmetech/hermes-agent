# Phase 3 Task 7 Specification Review 1

**Review range:** `0d99b3037f3450e361cf137b4be0e523cdab2181..47d0aa7413407ed3ca66643d38ee619cc473da6f`

**Reviewed tree:** `ca1a2f8d12f636d503e0a50d4a9805ad845f4852`

**Verdict:** WITH FIXES

**Findings:** 0 Critical, 2 Important, 0 Minor

## Scope and evidence

I read the complete repository `AGENTS.md`, the complete approved Phase 3
design, the complete implementation plan and Task 7 requirements, the full
commit diff, and the surrounding scheduler, resource, output-resolution, AI,
script, approval, loop, and affected test paths. The review was read-only for
production and tests; this report is the only file created.

Fresh verification used the required wrapper with file retries disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_strict_output_references.py \
  tests/plugins/workflow/test_ai_executor.py \
  tests/plugins/workflow/test_script_executor.py \
  tests/plugins/workflow/test_approval.py \
  tests/plugins/workflow/test_loop_executor.py \
  tests/plugins/workflow/test_node_mcp.py \
  tests/plugins/workflow/test_node_skills.py \
  tests/plugins/workflow/test_node_hooks.py \
  tests/plugins/workflow/test_ai_extensions_middleware_e2e.py
```

The wrapper discovered 9 files and reported **466 passed, 0 failed**, with no
retry/flaky section. `git diff --check` also passed.

## Strengths

- `StrictSubstitutionRenderer` is a single frozen, dependency-scoped v3
  facade. It consumes the canonical resolver's `rendered_text`, does not
  rescan substituted text, and leaves the existing `VariableContext` path in
  place for normalizer v1/v2.
- Prompt, authenticated command body, inline Bun/uv script, approval message,
  rejection prompt, loop prompt, and `until_bash` are all wired through that
  facade. Named script bytes remain stdin/file authority and are not rendered.
- The scheduler limits the v3 executor variable snapshot to direct
  dependencies and rechecks dependency membership again in the renderer.
  Strict failures retain their originating code at the scheduler boundary,
  are terminal, and report zero additional provider attempts.
- Prompt substitution changes only `PluginAgentRunRequest.prompt`. The tool
  selection, ephemeral system prompt, MCP/skills/hooks configuration, and
  existing isolated-agent request shape are not rewritten. The focused MCP,
  skills, hooks, and middleware suites remain green.
- The diff does not add loop syntax, Task 8 timeout semantics, Task 11 Bash
  descriptor rendering, a core tool, or an API/evidence surface.

## Important findings

### I-1 — V3's pre-side-effect ordering was applied globally and changes legacy failure behavior

**Files:**

- `plugins/workflow/executors/ai.py:676-690`
- `plugins/workflow/executors/script.py:156-170`

The design requires exact unversioned and `hermes-legacy` behavior. Before
this commit, the AI executor selected/validated the entitled runner and the
structured-output contract before evaluating `_prompt(context)` inside its
existing guarded request-construction block. The commit now evaluates the
prompt unconditionally at line 676. For a legacy command whose authenticated
command resource is unavailable, this changes both precedence and failure
classification: an unavailable runner previously returned
`agent_runner_unavailable`, and with a runner the guarded `FileNotFoundError`
followed the existing `OSError` path; now the resource error escapes before
either legacy decision and becomes the scheduler's generic executor failure.

The script executor has the same profile-wide ordering change. It previously
created the attempt and artifact directories before `_execution_plan()`; it
now plans first so a missing legacy named script or other validation error
leaves different durable filesystem state. That move is necessary for v3
strict substitution, but it is not gated to v3.

**Required fix:** Keep the new render-before-side-effect ordering only for a
normalizer-v3 execution. Preserve the old AI error precedence/guarding and the
old script attempt-directory ordering for v1/v2. Add RED legacy regression
tests for a missing command resource with unavailable/available runners and a
missing named script, comparing the exact result code and attempt-directory
behavior with the baseline. Retain the v3 tests proving resolver failure occurs
before MCP materialization, attempt-directory creation, or provider launch.

### I-2 — `until_bash` strict reference failures can occur after a provider side effect

**File:** `plugins/workflow/executors/loop.py:140-215`

The loop executor renders only the child prompt before calling
`self._agent.execute()` at line 164. It does not ask the strict facade to
resolve output references that appear only in `until_bash` until lines
207-215, after the provider iteration has completed and its output artifact
has been processed. Consequently an execution-time direct-dependency or field
failure in `until_bash` can be raised only after one model/provider request.

The new test named
`test_v3_until_bash_reference_failure_precedes_spill_side_effect` verifies
that the spill directory is absent, but deliberately supplies a working fake
runner and does not assert `runner.requests == []`; the implementation does in
fact call that runner first. This does not meet Task 7's requirement to resolve
strict substitutions before every executor side effect. Scheduler static
preflight normally catches an unchanged admitted reference, but the Task 7
runtime recheck must remain safe on its own and preserve the guarantee when a
runtime resolver/integrity failure is observed.

**Required fix:** Before the first loop iteration launches an agent, resolve
all v3 output-reference facets used by both the loop prompt and
`until_bash`, without rendering dynamic `$LOOP_PREV_OUTPUT` or creating spill
files. Continue rendering the dynamic Bash body at each existing iteration;
do not add Phase 4 loop behavior. Add RED tests where only `until_bash`
contains (a) an undeclared producer and (b) a declared producer with a strict
field/integrity failure, asserting the stable code, zero provider requests,
zero Bash launches, and no spill/artifact side effects. Keep a successful case
proving the later dynamic loop value is unchanged.

## Assessment

The shared v3 renderer, canonical rendered facets, consumer coverage,
dependency scoping, stable-code propagation, authenticated resource handling,
and cache/narrow-waist boundaries are otherwise aligned with Task 7. The
profile-wide ordering changes must be narrowed to v3, and `until_bash` runtime
references must be resolved before the first provider effect, before this task
is accepted.
