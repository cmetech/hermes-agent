# Phase 3 Task 7 Specification Closure Rereview 2

**Review date:** 2026-08-02

**Task 7 baseline:** `0d99b3037f3450e361cf137b4be0e523cdab2181`

**Original implementation:** `47d0aa7413407ed3ca66643d38ee619cc473da6f`

**First fix:** `e400cc4102b9201287471268ed0bc07d76ee363c`

**Closure fix:** `de553b11f8c7cd62d96db97fc76465cd9b999c0d`

**Reviewed tree:** `e5c99384b1f23063794143f30133cb280488d305`

**Verdict:** PASS

**Findings:** 0 Critical, 0 Important, 0 Minor

## Scope and independent evidence

I reread the approved Phase 3 design, the complete implementation plan and
Task 7 contract, all four prior Task 7 specification/quality review reports,
the complete implementation and both fix diffs, and the current scheduler,
language grammar, strict renderer, AI, script, approval, loop, output resolver,
and affected tests. I reviewed production and tests read-only; this report is
the only file I created.

Fresh verification used only the required wrapper with retries disabled:

1. Task 7 plus immutable scheduler snapshot gate:

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

   The wrapper discovered 12 files and reported **567 passed, 0 failed**, with
   no retry/flaky section.

2. Adjacent bounded scheduler, portable compatibility, compatibility matrix,
   language snapshot, and legacy Bash gate:

   ```text
   HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
     scripts/run_tests.sh \
     tests/plugins/workflow/test_scheduler.py \
     tests/plugins/workflow/test_portable_compatibility_e2e.py \
     tests/plugins/workflow/test_compat_matrix.py \
     tests/plugins/workflow/test_language_snapshot.py \
     tests/plugins/workflow/test_bash_e2e.py
   ```

   The wrapper discovered 5 files and reported **175 passed, 0 failed**, with
   no retry/flaky section.

3. Ruff passed on every production and test module changed by Task 7.
4. `git diff --check 459a8e01a..de553b11f` passed, as did the complete Task 7
   production/test range `0d99b3037..de553b11f` when restricted to
   `plugins/workflow` and `tests/plugins/workflow`.

## Closure of every prior finding

- **Immutable preclaim authority and both scheduler paths — closed.**
  `_StrictReferenceSnapshot` is frozen and contains both dependency-scoped
  immutable producer outputs and exact `ResolvedOutputReference` facets
  (`plugins/workflow/scheduler.py:114-130`). Preflight constructs that snapshot
  before claim (`plugins/workflow/scheduler.py:1466-1595`). Both bounded
  `advance()` and fair `advance_all()` retain it alongside the candidate and
  carry the same object through claim into `_execute_claim()`
  (`plugins/workflow/scheduler.py:3431-3523` and
  `plugins/workflow/scheduler.py:3628-3773`). `_variables()` receives the
  snapshot's output mapping and `NodeExecutionContext.output_resolver` receives
  its facet lookup (`plugins/workflow/scheduler.py:2911-2968`). There is no
  output-storage or evictable-cache reread after claim, action-grant
  consumption, or heartbeat start. The focused scheduler test purges the cache
  after the authoritative read and proves exact facet object identity.

- **One canonical v3 grammar and exact malformed attribution — closed.**
  Output spans come only from `iter_output_references(...,
  normalizer_version=3)` (`plugins/workflow/resources.py:765-800`). Malformed
  candidates fail closed before substitution. `WorkflowReferenceSyntaxError`
  now carries the precise candidate offset and the renderer identifies the
  producer at that offset rather than rescanning from the beginning
  (`plugins/workflow/language_schema.py:110-117,156-182` and
  `plugins/workflow/resources.py:765-778`). Mixed valid/malformed templates now
  prove the stable code and exact malformed producer identity.

- **Canonical output spans own scalar overlaps — closed.** The renderer builds
  output spans first and excludes every scalar match that intersects one
  (`plugins/workflow/resources.py:829-856`). Relationship coverage exercises
  whole and field references for every built-in scalar-named uppercase producer
  (`ARGUMENTS`, `USER_MESSAGE`, `ARTIFACTS_DIR`, `WORKFLOW_ID`, `BASE_BRANCH`,
  `DOCS_DIR`, `CONTEXT`, `LOOP_USER_INPUT`, `LOOP_PREV_OUTPUT`, and
  `REJECTION_REASON`) beside a genuine scalar, in prompt and real `/bin/sh`
  rendering. Each canonical reference renders exactly once.

- **Exact legacy ordering — closed.** AI early prompt/command resolution is
  gated jointly on effective Archon profile and normalizer v3
  (`plugins/workflow/executors/ai.py:672-681`); v1/v2 retain entitlement,
  structured-output, and shared-session precedence before request-time resource
  loading. Script early planning has the same v3-only gate, while legacy still
  creates attempt/artifact directories before validation
  (`plugins/workflow/executors/script.py:142-184`). Golden tests cover missing
  commands under unavailable and invalid entitlement, incompatible shared
  context, the guarded runner path, and missing named Bun/uv scripts including
  their attempt-tree evidence.

- **Loop zero-effect resolution and single authored-template render — closed.**
  The loop resolves prompt and `until_bash` output facets together before the
  first provider iteration and exposes only the frozen map thereafter
  (`plugins/workflow/executors/loop.py:102-121`). It keeps the original prompt
  template and passes the frozen resolver into the synthetic child, so the AI
  renderer performs output and authored scalar substitution in one span pass
  (`plugins/workflow/executors/loop.py:145-185`). Output-derived
  `$ARGUMENTS`, valid/malformed reference-looking text, and
  `$LOOP_PREV_OUTPUT` remain literal data and are not recursively scanned.
  Authored `$LOOP_PREV_OUTPUT` still changes per iteration. `until_bash` uses
  the same frozen output facets with the current iteration's dynamic loop value
  (`plugins/workflow/executors/loop.py:218-236`). Failure tests prove zero
  provider/Bash calls and no spill/attempt side effects.

## Remaining specification boundaries

- Prompt/command, inline Bun/uv script, approval message and rejection prompt,
  loop prompt, and `until_bash` consume the strict facade's deterministic
  `rendered_text`. Direct dependency membership is rechecked at rendering.
- Authenticated command bytes remain template authority; command content is
  obtained through the sealed `ResourceResolver`. Named script bytes remain
  authenticated executable authority and are never interpolated.
- Substitution changes only the initial isolated request/body. Skills, MCP,
  hooks, tool selection, ephemeral system prompt, history, and role alternation
  are not rewritten. The focused MCP/skills/hooks/middleware suites pass.
- Legacy `VariableContext` substitution is unchanged. Task 11 remains the owner
  of the standard Bash executor's bounded descriptor renderer; the existing
  loop `until_bash` materialization intentionally remains current behavior as
  required by the design.
- No Task 8 timeout enforcement, Task 11 descriptor materialization, Phase 4
  loop/include syntax, core model tool, MCP/skills node kind, API endpoint, raw
  provider response, path-taking surface, or unbounded evidence projection was
  introduced.

## Final assessment

Task 7 meets its approved specification at the reviewed tree. Every original
and closure-rereview finding is closed, all required authority and compatibility
boundaries are preserved, and fresh focused/adjacent verification reports 742
tests passed with retries disabled. It is ready for controller closure and the
Task 8 handoff.
