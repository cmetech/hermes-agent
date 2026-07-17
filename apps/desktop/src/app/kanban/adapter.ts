import type { ActivityBoardColumn, ActivityBoardModel } from '@/components/activity-board/types'
import type { KanbanBoardSummary, KanbanTaskSnapshot } from '@/types/hermes'

const COLUMN_LABELS: Record<string, string> = {
  blocked: 'Blocked',
  done: 'Done',
  ready: 'Ready',
  review: 'Review',
  running: 'Running',
  scheduled: 'Scheduled',
  todo: 'Todo',
  triage: 'Triage'
}

export function kanbanBoardModel(
  summary: KanbanBoardSummary,
  tasks: readonly KanbanTaskSnapshot[],
  stale = false
): ActivityBoardModel {
  const columns: ActivityBoardColumn[] = Object.entries(summary.column_counts).map(([id, count]) => ({
    cards: tasks
      .filter(task => task.status === id)
      .map(task => ({
        ariaDescription: `${task.title}, ${task.status}`,
        badges: task.assignee ? [{ label: task.assignee }] : [],
        exactState: task.status,
        health: task.status === 'blocked' ? 'attention' : task.status === 'done' ? 'terminal' : 'healthy',
        id: task.id,
        title: task.title,
        updatedAt: 0
      })),
    count,
    id,
    label: COLUMN_LABELS[id] ?? id,
    nextCursor: null
  }))
  return {
    columns,
    revision: `${summary.latest_event_id}`,
    scopeLabel: `Kanban: ${summary.board}`,
    source: 'kanban',
    stale
  }
}
