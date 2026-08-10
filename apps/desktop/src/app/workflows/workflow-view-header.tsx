import type { RefObject } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { SearchField } from '@/components/ui/search-field'
import { useI18n } from '@/i18n'
import type { WorkflowRunView } from '@/types/hermes'

interface WorkflowViewHeaderProps {
  headingRef: RefObject<HTMLHeadingElement | null>
  loadedRunCount: number
  onViewChange: (view: WorkflowRunView) => void
  view: WorkflowRunView
}

const VIEWS = ['workflows', 'board', 'history', 'archive'] as const

export function WorkflowViewHeader({ headingRef, loadedRunCount, onViewChange, view }: WorkflowViewHeaderProps) {
  const { t } = useI18n()
  const copy = t.operations

  const label = (candidate: WorkflowRunView) =>
    candidate === 'workflows'
      ? copy.workflows
      : candidate === 'board'
        ? copy.activeBoard
        : candidate === 'history'
          ? copy.history
          : copy.archive

  return (
    <header className="flex shrink-0 flex-wrap items-center gap-2">
      <h1 className="me-2 text-lg font-medium" ref={headingRef} tabIndex={-1}>
        {copy.workflows}
      </h1>
      <div aria-label={copy.workflowViews} className="flex flex-wrap gap-2" role="tablist">
        {VIEWS.map(candidate => (
          <Button
            aria-selected={view === candidate}
            key={candidate}
            onClick={() => onViewChange(candidate)}
            role="tab"
            size="sm"
            variant={view === candidate ? 'default' : 'secondary'}
          >
            {label(candidate)}
          </Button>
        ))}
      </div>
      {view !== 'workflows' && (
        <div className="ms-auto flex min-w-0 items-center gap-1.5" data-workflow-run-toolbar>
          <span
            aria-label={copy.workflowLoadedRunCount(loadedRunCount)}
            className="rounded-full bg-(--ui-bg-quaternary) px-1.5 py-px text-[0.625rem] tabular-nums text-(--ui-text-tertiary)"
          >
            {loadedRunCount}
          </span>
          <Button aria-label={copy.workflowRunFiltersComingSoon} disabled size="icon-xs" variant="ghost">
            <Codicon name="filter" size="0.85rem" />
          </Button>
          <SearchField
            aria-label={copy.workflowRunSearchComingSoon}
            disabled
            onChange={() => undefined}
            placeholder={copy.workflowRunSearchComingSoon}
            value=""
          />
        </div>
      )}
    </header>
  )
}
