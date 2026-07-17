import { atom } from 'nanostores'

export const $kanbanBoard = atom('default')
export const $kanbanSelectedTaskId = atom<null | string>(null)

export function selectKanbanTask(taskId: null | string): void {
  $kanbanSelectedTaskId.set(taskId)
}
