import type { ActivityBoardCard, ActivityBoardModel } from './types'
import { VirtualCardColumn } from './virtual-card-column'

interface ActivityBoardProps {
  model: ActivityBoardModel
  onLoadMore: (columnId: string, cursor: string) => void
  onOpenCard: (card: ActivityBoardCard) => void
}

export function ActivityBoard({ model, onLoadMore, onOpenCard }: ActivityBoardProps) {
  return (
    <div aria-label={`${model.scopeLabel} activity board`} className="min-w-0" data-source={model.source}>
      {model.stale && <p role="status">Data is stale. Reconnecting…</p>}
      <div className="grid min-w-0 grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
        {model.columns.map(column => (
          <VirtualCardColumn column={column} key={column.id} onLoadMore={onLoadMore} onOpenCard={onOpenCard} />
        ))}
      </div>
    </div>
  )
}
