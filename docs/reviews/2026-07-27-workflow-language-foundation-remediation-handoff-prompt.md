# Fresh-context handoff prompt — Workflow language foundation remediation

Paste everything below the line into a fresh capable coding-agent session with
read/write shell access to this repository:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

---

You are the implementation orchestrator for the Workflow Language Foundation
Phase 1 remediation in the Hermes fork.

Your job is to execute the approved remediation plan completely, test-first,
using fresh sub-agents and atomic commits. Do not merely review or restate the
plan. Continue until all tasks and final evidence are complete, or stop only
for a genuine blocker requiring user authority.

## Required skills and execution mode

Before taking implementation actions, read and follow these skills completely:

1. `superpowers:using-superpowers`
2. `superpowers:using-git-worktrees`
3. `superpowers:subagent-driven-development`
4. `superpowers:test-driven-development`
5. `superpowers:verification-before-completion`
6. `superpowers:requesting-code-review` before final integration

Use subagent-driven development: one fresh implementation sub-agent per plan
task, followed by the skill's specification-compliance and code-quality review
stages. Tell every worker exactly which files/task it owns, that it is not
alone in the codebase, not to revert other workers' edits, and to accommodate
concurrent changes. Because several tasks share `compat.py`, `cli.py`, snapshot
code, tests, and the customization ledger, execute Tasks 1-5 sequentially.
Tasks 6 and 7 may proceed independently only if they use separate worktrees or
have non-overlapping ownership. Execute Task 8 after Tasks 3 and 5, Task 9
after every production/UI task, and Task 10 last.

## Repository and branch rules

Repository:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

In this fork, the development main is `base`. Literal `main` is
synchronization-only. Do not develop, commit, merge feature work, release, or
open a PR from literal `main`. Do not push or open a PR against upstream
Hermes. The intended result is a local remediation feature branch that is
verified and then merged into `base` through the fork's normal local process.

At handoff creation:

- `base` is at `a34a50875b4b913af10226b8e6a10be883457a2a` and is ahead of
  `origin/base` by 58 commits.
- Original feature baseline:
  `854a66a882a20129a6a53c675210328d277498fb`.
- Original feature tip:
  `de8a8082fbac10651652cc268dab43c0739ac90a`.
- Original local merge:
  `cf470f332e458047987e18527f53ce3699f86998`.

Recheck refs before acting. Start the remediation branch from the current
`base`; do not reset `base` to the handoff SHA if it has legitimately advanced.

The shared checkout contains user-owned documentation changes:

- modified
  `docs/reviews/2026-07-27-workflow-language-foundation-adversarial-review-prompt.md`;
- untracked Claude, Grok, and GLM adversarial reviews;
- this handoff prompt;
- an ignored remediation plan under `docs/superpowers/plans/`.

Do not clean, reset, stash, overwrite, or delete any of them. Create an isolated
worktree from `base` using the worktree skill. The plan is intentionally under
an ignored `docs/superpowers/*` path, so read it from the original shared
checkout before moving into the worktree and keep its absolute path available
throughout execution. Do not commit planning/review documents unless the plan
explicitly assigns one or the user asks.

## Read before implementation

Read these files completely:

1. `AGENTS.md`
2. `apps/desktop/AGENTS.md`
3. `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-07-27-workflow-language-foundation-remediation.md`
4. `docs/superpowers/specs/2026-07-25-workflow-language-compatibility-expansion-design.md`
5. `docs/superpowers/plans/2026-07-25-workflow-language-foundation.md`
6. `docs/upstream-customizations/README.md`
7. `docs/upstream-customizations/workflow-orchestration.yaml`
8. `docs/upstream-customizations/merge-evidence.schema.json`
9. The three adversarial reviews in `docs/reviews/` for Claude, Grok, and GLM.

The remediation plan is authoritative for task order, interfaces, tests,
commit boundaries, and acceptance. If current code has moved and a named line
number is stale, preserve the intended behavior and use the current symbol.
Do not silently change an interface named by the plan; record and review any
necessary deviation.

## Scope and key decisions

The remediation must resolve:

1. Archon blocking ignored by validate, trust, CLI Run, and Gateway Run.
2. Lost workflow process exit codes and collapsed typed error codes.
3. Legacy run-root writes rejected by modern sealed-tree exclusivity.
4. Resume consulting unauthenticated `always_run` YAML.
5. Quadratic/unbounded compatibility findings and oversized projections.
6. JSON Schema/loader applicability drift and dishonest Archon
   `idle_timeout` classification.
7. Desktop fail-open-looking affordances for unknown backend reasons.
8. FastAPI-version-sensitive route-internals tests.
9. Missing mutation-resistant normalizer/file-bound tests.
10. Upstream ledger, diff, dirty-tree, evidence, interpreter, conflict, and
    load-sensitive rehearsal weaknesses.

Do not implement Phase 2+ semantics. `output_format`/`output_type` runtime
contracts remain Phase 2; timeout/retry normalization and session recovery
remain Phase 3; loops/includes remain Phase 4; provider budget/sandbox
portability remains Phase 5; `loop_group` remains Phase 6. These declarations
must block under `archon-2026-07` until their implementing phase.

Do not treat the reported mapping-key digest collision as a production defect:
loaded YAML keys are frozen to strings before normalization and therefore have
the same executed semantics. Do not add another historical-store fixture: the
checked-in v2.0.9 fixture already exercises a real historical scheduler path.

Preserve these non-negotiable properties:

- no system-prompt, tool-schema, history, or prompt-cache mutation;
- exact authenticated execution bytes and private MCP closure;
- symlink/special-file rejection and 512 / 1 MiB / 8 MiB authority bounds;
- backend-owned compatibility and read-only Desktop definitions;
- legacy workflow behavior;
- no upstream PR, push, or direct upstream mutation;
- every upstream-owned edit ledgered with machine-detectable symbols and real
  invariant tests.

## Working protocol

For each task:

1. Dispatch a fresh implementation sub-agent with the complete task text and
   explicit file ownership.
2. Require the new behavioral test to fail for the expected reason before
   production edits.
3. Implement the smallest complete fix without weakening adjacent invariants.
4. Run the task's focused commands through `scripts/run_tests.sh` where the
   plan specifies per-file isolation.
5. Run specification-compliance review, then code-quality review. Address all
   confirmed findings before proceeding.
6. Update the relevant customization-ledger entry in the same commit as every
   upstream-owned code change.
7. Stage only the assigned files and commit with the plan's commit subject.
8. Record deviations, exact tests, and commit SHA for the final verification
   document.

Never use destructive Git commands, whole-file ours/theirs conflict
resolution, source-text assertions, arbitrary sleep-based tests, or test-only
production seams. Do not weaken a safety check merely to make a legacy test
green; preserve the safety property while restoring the documented behavior.

## First concrete action

1. Run `git status --short --branch`, `git rev-parse base`, and
   `git worktree list` in the shared checkout.
2. Read the remediation plan completely.
3. Use `superpowers:using-git-worktrees` to create an isolated remediation
   worktree and feature branch from current `base`.
4. Confirm the worktree has a usable `.venv`, `venv`, or shared Hermes venv
   according to `AGENTS.md`.
5. Begin Task 1 with its failing CLI/Gateway compatibility tests.

## Completion conditions

Do not claim completion until all ten plan tasks are committed and Task 10 has
recorded:

- focused Phase 1 suites green;
- complete base merge gate green with exact `TESTED_BASE_SHA`;
- installed-distribution integration green;
- full Desktop tests and all TypeScript typechecks green;
- six middleware E2E files green on locked and newest-permitted FastAPI;
- both mutation checks proven to fail when their guards are removed;
- customization checker green;
- controlled upstream plus `otto`/`loop24` brand rehearsal green with no
  protected ref or pre-existing worktree mutation;
- a fresh adversarial code review with no unresolved Critical, High, or Medium
  Phase 1 foundation finding.

If the branch is complete and all evidence passes, use
`superpowers:finishing-a-development-branch` and follow this repository's
rule that completed feature work merges locally into `base`, never literal
`main`. Do not push or create a PR unless the user separately authorizes it.
