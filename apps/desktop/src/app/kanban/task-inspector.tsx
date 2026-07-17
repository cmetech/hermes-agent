import type { KanbanTaskSnapshot } from '@/types/hermes'

export function TaskInspector({ task }: { task: KanbanTaskSnapshot }) {
  return (
    <aside aria-label={`${task.title} task inspector`} className="py-4">
      <h2 className="text-base font-medium">{task.title}</h2>
      <p className="text-sm text-(--ui-text-secondary)">{task.status}</p>
    </aside>
  )
}
