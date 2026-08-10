# Workflow Inspector 45rem Follow-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Increase the Desktop Workflows run inspector's responsive desktop cap from `40rem` to `45rem`.

**Architecture:** Keep the existing nonmodal, right-anchored workflow drawer and change only its responsive width token. Update the existing class-contract regression test first so the current `40rem` implementation fails, then apply the one-token production change. The inspector tab list and every interaction/data behavior remain unchanged.

**Tech Stack:** React 19, TypeScript, Tailwind CSS v4 utility classes, Vitest, Testing Library.

## Global Constraints

- The drawer width contract is exactly `w-full sm:w-[min(45rem,calc(100%-2rem))]`.
- Preserve full-width behavior on narrow screens and the two-rem viewport margin on wider screens.
- Keep `max-w-full justify-start overflow-x-auto` on the inspector `TabsList`; do not wrap or vertically stack tabs.
- Do not change focus, Escape ownership, close behavior, landmarks, loading/error states, selection, actions, data fetching, or scroll ownership.
- Do not change workflow lane colors or card rendering.
- Do not add state, persistence, localization strings, raw colors, or dependencies.
- Preserve unrelated worktree changes and untracked documentation.

---

### Task 1: Revise the workflow drawer cap to 45rem

**Files:**
- Modify: `apps/desktop/src/app/workflows/workflow-run-drawer.test.tsx:47-65`
- Modify: `apps/desktop/src/app/workflows/workflow-run-drawer.tsx:69-75`

**Interfaces:**
- Consumes: Existing `WorkflowRunDrawer` aside class contract `w-full sm:w-[min(40rem,calc(100%-2rem))]` and the `classTokens` regression-test helper.
- Produces: Exact responsive class contract `w-full sm:w-[min(45rem,calc(100%-2rem))]`.
- Preserves: `RunInspector` and its `TabsList` class `mt-3 max-w-full justify-start overflow-x-auto` without modification.

- [ ] **Step 1: Write the failing 45rem class-contract test**

In `apps/desktop/src/app/workflows/workflow-run-drawer.test.tsx`, replace the current desktop-width assertions inside `renders distinct run-details and run-inspector complementary regions` with:

```ts
expect(classTokens(drawer)).toContain('w-full')
expect(classTokens(drawer)).toContain('sm:w-[min(45rem,calc(100%-2rem))]')
expect(classTokens(drawer)).not.toContain('sm:w-[min(40rem,calc(100%-2rem))]')
```

Keep the surrounding anchoring, border ownership, height, and landmark assertions unchanged.

- [ ] **Step 2: Run the drawer test and verify RED**

Run from `apps/desktop`:

```bash
npx vitest run --project ui src/app/workflows/workflow-run-drawer.test.tsx
```

Expected: FAIL because the rendered drawer still contains the `40rem` token, lacks the `45rem` token, or both. The other four drawer tests remain passing.

- [ ] **Step 3: Apply the exact 45rem responsive width**

In `apps/desktop/src/app/workflows/workflow-run-drawer.tsx`, replace only the outer aside's width class so the complete class value is:

```tsx
className="absolute inset-y-0 right-0 z-20 w-full bg-(--ui-bg-elevated) sm:w-[min(45rem,calc(100%-2rem))]"
```

Do not edit `run-inspector.tsx`; its horizontal overflow fallback is already correct.

- [ ] **Step 4: Run the drawer test and verify GREEN**

Run from `apps/desktop`:

```bash
npx vitest run --project ui src/app/workflows/workflow-run-drawer.test.tsx
```

Expected: PASS — one file and five tests, including the exact `45rem` contract and the absence of the retired `40rem` token.

- [ ] **Step 5: Run the workflow UI regression suite**

Run from `apps/desktop`:

```bash
npm run test:workflow-ui
```

Expected: PASS across the workflow drawer, inspector, activity board, Kanban compatibility adapter, accessibility, virtualization, and workflow operations.

- [ ] **Step 6: Run static gates**

Run from `apps/desktop`:

```bash
npm run typecheck
npm run lint
```

Expected: typecheck exits zero. Lint exits zero with no new warning attributable to the two modified files; pre-existing repository warnings may remain.

- [ ] **Step 7: Verify scope and whitespace**

Run from the repository root:

```bash
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors and exactly the two assigned workflow drawer files modified. Existing unrelated untracked documentation remains untouched.

- [ ] **Step 8: Commit the 45rem follow-up**

```bash
git add \
  apps/desktop/src/app/workflows/workflow-run-drawer.tsx \
  apps/desktop/src/app/workflows/workflow-run-drawer.test.tsx
git commit -m "style(desktop): widen workflow inspector to 45rem"
```

- [ ] **Step 9: Verify the committed state**

Run from the repository root:

```bash
git log -2 --oneline
git diff --check HEAD~1..HEAD
git status --short --branch
```

Expected: the new style commit is present, its diff has no whitespace errors, and only pre-existing unrelated untracked files remain.
