# Phase 3 Task 7 Independent Quality Review 1

**Review date:** 2026-08-02  
**Baseline:** `0d99b3037f3450e361cf137b4be0e523cdab2181`  
**Implementation:** `47d0aa7413407ed3ca66643d38ee619cc473da6f`  
**Implementation tree:** `ca1a2f8d12f636d503e0a50d4a9805ad845f4852`  
**Verdict:** CHANGES REQUIRED

## Severity summary

- Critical: 0
- Important: 3
- Minor: 0

## Scope and evidence reviewed

I read the complete repository `AGENTS.md`, the complete approved Phase 3
design, the implementation plan and Task 7 requirements, the full
`0d99b3037..47d0aa741` diff, every changed production module and caller, the
strict reference grammar and resolver, scheduler preflight/cache/claim paths,
AI session/MCP/skills/provider request construction, script process setup,
approval and rejection handling, loop prompt and `until_bash` handling, and all
new and adjacent tests. I specifically checked dependency bypasses, resolver
scope and copies, authenticated command-byte authority, path races, side effects
before resolution, prompt caching, tool/system/history stability, role
alternation, error taxonomy, attempt accounting, exact legacy behavior, and
Phase 4/Task 8 scope. The review made no production or test edits; this report
is the only file it created.

The following commands were run with flaky file retries disabled:

1. Exact Task 7 gate:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_loop_executor.py tests/plugins/workflow/test_node_mcp.py tests/plugins/workflow/test_node_skills.py tests/plugins/workflow/test_node_hooks.py tests/plugins/workflow/test_ai_extensions_middleware_e2e.py`
   — 9 files, 466 tests passed, 0 failed.
2. Adjacent resources, scheduler, parallel scheduler, portable compatibility,
   language snapshot, compatibility, and Bash gate:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_resources.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_portable_compatibility_e2e.py tests/plugins/workflow/test_compat_matrix.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_bash_e2e.py`
   — 7 files, 231 tests passed, 0 failed.
3. Ruff on all changed Python files — clean.
4. `git diff --check 0d99b3037..47d0aa7413407ed3ca66643d38ee619cc473da6f`
   — clean.

I also ran a read-only production-facade probe showing that two strings rejected
by the canonical v3 grammar are nevertheless substituted by the Task 7
renderer. Its exact outcome is recorded in I-2.

## Important findings

### I-1 — Preflight discards resolved facets, then execution rereads outputs after claim-side effects

`_preflight_strict_node_references()` resolves each authenticated reference but
immediately discards the `ResolvedOutputReference`; it retains only publication
identities for wait fencing (`plugins/workflow/scheduler.py:1480-1494`). After
that preflight returns, the scheduler claims and marks the node started, consumes
its one-time action grant, and starts its heartbeat
(`plugins/workflow/scheduler.py:2774-2838`). Only then does `_variables()` call
`_output_values()` again to construct the objects actually passed to the strict
renderer (`plugins/workflow/scheduler.py:2859-2869` and
`plugins/workflow/scheduler.py:1073-1091`). Thus the facade is not backed by the
immutable objects resolved before side effects; it is backed by a second lookup
performed after durable claim mutations.

The process-local cache makes this appear safe for small happy-path runs, but it
is not an authority boundary. The cache is explicitly evictable at 16 MiB
(`plugins/workflow/scheduler.py:939-952`), while a workflow can depend on many
bounded 500 KiB publications. A large dependency set can therefore evict an
earlier preflight object before `_variables()` rereads it. A transient host read
or integrity failure at that second lookup happens after the action grant was
consumed and after the attempt was marked started. Worse, the broad executor
exception boundary converts every `WorkflowOutputReferenceError`, including
`output_reference_temporarily_unavailable`, into a terminal claimed-attempt
failure (`plugins/workflow/scheduler.py:3011-3020`), bypassing the Task 6 durable
resolution-wait protocol.

This violates three reviewed contracts: strict values must be resolved before
claim/executor side effects, the renderer must consume only those resolved
immutable facets, and transient output reads must never be terminally converted
or consume attempts/action approvals.

**Required remediation:** Return an immutable, dependency-scoped resolved
reference/output snapshot from preflight and carry that exact snapshot through
claim into `NodeExecutionContext`; do not perform a second storage read to build
the renderer. Keep predecessor session evidence separately scoped if it needs
non-reference dependency metadata. Add genuine RED scheduler tests that force
cache eviction or a second-read transient/integrity failure and prove: one
pre-claim read authority, no claim/attempt/heartbeat/provider/process/session or
action-grant mutation on failure, transient errors enter the durable wait, and
all consumers use the exact preflight object identity.

### I-2 — The v3 renderer uses the permissive legacy regex instead of the one canonical ASCII grammar

The design requires one v3 grammar in inventory, admission, condition parsing,
and rendering. The central implementation is `iter_output_references()`
(`plugins/workflow/language_schema.py:180-199`), which rejects incomplete and
unsupported path candidates. `StrictSubstitutionRenderer`, however, calls the
legacy `_VARIABLE` regex (`plugins/workflow/resources.py:41-45` and
`plugins/workflow/resources.py:765-804`). That regex accepts path segments that
the v3 grammar forbids and also partially matches a malformed suffix as a valid
whole-output reference.

The review probe used a valid immutable structured output and the real facade:

```text
$producer.output.1-child => secret
$producer.output-field => {"1-child":"secret"}-field
```

Both authored strings are rejected by `iter_output_references()` as
`output_reference_path_unsupported`, yet the runtime facade gives them meaning.
Current sealed admission normally catches these strings, but the split grammar
breaks defense in depth, makes direct execution and future consumer adoption
unsafe, and permits admission/rendering drift by construction. The new tests
cover only valid renderer input and therefore miss the divergence.

**Required remediation:** Tokenize v3 output substitutions with the canonical
`iter_output_references()` authority and use a separate, non-overlapping path
for existing positional/uppercase legacy variables. Do not allow the legacy
regex to recognize a v3 output candidate. Add relationship tests that every
accepted v3 token renders and every malformed candidate in the existing grammar
matrix raises `output_reference_path_unsupported` without partial substitution,
including mapping keys that would otherwise make the invalid path resolve.

### I-3 — V3-motivated side-effect ordering changes unversioned and `hermes-legacy` failures

To resolve v3 output references before MCP/session/provider side effects,
`AgentNodeExecutor.execute()` now calls `_prompt()` before selecting or
validating the entitled runner (`plugins/workflow/executors/ai.py:672-689`). At
the baseline, prompt/command loading and rendering happened only while building
the request after entitlement, structured-output, and session checks (baseline
`plugins/workflow/executors/ai.py:894`). Consequently a legacy command with an
unavailable runner and a missing/invalid command resource now raises during
prompt loading (and becomes `executor_crash` under the scheduler) instead of
returning the established `agent_runner_unavailable` result. Similar precedence
changes apply to invalid entitlement and incompatible shared-session cases.

The script executor also moved `_execution_plan()` before attempt and artifacts
directory creation for every profile (`plugins/workflow/executors/script.py:156-170`);
the baseline created those durable attempt paths first (baseline lines 147-160).
Legacy validation failures therefore no longer leave the same attempt-owned
filesystem evidence. Neither change is gated on Archon v3, despite the phase's
explicit requirement that unversioned and `hermes-legacy` behavior remain exact.

**Required remediation:** Gate the early-resolution ordering on the effective
v3 semantic bundle. Preserve the original legacy error precedence and attempt
filesystem sequencing byte-for-byte while still resolving v3 references before
all v3 side effects. Add golden legacy tests for unavailable/invalid entitlement,
missing command resources, incompatible shared context, and invalid/missing
inline/named script resources, asserting exact error codes and attempt-tree
effects against the baseline.

## Positive findings

- All five planned consumer families now call one facade for valid v3 values,
  and named script bytes remain authenticated and uninterpolated.
- AI prompt rendering occurs before MCP materialization, persistent-session
  lookup, provider request construction, and provider execution for the normal
  v3 path. Approval messages render before pause publication, rejection prompts
  before their provider call, and inline scripts before attempt-directory and
  process creation.
- Command bodies are still read from sealed authenticated bytes and the exact
  rendered string is passed into the isolated request; no mutable command path,
  raw output value, provider response, or path-taking API was introduced.
- Valid strict substitutions use the resolver's deterministic `rendered_text`
  facet and `re.sub` callback replacement does not recursively rescan inserted
  output text.
- The prompt-cache waist remains narrow: tool lists and ephemeral system prompt
  are unchanged, substitution is confined to the initial node body, and no
  history/system-message mutation or same-role message insertion was added.
- Loop changes reuse existing prompt and `until_bash` fields without adding a
  loop construct. The existing loop Bash spill/path behavior is intentionally
  retained for Phase 4, and Task 8 timeout semantics were not implemented here.
- The diff adds no core model tool, MCP/skills node kind, raw evidence surface,
  provider history, new endpoint, or Phase 4/5 behavior.

## Final assessment

Task 7 is not ready to hand off. Its valid-value consumer wiring, authenticated
command handling, narrow-waist behavior, and 697 focused/adjacent tests are
sound. However, the scheduler discards pre-claim resolved authority and rereads
after durable side effects, the facade has a second permissive output-reference
grammar, and v3-specific ordering changes legacy failure behavior. All three
Important findings need a bounded fix round, genuine RED coverage, and fresh
focused verification.
