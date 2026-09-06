# Workflow Studio loop-group contract final-review remediation

Date: 2026-09-04

## Scope

This addendum remediates only final-review finding FBR-001. It does not change
workflow execution, scheduling, persistence, provider resolution, contract
shape, corpus data, duplicate-dependency behavior, or Workflow Studio product
files. No commit, push, merge, release, branch switch, or native Windows check
was performed.

## Finding and root cause

The published `loop-group-work-product-v1` descriptor and
`loop_group_node_work_factors()` consumed the named work-factor constants, but
neither live admission calculation called that selector.
`_loop_group_work_bounds()` and `_loop_group_capacity_bounds()` separately
encoded multiplier/retry precedence and the `1`, `2`, `0`, and `3` defaults.
Changing `LOOP_GROUP_COMMAND_PROMPT_DEFAULT_RETRIES` therefore changed the
contract while runtime admission remained at one execution and three attempts.

The earlier LGR-005 remediation had restored merge-base raw-value behavior by
restoring both duplicated calculations. That fixed validation order but removed
the shared runtime authority introduced before LGR-005.

## Behavior-first RED evidence

Before production edits, this test patched the published command/prompt retry
default from 2 to 4, compiled a real one-iteration v6 loop group, and compared
the contract with both sealed work semantics and capacity semantics:

```sh
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_language.py \
  -k published_command_prompt_default_drives_runtime_admission -q
```

Exit 1: 1 failed, 111 deselected. The contract published 4, but runtime produced
`(child_executions, child_attempts, capacity.output_attempts) == (1, 3, 3)`
instead of `(1, 5, 5)`.

## Remediation

`LoopGroupWorkFactorAuthority` now declares the node kinds, field names,
authored paths, precedence, and four defaults once. The raw
`loop_group_node_work_factors()` selector consumes that authority and returns
`tuple[object, object]`; it deliberately does not validate or replace authored
values. Both `_loop_group_work_bounds()` and `_loop_group_capacity_bounds()`
call the selector, retaining their starting-HEAD coercion points. Contract paths,
defaults, and the published precedence string derive from the same authority.

The behavioral matrix covers the ordinary-loop multiplier, approval
`on_reject`, explicit retry, command default, prompt default, and other default.
Default-mutation cases prove that publication, the work-bound calculation, and
the capacity calculation move together rather than merely sharing current
literal values.

LGR-005 behavior remains exact: boolean, negative, and fractional child retry
values reach `archon_retry_invalid` at `nodes[0].retry.max_attempts`; the
pre-existing string value `"nope"` remains a bare `ValueError`; and the exact
4,096/4,097 work boundary remains unchanged.

## GREEN and verification evidence

- Focused FBR-001/default/precedence/LGR-005 selection: 14 passed, 0 failed.
- Complete Phase 6 language plus admission files: 148 passed, 0 failed.
- Existing work-bound/retry selection across seven files: 79 passed, 0 failed.
- Exact 11-file workflow language/schema/corpus/compatibility/CLI battery:
  1,243 passed, 0 failed.
- Installed-distribution schema-corpus integration: 1 passed, 0 failed.
- Contract/corpus bounds and determinism selection: 14 passed, 0 failed.
- Repository-wide `.venv/bin/ruff check .`: `All checks passed!`
- `git diff --check`: exit 0 with no output.

Measured canonical outputs are unchanged by this remediation:

- Hermes legacy contract: 226,976 bytes, SHA-256
  `f4de08444f110fb8c4e2b36d9246df4194b8a70dde8bae495a381d757fdf6e07`.
- Archon contract: 283,440 bytes against the unchanged 284,000-byte usable
  ceiling, SHA-256
  `d5f4e69bc441f19362c2b77e534aed977a0b59515985492d877f7f22db4aaed4`.
- Archon corpus: 48 cases and 126,533 compact UTF-8 JSON bytes against the
  64-case and 160,000-byte limits.

Native Windows execution was not performed and is not represented as passing.
