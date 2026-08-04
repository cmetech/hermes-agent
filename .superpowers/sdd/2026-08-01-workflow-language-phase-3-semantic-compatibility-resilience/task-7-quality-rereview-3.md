# Phase 3 Task 7 Independent Quality Rereview 3

**Review date:** 2026-08-02

**Implementation baseline:** `47d0aa7413407ed3ca66643d38ee619cc473da6f`

**First fix:** `e400cc4102b9201287471268ed0bc07d76ee363c`

**Second fix:** `de553b11f8c7cd62d96db97fc76465cd9b999c0d`

**Boundedness fix:** `1c4642e043c7e7faf17890bff522d9112e81803c`

**Reviewed tree:** `bc8f52235c1abaf66907cfdb1c9a69f4e0c6eeb1`

**Verdict:** CHANGES REQUIRED

## Severity summary

- Critical: 0
- Important: 2
- Minor: 0

## Scope and verification evidence

I reread the approved Phase 3 design, the complete Task 7 plan, and every Task 7
specification and quality review through quality rereview 2. I inspected the
complete Task 7 production/test range and the exact
`de553b11f..1c4642e04` boundedness fix. I retraced canonical tokenization,
malformed-reference attribution, uppercase producer/scalar overlap, immutable
preclaim snapshots through both scheduler claim paths, AI/script legacy gates,
approval and loop rendering, output-data non-rescanning, Bash quoting, side
effects, prompt/tool/history boundaries, and Task 8/11/Phase 4 scope. Production
and test sources were not edited; this report is the only file I created.

Fresh verification used only the required test wrapper with retries disabled:

1. Task 7, scheduler-authority, resource, and prompt-cache gate:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_phase3_resolution_waits.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_loop_executor.py tests/plugins/workflow/test_node_mcp.py tests/plugins/workflow/test_node_skills.py tests/plugins/workflow/test_node_hooks.py tests/plugins/workflow/test_ai_extensions_middleware_e2e.py tests/plugins/workflow/test_resources.py tests/plugins/workflow/test_parallel_scheduler.py`
   -- **12 files, 568 tests passed, 0 failed**.
2. Adjacent scheduler, portable compatibility, compatibility matrix, language
   snapshot, Bash, and performance gate:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_portable_compatibility_e2e.py tests/plugins/workflow/test_compat_matrix.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_bash_e2e.py tests/plugins/workflow/test_performance_bounds.py`
   -- **6 files, 182 tests passed, 0 failed**.
3. Ruff over all Task 7 production/test Python files -- clean.
4. `git diff --check 0d99b3037..1c4642e04 -- plugins/workflow tests/plugins/workflow`
   -- clean.

I also ran a read-only production-facade scaling probe for the Bash finding
below. It did not edit the repository.

## Closure of prior findings and the fix under review

- **The reported overlap scan is closed.** Canonical references and scalar
  matches are both start ordered. The new monotonic reference cursor advances
  past references ending at or before the current scalar, tests only the one
  possible current overlap, and leaves adjacency non-overlapping. Canonical
  output spans still own scalar prefixes at the same start. The replacement
  list is ordered before its one-pass splice. This is correct for scalar spans
  before, inside, after, adjacent to, and overlapping canonical spans.
- **The new deterministic scaling test is effective for that algorithm.** Its
  counting wrapper observes reference-span reads rather than wall-clock time,
  compares a fourfold token increase against a fivefold operation ceiling, and
  verifies exact alternating output/scalar bytes. The former all-reference scan
  would grow by roughly sixteen times and fail. It also preserves real
  uppercase-producer and `/bin/sh` behavior coverage. It does not, however,
  exercise the later Bash quote-context pass or positional scalar expansion;
  those omissions expose the findings below.
- **Correctness and authority fixes remain closed.** Malformed candidates use
  the canonical v3 parser and precise candidate offset; uppercase scalar-named
  producer references render exactly once; loop output data is not rescanned;
  prompt and `until_bash` facets are frozen before the provider; authored
  `$LOOP_PREV_OUTPUT` remains iteration-local; and both scheduler selection
  paths carry the same immutable preclaim snapshot through claim, grant, and
  heartbeat without a post-claim publication/cache reread.
- **Legacy, side-effect, and scope boundaries remain closed.** Early AI/script
  ordering is Archon-v3-only, named scripts remain authenticated and
  uninterpolated, strict failures retain stable diagnostics and precede the
  covered executor effects, and no prompt-history/tool-schema mutation, Task 8
  timeout/retry behavior, Task 11 descriptor renderer, Phase 4 syntax, core
  tool, or API surface was added.

## Important findings

### I-1 -- V3 `until_bash` quote-context rendering still rescans the template quadratically

**Files:**

- `plugins/workflow/resources.py:76-91`
- `plugins/workflow/resources.py:890-921`
- `plugins/workflow/executors/loop.py:218-236`
- `plugins/workflow/language_schema.py:23`
- `tests/plugins/workflow/test_strict_output_references.py:1347-1418`

The boundedness fix makes output/scalar span composition linear, but
`StrictSubstitutionRenderer.render_bash()` subsequently calls
`_shell_quote_context(template, start)` for every substitution. That helper
restarts at byte/character zero and walks `template[:start]`. For `N`
start-ordered substitutions, the work is proportional to the sum of all token
offsets, so a valid repeated-token Bash template remains quadratic.

This is reachable on the Task 7 Archon-v3 `until_bash` consumer. The workflow
document bound is 2 MiB and there is no smaller `until_bash` token-count bound.
Repeated unquoted, single-quoted, or double-quoted direct output references are
valid authenticated input and are rendered after an iteration but before the
Bash process launches. The existing uppercase/Bash relationship test proves
small semantic examples only, while the new operation-count test calls
`render_prompt()` and never enters this quote-context path.

A read-only probe using the real v3 facade, immutable
`ResolvedOutputReference`, and alternating `$ARGUMENTS.output $ARGUMENTS`
tokens produced:

```text
tokens  template bytes  render_bash wall time  exact output
250     7,249           0.0335 s               yes
500     14,499          0.1306 s               yes
1,000   28,999          0.5208 s               yes
2,000   57,999          2.0733 s               yes
```

Doubling the accepted template produces approximately four times the work;
the maximum admitted surface can monopolize a scheduler worker for far longer
than these small green tests reveal. Task 11 intentionally owns the standard
Bash descriptor renderer and Phase 4 owns the full loop contract, but neither
requires this Task 7 strict facade to retain a quadratic quote-state lookup.
Legacy behavior can remain untouched.

**Required remediation:** For the strict v3 Bash facade, derive quote contexts
with one monotonic scan (or attach them while traversing the template) and
consume the already ordered substitutions without restarting at the beginning.
Preserve exact current replacement bytes for unquoted, single-quoted, and
double-quoted placeholders and do not implement Task 11 spills. Add a
deterministic operation-growth relationship test that enters `render_bash()`
with many alternating uppercase-producer output tokens and genuine scalars in
all three valid quote contexts, plus adjacency and malformed-token cases.

### I-2 -- Positional scalar expansion reparses the entire arguments value for every token

**Files:**

- `plugins/workflow/resources.py:802-810`
- `plugins/workflow/resources.py:844-859`
- `plugins/workflow/scheduler.py:1087-1100`
- `tests/plugins/workflow/test_strict_output_references.py:1347-1418`

Every recognized positional scalar (`$1`, `$2`, and so on) calls
`shlex.split(self.variables.arguments)` independently. A v3 template may
contain many positional placeholders, and the sealed scheduler accepts an
arguments value up to 500,000 bytes. Consequently strict rendering performs
`O(positional_token_count * argument_bytes)` parsing before the executor side
effect. Scaling the admitted template and admitted argument together is a
quadratic resource-consumption path; malformed quoting takes the same repeated
fallback split path.

The new scaling test alternates a canonical output with named `$ARGUMENTS`, so
its span-read metric cannot observe argument reparsing. This behavior also
exists in the legacy adapter, but exact legacy preservation does not require
the new strict renderer to repeat it: the strict facade can parse one immutable
argument snapshot once per render and reuse it without changing rendered
bytes.

**Required remediation:** Parse the v3 facade's immutable argument string at
most once per render (including the existing fallback semantics) and reuse the
result for every positional token. Add a deterministic relationship test that
counts parser calls across many `$N` substitutions for valid and malformed
argument strings, verifies exact before/adjacent/after composition with output
tokens, and leaves the legacy `VariableContext` path unchanged.

## Final assessment

The third fix correctly closes the reported canonical/scalar overlap algorithm,
and all earlier correctness, authority, diagnostic, legacy, injection, and
scope findings remain closed. The Task 7 facade is still not bounded across two
authenticated large-input paths that the new test does not exercise: Bash
quote-context lookup and positional argument parsing. Task 7 should not close
until both receive bounded v3-only fixes, genuine RED relationship coverage,
and fresh independent verification.
