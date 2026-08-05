# Opening kanban task attachments inside the app (Tier 2)

Status: design approved, not implemented.
Date: 2026-08-05
Builds on: Tier 1, shipped in `01ca95f06` (OTTO/LOOP24 v5.2.4).

## The problem

A completed kanban task auto-attaches its output artifact. Tier 1 made that
attachment reachable: the drawer row became a control that reveals the file in
Finder/Explorer. That solved "the user cannot get to the file at all."

It did not solve the thing the user actually wants, which is to **see the
artifact**. Today a click ejects them from the app into the OS file manager,
where they must find and open the file themselves. For the non-technical users
this product targets, being thrown into Explorer is close to a dead end.

Two requirements, in priority order:

1. Clicking an attachment **shows the file's contents inside the app**.
2. The user can still **get to the folder** when they need the real file — to
   copy it elsewhere, or hand it to another tool.

Requirement 2 is already built. Tier 1 is it. Tier 2 adds requirement 1 and
demotes Tier 1's reveal to a secondary control.

## What Tier 1 established (the seam being extended)

Tier 1's chain, unchanged by this design:

```
AttachmentRow → revealAttachment(path) → ctx.os.revealPath → shell.showItemInFolder
```

`revealAttachment` and `bindOs` live in `apps/desktop/src/plugins/kanban/api.ts`;
`ctx.os` is the curated plugin OS door bound at register time in `plugin.tsx`,
exactly like `ctx.rest`. `KanbanAttachment.stored_path` carries the absolute
path, which the backend has always sent (`_attachment_dict`).

Tier 2 adds a **parallel** chain for viewing. It does not replace or rewrite the
reveal chain.

## Findings that determined the approach

These were verified against the code before choosing, because they decide
whether this is a small change or a large one.

### The preview panel is independent of the file tree, the workspace, and cwd

`openPreview()` (`apps/desktop/src/store/preview.ts:170`) adds a right-rail tab,
opens the preview pane, and selects the tab. It consults no workspace root, no
session cwd, and no tree state.

### The main-process resolver accepts an absolute path anywhere

`resolveRequestedPathForIpc` (`apps/desktop/electron/hardening.ts:175`) rejects
unsafe path syntax and resolves **relative** paths against `baseDir`. An
absolute path resolves to itself:

```ts
const resolvedPath = path.resolve(resolvedBase, raw)
```

There is no confinement to a workspace root, so
`<HERMES_HOME>/kanban/attachments/<task_id>/<file>` previews like any other
path. `previewFileTarget` (`apps/desktop/electron/main.ts:5245`) classifies the
result as `html | image | binary | text`.

### The viewer already handles the hard cases

Binary and oversized files render a refusal screen with a "Preview anyway"
action (`apps/desktop/src/app/chat/right-rail/preview-file.tsx:893`), not a
blank panel. And `previewFileTarget` returns `null` for a missing file, which
the caller turns into an error — so previewing a deleted attachment reports
failure.

That last point matters beyond Tier 2: Tier 1's `shell.showItemInFolder`
**silently no-ops on a missing path** (`main.ts:11528` returns `true`
regardless), so a deleted attachment currently produces a click that does
nothing. Routing the primary action through preview closes that gap for the
common case.

### The file tree cannot do this, and fails silently if asked

`revealFileInTree` / `$revealInTreeRequest` (`apps/desktop/src/store/layout.ts:323`)
does exist and does "expand ancestors, select, scroll". But the tree is
single-rooted at the session cwd (`right-sidebar/index.tsx:51`), and
`revealNode` (`right-sidebar/files/tree.tsx:106`) computes:

```ts
const rel = target.startsWith(root) ? target.slice(root.length).replace(/^[\\/]+/, '') : ''
```

A path outside the root yields `rel = ''` → no segments → no ancestors expanded
→ `select()`/`scrollTo()` against a node id that is not in the tree. No error,
no feedback. A detached chat has no cwd at all, so there is no tree to reveal
into.

Wiring the attachment click to the tree today would therefore ship a control
that silently does nothing — reintroducing the exact defect Tier 1 fixed. The
tree route requires a multi-root model first, and the user's priority ordering
makes it unnecessary.

## Decision

Clicking an attachment opens it in the **existing preview panel**. The file tree
is not involved. Tier 1's OS reveal is retained as a secondary control on the
same row.

Rejected alternatives:

- **Modal dialog showing the file.** Re-implements text/image/binary rendering
  that already exists, adds a second viewer users must learn, and supports
  neither tabs nor persistence. Strictly more code for a worse result.
- **Tree-first, then double-click.** Requires a new multi-root model in
  `use-project-tree` plus a fix for `revealNode`'s silent no-op, and still has
  nowhere to go in a detached chat. This is the bulk of a separate feature and
  is out of scope here.

## Architecture

```
click filename → previewAttachment(path) → host.previewFile(path)
                                             → normalizeOrLocalPreviewTarget(path)
                                             → openPreview(target, 'tool-result')
                                             → right-rail preview tab

click folder   → revealAttachment(path)  → ctx.os.revealPath  (Tier 1, unchanged)
```

### The new capability: a preview door on the plugin SDK

This is the only genuinely new surface. Plugins may import **only**
`@hermes/plugin-sdk` (enforced by `no-restricted-imports`, whose message is
"Missing something? Add it to the SDK"), and the SDK exports nothing
preview-related today.

```ts
host.previewFile(path: string): Promise<boolean>
```

Contract:

- Resolves `true` when a preview tab was opened.
- Resolves `false` when the path is empty, the file is missing or unreadable, or
  no desktop bridge is present. **Never throws** — matching every other `host`
  door, which are wrapped so an internal sync throw becomes a rejection rather
  than an error-boundary crash.

It belongs on `host`, not `ctx.os`: `ctx.os` is documented as "every way a
plugin reaches outside the app window," and preview is squarely inside it.

`apps/desktop/src/sdk/index.ts` is a shared upstream file, so this is an
additive export and requires ledger coverage.

### The source tag is load-bearing

`openPreview(target, source)` decides HTML render mode
(`preview.ts:159`):

```ts
renderMode: isFilePreviewSource(source) ? 'source' : 'preview'
// isFilePreviewSource → 'file-browser' | 'manual'
```

Copying `'file-browser'` from the file tree — the obvious move, since that is
the sibling caller — would display an HTML report **as raw source code**. A task
artifact should render.

The door uses **`'tool-result'`**, which produces `renderMode: 'preview'` and is
also semantically exact: the file is a tool result. The tag is hard-coded inside
the door rather than exposed as a parameter, keeping the core
`PreviewRecordSource` enum out of the SDK surface. A future plugin that needs
source view can widen it then.

### The attachment row becomes two sibling controls

Tier 1 made the entire row a single `<Button>`. Two actions cannot nest — a
button inside a button is invalid HTML and breaks activation semantics — so the
row becomes a flex container holding two siblings:

- **Primary**: `flex-1`, left-aligned, file codicon + filename. Opens the
  preview. Accessible **name** stays the filename (what the user is hunting
  for); the action goes in the **description**, preserving Tier 1's deliberate
  a11y decision and its locale-independent role+name test queries.
- **Secondary**: icon-only folder button. Reveals in the file manager, reusing
  Tier 1's existing `revealAttachment` i18n key as its `aria-label`.

The secondary control is **always rendered**, not hover-only: hover-gating would
strand keyboard and touch users, and this is the escape hatch when the viewer
refuses a file type.

`stored_path` absent or empty → the row stays plain text with neither control,
exactly as Tier 1 specifies. That rule is unchanged.

## Error handling

| Case | Behavior |
|---|---|
| File deleted or moved | `normalizePreviewTarget` returns `null` → door resolves `false` → warning toast. Closes the Tier 1 silent-no-op gap. |
| Binary or oversized | Existing refusal screen with "Preview anyway". No new code. |
| No desktop bridge (browser, older shell) | Door resolves `false` → same toast. |
| No `stored_path` | No controls rendered; plain text. |

The reveal button keeps Tier 1's behavior and its `couldNotReveal` toast
unchanged, including the known limitation that `showItemInFolder` cannot report
a missing file.

## i18n

One new key, `couldNotOpenAttachment`, added to the **plugin-scoped** bundle
`apps/desktop/src/plugins/kanban/i18n.ts` in all four locale objects (`en`,
`ja`, `zh`, `zh-hant`) — the `KanbanMessages` type requires every key in every
object, so a missed one fails `tsc`.

No core `src/i18n/` changes. If a global key ever becomes necessary it must go
in `types.ts` **and** `en.ts` **and** `zh.ts`, never `ar`/`ja`/`zh-hant`, which
use `defineLocale()` and fall back.

## Testing

TDD, extending `apps/desktop/src/plugins/kanban/attachments.test.tsx`:

- Primary click calls the preview door with `stored_path`.
- Secondary click calls `revealPath` with `stored_path`.
- A `false` result from the preview door surfaces a warning toast.
- No `stored_path` → neither control rendered, filename still visible.
- The plugin binds the preview door at register time — the Tier 1 lesson: the
  feature is inert in the real app while every render test passes if the door
  is never bound.

Plus an SDK-level test that `host.previewFile` resolves `false` for a missing
file rather than throwing.

Gates, both required:

```bash
npm run test:ui --prefix apps/desktop -- src/plugins/kanban
cd apps/desktop && npx tsc --noEmit -p tsconfig.json
```

`npm run test:ui` does **not** typecheck. In Tier 1 a missing type declaration
passed every test and failed only `tsc`.

Note: `src/app/settings/model-settings.test.tsx` has three failures that
pre-exist on `base` and are unrelated to this work.

## Upstream customization ledger

Extend the **existing** `docs/upstream-customizations/kanban-attachment-access.yaml`
with a second entry, `kanban-attachment-preview` — same feature, and the Tier 1
entry already governs the row. Do not create a new manifest file.

The entry adds `apps/desktop/src/sdk/index.ts` to the owned files and must
record:

- The `'tool-result'` source tag and why `'file-browser'` is wrong. A merge that
  "corrects" it to match the file tree silently degrades HTML artifacts to
  source view, with no test failure outside the named file.
- The two-sibling-button structure, and that the primary control's accessible
  name must stay the filename.
- That the door is bound at register time, like Tier 1's `bindOs`.

Validate with:

```bash
python3 scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/kanban-attachment-access.yaml --strict
```

## Out of scope

- **File tree integration and any multi-root work.** Documented above as a
  separate feature; not required by the priority ordering.
- **Editing an attachment in place.** Preview only.
- **Backend changes.** `stored_path` already ships.

## Constraints

`base` is brand-neutral: no brand names in code, copy, or tests. Changes to
shared upstream files (`src/sdk/index.ts`, the kanban plugin modules) are
additive only.
