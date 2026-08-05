# Kanban Attachment Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clicking a kanban task's attachment opens the file in the app's existing preview panel, with Tier 1's OS reveal retained as a secondary control on the same row.

**Architecture:** Add one door to the plugin SDK (`host.previewFile`) that normalizes an absolute path through the existing main-process resolver and opens a right-rail preview tab. The kanban attachment row then splits into two sibling buttons: the filename opens the preview, a folder icon reveals in the OS file manager (Tier 1's existing chain, unchanged). No file-tree, workspace, or cwd involvement.

**Tech Stack:** TypeScript, React 19, Vitest + @testing-library/react, nanostores, Electron IPC.

**Design doc:** `docs/plans/2026-08-05-kanban-attachment-preview-design.md`

**Note on location:** plans live in `docs/plans/*-plan.md`, not
`docs/superpowers/plans/` — `.gitignore:136` ignores all of
`docs/superpowers/*`, so a plan written there is untracked and `git add -A`
skips it silently.

## Global Constraints

- Branch: `base`. It is brand-neutral — no brand names (OTTO, LOOP24) in code, copy, or tests.
- Plugins under `src/plugins/` may import **only** `@hermes/plugin-sdk` (and `react`). Enforced by the `no-restricted-imports` ESLint rule; its message is "Missing something? Add it to the SDK". Test files under `src/plugins/` are **not** exempt.
- Shared upstream files get **additive** changes only; do not restructure.
- Kanban i18n keys live in `apps/desktop/src/plugins/kanban/i18n.ts` and must be added to **all four** locale objects (`en`, `ja`, `zh`, `zh-hant`) — the `KanbanMessages` type requires every key in every object.
- Both gates must pass before any commit is considered done:
  - `npm run test:ui --prefix apps/desktop -- src/plugins/kanban src/sdk`
  - `cd apps/desktop && npx tsc --noEmit -p tsconfig.json`
- `npm run test:ui` does **not** typecheck. A missing type declaration passes every test and fails only `tsc`.
- Pre-existing, unrelated: `src/app/settings/model-settings.test.tsx` has 3 failures on clean `base`. Do not try to fix them; do not treat them as caused by this work.
- Run all commands from the repo root unless a step says otherwise.

## File Structure

| File | Responsibility |
|---|---|
| `apps/desktop/src/sdk/index.ts` | **Modify.** Add the `host.previewFile` door + its two core imports. Shared upstream file. |
| `apps/desktop/src/sdk/preview-file.test.ts` | **Create.** Unit tests for the door. No SDK test file exists today. |
| `apps/desktop/src/plugins/kanban/i18n.ts` | **Modify.** One new key in the type + four locale objects. |
| `apps/desktop/src/plugins/kanban/drawer.tsx` | **Modify.** Split `AttachmentRow` into two sibling controls. |
| `apps/desktop/src/plugins/kanban/attachments.test.tsx` | **Modify.** Extend Tier 1's tests for the two controls. |
| `docs/upstream-customizations/kanban-attachment-access.yaml` | **Modify.** Add a second entry. Do **not** create a new manifest. |

---

### Task 1: Add the `previewFile` door to the plugin SDK

**Files:**
- Modify: `apps/desktop/src/sdk/index.ts` (imports near line 21-30; `host` object ends at line 112)
- Test: `apps/desktop/src/sdk/preview-file.test.ts` (create)

**Interfaces:**
- Consumes: `normalizeOrLocalPreviewTarget(rawTarget: string, cwd?: string | null): Promise<PreviewTarget | null>` from `@/lib/local-preview`; `openPreview(target: PreviewTarget, source?: PreviewRecordSource): void` from `@/store/preview`.
- Produces: `host.previewFile(path: string): Promise<boolean>` — resolves `true` when a preview tab was opened, `false` when the path is empty or the file is missing/unreadable/has no desktop bridge. Never throws. Task 3 consumes this.

**Why `'tool-result'` and not `'file-browser'`:** `openPreview` maps the source tag to HTML render mode in `previewTargetForSource` (`src/store/preview.ts:159`) — `'file-browser'` and `'manual'` produce `renderMode: 'source'`, everything else produces `'preview'`. The file tree passes `'file-browser'`, so copying it is the obvious move and it is wrong: an HTML artifact would render as raw source code. `'tool-result'` renders it, and is semantically exact.

- [ ] **Step 1: Write the failing test**

Create `apps/desktop/src/sdk/preview-file.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'

const normalizeOrLocalPreviewTarget = vi.fn()
const openPreview = vi.fn()

vi.mock('@/lib/local-preview', () => ({
  normalizeOrLocalPreviewTarget: (...args: unknown[]) => normalizeOrLocalPreviewTarget(...args)
}))

vi.mock('@/store/preview', () => ({
  openPreview: (...args: unknown[]) => openPreview(...args)
}))

const { host } = await import('./index')

const fileTarget = { kind: 'file' as const, label: 'report.md', path: '/tmp/report.md', url: 'file:///tmp/report.md' }

afterEach(() => {
  vi.clearAllMocks()
})

describe('host.previewFile', () => {
  it('opens a preview tab for a resolvable file', async () => {
    normalizeOrLocalPreviewTarget.mockResolvedValue(fileTarget)

    await expect(host.previewFile('/tmp/report.md')).resolves.toBe(true)
    expect(normalizeOrLocalPreviewTarget).toHaveBeenCalledWith('/tmp/report.md')
    expect(openPreview).toHaveBeenCalledWith(fileTarget, 'tool-result')
  })

  // 'file-browser'/'manual' flip HTML to renderMode 'source'. A task artifact
  // must RENDER, so the tag has to stay outside that set.
  it('tags the preview as a tool result so HTML renders instead of showing source', async () => {
    normalizeOrLocalPreviewTarget.mockResolvedValue(fileTarget)

    await host.previewFile('/tmp/report.html')

    expect(openPreview.mock.calls[0][1]).toBe('tool-result')
  })

  it('reports failure for a missing or unreadable file instead of throwing', async () => {
    normalizeOrLocalPreviewTarget.mockResolvedValue(null)

    await expect(host.previewFile('/tmp/gone.md')).resolves.toBe(false)
    expect(openPreview).not.toHaveBeenCalled()
  })

  it('reports failure for an empty path without touching the resolver', async () => {
    await expect(host.previewFile('')).resolves.toBe(false)
    expect(normalizeOrLocalPreviewTarget).not.toHaveBeenCalled()
  })

  it('reports failure instead of propagating a resolver throw', async () => {
    normalizeOrLocalPreviewTarget.mockRejectedValue(new Error('no bridge'))

    await expect(host.previewFile('/tmp/report.md')).resolves.toBe(false)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/desktop && npx vitest run --project ui src/sdk/preview-file.test.ts`
Expected: FAIL — `host.previewFile is not a function`.

- [ ] **Step 3: Add the two imports**

In `apps/desktop/src/sdk/index.ts`, add to the existing `@/…` import block (keep it alphabetically sorted by path — the `perfectionist/sort-imports` rule enforces this; `@/lib/local-preview` sorts after `@/hermes`, and `@/store/preview` after `@/store/gateway`):

```ts
import { normalizeOrLocalPreviewTarget } from '@/lib/local-preview'
import { openPreview } from '@/store/preview'
```

- [ ] **Step 4: Add the door to the `host` object**

In the same file, inside the `host` object literal, after the `navigate` member and before `onEvent` (members are grouped loosely by purpose; this keeps the doc comment blocks readable):

```ts
  /** Open a local file in the app's preview rail — the in-app way to SHOW a
   *  file, as opposed to `ctx.os.revealPath`, which hands it to the OS file
   *  manager. Absolute paths anywhere on disk are accepted; the main process
   *  resolver confines nothing, so a plugin's own data directory works.
   *  Resolves false when the path is empty or the file is missing/unreadable,
   *  so callers can tell the user instead of leaving a dead click. */
  previewFile: async (path: string): Promise<boolean> => {
    if (!path) {
      return false
    }

    try {
      const target = await normalizeOrLocalPreviewTarget(path)

      if (!target) {
        return false
      }

      // NOT 'file-browser'/'manual' — those flip HTML to renderMode 'source'
      // (previewTargetForSource). A task artifact must render.
      openPreview(target, 'tool-result')

      return true
    } catch {
      return false
    }
  },
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd apps/desktop && npx vitest run --project ui src/sdk/preview-file.test.ts`
Expected: PASS, 5 tests.

- [ ] **Step 6: Typecheck and lint**

Run:
```bash
cd apps/desktop && npx tsc --noEmit -p tsconfig.json
npx eslint src/sdk/
npx prettier --check 'src/sdk/*.ts'
```
Expected: all exit 0. If prettier complains, run `npx prettier --write 'src/sdk/*.ts'`.

- [ ] **Step 7: Commit**

```bash
git add apps/desktop/src/sdk/index.ts apps/desktop/src/sdk/preview-file.test.ts
git commit -m "feat(sdk): let a plugin open a local file in the preview rail

Plugins may import only @hermes/plugin-sdk, and the SDK exposed no way to
show a file in the app -- only ctx.os.revealPath, which hands it to the OS
file manager and ejects the user from the app.

The door tags the preview 'tool-result', not 'file-browser'. The tag drives
HTML render mode: 'file-browser' and 'manual' show raw source, everything
else renders. A file a plugin surfaces is a result to look at, not source
to read."
```

---

### Task 2: Add the failure-toast copy to the kanban i18n bundle

**Files:**
- Modify: `apps/desktop/src/plugins/kanban/i18n.ts`

**Interfaces:**
- Produces: `k.couldNotOpenAttachment: string` on the `KanbanMessages` shape. Task 3 consumes it.

This task has no test of its own — the `KanbanMessages` type makes a missing locale a `tsc` failure, and Task 3's toast test exercises the value. It is separated from Task 3 only because it touches a different file with a mechanical four-locale edit that is easy to half-do.

- [ ] **Step 1: Add the key to the type**

In the `KanbanMessages` type, immediately after the existing `revealAttachment` line (Tier 1 added it after `uploadAttachment`):

```ts
  revealAttachment: (name: string) => string
  couldNotReveal: string
  couldNotOpenAttachment: string
```

(`revealAttachment` and `couldNotReveal` already exist — the new line is only `couldNotOpenAttachment`.)

- [ ] **Step 2: Add the value to all four locale objects**

Add one line after each existing `couldNotReveal:` line:

```ts
// en
  couldNotOpenAttachment: 'Could not open this file',
// ja
  couldNotOpenAttachment: 'このファイルを開けませんでした',
// zh
  couldNotOpenAttachment: '无法打开此文件',
// zh-hant
  couldNotOpenAttachment: '無法開啟此檔案',
```

- [ ] **Step 3: Verify every locale got it**

Run: `grep -c "couldNotOpenAttachment" apps/desktop/src/plugins/kanban/i18n.ts`
Expected: `5` (one type declaration + four locale values). Any other number means a locale was missed.

- [ ] **Step 4: Typecheck**

Run: `cd apps/desktop && npx tsc --noEmit -p tsconfig.json`
Expected: exit 0. A missed locale fails here with "Property 'couldNotOpenAttachment' is missing".

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/plugins/kanban/i18n.ts
git commit -m "i18n(kanban): add copy for an attachment that could not be opened"
```

---

### Task 3: Split the attachment row into view + reveal controls

**Files:**
- Modify: `apps/desktop/src/plugins/kanban/drawer.tsx` (`AttachmentRow`, currently one `<Button>`)
- Modify: `apps/desktop/src/plugins/kanban/attachments.test.tsx`

**Interfaces:**
- Consumes: `host.previewFile(path: string): Promise<boolean>` (Task 1); `k.couldNotOpenAttachment: string` (Task 2); Tier 1's `revealAttachment(path: string): Promise<boolean>` from `./api` and `k.revealAttachment(name)` / `k.couldNotReveal`, all unchanged.
- Produces: nothing consumed by later tasks.

**Two constraints that are easy to get wrong:**

1. **No nested buttons.** Tier 1 made the whole row a single `<Button>`. A button inside a button is invalid HTML and breaks activation. The row becomes a flex container with two *sibling* buttons.
2. **The primary control's accessible name stays the filename.** Tier 1 deliberately put the filename in the name and the action in the `title` (description). Do not "improve" this into an `aria-label` — it would hide the filename from assistive tech and break the role+name queries, which must stay locale-independent because a plugin test cannot register locale bundles (the `@/i18n` import is lint-fenced).

- [ ] **Step 1: Write the failing tests**

In `apps/desktop/src/plugins/kanban/attachments.test.tsx`, add `previewFile` to the `host` import usage and append these cases. Keep every existing Tier 1 test unchanged.

```tsx
describe('attachment row controls', () => {
  it('opens the file in the preview rail when the filename is activated', async () => {
    const previewFile = vi.spyOn(host, 'previewFile').mockResolvedValue(true)
    const revealPath = vi.fn().mockResolvedValue(true)

    bindOs({ revealPath })
    renderSection([attachment()])

    fireEvent.click(screen.getByRole('button', { name: /report\.md/ }))
    await waitFor(() => expect(previewFile).toHaveBeenCalled())

    expect(previewFile).toHaveBeenCalledWith('/home/u/.hermes/kanban/attachments/t-1/report.md')
    // Viewing must not also throw the user into the OS file manager.
    expect(revealPath).not.toHaveBeenCalled()
  })

  it('keeps a separate control that reveals the file in the file manager', () => {
    const revealPath = vi.fn().mockResolvedValue(true)
    const previewFile = vi.spyOn(host, 'previewFile').mockResolvedValue(true)

    bindOs({ revealPath })
    renderSection([attachment()])

    // Plugin tests can't register locale bundles (@/i18n is lint-fenced for
    // plugins), so k.revealAttachment(...) yields its raw key here.
    fireEvent.click(screen.getByRole('button', { name: 'revealAttachment' }))

    expect(revealPath).toHaveBeenCalledWith('/home/u/.hermes/kanban/attachments/t-1/report.md')
    expect(previewFile).not.toHaveBeenCalled()
  })

  it('says so when the file could not be opened', async () => {
    vi.spyOn(host, 'previewFile').mockResolvedValue(false)
    const notify = vi.spyOn(host, 'notify').mockReturnValue('toast-id')

    bindOs({ revealPath: vi.fn().mockResolvedValue(true) })
    renderSection([attachment()])

    fireEvent.click(screen.getByRole('button', { name: /report\.md/ }))
    await waitFor(() => expect(notify).toHaveBeenCalled())

    expect(notify.mock.calls[0][0]).toMatchObject({ kind: 'warning' })
  })

  it('renders neither control without a stored path', () => {
    renderSection([attachment({ stored_path: '' })])

    expect(screen.getByText('report.md')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /report\.md/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'revealAttachment' })).toBeNull()
  })
})
```

**Accessible names under test — use these exactly.** Plugin tests cannot register locale bundles (the `@/i18n` import is lint-fenced), so every `k.*` call returns its raw key. Tier 1 proved this empirically: its first failing run dumped `aria-label="revealAttachment"`. Therefore:

- **primary** button's name is `report.md` — it comes from the button's *content*, so it is locale-independent and stays correct in the app.
- **secondary** button's name is the literal string `revealAttachment` — it comes from `aria-label={k.revealAttachment(filename)}`, which is the untranslated key under test.

The queries in the test code above already use these names.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/desktop && npx vitest run --project ui src/plugins/kanban/attachments.test.tsx`
Expected: the four new tests FAIL (only one button in the row; `host.previewFile` never called). Tier 1's seven tests still PASS.

If a query fails on the accessible name rather than on the missing behavior, read the "accessible roles" dump testing-library prints on failure — it lists every button with its resolved name — and correct the query to match. Do not change the component to satisfy a query.

- [ ] **Step 3: Rewrite `AttachmentRow` as two sibling controls**

Replace the whole `AttachmentRow` function in `drawer.tsx` with:

```tsx
// The clickable form of an attachment row: TWO sibling controls, never nested
// (a button inside a button is invalid and breaks activation). The filename
// opens the file in the preview rail — the thing the user actually wants — and
// the folder icon hands it to the OS file manager for copying it elsewhere.
// The filename stays the primary control's accessible NAME (that's what the
// user is looking for); what a click does is the description.
function AttachmentRow({ filename, storedPath }: { filename: string; storedPath: string }) {
  const k = useKanban()

  return (
    <div className="flex min-w-0 flex-1 items-center gap-0.5">
      <Button
        className="-mx-1 h-auto min-w-0 flex-1 justify-start gap-1.5 px-1 py-0.5 font-normal text-(--ui-text-tertiary)"
        onClick={() =>
          void host.previewFile(storedPath).then(ok => {
            if (!ok) {
              host.notify({ kind: 'warning', message: k.couldNotOpenAttachment })
            }
          })
        }
        size="xs"
        title={k.openAttachment(filename)}
        variant="ghost"
      >
        <Codicon name="file" size="0.75rem" />
        <span className="truncate">{filename}</span>
      </Button>
      <Button
        aria-label={k.revealAttachment(filename)}
        className="shrink-0"
        onClick={() =>
          void revealAttachment(storedPath).then(ok => {
            if (!ok) {
              host.notify({ kind: 'warning', message: k.couldNotReveal })
            }
          })
        }
        size="icon-xs"
        variant="ghost"
      >
        <Codicon name="folder-opened" size="0.75rem" />
      </Button>
    </div>
  )
}
```

**This introduces `k.openAttachment`, which does not exist yet.** Add it now, in the same shape as Task 2's key — one line in the `KanbanMessages` type and one in each of the four locale objects:

```ts
  openAttachment: (name: string) => string
```
```ts
// en
  openAttachment: name => `Open ${name}`,
// ja
  openAttachment: name => `${name} を開く`,
// zh
  openAttachment: name => `打开 ${name}`,
// zh-hant
  openAttachment: name => `開啟 ${name}`,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/desktop && npx vitest run --project ui src/plugins/kanban/attachments.test.tsx`
Expected: PASS, 11 tests (Tier 1's 7 + 4 new).

- [ ] **Step 5: Typecheck, lint, format**

Run:
```bash
cd apps/desktop && npx tsc --noEmit -p tsconfig.json
npx eslint src/plugins/kanban/
npx prettier --check 'src/plugins/kanban/*.{ts,tsx}'
```
Expected: all exit 0. `grep -c "openAttachment" src/plugins/kanban/i18n.ts` should be `5`.

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/src/plugins/kanban/drawer.tsx \
        apps/desktop/src/plugins/kanban/i18n.ts \
        apps/desktop/src/plugins/kanban/attachments.test.tsx
git commit -m "feat(kanban): open a task attachment in the app instead of the file manager

Tier 1 made the attachment reachable by revealing it in Finder/Explorer.
That solved 'cannot get to the file', not 'wants to see the file' -- a
click still ejected a non-technical user out of the app.

The filename now opens the file in the preview rail. Tier 1's reveal
becomes a separate folder button for when the user needs the real file on
disk. Two sibling controls, never nested."
```

---

### Task 4: Record the customization in the upstream ledger

**Files:**
- Modify: `docs/upstream-customizations/kanban-attachment-access.yaml`

**Interfaces:**
- Consumes: the symbols added in Tasks 1 and 3.
- Produces: nothing.

Extend the **existing** manifest — this is the same feature Tier 1 registered, and its `kanban-attachment-reveal` entry already governs the row. Do not create a new file.

- [ ] **Step 1: Append the second entry**

Add to the `upstream_changes:` list, after the existing `kanban-attachment-reveal` entry:

```yaml
- id: kanban-attachment-preview
  change_class: product-surface-generic
  owner: downstream-edge-capability
  overlap_policy: any_owned_file
  files:
  - apps/desktop/src/sdk/index.ts
  - apps/desktop/src/sdk/preview-file.test.ts
  - apps/desktop/src/plugins/kanban/drawer.tsx
  - apps/desktop/src/plugins/kanban/i18n.ts
  - apps/desktop/src/plugins/kanban/attachments.test.tsx
  owned_symbols:
  - previewFile
  - AttachmentRow
  tests:
  - apps/desktop/src/sdk/preview-file.test.ts
  - apps/desktop/src/plugins/kanban/attachments.test.tsx
  expected_commit_subject: 'feat(kanban): open a task attachment in the app instead of the file manager'
  upstream_candidate: true
  merge_guidance: >-
    UNION on merge, never --theirs. Additive throughout, so an upstream rewrite
    of any file drops it with no conflict and no failing test outside the named
    ones.

    THE SOURCE TAG IS THE SILENT TRAP. host.previewFile calls
    openPreview(target, 'tool-result'). previewTargetForSource maps
    'file-browser' and 'manual' to renderMode 'source' and everything else to
    'preview'. The file tree -- the sibling caller anyone will look at first --
    passes 'file-browser', so "fixing" this to match it degrades every HTML
    artifact to raw source code. There is no type error and no failing test
    outside the "tags the preview as a tool result" case in
    src/sdk/preview-file.test.ts. Verify that test by name after any merge
    touching either file.

    THE ROW IS TWO SIBLING BUTTONS, NOT ONE. Tier 1 shipped a single Button for
    the whole row; Tier 2 splits it because a second action cannot nest inside
    it. A merge that restores Tier 1's single-button shape produces valid-looking
    markup that silently drops the view action -- the primary one.

    The primary control's accessible NAME must stay the filename, with the
    action in `title`. An aria-label would replace the name, hide the filename
    from assistive tech, and break the role+name queries. Those queries must
    stay locale-independent: plugins may import only @hermes/plugin-sdk, so a
    plugin test cannot register locale bundles and every k.* call returns its
    raw key under test.

    There is deliberately no bind-at-register-time step here, unlike Tier 1's
    bindOs. `host` is a module-level SDK object closing over core functions, so
    a host door needs no per-plugin binding. Do not add one.

    i18n keys openAttachment and couldNotOpenAttachment live in the
    PLUGIN-scoped bundle and are required by KanbanMessages in all four locale
    objects. `npm run test:ui` does NOT typecheck; a dropped key fails only
    `npx tsc --noEmit -p apps/desktop/tsconfig.json`.
  removal_condition: >-
    Remove when upstream's own kanban drawer opens an attachment in the preview
    rail -- i.e. when clicking a completed task's output file shows it in the
    app without this code. Keep the tests.
  last_verified_upstream: 36cb5ae5530a75def7df3195e49b7a4aa2add482
```

- [ ] **Step 2: Validate the manifest**

Run:
```bash
python3 scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/kanban-attachment-access.yaml --strict
```
Expected: exit 0, no output.

Note: the checker needs Python 3.11+ for `tomllib`. If the system `python3` is older, use the repo venv: `./venv/bin/python`. It resolves `owned_symbols` against the **committed** HEAD, so Tasks 1 and 3 must be committed first.

- [ ] **Step 3: Commit**

```bash
git add docs/upstream-customizations/kanban-attachment-access.yaml
git commit -m "docs(ledger): record the attachment preview door and the split row"
```

---

### Task 5: Full-suite verification

**Files:** none modified.

- [ ] **Step 1: Run the kanban and SDK suites**

Run: `npm run test:ui --prefix apps/desktop -- src/plugins/kanban src/sdk`
Expected: PASS — 16 kanban (Tier 1's 5 in `model-override` + 11 in `attachments`) plus 5 SDK.

- [ ] **Step 2: Typecheck the whole desktop app**

Run: `cd apps/desktop && npx tsc --noEmit -p tsconfig.json`
Expected: exit 0.

- [ ] **Step 3: Run the full UI suite and confirm the only failures are pre-existing**

Run: `npm run test:ui --prefix apps/desktop`
Expected: exactly 3 failures, all in `src/app/settings/model-settings.test.tsx`. If anything else fails, it was caused by this work — fix it before proceeding.

To confirm a suspected pre-existing failure: `git stash -u`, re-run that one file, `git stash pop`.

- [ ] **Step 4: Verify by hand in the running app**

Run: `npm run dev --prefix apps/desktop` (or launch the built app). Open a kanban task with an attachment and check:
1. Clicking the filename opens the file in the right-rail preview.
2. An HTML artifact **renders**; it does not show raw source. This is the `'tool-result'` behavior and no automated test can prove it end-to-end.
3. The folder button opens Finder/Explorer with the file selected.
4. Renaming the file on disk, then clicking the filename, shows the warning toast rather than doing nothing.

---

## Self-Review

**Spec coverage:** SDK door → Task 1. `'tool-result'` tag → Task 1 (test + comment) and Task 4 (ledger). Two-sibling-button row → Task 3. Accessible-name rule → Task 3. `stored_path` absent → Task 3. Error handling/toast → Tasks 2 and 3. i18n four-locale rule → Tasks 2 and 3. Testing + `tsc` gate → every task, plus Task 5. Ledger → Task 4. Out-of-scope items (tree, multi-root, editing, backend) have no tasks, correctly.

**Type consistency:** `host.previewFile(path: string): Promise<boolean>` is defined in Task 1 and consumed with that exact signature in Task 3. `revealAttachment` and `bindOs` keep Tier 1's signatures. i18n keys `couldNotOpenAttachment` (Task 2) and `openAttachment` (Task 3) are declared where introduced and used with matching arity.

**Placeholder scan:** clean. The one open fork in the first draft — the reveal button's accessible name under test — was resolved from Tier 1's recorded evidence (its first failing run dumped `aria-label="revealAttachment"`) rather than deferred to the implementer. Task 3 now states both names exactly.

**Spec drift corrected during planning:** the design doc's testing section called for a "binds the door at register time" test, mirroring Tier 1's `bindOs(ctx.os)`. That is wrong for this surface — `host` is a module-level SDK object closing over core functions (`sdk/index.ts:58`), so a `host` door needs no per-plugin binding. The design doc was corrected in the same commit as this plan, and Task 4's ledger guidance records it so a future reader does not re-add the step.
