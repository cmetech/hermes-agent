import { useStore } from '@nanostores/react'
import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'

import { ActivityBoard } from '@/components/activity-board/activity-board'
import { PageLoader } from '@/components/page-loader'
import { getKanbanBoardSummary, listKanbanTasks } from '@/hermes'

import { PAGE_INSET_X } from '../layout-constants'
import { kanbanBoardModel } from './adapter'
import { $kanbanBoard, $kanbanSelectedTaskId, selectKanbanTask } from './store'
import { TaskInspector } from './task-inspector'

export function KanbanView() {
  const board = useStore($kanbanBoard)
  const selectedTaskId = useStore($kanbanSelectedTaskId)
  const summary = useQuery({
    queryFn: () => getKanbanBoardSummary(board),
    queryKey: ['kanban-summary', board],
    refetchInterval: 20_000
  })
  const tasks = useQuery({
    queryFn: () => listKanbanTasks(board),
    queryKey: ['kanban-tasks', board],
    refetchInterval: 20_000
  })
  const model = useMemo(
    () =>
      summary.data ? kanbanBoardModel(summary.data, tasks.data?.tasks ?? [], summary.isError || tasks.isError) : null,
    [summary.data, summary.isError, tasks.data?.tasks, tasks.isError]
  )
  const selected = tasks.data?.tasks.find(task => task.id === selectedTaskId)

  if (summary.isLoading || tasks.isLoading) return <PageLoader />
  if (!model)
    return <p className={PAGE_INSET_X}>Kanban plugin unavailable. Enable it with hermes plugins enable kanban.</p>
  return (
    <main className={`min-w-0 overflow-x-hidden py-6 ${PAGE_INSET_X}`}>
      <h1 className="mb-1 text-lg font-medium">Kanban</h1>
      <p className="mb-4 text-sm text-(--ui-text-secondary)">Machine-shared physical board: {board}</p>
      <ActivityBoard model={model} onLoadMore={() => void 0} onOpenCard={card => selectKanbanTask(card.id)} />
      {selected && <TaskInspector task={selected} />}
    </main>
  )
}
