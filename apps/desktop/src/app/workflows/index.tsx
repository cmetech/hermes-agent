import { useStore } from '@nanostores/react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo } from 'react'

import { ActivityBoard } from '@/components/activity-board/activity-board'
import { PageLoader } from '@/components/page-loader'
import { getApiRequestProfile, getWorkflowRun, listWorkflowAttention, listWorkflowEvents, listWorkflowRuns, mutateWorkflowRun } from '@/hermes'
import { useI18n } from '@/i18n'
import type { WorkflowEventPage, WorkflowRunPage } from '@/types/hermes'

import { PAGE_INSET_X } from '../layout-constants'

import { workflowBoardModel } from './adapter'
import { AttentionInbox } from './attention-inbox'
import { RunInspector } from './run-inspector'
import { $workflowSelectedRunId, selectWorkflowRun } from './store'

export function WorkflowsView() {
  const { t } = useI18n()
  const profile = getApiRequestProfile() ?? 'default'
  const queryClient = useQueryClient()
  const selectedRunId = useStore($workflowSelectedRunId)

  const runs = useInfiniteQuery({
    getNextPageParam: (page: WorkflowRunPage) => page.next_cursor ?? undefined,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => listWorkflowRuns(pageParam as string | undefined),
    queryKey: ['workflow-runs', profile],
    refetchInterval: () => document.visibilityState === 'visible' ? 20_000 : false
  })

  const attention = useQuery({
    queryFn: listWorkflowAttention,
    queryKey: ['workflow-attention', profile],
    refetchInterval: () => document.visibilityState === 'visible' ? 20_000 : false
  })

  const selected = useQuery({
    enabled: Boolean(selectedRunId),
    queryFn: () => getWorkflowRun(selectedRunId!),
    queryKey: ['workflow-run', profile, selectedRunId]
  })

  const eventQueryKey = ['workflow-events', profile, selectedRunId] as const

  const events = useQuery({
    enabled: Boolean(selectedRunId),
    queryFn: async () => {
      const previous = queryClient.getQueryData<WorkflowEventPage>(eventQueryKey)
      const page = await listWorkflowEvents(selectedRunId!, previous?.next_cursor ?? 0)

      if (!previous || page.cursor_reset) {return page}

      return { ...page, events: [...previous.events, ...page.events].slice(-200) }
    },
    queryKey: eventQueryKey,
    refetchInterval: () => document.visibilityState === 'visible' ? 1_000 : false
  })

  const mutation = useMutation({
    mutationFn: (action: string) => mutateWorkflowRun(selectedRunId!, action, {
      expected_version: selected.data?.state_version ?? -1,
      interaction_id: selected.data?.pending_interaction?.interaction_id
    }),
    onSuccess: run => {
      queryClient.setQueryData(['workflow-run', profile, selectedRunId], run)
      void queryClient.invalidateQueries({ queryKey: ['workflow-runs', profile] })
      void queryClient.invalidateQueries({ queryKey: ['workflow-attention', profile] })
    }
  })

  const pages = (runs.data?.pages ?? []) as WorkflowRunPage[]
  const runItems = pages.flatMap(page => page.runs)
  const nextCursor = pages.at(-1)?.next_cursor ?? null

  const model = useMemo(
    () => workflowBoardModel(runItems, { nextCursor, scopeLabel: t.operations.workflows, stale: runs.isError }),
    [nextCursor, runItems, runs.isError, t.operations.workflows]
  )

  if (runs.isLoading) {return <PageLoader />}

  if (runs.isError && !runs.data) {
    return <p className={PAGE_INSET_X}>{t.operations.workflowUnavailable}</p>
  }

  return (
    <main className={`min-w-0 overflow-x-hidden py-6 ${PAGE_INSET_X}`}>
      <h1 className="mb-4 text-lg font-medium">{t.operations.workflows}</h1>
      <AttentionInbox items={attention.data?.items ?? []} />
      <ActivityBoard model={model} onLoadMore={() => void runs.fetchNextPage()} onOpenCard={card => selectWorkflowRun(card.id)} />
      {selected.data && <RunInspector events={events.data?.events} onAction={action => mutation.mutate(action)} run={selected.data} />}
    </main>
  )
}
