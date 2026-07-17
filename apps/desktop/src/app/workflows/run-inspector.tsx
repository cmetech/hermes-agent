import { useI18n } from '@/i18n'
import type { WorkflowRunSnapshot } from '@/types/hermes'

interface RunInspectorProps {
  actionsDisabled?: boolean
  events?: Array<Record<string, unknown>>
  onAction?: (action: string) => void
  run: WorkflowRunSnapshot
}

const MUTATING_ACTIONS = new Set(['approve', 'reject', 'cancel', 'resume', 'retry', 'abandon'])

export function RunInspector({ actionsDisabled = false, events = [], onAction, run }: RunInspectorProps) {
  const { t } = useI18n()
  const copy = t.operations

  return (
    <aside aria-label={`${run.workflow} run inspector`} className="min-w-0 py-4">
      <h2 className="text-base font-medium">{run.workflow}</h2>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt>{copy.status}</dt>
        <dd>{run.status}</dd>
        <dt>{copy.health}</dt>
        <dd>{run.health}</dd>
        <dt>{copy.graphProgress}</dt>
        <dd>
          {run.progress.completed_nodes}/{run.progress.total_nodes}
        </dd>
        <dt>{copy.completionEstimate}</dt>
        <dd>{copy.estimateUnavailable}</dd>
        <dt>{copy.nextActions}</dt>
        <dd>{run.next_actions.join(', ')}</dd>
        <dt>{copy.definition}</dt>
        <dd className="break-all">{String(run.definition_digest ?? copy.estimateUnavailable)}</dd>
        <dt>{copy.trigger}</dt>
        <dd>{String(run.trigger ?? copy.estimateUnavailable)}</dd>
        <dt>{copy.waitingFor}</dt>
        <dd>{run.pending_interaction ? String(run.pending_interaction.type ?? 'interaction') : copy.estimateUnavailable}</dd>
        <dt>{copy.retryAt}</dt>
        <dd>{String(run.next_retry_at ?? copy.estimateUnavailable)}</dd>
        <dt>{copy.artifacts}</dt>
        <dd>{Array.isArray(run.artifacts) ? run.artifacts.length : 0}</dd>
        <dt>{copy.timeline}</dt>
        <dd>{events.length}</dd>
      </dl>
      {onAction && (
        <div aria-label={copy.nextActions} className="mt-3 flex flex-wrap gap-2">
          {run.next_actions.filter(action => MUTATING_ACTIONS.has(action)).map(action => (
            <button
              className="border border-current/20 px-2 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-50"
              disabled={actionsDisabled}
              key={action}
              onClick={() => onAction(action)}
              type="button"
            >
              {copy[action as 'approve' | 'reject' | 'cancel' | 'resume' | 'retry' | 'abandon']}
            </button>
          ))}
        </div>
      )}
    </aside>
  )
}
