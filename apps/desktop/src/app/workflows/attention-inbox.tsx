import { useI18n } from '@/i18n'
import type { WorkflowAttentionItem, WorkflowPublicAction } from '@/types/hermes'

import { isLoopSignalConfirmation } from './run-inspector'

interface AttentionInboxProps {
  items: WorkflowAttentionItem[]
  onOpenRun: (runId: string) => void
}

function ageParts(updatedAt: string): { count: number; unit: 'day' | 'hour' | 'minute' } {
  const elapsedMinutes = Math.max(0, Math.floor((Date.now() - Date.parse(updatedAt)) / 60_000))

  if (elapsedMinutes >= 24 * 60) {
    return { count: Math.floor(elapsedMinutes / (24 * 60)), unit: 'day' }
  }

  if (elapsedMinutes >= 60) {
    return { count: Math.floor(elapsedMinutes / 60), unit: 'hour' }
  }

  return { count: elapsedMinutes, unit: 'minute' }
}

export function AttentionInbox({ items, onOpenRun }: AttentionInboxProps) {
  const { t } = useI18n()

  if (items.length === 0) {
    return null
  }

  return (
    <section aria-label={t.operations.workflowAttention} className="py-3">
      <h2 className="text-sm font-medium">{t.operations.needsAttention}</h2>
      <ul className="mt-2 grid min-w-0 gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {items.map(item => {
          const age = ageParts(item.updated_at)
          const action = item.next_actions.find(candidate => !['events', 'status'].includes(candidate))
          const signalConfirmation = isLoopSignalConfirmation(item.interaction)

          const actionLabels: Partial<Record<WorkflowPublicAction, string>> = {
            abandon: t.operations.abandon,
            approve: signalConfirmation ? t.operations.acceptResult : t.operations.approve,
            cancel: t.operations.cancel,
            reconcile: t.operations.reconcile,
            reject: t.operations.reject,
            resume: t.operations.resume,
            retry: t.operations.retry,
            'provide-input': signalConfirmation ? t.operations.continueWithFeedback : t.operations.provideInput
          }

          const actionLabel = action ? (actionLabels[action] ?? action.replaceAll('-', ' ')) : null

          return (
            <li className="min-w-0" key={`${item.run_id}:${item.node_id ?? item.kind}`}>
              <button
                aria-label={t.operations.openWorkflowRun(item.workflow)}
                className="w-full min-w-0 rounded-md border border-(--ui-border) p-3 text-left hover:bg-(--ui-bg-hover)"
                onClick={() => onOpenRun(item.run_id)}
                type="button"
              >
                <span className="block truncate text-sm font-medium">{item.workflow}</span>
                <span className="mt-1 block text-xs text-(--ui-text-secondary)">
                  {item.origin} · {t.operations.attentionAge(age.count, age.unit)}
                </span>
                <span className="mt-2 block break-words text-sm">{item.cause}</span>
                <span className="mt-2 block text-xs text-(--ui-text-secondary)">
                  {item.kind.replaceAll('_', ' ')}
                  {actionLabel ? ` · ${actionLabel}` : ''}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
