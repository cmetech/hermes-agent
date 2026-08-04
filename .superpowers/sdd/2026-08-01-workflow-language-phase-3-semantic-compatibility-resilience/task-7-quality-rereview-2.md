# Phase 3 Task 7 Independent Quality Rereview 2

**Review date:** 2026-08-02
**Implementation baseline:** `47d0aa7413407ed3ca66643d38ee619cc473da6f`
**First fix:** `e400cc4102b9201287471268ed0bc07d76ee363c`
**Second fix:** `de553b11f8c7cd62d96db97fc76465cd9b999c0d`
**Reviewed tree:** `e5c99384b1f23063794143f30133cb280488d305`
**Verdict:** CHANGES REQUIRED

## Severity summary

- Critical: 0
- Important: 1
- Minor: 0

## Scope and verification evidence

I reread the approved Phase 3 design, the complete Task 7 plan, and all four
prior Task 7 review/rereview reports. I reviewed the full
`0d99b3037..de553b11f` production and test diff, with particular attention to
the second fix's parser-offset propagation, canonical/scalar span composition,
and loop child-context change. I retraced the scheduler's preclaim snapshots
through both `advance()` and `advance_all()`, claim/grant/heartbeat ordering,
AI/script legacy gates, approval and loop surfaces, Bash quoting, authenticated
command bytes, output resolution and cache authority, prompt/tool/system/history
boundaries, diagnostics, and the adjacent tests. Production and test files were
not edited; this report is the only file created.

Fresh verification used only the required wrapper with retries disabled:

1. Task 7, scheduler-authority, resource, and prompt-cache gate:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_phase3_resolution_waits.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_loop_executor.py tests/plugins/workflow/test_node_mcp.py tests/plugins/workflow/test_node_skills.py tests/plugins/workflow/test_node_hooks.py tests/plugins/workflow/test_ai_extensions_middleware_e2e.py tests/plugins/workflow/test_resources.py tests/plugins/workflow/test_parallel_scheduler.py`
   -- **12 files, 567 tests passed, 0 failed**.
2. Adjacent scheduler, portable compatibility, compatibility matrix, language
   snapshot, and Bash gate:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_portable_compatibility_e2e.py tests/plugins/workflow/test_compat_matrix.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_bash_e2e.py`
   -- **5 files, 175 tests passed, 0 failed**.
3. Ruff over all Task 7 production/test Python files -- clean.
4. `git diff --check 459a8e01a..de553b11f -- plugins/workflow tests/plugins/workflow`
   -- clean.

I also ran a read-only renderer scaling probe using the repository virtual
environment. Its result is recorded in I-1.

## Prior finding closure

- **Immutable preclaim authority remains closed.** The scheduler resolves one
  frozen `_StrictReferenceSnapshot` before claim, retains it in both bounded
  and fair selection paths, and passes it to `_execute_claim()`. Execution uses
  `snapshot.outputs` for variables and `snapshot.resolve` for exact facets; it
  does not reread publication storage or depend on the evictable cache after
  claim/action-grant/heartbeat side effects. Transient preflight reads still
  enter the Task 6 wait protocol without consuming an attempt.
- **Canonical grammar, attribution, and non-overlapping token ownership are
  functionally closed.** Output spans come only from
  `iter_output_references(..., normalizer_version=3)`. Scalars whose spans
  overlap those canonical tokens are skipped, so every built-in uppercase
  scalar name can also be a valid producer ID without corrupting whole or
  field references, including through Bash quoting. The parser now carries the
  malformed candidate offset, and the renderer attributes a later malformed
  token to its actual producer rather than the first token in the template.
- **Loop authority and single-pass rendering are closed.** Loop prompt and
  `until_bash` facets are resolved before the first provider side effect. The
  original prompt template and frozen resolver are passed to the child, where
  output and iteration-local scalar tokens are composed once. Output data that
  looks like a scalar, valid reference, or malformed reference remains literal;
  authored `$LOOP_PREV_OUTPUT` remains dynamic per iteration. `until_bash`
  continues to use the same frozen facets and current dynamic loop value.
- **Exact legacy and surface behavior remains closed.** Early AI/script
  ordering is gated to the effective Archon-v3 bundle; v1/v2 preserve their
  prior entitlement/session/error precedence and attempt-directory timing.
  Approval, authenticated command bodies, and inline scripts share the facade;
  named script bytes remain uninterpolated authority. Strict failures precede
  executor side effects and keep their stable codes.
- **Prompt-cache and scope boundaries remain closed.** Only the initial
  isolated request/body is rendered. Tool schemas, system prompts, history,
  role alternation, MCP/skills/hooks configuration, and raw provider/session
  evidence are unchanged. No Task 8 timeout/retry behavior, Task 11 Bash
  descriptor work, Phase 4 loop syntax, core tool, or API endpoint was added.

## Important finding

### I-1 -- Overlap filtering is quadratic within the admitted document bound

**Files:**

- `plugins/workflow/resources.py:829-856`
- `plugins/workflow/language_schema.py:23`

The second fix correctly prevents scalar spans from overlapping canonical
output spans, but it implements that check by scanning the complete tuple of
references for every scalar match:

```python
for match in _SCALAR_VARIABLE.finditer(template):
    if any(overlaps(match, reference) for reference in references):
        continue
```

This is quadratic for the exact valid relationship the fix adds. A template
containing repeated `$ARGUMENTS.output` tokens has one canonical output span
and one overlapping scalar-prefix match per token. Match 1 finds reference 1,
match 2 scans through reference 2, and so on. The workflow document bound is
2 MiB and there is no smaller prompt/reference-count bound, so an admitted
authenticated template can contain over 100,000 such tokens.

The read-only production-facade probe used a frozen `ResolvedOutputReference`
and valid direct dependency. Results on this checkout were:

```text
references  template bytes  render_prompt wall time
1,000       18,000          0.0135 s
2,000       36,000          0.0545 s
5,000       90,000          0.3190 s
10,000      180,000         1.2812 s
```

Doubling token count produces approximately four times the work. Extrapolation
to the existing 2 MiB admission ceiling is measured in minutes, allowing a
valid package to monopolize a scheduler worker before provider/process launch.
The current relationship tests cover semantic correctness for every uppercase
name but contain one token pair per case, so they cannot detect this regression.

**Required remediation:** Merge the already start-ordered canonical and scalar
iterators with a monotonic reference cursor, or otherwise test overlap in
linear/log-linear time without rescanning the full reference tuple per scalar.
Preserve output-token ownership and single-pass replacement. Add a bounded
relationship/performance test with many alternating uppercase-producer output
tokens and ordinary scalars; assert exact output and growth consistent with a
single scan rather than an absolute machine-specific microbenchmark.

## Final assessment

All prior correctness, authority, compatibility, diagnostic, injection, and
scope findings are closed, and 742 focused/adjacent tests pass without retry.
Task 7 is not ready to close because the overlap correction introduces
quadratic work across an admitted 2 MiB surface. One bounded performance fix
with RED relationship coverage and fresh verification should close the task.
