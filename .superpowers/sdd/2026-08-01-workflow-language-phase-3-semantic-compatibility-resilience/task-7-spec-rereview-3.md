# Phase 3 Task 7 Final Specification Closure Rereview 3

**Review date:** 2026-08-02

**Task 7 baseline:** `0d99b3037f3450e361cf137b4be0e523cdab2181`

**Original implementation:** `47d0aa7413407ed3ca66643d38ee619cc473da6f`

**First fix:** `e400cc4102b9201287471268ed0bc07d76ee363c`

**Second fix:** `de553b11f8c7cd62d96db97fc76465cd9b999c0d`

**Final bounded-composition fix:** `1c4642e043c7e7faf17890bff522d9112e81803c`

**Reviewed tree:** `bc8f52235c1abaf66907cfdb1c9a69f4e0c6eeb1`

**Verdict:** PASS

**Findings:** 0 Critical, 0 Important, 0 Minor

## Scope and independent evidence

I read the complete approved Phase 3 design, the Task 7 implementation-plan
contract and adjacent Task 6/Task 8 boundary, every Task 7 specification and
quality review/rereview report through `task-7-quality-rereview-2.md`, the full
Task 7 production/test diff, and the final bounded-composition fix separately.
I retraced the current strict renderer and grammar, scheduler preclaim
resolution and both claim-selection paths, AI/script/approval/loop consumers,
authenticated resource handling, output resolver, and all changed and adjacent
tests. Production and test sources were reviewed read-only; this report is the
only file I created.

Fresh verification used only `scripts/run_tests.sh` with the repository Python
and file retries disabled:

1. Task 7, immutable-snapshot, renderer, and prompt-cache gate:

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

   The wrapper discovered 12 files and reported **568 passed, 0 failed**, with
   no retry/flaky section.

2. Adjacent scheduler, portable compatibility, compatibility matrix, language
   snapshot, and legacy Bash gate:

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

3. Ruff passed on every Task 7 production and test module.
4. `git diff --check 0d99b3037..1c4642e04 -- plugins/workflow tests/plugins/workflow`
   passed. The worktree was clean before this report was added.

## Final closure assessment

- **Immutable preclaim authority remains closed.** The frozen
  `_StrictReferenceSnapshot` owns the dependency-scoped outputs and exact
  resolved facets. Preflight constructs it before claim, and both `advance()`
  and `advance_all()` carry that same snapshot through claim into execution
  (`plugins/workflow/scheduler.py:114-131,1466-1595,3430-3519,3625-3795`).
  Executor variables and `output_resolver` consume the snapshot rather than
  rereading publication storage or relying on the evictable cache after claim,
  action grant, or heartbeat. Transient reads therefore retain the Task 6
  durable-wait boundary and consume no executor attempt.

- **Canonical grammar and exact malformed attribution remain closed.** Strict
  output spans come only from `iter_output_references(...,
  normalizer_version=3)`. Parser failure positions flow into the strict error
  attribution path, so malformed later tokens fail closed under
  `output_reference_path_unsupported` and name their own producer rather than a
  prior valid token (`plugins/workflow/language_schema.py:110-182` and
  `plugins/workflow/resources.py:765-800`). The permissive legacy regex remains
  confined to the unchanged legacy renderer.

- **Output/scalar ownership remains exact.** Canonical output tokens are built
  first and own their complete spans. The final fix advances one monotonic
  cursor over the already ordered reference tuple while scalar matches advance
  in source order (`plugins/workflow/resources.py:829-861`). A scalar prefix of
  an uppercase producer reference is still excluded, while a neighboring real
  scalar remains independently rendered. Whole and field references for every
  built-in scalar-named producer, including real `/bin/sh` rendering, remain
  covered.

- **Composition is bounded without changing semantics.** The final production
  change modifies only overlap discovery: it removes the per-scalar full scan
  of all references and performs a monotonic `O(references + scalar matches)`
  overlap pass, followed by the existing bounded ordering/replacement step.
  It does not change the selected spans, resolved values, quote context,
  replacement order, stable failures, or command bytes. The relationship test
  uses 128 and 512 alternating output/scalar pairs, asserts exact rendered
  output, and counts reference-span reads so the former quadratic algorithm
  fails while the monotonic implementation stays within linear growth.

- **Legacy ordering remains exact.** Early AI prompt/resource resolution and
  early script planning are jointly gated to the effective Archon-v3 semantic
  bundle (`plugins/workflow/executors/ai.py:672-681` and
  `plugins/workflow/executors/script.py:142-184`). V1/v2 retain their prior
  runner/entitlement/shared-session error precedence and attempt-directory
  timing, with golden regression coverage for missing command and named-script
  cases.

- **Loop one-pass and zero-effect behavior remain closed.** Loop prompt and
  `until_bash` output facets are resolved together before the first provider
  call. The original prompt template and frozen resolver enter the child, so
  authored output and dynamic scalar tokens are composed once; output-derived
  scalar/reference-looking text is never rescanned. `until_bash` reuses the
  same frozen facets while `$LOOP_PREV_OUTPUT` remains iteration-local
  (`plugins/workflow/executors/loop.py:99-121,145-185,218-236`). Strict
  failures still prove zero provider calls, Bash launches, spill creation, and
  attempt side effects.

- **Consumer, authority, and narrow-waist boundaries remain closed.** Prompt
  and authenticated command bodies, inline Bun/uv scripts, approval messages
  and rejection prompts, loop prompts, and `until_bash` consume deterministic
  `rendered_text`. Authenticated command bytes remain template authority;
  named scripts remain uninterpolated executable bytes. Substitution changes
  only the initial isolated body. Tool schemas, system prompts, MCP, skills,
  hooks, history, role alternation, raw provider/session data, and API/evidence
  projections are unchanged.

- **Scope remains Task 7 only.** The final fix changes only
  `plugins/workflow/resources.py` and its strict-reference tests. The complete
  Task 7 range adds no Task 8 timeout enforcement, retry ledger, Task 11 Bash
  descriptor materialization, Phase 4 loop/include syntax, core tool,
  MCP/skills node kind, path-taking endpoint, or raw provider response.

## Final verdict

Task 7 meets the approved specification at
`1c4642e043c7e7faf17890bff522d9112e81803c`. Every semantic, authority,
compatibility, diagnostic, performance, prompt-cache, and scope finding from
the prior reviews remains closed. Fresh focused and adjacent verification
reports **743 tests passed, 0 failed** with retries disabled. Task 7 is ready
for controller closure and the Task 8 handoff.
