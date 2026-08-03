# Task 11 Specification Rereview — Fix Round 2

## Reviewed identities

- Fix base: `9e0e6cb3aa87aef2051d966a8a5a4383f55c6991`
- Head: `6a824d52a0fab0bf7c4e50c39f4102d315e9c54d`
- Head tree: `c985c6f5d7a8818451bd6e2136eafcfd663f660a`
- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Package: `review-9e0e6cb3a..6a824d52a.diff`
- The checked-out HEAD/tree matched the requested identities, and the supplied package matched `git diff -U10` for the exact fix range. The worktree was clean before this permitted report write.
- Scope was only the remaining I-2 finding and the two-file Fix Round 2 diff. The Fix Round 2 report's broader `220 passed` and `173 passed` results remain claims. I independently ran only the new scheduler regression through `scripts/run_tests.sh tests/plugins/workflow/test_bash_e2e.py -k literal_bash_output_references`; result: `1 passed, 0 failed`.

## Finding verdict

### I-2 — ADDRESSED

The preflight remains explicitly confined to sealed Archon normalizer v3 (`plugins/workflow/scheduler.py:1506-1511`). Within that path, it now inventories each strict template, parses canonical references, and, for Bash nodes only, filters those spans through the shared `classify_bash_reference_spans()` authority (`plugins/workflow/scheduler.py:1522-1547`). Only the surviving admitted references enter the deduplicated resolution inventory (`plugins/workflow/scheduler.py:1548-1551`) and the subsequent output lookup/resolution loop (`plugins/workflow/scheduler.py:1577-1586`). This is the same classifier used by admission (`plugins/workflow/schema.py:1109-1135`) and strict runtime substitution (`plugins/workflow/resources.py:875-888`), so escaped/comment spans no longer reappear between those layers. Non-Bash strict consumers retain their prior reference inventory because the new filter is guarded by `node.node_type == "bash"`.

The added regression is scheduler-level and behavioral: it builds and admits an Archon workflow with no producer node or dependency, includes both an escaped `$producer.output` token and a comment-only occurrence, executes through `RunScheduler.advance()`, and requires a succeeded run plus exact shell stdout `$producer.output` (`tests/plugins/workflow/test_bash_e2e.py:320-353`). The focused repository-runner invocation passed independently. This directly reproduces the previously failing sibling path and proves ignored references remain literal through admission, scheduler preflight, and real Bash execution.

## New breakage with severities

- Critical: 0
- Important: 0
- Minor: 0

## Out-of-scope observations

- None. Untouched behavior was inspected only as needed to establish the shared classifier and scheduler execution contract for I-2.

## Final verdict

**PASS.** I-2 is addressed: scheduler preflight now uses the same Bash span authority as admission/runtime, and the real scheduler execution regression passes while proving escaped and comment-only output references stay literal.
