import { useEffect } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n'
import type { WorkflowRunSnapshot, WorkflowTimelineEvent } from '@/types/hermes'

import { RunInspector } from './run-inspector'

interface WorkflowRunDrawerProps {
  actionsDisabled: boolean
  error: null | unknown
  events?: WorkflowTimelineEvent[]
  loading: boolean
  onAction: (action: string, body?: Record<string, unknown>) => void
  onClose: () => void
  run: null | WorkflowRunSnapshot
  selectedRunId: string
}

export function WorkflowRunDrawer({
  actionsDisabled,
  error,
  events = [],
  loading,
  onAction,
  onClose,
  run,
  selectedRunId
}: WorkflowRunDrawerProps) {
  const { t } = useI18n()
  const copy = t.operations

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) {
        return
      }

      event.preventDefault()
      event.stopPropagation()
      onClose()
    }

    window.addEventListener('keydown', onKeyDown)

    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const label = copy.workflowRunDrawerLabel(run?.workflow ?? selectedRunId)

  return (
    <aside
      aria-busy={loading && !run}
      aria-label={label}
      className="absolute inset-y-0 right-0 z-20 flex w-full flex-col border-l border-(--ui-stroke-tertiary) bg-(--ui-bg-elevated) sm:w-[min(32rem,calc(100%-2rem))]"
      id="workflow-run-drawer"
    >
      <header className="flex shrink-0 justify-end px-3 pt-3">
        <Button aria-label={t.common.close} onClick={onClose} size="icon-xs" variant="ghost">
          <Codicon name="close" size="0.9rem" />
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4" data-selectable-text="true">
        {run ? (
          <RunInspector
            actionsDisabled={actionsDisabled}
            events={events}
            key={run.run_id}
            onAction={onAction}
            run={run}
          />
        ) : error ? (
          <ErrorState className="mt-12" title={copy.workflowRunDetailError} />
        ) : (
          <div className="grid h-40 place-items-center">
            <Loader label={copy.workflowRunDetailLoading} type="lemniscate-bloom" />
          </div>
        )}
      </div>
    </aside>
  )
}
