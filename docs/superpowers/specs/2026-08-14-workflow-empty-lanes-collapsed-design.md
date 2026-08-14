# Workflow Empty Lanes Collapsed by Default

## Context

The Desktop workflow Active Board, History, and Archive views use the shared
`ActivityBoard` collapsible-lane primitive. Empty lanes currently collapse
automatically only when another lane contains a run. When the entire workflow
view is empty, every lane instead opens at full width and renders its empty
message.

The requested behavior is to retain the individual lane rails while making all
empty lanes collapsed by default, including when the whole view has no runs.

## Goals

- Default every empty workflow lane to collapsed in Active Board, History, and
  Archive.
- Keep occupied lanes expanded by default.
- Preserve the existing accessible expand/collapse controls.
- Preserve manual overrides until the lane's empty/occupied phase or the view
  scope changes.

## Non-goals

- Do not replace the workflow board with a single empty-state panel.
- Do not change workflow data loading, filtering, pagination, or run status
  mapping.
- Do not change the standalone Kanban plugin.
- Do not persist lane presentation state across application restarts.

## Design

The shared lane-collapse policy will define the automatic state from the lane
itself:

- empty lane: collapsed;
- occupied lane: expanded.

The policy will no longer depend on whether the board contains any cards.
`ActivityBoard` will continue to reconcile presentation state by
`collapseScope`, so Active Board, History, and Archive retain independent
ephemeral overrides. A user's manual expansion of an empty lane remains active
while that lane stays empty. When its occupancy changes, reconciliation removes
the stale override and applies the automatic state for the new phase.

This belongs in the shared pure lane-collapse helper rather than in the three
workflow tabs. That keeps one authority for lane presentation and avoids
tab-specific state or a new opt-in component prop.

## Accessibility and interaction

Collapsed lanes remain named regions with an Expand button and
`aria-expanded="false"`. Expanding a lane exposes the existing empty message;
collapsing it restores focus to the corresponding Expand button. No keyboard,
focus, drag, or card interaction changes are introduced.

## Testing

Implementation will follow RED → GREEN → refactor:

1. Change the pure-policy test to require every empty lane to auto-collapse
   when the complete board is empty.
2. Change the `ActivityBoard` component test to require collapsed accessible
   rails for an entirely empty model and prove manual expansion still works.
3. Add a workflow integration regression covering Active Board, History, and
   Archive with an empty run response.
4. Run the focused workflow UI suite, Desktop typecheck, and Desktop lint.

## Acceptance criteria

- Empty lanes initially render as collapsed rails in all three workflow run
  views, even when zero runs exist anywhere in the view.
- Occupied lanes remain initially expanded.
- Users can expand and collapse empty lanes normally.
- Scope and occupancy reconciliation continue to discard stale overrides.
- Focused workflow UI tests, typecheck, and lint pass.
