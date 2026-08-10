# Workflow Kanban Adversarial Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clear the Fable 5 implementation-review BLOCK by making drawer Escape/focus behavior shell-safe, preserving the drawer separator inside pane zones, and keeping the board and cleanup controls reachable under a large Attention inbox.

**Architecture:** Reuse the Desktop shell's existing escape-layer and pane-visibility contracts instead of creating another keyboard system. Keep the drawer as a nonmodal page-local `aside`, but move its separator to an inner frame that is outside the shell's edge-chrome reset. Make Attention a bounded flex child whose list owns overflow, without changing workflow data or actions.

**Tech Stack:** React 19, TypeScript 6, nanostores, TanStack Query, Tailwind CSS 4, Vitest 4, Testing Library, Desktop pane-shell contexts.

## Global Constraints

- Execute only in `.worktrees/workflow-kanban-view-alignment` on `feat/workflow-kanban-view-alignment`.
- Development main means `base`; literal `main` is synchronization-only.
- Preserve the implementation boundary: no Python, backend, plugin, SDK Kanban, Electron main-process, dependency, config, environment-variable, or locale changes.
- Workflow queries, mutations, action payloads, 409 recovery, polling, cleanup semantics, selected-run identity authority, and the seven-tab inspector remain unchanged.
- Keep the drawer nonmodal; do not add a backdrop, focus trap, portal, or modal dialog semantics.
- Hidden kept-alive panes must not register global Escape ownership.
- A single Escape closes at most one intended surface; higher-priority app layers and Radix surfaces win.
- Focus restoration must never move focus from another pane or shell surface into Workflows.
- Attention ordering, item copy, actions, and click-through behavior remain unchanged.
- Every production correction follows RED → GREEN. Run focused tests after each task and commit atomically.
- Final verification is `npm run test:workflow-ui`, `npm run typecheck`, `npm run lint`, and `git diff --check` from `apps/desktop` unless the command is repository-scoped.

---

### Task 0: Reconfirm the approved execution baseline

**Files:** None

**Interfaces:** None; this is a read-only safety gate.

- [ ] **Step 1: Verify checkout, branch, and clean state.**

  Run from the worktree root:

  ```bash
  pwd
  git branch --show-current
  git rev-parse HEAD
  git status --short
  git log -3 --oneline
  ```

  Expected:

  - path ends with `.worktrees/workflow-kanban-view-alignment`;
  - branch is `feat/workflow-kanban-view-alignment`;
  - HEAD includes design-addendum commit `3341765d8`;
  - the worktree is clean.

- [ ] **Step 2: Run the pre-remediation focused baseline.**

  Run from `apps/desktop`:

  ```bash
  npx vitest run --project ui \
    src/app/workflows/workflow-run-drawer.test.tsx \
    src/app/workflows/index.test.tsx \
    src/lib/escape-layers.test.ts
  ```

  Expected: existing tests pass before new RED cases are added. If not, stop and diagnose the baseline failure.

---

### Task 1: Make drawer Escape ownership and focus restoration shell-safe

**Finding:** F-1 (HIGH)

**Files:**

- Modify: `apps/desktop/src/lib/escape-layers.ts`
- Modify: `apps/desktop/src/lib/escape-layers.test.ts`
- Modify: `apps/desktop/src/app/workflows/workflow-run-drawer.tsx`
- Modify: `apps/desktop/src/app/workflows/workflow-run-drawer.test.tsx`
- Modify: `apps/desktop/src/app/workflows/index.tsx`
- Modify: `apps/desktop/src/app/workflows/index.test.tsx`

**Interfaces:**

- Consumes: `usePaneVisible(): boolean`, `pushEscapeLayer(priority): () => void`, and `isTopEscapeLayer(priority): boolean`.
- Produces: `ESCAPE_PRIORITY.workflowDrawer === 5`; no new public component props.
- Focus boundary: `pageRef: React.RefObject<HTMLElement | null>` on the Workflows `<main>`.

- [ ] **Step 1: Add RED priority-ordering coverage.**

  Extend `escape-layers.test.ts` with an assertion that a narrow overlay outranks the drawer while the drawer remains top when alone:

  ```ts
  it('keeps the workflow drawer below every shell overlay', () => {
    const releaseDrawer = pushEscapeLayer(ESCAPE_PRIORITY.workflowDrawer)
    expect(isTopEscapeLayer(ESCAPE_PRIORITY.workflowDrawer)).toBe(true)

    const releaseNarrow = pushEscapeLayer(ESCAPE_PRIORITY.narrowOverlay)
    expect(isTopEscapeLayer(ESCAPE_PRIORITY.workflowDrawer)).toBe(false)

    releaseNarrow()
    expect(isTopEscapeLayer(ESCAPE_PRIORITY.workflowDrawer)).toBe(true)
    releaseDrawer()
  })
  ```

  Run from `apps/desktop`:

  ```bash
  npx vitest run --project ui src/lib/escape-layers.test.ts
  ```

  Expected: FAIL because `ESCAPE_PRIORITY.workflowDrawer` does not exist.

- [ ] **Step 2: Add the minimal drawer priority.**

  Add the lowest app-layer priority in `escape-layers.ts`:

  ```ts
  export const ESCAPE_PRIORITY = {
    workflowDrawer: 5,
    narrowOverlay: 10,
    layoutEdit: 20,
    zoneEditor: 30,
    overlay: 40,
    drag: 50
  } as const
  ```

  Run the test from Step 1. Expected: PASS.

- [ ] **Step 3: Add RED drawer tests for higher-layer and hidden-pane ownership.**

  In `workflow-run-drawer.test.tsx`, import `PaneVisibleContext` and the escape-layer helpers. Add two tests:

  ```tsx
  it('yields Escape to a higher application layer', () => {
    const onClose = vi.fn()
    const releaseOverlay = pushEscapeLayer(ESCAPE_PRIORITY.overlay)

    render(
      <I18nProvider configClient={null} initialLocale="en">
        <WorkflowRunDrawer {...base} onClose={onClose} />
      </I18nProvider>
    )
    fireEvent.keyDown(window, { key: 'Escape' })

    expect(onClose).not.toHaveBeenCalled()
    releaseOverlay()
  })

  it('does not own Escape while its kept-alive pane is hidden', () => {
    const onClose = vi.fn()

    render(
      <PaneVisibleContext.Provider value={false}>
        <I18nProvider configClient={null} initialLocale="en">
          <WorkflowRunDrawer {...base} onClose={onClose} />
        </I18nProvider>
      </PaneVisibleContext.Provider>
    )
    fireEvent.keyDown(window, { key: 'Escape' })

    expect(onClose).not.toHaveBeenCalled()
  })
  ```

  Use `try/finally` around the first assertion if needed so the overlay layer is always released.

  Run:

  ```bash
  npx vitest run --project ui src/app/workflows/workflow-run-drawer.test.tsx
  ```

  Expected: both new tests FAIL because the drawer always binds a window listener and never checks layer priority.

- [ ] **Step 4: Integrate the drawer with pane visibility and escape layers.**

  In `workflow-run-drawer.tsx`:

  ```ts
  import { usePaneVisible } from '@/components/pane-shell/pane-visibility'
  import { ESCAPE_PRIORITY, isTopEscapeLayer, pushEscapeLayer } from '@/lib/escape-layers'
  ```

  Inside the component, read visibility and replace the current effect:

  ```ts
  const visible = usePaneVisible()

  useEffect(() => {
    if (!visible) {
      return
    }

    const releaseLayer = pushEscapeLayer(ESCAPE_PRIORITY.workflowDrawer)
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.key !== 'Escape' ||
        event.defaultPrevented ||
        !isTopEscapeLayer(ESCAPE_PRIORITY.workflowDrawer)
      ) {
        return
      }

      event.preventDefault()
      event.stopPropagation()
      onClose()
    }

    window.addEventListener('keydown', onKeyDown)

    return () => {
      window.removeEventListener('keydown', onKeyDown)
      releaseLayer()
    }
  }, [onClose, visible])
  ```

  Run the drawer test. Expected: PASS.

- [ ] **Step 5: Add a RED integration test for cross-surface focus theft.**

  In `index.test.tsx`, open the drawer, focus a button appended outside the Workflows `<main>`, dispatch Escape, and assert that selection closes without moving focus:

  ```tsx
  it('does not restore Workflows focus when the drawer closes from another surface', async () => {
    $workflowSelectedRunId.set(null)
    getWorkflowRun.mockResolvedValue(run())
    listWorkflowRuns.mockResolvedValue({ next_cursor: null, runs: [run()], schema_version: 1 })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client)
    fireEvent.click(await screen.findByRole('button', { name: /Laptop diagnostic/ }))
    await screen.findByRole('complementary', { name: 'Laptop diagnostic run details' })

    const outside = document.createElement('button')
    document.body.append(outside)
    outside.focus()
    fireEvent.keyDown(window, { key: 'Escape' })

    await waitFor(() => expect($workflowSelectedRunId.get()).toBeNull())
    expect(document.activeElement).toBe(outside)
    outside.remove()
  })
  ```

  Ensure cleanup removes the external button even if the assertion fails.

  Run:

  ```bash
  npx vitest run --project ui src/app/workflows/index.test.tsx -t 'does not restore Workflows focus'
  ```

  Expected: FAIL because `closeRunDrawer` unconditionally focuses the card or heading.

- [ ] **Step 6: Gate focus restoration on the Workflows page boundary.**

  In `index.tsx`, add `pageRef` beside the existing refs and attach it to `<main>`:

  ```ts
  const pageRef = useRef<HTMLElement>(null)
  // ...
  <main ref={pageRef} ...>
  ```

  Capture the boundary before clearing selection:

  ```ts
  const closeRunDrawer = () => {
    const target = runReturnFocusRef.current
    const restoreFocus = pageRef.current?.contains(activeElement()) ?? false

    runReturnFocusRef.current = null
    selectWorkflowRun(null)

    if (!restoreFocus) {
      return
    }

    requestAnimationFrame(() => {
      if (target?.isConnected) {
        target.focus()
      } else {
        headingRef.current?.focus()
      }
    })
  }
  ```

  Update the existing card-focus test to call `card.focus()` before opening and focus the drawer Close button before clicking it, matching browser focus behavior rather than relying on Testing Library's synthetic click to move focus.

  Run:

  ```bash
  npx vitest run --project ui \
    src/lib/escape-layers.test.ts \
    src/app/workflows/workflow-run-drawer.test.tsx \
    src/app/workflows/index.test.tsx
  ```

  Expected: all tests PASS.

- [ ] **Step 7: Commit the Escape/focus fix.**

  ```bash
  git add \
    apps/desktop/src/lib/escape-layers.ts \
    apps/desktop/src/lib/escape-layers.test.ts \
    apps/desktop/src/app/workflows/workflow-run-drawer.tsx \
    apps/desktop/src/app/workflows/workflow-run-drawer.test.tsx \
    apps/desktop/src/app/workflows/index.tsx \
    apps/desktop/src/app/workflows/index.test.tsx
  git diff --cached --check
  git commit -m "fix(desktop): respect workflow drawer escape ownership"
  ```

---

### Task 2: Preserve the drawer separator inside pane zones

**Finding:** F-2 (MEDIUM)

**Files:**

- Modify: `apps/desktop/src/app/workflows/workflow-run-drawer.tsx`
- Modify: `apps/desktop/src/app/workflows/workflow-run-drawer.test.tsx`

**Interfaces:**

- Consumes: the existing shell contract that resets borders on zone-descendant `aside` elements.
- Produces: private marker `data-workflow-run-drawer-frame` on the full-height inner drawer frame.

- [ ] **Step 1: Add the RED separator-ownership test.**

  Extend the first drawer test:

  ```ts
  const classTokens = (element: Element) => element.className.split(/\s+/)
  const frame = drawer.querySelector('[data-workflow-run-drawer-frame]')!

  expect(classTokens(drawer)).not.toContain('border-l')
  expect(classTokens(frame)).toContain('border-l')
  expect(classTokens(frame)).toContain('h-full')
  ```

  Run:

  ```bash
  npx vitest run --project ui src/app/workflows/workflow-run-drawer.test.tsx
  ```

  Expected: FAIL because the `aside` currently owns `border-l` and no inner frame exists.

- [ ] **Step 2: Move the hairline to a full-height inner frame.**

  Keep positioning, width, background, id, and accessibility on `<aside>`, but remove `flex`, `flex-col`, and `border-l border-(--ui-stroke-tertiary)` from it. Wrap the existing header and scroll body with:

  ```tsx
  <div
    className="flex h-full min-h-0 flex-col border-l border-(--ui-stroke-tertiary)"
    data-workflow-run-drawer-frame
  >
    {/* existing header and body, unchanged */}
  </div>
  ```

  Do not weaken or special-case the pane-shell CSS rule.

  Run the drawer test. Expected: PASS.

- [ ] **Step 3: Commit the separator fix.**

  ```bash
  git add \
    apps/desktop/src/app/workflows/workflow-run-drawer.tsx \
    apps/desktop/src/app/workflows/workflow-run-drawer.test.tsx
  git diff --cached --check
  git commit -m "fix(desktop): preserve workflow drawer separator"
  ```

---

### Task 3: Bound Attention without hiding the board or cleanup controls

**Finding:** F-3 (MEDIUM)

**Files:**

- Modify: `apps/desktop/src/app/workflows/attention-inbox.tsx`
- Modify: `apps/desktop/src/app/workflows/index.tsx`
- Modify: `apps/desktop/src/app/workflows/index.test.tsx`

**Interfaces:**

- Consumes: `AttentionInbox({ items, onOpenRun })`; prop and item semantics remain unchanged.
- Produces: `data-workflow-attention-list` on the internally scrolling `<ul>`.
- Layout contract: the Attention `<section>` is a direct run-view flex child with `max-h-[40%] min-h-0 shrink-0`; its list uses `min-h-0 overflow-y-auto`.

- [ ] **Step 1: Add a RED bounded-layout integration assertion.**

  Extend the existing actionable-Attention test in `index.test.tsx` after the six rows render:

  ```ts
  const runView = screen.getByRole('main').querySelector('[data-workflow-run-view]')!
  const attention = screen.getByRole('region', { name: 'Workflow attention' })
  const attentionList = attention.querySelector('[data-workflow-attention-list]')!

  expect(attention.parentElement).toBe(runView)
  expect(attention.className).toContain('max-h-[40%]')
  expect(attention.className).toContain('min-h-0')
  expect(attention.className).toContain('shrink-0')
  expect(attentionList.className).toContain('min-h-0')
  expect(attentionList.className).toContain('overflow-y-auto')
  expect(runView.querySelector('[data-workflow-board-shell]')).toBeTruthy()
  ```

  Run:

  ```bash
  npx vitest run --project ui src/app/workflows/index.test.tsx -t 'renders actionable attention rows'
  ```

  Expected: FAIL because a wrapper owns `shrink-0`, the section is unbounded, and the list has no overflow marker.

- [ ] **Step 2: Make Attention the bounded flex child and its list the scroll owner.**

  In `index.tsx`, replace the wrapper with the component directly:

  ```tsx
  <AttentionInbox items={attention.data?.items ?? []} onOpenRun={openRunId} />
  ```

  In `attention-inbox.tsx`, update only layout classes and add the private marker:

  ```tsx
  <section
    aria-label={t.operations.workflowAttention}
    className="flex max-h-[40%] min-h-0 shrink-0 flex-col py-3"
  >
    <h2 className="shrink-0 text-sm font-medium">{t.operations.needsAttention}</h2>
    <ul
      className="mt-2 grid min-h-0 min-w-0 overflow-y-auto gap-2 sm:grid-cols-2 xl:grid-cols-3"
      data-workflow-attention-list
    >
  ```

  Do not change items, keys, labels, action selection, ordering, or click handlers.

  Run the focused test. Expected: PASS.

- [ ] **Step 3: Run workflow layout and Attention regressions.**

  ```bash
  npx vitest run --project ui \
    src/app/workflows/index.test.tsx \
    src/app/workflows/workflow-run-drawer.test.tsx \
    src/components/activity-board/activity-board.test.tsx \
    src/components/activity-board/activity-board.performance.test.tsx
  ```

  Expected: all tests PASS; board virtualization and lane scrolling remain unchanged.

- [ ] **Step 4: Commit the bounded-Attention fix.**

  ```bash
  git add \
    apps/desktop/src/app/workflows/attention-inbox.tsx \
    apps/desktop/src/app/workflows/index.tsx \
    apps/desktop/src/app/workflows/index.test.tsx
  git diff --cached --check
  git commit -m "fix(desktop): bound workflow attention layout"
  ```

---

### Task 4: Verify the full remediation and immutable boundaries

**Files:** None unless a verification failure reveals a defect; any correction must return to a new RED test before production edits.

**Interfaces:** The full workflow UI and Desktop compiler/linter gates.

- [ ] **Step 1: Run the full workflow UI suite.**

  From `apps/desktop`:

  ```bash
  npm run test:workflow-ui
  ```

  Expected: every workflow, Kanban, ActivityBoard, route, and workflow-topology test passes with zero failures.

- [ ] **Step 2: Run all Desktop TypeScript projects.**

  ```bash
  npm run typecheck
  ```

  Expected: renderer, Electron, and E2E TypeScript checks exit 0.

- [ ] **Step 3: Run Desktop lint.**

  ```bash
  npm run lint
  ```

  Expected: zero lint errors. Existing repository warnings may remain only if they are outside this change.

- [ ] **Step 4: Audit the final range and worktree.**

  From the worktree root:

  ```bash
  git diff --check
  git status --short --branch
  git log --oneline --decorate -7
  git diff --stat fb061582fb496d59e13316116701e34a4ba90d09..HEAD
  git diff --name-only fb061582fb496d59e13316116701e34a4ba90d09..HEAD
  ```

  Expected:

  - no whitespace errors;
  - clean worktree;
  - only the approved Desktop renderer/tests/design/plan files differ from the immutable baseline;
  - no backend, plugin, SDK Kanban, Electron main-process, dependency, config, or environment file appears.

- [ ] **Step 5: Prepare the targeted adversarial re-review range.**

  Record the new immutable candidate SHA and compare it against `dbb1f4795e9d6eb40122dc711df81ddb34133116`, the candidate reviewed by Fable 5. A targeted re-review should prove F-1 through F-3 closed and check that no new Critical, High, or Medium production issue was introduced.
