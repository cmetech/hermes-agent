import { useStore } from '@nanostores/react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'

import { ActivityBoard } from '@/components/activity-board/activity-board'
import { PageLoader } from '@/components/page-loader'
import { getApiRequestProfile, getKanbanBoardSummary, listKanbanTasks } from '@/hermes'
import { useI18n } from '@/i18n'
import type { KanbanTaskPage } from '@/types/hermes'

import { PAGE_INSET_X } from '../layout-constants'

import { kanbanBoardModel } from './adapter'
import { $kanbanBoard, $kanbanSelectedTaskId, selectKanbanTask } from './store'
import { TaskInspector } from './task-inspector'

export function KanbanView() {
  const { t } = useI18n()
  const profile = getApiRequestProfile() ?? 'default'
  const board = useStore($kanbanBoard)
  const selectedTaskId = useStore($kanbanSelectedTaskId)

  const summary = useQuery({
    queryFn: () => getKanbanBoardSummary(board),
    queryKey: ['kanban-summary', profile, board],
    refetchInterval: () => document.visibilityState === 'visible' ? 20_000 : false
  })

  const tasks = useInfiniteQuery({
    getNextPageParam: (page: KanbanTaskPage) => page.next_cursor ?? undefined,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => listKanbanTasks(board, undefined, pageParam),
    queryKey: ['kanban-tasks', profile, board],
    refetchInterval: () => document.visibilityState === 'visible' ? 20_000 : false
  })

  const pages = (tasks.data?.pages ?? []) as KanbanTaskPage[]
  const taskItems = pages.flatMap(page => page.tasks)
  const nextCursor = pages.at(-1)?.next_cursor ?? null

  const model = useMemo(
    () =>
      summary.data ? kanbanBoardModel(summary.data, taskItems, summary.isError || tasks.isError, nextCursor) : null,
    [nextCursor, summary.data, summary.isError, taskItems, tasks.isError]
  )

  const selected = taskItems.find(task => task.id === selectedTaskId)

  if (summary.isLoading || tasks.isLoading) {return <PageLoader />}

  if (!model) {return <p className={PAGE_INSET_X}>{t.operations.kanbanUnavailable}</p>}

  return (
    <main className={`min-w-0 overflow-x-hidden py-6 ${PAGE_INSET_X}`}>
      <h1 className="mb-1 text-lg font-medium">{t.operations.kanban}</h1>
      <p className="mb-4 text-sm text-(--ui-text-secondary)">{t.operations.physicalBoard(board)}</p>
      <ActivityBoard model={model} onLoadMore={() => void tasks.fetchNextPage()} onOpenCard={card => selectKanbanTask(card.id)} />
      {selected && <TaskInspector task={selected} />}
    </main>
  )
}
