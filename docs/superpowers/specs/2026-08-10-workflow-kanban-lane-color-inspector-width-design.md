# Workflow Kanban Lane Color and Inspector Width Design

## Goal

Polish the Desktop Workflows board so its lane markers use the same stable,
status-oriented color treatment as the Kanban board and its run inspector is
wide enough to keep the seven inspector tabs comfortably horizontal on typical
desktop monitors.

This is a presentation-only follow-up to the workflow Kanban view alignment.
It does not change workflow lifecycle authority, grouping, filtering, card
selection, inspector data, actions, or responsive behavior on narrow screens.

## Lane color contract

Each workflow board lane supplies an explicit presentation tone through the
shared `ActivityBoardColumn` model:

| Workflow lane | Tone |
| --- | --- |
| Queued | `var(--ui-blue)` |
| Active | `var(--ui-green)` |
| Needs attention | `var(--ui-yellow)` |
| Completed | `var(--ui-text-tertiary)` |
| Failed / stopped | `var(--ui-red)` |

The workflow adapter owns this mapping because it owns workflow grouping
semantics. The generic activity board must not infer workflow meaning from lane
IDs.

The shared activity-board column model gains an optional lane tone. In the
collapsible-lane layout, both expanded and collapsed lane header dots render
that explicit tone. The tone remains visible for empty lanes. Consumers that do
not supply a lane tone retain the existing first-card-health fallback, followed
by the existing muted empty-lane fallback.

Card left borders remain health-colored. This preserves the distinction between
the lane's lifecycle category and an individual card's health.

## Inspector width contract

The workflow run drawer remains a nonmodal, right-anchored complementary panel.
Its responsive width changes from a `32rem` desktop cap to:

```text
w-full sm:w-[min(40rem,calc(100%-2rem))]
```

This keeps the existing full-width narrow-screen behavior and the two-rem
viewport margin on wider screens. The inspector tab list keeps horizontal
overflow as a defensive fallback for unusually long translations or constrained
windows; widening the panel must not introduce tab wrapping or vertical tab
stacking.

No focus, Escape-layer, close-button, landmark, loading, error, selection, or
scroll-ownership behavior changes.

## Implementation boundaries

Expected production changes are limited to:

- the shared activity-board column type;
- collapsible-lane dot rendering;
- the workflow board adapter's lane metadata; and
- the workflow run drawer's responsive width class.

No new state, persistence, API calls, localization strings, raw color literals,
or workflow mutations are required.

## Verification

Focused tests will prove these invariants before implementation:

1. The workflow adapter emits the complete lane-to-tone mapping.
2. Expanded and collapsed lane dots use a source-supplied tone, including when
   a lane is empty.
3. Per-card border color remains derived from card health rather than lane tone.
4. The workflow drawer uses the `40rem` desktop cap while retaining `w-full` and
   the existing viewport-margin constraint.
5. Existing workflow drawer interaction, activity-board accessibility,
   virtualization, typecheck, and lint checks remain green.

## Acceptance criteria

- The five workflow lane dots are visibly differentiated with the approved
  blue, green, yellow, muted, and red token palette.
- Empty and collapsed lanes retain their assigned lane color.
- Workflow card borders continue to communicate individual run health.
- The run inspector is capped at `40rem` on desktop and remains full width on
  narrow screens.
- Inspector tabs remain a horizontal row under ordinary desktop widths, with
  horizontal overflow available only as a fallback.
- No workflow behavior or authority boundary changes.
