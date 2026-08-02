# Phase 3 Task 7 Specification Closure Rereview 4

**Review date:** 2026-08-02

**Task 7 baseline:** `0d99b3037f3450e361cf137b4be0e523cdab2181`

**Original implementation:** `47d0aa7413407ed3ca66643d38ee619cc473da6f`

**Prior fixes:** `e400cc4102b9201287471268ed0bc07d76ee363c`,
`de553b11f8c7cd62d96db97fc76465cd9b999c0d`, and
`1c4642e043c7e7faf17890bff522d9112e81803c`

**Final bounded-rendering fix:** `cfe6a7939e21d4430430c53507156fd6fde48b57`

**Reviewed tree:** `351258faee7cf8f9aa05deb31dfadbc68eabd417`

**Verdict:** PASS

**Findings:** 0 Critical, 0 Important, 0 Minor

## Scope and independent evidence

I read the complete approved Phase 3 design, the Task 7 implementation-plan
contract and global constraints, and every retained Task 7 specification and
quality review through `task-7-quality-rereview-3.md`. I inspected the complete
Task 7 implementation range and reviewed the final fix separately. I retraced
the strict renderer and canonical parser, immutable preclaim authority through
both scheduler claim paths, AI/script/approval/loop consumers, authenticated
resources, legacy gates, output-data non-rescanning, prompt-cache boundaries,
and Task 8/Task 11/Phase 4 exclusions. Production and test sources were
reviewed read-only; this report is the only file I created.

Fresh verification used only `scripts/run_tests.sh` with the repository Python
and file retries disabled:

1. Task 7 consumers, immutable scheduler authority, renderer, and prompt-cache
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

2. Adjacent scheduler, compatibility, snapshot, Bash, and performance gate:

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

3. A read-only equivalence probe compared `_iter_shell_quote_contexts()` with
   the prior `_shell_quote_context()` at every prefix offset of 1,006 fixed and
   seeded templates. It covered unquoted, single-quoted, double-quoted,
   backslash-escaped, adjacent, and mixed states and reported exact equality.
4. Ruff passed on both files changed by the final fix.
5. `git diff --check 0d99b3037..cfe6a7939 -- plugins/workflow
   tests/plugins/workflow` passed.

## Closure of the final quality findings

- **Bash quote-state rescanning is closed.** The final fix replaces the
  per-substitution prefix scan with `_iter_shell_quote_contexts()`, which
  consumes the already start-ordered substitution offsets while advancing one
  monotonic cursor through the authenticated template. Its state transition is
  the same as the prior helper: backslash escaping is inactive only in single
  quotes, escaped characters cannot change quote state, and single/double quote
  delimiters toggle only outside the other quote kind. It yields the state
  immediately before each substitution start. Placeholder tokens contain no
  quote delimiters, so advancing through an earlier adjacent token preserves
  the original lexical state for the next token. The equivalence probe confirms
  exact old-state results at all tested offsets, and the relationship test
  enters real `render_bash()` in unquoted, single-quoted, and double-quoted
  adjacent forms while bounding character reads to linear growth.

- **Positional arguments are parsed once per render.** `_substitutions()` now
  owns a render-local `positional_values` cache. It initializes the cache only
  on the first non-overlapping positional token, uses the existing
  `shlex.split(arguments)` result, and preserves the exact existing
  `arguments.split()` fallback on malformed shell quoting. Every later `$N`
  token reads that immutable list, including missing positions returning the
  same empty string. The cache is local to one render, so separate render calls
  do not share mutable state. Relationship coverage proves one parser call for
  256 positional substitutions in both valid and malformed argument cases and
  verifies exact composition before, adjacent to, and after an output token.
  The legacy `VariableContext` implementation is unchanged.

- **Malformed and side-effect behavior remains fail-closed.** Canonical output
  parsing still occurs before Bash spill-directory creation. The malformed
  grammar matrix now exercises `render_bash()` and proves
  `output_reference_path_unsupported` with no spill path created. The final fix
  does not weaken exact malformed-producer attribution or direct-dependency
  enforcement.

## Prior closure invariants remain satisfied

- `_StrictReferenceSnapshot` still contains dependency-scoped immutable output
  objects and exact resolved facets. V3 preflight creates it before claim, and
  both `advance()` and `advance_all()` carry it through claim into execution.
  Executor variables and `output_resolver` therefore perform no post-claim
  publication/cache reread after action-grant or heartbeat effects; transient
  reads retain the Task 6 durable-wait path and consume no executor attempt.
- Output tokens still come exclusively from the canonical v3 iterator.
  Canonical spans own overlapping uppercase scalar prefixes through the prior
  monotonic composition cursor, and malformed later tokens retain their own
  producer attribution. The final fix changes neither grammar nor ownership.
- Loop prompt and `until_bash` output facets are still resolved and frozen
  before the first provider request. The original prompt template and frozen
  resolver enter the synthetic child, so output data is rendered exactly once
  and scalar/reference-looking output text remains literal. `until_bash`
  reuses those facets while authored `$LOOP_PREV_OUTPUT` remains dynamic for
  each iteration. Strict failure still precedes provider, Bash, spill, and
  attempt side effects.
- AI and script early ordering remains gated jointly to effective Archon v3.
  Unversioned, `hermes-legacy`, and admitted v1/v2 execution retain established
  runner/entitlement/session failure precedence and attempt-tree timing.
- Prompt and authenticated command bodies, inline scripts, approval messages
  and rejection prompts, loop prompts, and `until_bash` continue to consume
  deterministic rendered facets. Authenticated command bytes remain template
  authority and named scripts remain uninterpolated executable authority.
- Rendering remains confined to each initial isolated body. Tool schemas,
  system prompts, MCP, skills, hooks, history, role alternation, output
  ownership, raw provider/session data, API/evidence projections, and command
  authority are unchanged.
- The cumulative Task 7 diff adds no Task 8 timeout enforcement or retry
  ledger, no Task 11 inherited-descriptor/spill renderer, no Phase 4 loop or
  include syntax, no core tool or MCP/skills node kind, and no path-taking or
  raw-response surface.

## Final assessment

Task 7 meets the approved specification at
`cfe6a7939e21d4430430c53507156fd6fde48b57`. The final fix closes both remaining
boundedness findings without changing command bytes or compatibility semantics,
and every earlier authority, grammar, attribution, loop, legacy, side-effect,
prompt-cache, and scope finding remains closed. Fresh focused and adjacent
verification reports **753 tests passed, 0 failed** with retries disabled.
Task 7 is ready for controller closure and the Task 8 handoff.
