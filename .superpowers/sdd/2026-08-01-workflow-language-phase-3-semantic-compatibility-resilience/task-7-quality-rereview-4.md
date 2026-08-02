# Phase 3 Task 7 Independent Quality Closure Rereview 4

**Review date:** 2026-08-02

**Task 7 baseline:** `0d99b3037f3450e361cf137b4be0e523cdab2181`

**Original implementation:** `47d0aa7413407ed3ca66643d38ee619cc473da6f`

**Prior boundedness fix:** `1c4642e043c7e7faf17890bff522d9112e81803c`

**Fourth bounded fix:** `cfe6a7939e21d4430430c53507156fd6fde48b57`

**Reviewed tree:** `351258faee7cf8f9aa05deb31dfadbc68eabd417`

**Verdict:** PASS

## Severity summary

- Critical: 0
- Important: 0
- Minor: 0

## Scope and independent verification

I read the complete approved Phase 3 design, the Task 7 implementation-plan
contract and adjacent Task 8 boundary, and every Task 7 specification and
quality report through `task-7-quality-rereview-3.md`. I inspected the
complete Task 7 production/test range and the exact fourth-fix diff, then
retraced the strict renderer, canonical grammar, immutable scheduler snapshot
through both claim paths, AI/script/approval/loop consumers, authenticated
resource authority, legacy gates, prompt/cache boundaries, and affected and
adjacent tests. Production and test sources were reviewed read-only; this
report is the only file I created.

Fresh verification used only `scripts/run_tests.sh` with the repository Python
and file retries disabled:

1. Task 7, immutable scheduler authority, renderer, resource, and prompt-cache
   gate:

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

   The wrapper discovered 12 files and reported **571 passed, 0 failed**, with
   no retry/flaky section.

2. Adjacent scheduler, portable compatibility, compatibility matrix, language
   snapshot, legacy Bash, and performance gate:

   ```text
   HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
     scripts/run_tests.sh \
     tests/plugins/workflow/test_scheduler.py \
     tests/plugins/workflow/test_portable_compatibility_e2e.py \
     tests/plugins/workflow/test_compat_matrix.py \
     tests/plugins/workflow/test_language_snapshot.py \
     tests/plugins/workflow/test_bash_e2e.py \
     tests/plugins/workflow/test_performance_bounds.py
   ```

   The wrapper discovered 6 files and reported **182 passed, 0 failed**, with
   no retry/flaky section.

3. Ruff passed on every Task 7 production and test module.
4. `git diff --check 0d99b3037..cfe6a7939 -- plugins/workflow
   tests/plugins/workflow` passed. The worktree was clean before this report
   was added.

## Fourth-fix closure assessment

### Bash quote-state scan is bounded and semantics-preserving

The strict v3 renderer no longer invokes `_shell_quote_context()` once per
substitution. `_iter_shell_quote_contexts()` consumes the already start-ordered
substitution offsets with one monotonic `position` cursor. Each template
character before the final substitution start is inspected at most once, so
quote-state discovery is linear in template length plus substitution count and
contains no prefix rescan (`plugins/workflow/resources.py:94-118,951-969`).

The new scanner uses the exact same state transitions as the prior helper:
backslash escapes outside single quotes, single quotes toggle outside double
quotes, and double quotes toggle outside single quotes. It preserves the
`escaped` state across adjacent substitution boundaries, which is equivalent
to rescanning the same prefix and is necessary when a token follows a
backslash. Canonical output and scalar token syntax contains no quote or
backslash character, while `_quote_shell_value()` still returns replacements
that end in their original lexical context. Scanning the original token bytes
between ordered starts therefore produces the same quote context as the prior
full-prefix helper for unquoted, single-quoted, double-quoted, escaped, and
adjacent tokens. Legacy `VariableContext.render_bash()` still uses the old
helper unchanged.

The deterministic relationship test enters real `render_bash()`, mixes whole
output references and genuine scalar variables adjacently in all three quote
contexts, verifies exact `/bin/sh` output, and compares a fourfold template
increase against a fivefold character-access ceiling. The former prefix-rescan
algorithm would fail this relationship. Malformed reference tests still fail
before spill-directory creation, and canonical output/scalar ownership tests
continue to cover every uppercase scalar-named producer.

### Positional arguments are parsed once per strict render

`StrictSubstitutionRenderer._substitutions()` now owns one render-local,
initially absent positional list. The first non-overlapping `$N` token performs
one `shlex.split()`; if quoting is malformed, the existing whitespace fallback
is performed once. Every later positional token reuses that same immutable
list (`plugins/workflow/resources.py:829-844,878-903`). Named scalar variables
do not trigger argument parsing, and a render without positional tokens parses
nothing. The legacy adapter remains unchanged.

The focused relationship test counts parser calls across 256 positional tokens
for both valid quoted input and malformed fallback input, composes one canonical
output with 256 adjacent positional tokens between fixed literal boundaries,
verifies exact bytes, and reports one parser call in both cases. The shared
`_substitutions()` path means the same bound applies to prompt, script,
approval, loop, and strict Bash rendering. Scheduler authority limits
authenticated argument bytes to 500,000 before constructing `VariableContext`
(`plugins/workflow/scheduler.py:1075-1100`), so worst-case argument parsing is
one bounded pass per render rather than the previous
token-count-by-argument-size multiplication.

## Prior-finding regression audit

- **Immutable output authority remains closed.** One frozen
  `_StrictReferenceSnapshot` contains dependency-scoped producer outputs and
  exact resolved facets before claim. Both `advance()` and `advance_all()`
  retain that same snapshot and pass it into `_execute_claim()`. Executor
  variables and `output_resolver` consume it after claim, grant, and heartbeat;
  no publication or evictable-cache reread is introduced. Transient reads
  remain in the durable Task 6 wait path with zero executor attempts.
- **Canonical grammar and diagnostics remain closed.** V3 output spans come
  only from `iter_output_references(..., normalizer_version=3)`. Malformed
  candidates fail closed and retain the exact offending producer attribution.
  Output spans own scalar prefixes, including uppercase producer IDs, and the
  monotonic overlap cursor remains bounded.
- **Loop authority and non-rescanning remain closed.** Prompt and
  `until_bash` facets are frozen before the first provider effect. The original
  prompt template is rendered once in the synthetic child, so output-derived
  scalar/reference-looking text remains literal. `until_bash` reuses frozen
  output facets while authored `$LOOP_PREV_OUTPUT` remains iteration-local.
- **Legacy behavior remains exact.** Early AI prompt/resource validation and
  script planning remain jointly gated to Archon v3. V1/v2 preserve runner,
  entitlement, shared-session, and missing-resource precedence plus legacy
  attempt-directory timing. The fourth fix changes only strict-renderer
  internals; the permissive legacy parser, quote scanner, threshold, and
  pathname spill behavior are untouched.
- **Side-effect and narrow-waist boundaries remain closed.** Strict failures
  precede the covered provider, process, MCP materialization, attempt-tree,
  spill, approval, and loop effects. Authenticated command bytes remain
  template authority and named scripts remain uninterpolated executable
  authority. Tool schemas, system prompts, MCP/skills/hooks selection,
  conversation history, role alternation, raw provider/session data, and
  API/evidence projections are unchanged.
- **Scope remains Task 7.** The cumulative range adds no Task 8 timeout
  enforcement, Task 9 retry ledger, Task 11 descriptor materialization,
  Phase 4 loop/include syntax, core model tool, MCP/skills node kind,
  path-taking endpoint, or raw provider-response surface.

## Final verdict

The fourth fix closes both remaining Important findings without reopening any
prior correctness, authority, security, compatibility, diagnostic,
prompt-cache, or scope issue. Fresh focused and adjacent verification reports
**753 tests passed, 0 failed** with retries disabled. Task 7 passes independent
quality closure at `cfe6a7939e21d4430430c53507156fd6fde48b57`.
