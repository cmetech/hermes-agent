import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { LogView } from '@/components/ui/log-view'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { getApiRequestProfile, getWorkflowEvidence } from '@/hermes'
import { useI18n } from '@/i18n'
import type { WorkflowEvidenceKind, WorkflowRunSnapshot } from '@/types/hermes'

import { isWorkflowTypedArtifact, TypedArtifactView } from './typed-artifact-view'

interface RunInspectorProps {
  actionsDisabled?: boolean
  events?: Array<Record<string, unknown>>
  onAction?: (action: string, body?: Record<string, unknown>) => void
  run: WorkflowRunSnapshot
}

type InspectorTab = 'artifacts' | 'attempts' | 'logs' | 'outputs' | 'overview' | 'recovery' | 'timeline'

const MUTATING_ACTIONS = new Set([
  'abandon',
  'archive',
  'approve',
  'cancel',
  'provide-input',
  'reconcile',
  'reject',
  'resume',
  'retry',
  'restore'
])

function asDisplay(value: unknown, fallback: string): string {
  if (value === null || value === undefined || value === '') {
    return fallback
  }

  return Array.isArray(value) ? value.join(', ') || fallback : String(value)
}

function EvidenceItems({
  emptyLabel,
  items,
  logs
}: {
  emptyLabel: string
  items: Array<Record<string, unknown>>
  logs?: boolean
}) {
  if (items.length === 0) {
    return <p className="text-sm text-(--ui-text-tertiary)">{emptyLabel}</p>
  }

  return (
    <div className="space-y-2" role="list">
      {items.map((item, index) => {
        const content = logs && typeof item.text === 'string' ? item.text : JSON.stringify(item, null, 2)

        return (
          <LogView className="max-h-72" key={`${String(item.sequence ?? item.attempt_id ?? index)}`} role="listitem">
            {content}
          </LogView>
        )
      })}
    </div>
  )
}

export function RunInspector({ actionsDisabled = false, events = [], onAction, run }: RunInspectorProps) {
  const { locale, t } = useI18n()
  const copy = t.operations
  const profile = getApiRequestProfile() ?? 'default'
  const [tab, setTab] = useState<InspectorTab>('overview')
  const [inputValue, setInputValue] = useState('')
  const [reconciliationOutcome, setReconciliationOutcome] = useState('')
  const evidenceKind = tab === 'overview' || tab === 'timeline' ? null : (tab satisfies WorkflowEvidenceKind)

  const evidence = useQuery({
    enabled: evidenceKind !== null,
    queryFn: () => getWorkflowEvidence(run.run_id, evidenceKind!),
    queryKey: ['workflow-evidence', profile, run.run_id, evidenceKind],
    staleTime: 5_000
  })

  const evidenceItems = evidence.data?.items ?? []
  const typedArtifacts = tab === 'artifacts' ? evidenceItems.filter(isWorkflowTypedArtifact) : []

  const tabs: Array<{ id: InspectorTab; label: string }> = [
    { id: 'overview', label: copy.overview },
    { id: 'timeline', label: copy.timeline },
    { id: 'attempts', label: copy.attempts },
    { id: 'logs', label: copy.logs },
    { id: 'outputs', label: copy.outputs },
    { id: 'artifacts', label: copy.artifacts },
    { id: 'recovery', label: copy.recovery }
  ]

  const currentNode = run.current_nodes?.[0]
  const provenance = run.provenance
  const coordinator = run.coordinator

  const scheduledAt =
    run.presentation_state === 'scheduled_wait' && typeof run.schedule_at === 'string' ? run.schedule_at : null

  const scheduled = scheduledAt !== null

  return (
    <aside aria-label={`${run.workflow} run inspector`} className="min-w-0 py-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-medium">{run.workflow}</h2>
        <span className="font-mono text-xs text-(--ui-text-tertiary)">{run.run_id}</span>
      </div>

      <Tabs onValueChange={value => setTab(value as InspectorTab)} value={tab}>
        <TabsList aria-label={`${run.workflow} inspector`} className="mt-3 max-w-full justify-start overflow-x-auto">
          {tabs.map(item => (
            <TabsTrigger key={item.id} value={item.id}>
              {item.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <section aria-live="polite" className="mt-3" role="tabpanel">
        {tab === 'overview' ? (
          <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-4 gap-y-1 text-sm">
            <dt>{copy.status}</dt>
            <dd>{scheduled ? copy.workflowScheduled : run.status}</dd>
            {scheduledAt ? (
              <>
                <dt>{copy.workflowScheduledAt}</dt>
                <dd className="grid gap-0.5">
                  <span>
                    {copy.workflowScheduledLocal}: <span>{new Date(scheduledAt).toLocaleString(locale)}</span>
                  </span>
                  <span className="font-mono text-xs">
                    {copy.workflowScheduledCanonical}: <span>{scheduledAt}</span>
                  </span>
                </dd>
              </>
            ) : null}
            <dt>{copy.health}</dt>
            <dd>{run.health}</dd>
            <dt>{copy.origin}</dt>
            <dd>{asDisplay(provenance?.source ?? run.trigger, copy.estimateUnavailable)}</dd>
            <dt>{copy.assurance}</dt>
            <dd>{asDisplay(provenance?.assurance, copy.estimateUnavailable)}</dd>
            <dt>{copy.currentNode}</dt>
            <dd>{asDisplay(currentNode, copy.estimateUnavailable)}</dd>
            <dt>{copy.previousNode}</dt>
            <dd>{asDisplay(run.previous_node, copy.estimateUnavailable)}</dd>
            <dt>{copy.lastProgress}</dt>
            <dd>{asDisplay(run.last_semantic_progress_at, copy.estimateUnavailable)}</dd>
            <dt>{copy.failureCause}</dt>
            <dd>{asDisplay(run.blocking_reason ?? run.last_error, copy.estimateUnavailable)}</dd>
            <dt>{copy.coordinator}</dt>
            <dd>
              {asDisplay(coordinator?.status, copy.estimateUnavailable)}
              {coordinator?.reason_code ? ` · ${coordinator.reason_code}` : ''}
            </dd>
            <dt>{copy.graphProgress}</dt>
            <dd>
              {run.progress.completed_nodes}/{run.progress.total_nodes}
            </dd>
            <dt>{copy.completionEstimate}</dt>
            <dd>{copy.estimateUnavailable}</dd>
            <dt>{copy.waitingFor}</dt>
            <dd>
              {run.pending_interaction
                ? asDisplay(run.pending_interaction.type, copy.estimateUnavailable)
                : copy.estimateUnavailable}
            </dd>
            <dt>{copy.retryAt}</dt>
            <dd>{asDisplay(run.next_retry_at, copy.estimateUnavailable)}</dd>
            <dt>{copy.definition}</dt>
            <dd className="break-all">{asDisplay(run.definition_digest, copy.estimateUnavailable)}</dd>
          </dl>
        ) : tab === 'timeline' ? (
          <EvidenceItems emptyLabel={copy.noEvidence} items={events} />
        ) : tab === 'artifacts' && typedArtifacts.length > 0 ? (
          <TypedArtifactView artifacts={typedArtifacts} runId={run.run_id} />
        ) : (
          <EvidenceItems emptyLabel={copy.noEvidence} items={evidenceItems} logs={tab === 'logs'} />
        )}
      </section>

      {onAction && (
        <div aria-label={copy.nextActions} className="mt-4 flex flex-wrap items-end gap-2">
          {run.next_actions
            .filter(action => MUTATING_ACTIONS.has(action))
            .map(action => {
              if (action === 'provide-input') {
                return (
                  <div className="flex min-w-60 flex-1 items-end gap-2" key={action}>
                    <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs text-(--ui-text-secondary)">
                      {copy.inputValue}
                      <Input onChange={event => setInputValue(event.target.value)} size="sm" value={inputValue} />
                    </label>
                    <Button
                      disabled={actionsDisabled || inputValue.length === 0}
                      onClick={() => onAction(action, { value: inputValue })}
                      size="sm"
                      variant="secondary"
                    >
                      {copy.provideInput}
                    </Button>
                  </div>
                )
              }

              if (action === 'reconcile') {
                return (
                  <div className="flex min-w-72 flex-1 items-end gap-2" key={action}>
                    <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs text-(--ui-text-secondary)">
                      {copy.reconciliationOutcome}
                      <Select onValueChange={setReconciliationOutcome} value={reconciliationOutcome}>
                        <SelectTrigger size="sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="confirmed-succeeded">{copy.confirmedSucceeded}</SelectItem>
                          <SelectItem value="confirmed-failed">{copy.confirmedFailed}</SelectItem>
                          <SelectItem value="safe-to-retry">{copy.safeToRetry}</SelectItem>
                        </SelectContent>
                      </Select>
                    </label>
                    <Button
                      disabled={actionsDisabled || reconciliationOutcome.length === 0}
                      onClick={() => onAction(action, { outcome: reconciliationOutcome })}
                      size="sm"
                      variant="secondary"
                    >
                      {copy.reconcile}
                    </Button>
                  </div>
                )
              }

              return (
                <Button
                  disabled={actionsDisabled}
                  key={action}
                  onClick={() => onAction(action)}
                  size="sm"
                  variant={action === 'cancel' || action === 'abandon' ? 'destructive' : 'secondary'}
                >
                  {
                    copy[
                      action as 'approve' | 'reject' | 'cancel' | 'resume' | 'retry' | 'abandon' | 'archive' | 'restore'
                    ]
                  }
                </Button>
              )
            })}
        </div>
      )}
    </aside>
  )
}
