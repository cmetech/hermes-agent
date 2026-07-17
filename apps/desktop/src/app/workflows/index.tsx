import { useStore } from '@nanostores/react'
import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'

import { ActivityBoard } from '@/components/activity-board/activity-board'
import { PageLoader } from '@/components/page-loader'
import { getWorkflowRun, listWorkflowAttention, listWorkflowRuns } from '@/hermes'
import { useI18n } from '@/i18n'

import { PAGE_INSET_X } from '../layout-constants'

import { workflowBoardModel } from './adapter'
import { AttentionInbox } from './attention-inbox'
import { RunInspector } from './run-inspector'
import { $workflowSelectedRunId, selectWorkflowRun } from './store'

export function WorkflowsView() {
  const { t } = useI18n()
  const selectedRunId = useStore($workflowSelectedRunId)
  const runs = useQuery({ queryFn: () => listWorkflowRuns(), queryKey: ['workflow-runs'], refetchInterval: 20_000 })

  const attention = useQuery({
    queryFn: listWorkflowAttention,
    queryKey: ['workflow-attention'],
    refetchInterval: 20_000
  })

  const selected = useQuery({
    enabled: Boolean(selectedRunId),
    queryFn: () => getWorkflowRun(selectedRunId!),
    queryKey: ['workflow-run', selectedRunId]
  })

  const model = useMemo(
    () => workflowBoardModel(runs.data?.runs ?? [], { scopeLabel: t.operations.workflows, stale: runs.isError }),
    [runs.data?.runs, runs.isError, t.operations.workflows]
  )

  if (runs.isLoading) {return <PageLoader />}

  if (runs.isError && !runs.data) {
    return <p className={PAGE_INSET_X}>{t.operations.workflowUnavailable}</p>
  }

  return (
    <main className={`min-w-0 overflow-x-hidden py-6 ${PAGE_INSET_X}`}>
      <h1 className="mb-4 text-lg font-medium">{t.operations.workflows}</h1>
      <AttentionInbox items={attention.data?.items ?? []} />
      <ActivityBoard model={model} onLoadMore={() => void 0} onOpenCard={card => selectWorkflowRun(card.id)} />
      {selected.data && <RunInspector run={selected.data} />}
    </main>
  )
}
