# Targeted adversarial re-review prompt — Workflow Kanban view alignment

This is a targeted re-review of the eight findings in the prior adversarial
design/plan review. It is not an implementation task and not a fresh unbounded
review. Verify from the revised immutable inputs and current production source
whether F1–F8 are actually closed, whether each correction is executable at the
right task boundary, and whether the corrections introduced a directly related
Critical or Important regression.

Do not modify production code, tests, generated files, existing documentation,
Git history, branches, worktrees, or refs. Do not create a branch, commit,
rebase, merge, push, publish, open a pull request, or build a release. The only
authorized repository write is the report named under **Required output**.
Existing untracked files are user-owned and must remain untouched.

## Repository and immutable inputs

Repository:
`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

Expected state at prompt creation:

- branch: `base`
- `HEAD`: `fb061582fb496d59e13316116701e34a4ba90d09`
- `origin/base`: `786f8dc0175410044000113233bec2bb610e7733`
- literal `main` is synchronization-only
- the review prompt/report files and unrelated `docs/assessments/`,
  `docs/handoffs/`, and Ericsson review artifacts are intentionally untracked

Read these three review inputs completely:

| Artifact | Expected SHA-256 |
| --- | --- |
| `docs/superpowers/specs/2026-08-09-workflow-kanban-view-alignment-design.md` | `286ffb2bf96d40c3a9d56ebb334faac0b819f6fb6ad00eba580e8edd69a2e942` |
| `docs/superpowers/plans/2026-08-09-workflow-kanban-view-alignment.md` | `d6492ae795e694bb1d0667e0855ef8380f1a5d54d2e484b40cd47e149098afd6` |
| `docs/reviews/2026-08-09-workflow-kanban-view-alignment-adversarial-plan-review-fable-5.md` | verify and record the current hash; this is the prior finding record, not evidence that a correction works |

Begin by recording `git status --short --branch`, current branch, `HEAD`,
`origin/base`, worktree list, and all three hashes. If `HEAD` or either revised
input hash differs, write `REVIEW_INPUT_CHANGED` and stop. Do not mutate refs to
force a match.

Read `AGENTS.md`, `apps/desktop/AGENTS.md`, and the relevant sections of
`apps/desktop/DESIGN.md`. Inspect the current affected sources and tests directly,
at minimum:

- `apps/desktop/src/components/activity-board/activity-board.tsx`
- `apps/desktop/src/components/activity-board/virtual-card-column.tsx`
- `apps/desktop/src/components/activity-board/activity-board.test.tsx`
- `apps/desktop/src/components/activity-board/activity-board.performance.test.tsx`
- `apps/desktop/src/app/kanban/index.tsx`
- `apps/desktop/src/app/workflows/index.tsx`
- `apps/desktop/src/app/workflows/index.test.tsx`
- `apps/desktop/src/app/workflows/attention-inbox.tsx`
- `apps/desktop/src/app/workflows/run-inspector.tsx`
- `apps/desktop/src/i18n/{types,en,ar,ja,zh,zh-hant}.ts`

Inspect other referenced source only when needed to establish a finding's
closure or a correction-caused regression. Do not treat the prior report as
proof; reduce every verdict to revised design text, revised plan steps, current
source contracts, and executable test/command reasoning.

## Findings that must each receive a closure verdict

### F1 — distinct drawer and inspector landmarks

Verify that the outer drawer uses localized `<workflow> run details`, the
unchanged inner `RunInspector` uses `<workflow> run inspector`, unit and
integration tests render both real semantic roles with distinct names, and no
old `workflowRunInspectorLabel` plan instruction remains.

### F2 — existing grid callback assertion

Verify that the plan explicitly updates the pre-existing exact
`onOpenCard(card)` grid assertion before the implementation step to expect the
native button origin, and that the RED then GREEN sequence is truthful for both
grid and lane appearances.

### F3 — bounded full-height overflow and virtualization

Reconstruct the exact planned chain: full-height page root → non-scrolling
run-view flex column → flex-grown board shell → height-aware ActivityBoard root
→ flex-grown horizontal strip → full-height lane → independently scrolling
lane body. Verify Attention and cleanup are shrink rows, catalog scrolling is
separate, the drawer scrolls independently, a lane-mode 1,000-card test uses a
real in-scope fixture, and launch acceptance covers a 300-card internal lane
scroll at 1440px.

### F4 — Attention replaces stale focus origin

Verify `AttentionInbox` is included in file ownership and Task 6, its callback
supplies `event.currentTarget`, all foreground open paths converge on a helper
that replaces the focus origin, and the integration test performs card A →
Attention run B → close → focus Attention button.

### F5 — workflow-only card chrome

Verify lane styling, health accent, selection treatment, and compact badge
hierarchy are gated on `appearance === 'lane'`; default grid classes/styles and
markup remain unchanged; and a shared-board regression test proves grid chrome
while still accepting the new origin callback.

### F6 — truthful RED/GREEN sequencing

Verify localization regression assertions are run before locale keys are added,
header module failure is isolated afterward, Task 6 explicitly classifies the
navigation-focus and background-refetch cases as pre-existing guards, and new
composition/drawer/reset behavior is tested before implementation. Flag any
remaining test that is claimed RED for the wrong reason or depends on a helper
introduced later.

### F7 — lane-mode performance coverage

Verify `activity-board.performance.test.tsx` has an explicit modifying step,
the 1,000-card fixture is in scope for both grid and lane tests, lane mode uses
the 600px measurement stub, and the gate requires fewer than 100 mounted
buttons in both appearances.

### F8 — RTL-safe header spacing

Verify the header implementation uses logical `me-*`/`ms-*` utilities, the
toolbar has a stable selector, the Arabic test rejects `ml-auto`, the design
requires logical alignment, and the acceptance checklist covers the RTL result.

## Closure standard

Give each F1–F8 exactly one status:

- `CLOSED` — the revised design and executable plan fully correct the finding
  at the proper task boundary with credible verification;
- `PARTIAL` — some correction exists, but a concrete part of the original
  failure remains; or
- `OPEN` — the original failure remains materially unresolved.

For every status, cite exact revised design/plan line numbers and direct current
source evidence. For `PARTIAL` or `OPEN`, include the smallest correction and
the exact test or verification needed. Do not downgrade an unresolved original
Important finding merely because the plan acknowledges it.

After F1–F8, run a narrow regression scan limited to interactions among these
corrections: TypeScript prop compatibility, test-fixture scope, TDD ordering,
height-chain class assertions versus the proposed JSX, distinct landmark query
behavior, Attention accessible name/fixture correctness, and staging scope.
Report a new finding only if it has direct evidence and a concrete consequence.

Severity:

- `CRITICAL`: false architecture/state premise requiring redesign or breaking a
  major existing surface.
- `IMPORTANT`: realistic executable-plan gap that could ship materially broken
  selection, focus, accessibility, layout, virtualization, localization, or
  shared-board behavior, or prevents the written GREEN gate from passing.
- `MINOR`: bounded clarity/diagnostic issue with a concrete consequence.

Overall verdict:

- `BLOCK` if any original finding is `OPEN`/`PARTIAL` at Important severity or
  any new Critical/Important regression exists;
- `CONDITIONAL` only if no Critical/Important issue remains and a Minor item
  requires an explicit product decision;
- `READY FOR IMPLEMENTATION` when all F1–F8 are `CLOSED` and no new
  Critical/Important regression exists.

## Required output

Write only the review report to:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/reviews/2026-08-10-workflow-kanban-view-alignment-adversarial-plan-targeted-rereview-fable-5.md`

The report must contain:

1. exact repository state, hashes, model, platform/date, and inspected sources;
2. overall verdict;
3. an F1–F8 closure table with status, direct evidence, and rationale;
4. a detailed closure proof for each finding;
5. narrow correction-regression findings, if any, with severity and proof;
6. a revised design/plan dependency and TDD-ordering audit for Tasks 3–6;
7. a verification matrix for distinct landmarks, grid preservation, full-height
   scrolling, both virtualization modes, Attention focus return, background
   selection invariance, and RTL header alignment;
8. remaining corrections, or an explicit statement that none remain; and
9. an exact command/evidence ledger distinguishing commands actually run from
   static reasoning.

Do not implement corrections. End with exactly one of:

- `IMPLEMENTATION MUST NOT BEGIN UNTIL CRITICAL AND IMPORTANT PLAN FINDINGS ARE RESOLVED.`
- `IMPLEMENTATION MAY BEGIN ONLY AFTER THE LISTED PRODUCT DECISIONS ARE APPROVED.`
- `THE REVIEWED DESIGN AND PLAN ARE READY FOR IMPLEMENTATION.`

