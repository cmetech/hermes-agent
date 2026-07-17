import { Codecs, persistentAtom } from '@/lib/persisted'

export const $kanbanBoard = persistentAtom(
  'hermes.desktop.kanban.board', 'default', Codecs.text
)
export const $kanbanSelectedTaskId = persistentAtom<null | string>(
  'hermes.desktop.kanban.selectedTask', null, Codecs.nullableText
)

export function selectKanbanTask(taskId: null | string): void {
  $kanbanSelectedTaskId.set(taskId)
}
