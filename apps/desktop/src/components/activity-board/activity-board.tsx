import { useEffect, useRef, useState } from 'react'

import { useGrabScroll } from '@/hooks/use-grab-scroll'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { laneIsCollapsed, reconcileLaneCollapseState, toggleLaneCollapse } from './lane-collapse'
import type { ActivityBoardCard, ActivityBoardLaneCopy, ActivityBoardModel } from './types'
import { VirtualCardColumn } from './virtual-card-column'

interface ActivityBoardBaseProps {
  model: ActivityBoardModel
  onLoadMore: (columnId: string, cursor: string) => void
  onOpenCard: (card: ActivityBoardCard, origin?: HTMLButtonElement) => void
  selectedCardId?: null | string
}

interface GridActivityBoardProps extends ActivityBoardBaseProps {
  layout?: 'grid'
}

interface CollapsibleActivityBoardProps extends ActivityBoardBaseProps {
  collapseScope: string
  laneCopy: ActivityBoardLaneCopy
  layout: 'collapsible-lanes'
}

type ActivityBoardProps = CollapsibleActivityBoardProps | GridActivityBoardProps

interface CollapsibleLaneStripProps extends CollapsibleActivityBoardProps {
  loadMoreLabel: string
}

function CollapsibleLaneStrip({
  collapseScope,
  laneCopy,
  loadMoreLabel,
  model,
  onLoadMore,
  onOpenCard,
  selectedCardId
}: CollapsibleLaneStripProps) {
  const [laneState, setLaneState] = useState(() => reconcileLaneCollapseState(null, collapseScope, model.columns))
  const reconciled = reconcileLaneCollapseState(laneState, collapseScope, model.columns)
  const boardHasCards = model.columns.some(column => column.cards.length > 0)
  const stripRef = useRef<HTMLDivElement>(null)
  const { grabbing, onMouseDown } = useGrabScroll(stripRef)

  useEffect(() => {
    setLaneState(current => reconcileLaneCollapseState(current, collapseScope, model.columns))
  }, [collapseScope, model.columns])

  return (
    <div
      className={cn(
        'flex min-h-0 min-w-0 flex-1 gap-2 overflow-x-auto overscroll-contain',
        grabbing && 'cursor-grabbing'
      )}
      data-layout="collapsible-lanes"
      onMouseDown={onMouseDown}
      ref={stripRef}
    >
      {model.columns.map(column => {
        const collapsed = laneIsCollapsed(reconciled, column, boardHasCards)

        return (
          <VirtualCardColumn
            appearance="lane"
            collapsed={collapsed}
            collapseLabel={laneCopy.collapse(column.label)}
            column={column}
            emptyLabel={laneCopy.empty}
            expandLabel={laneCopy.expand(column.label)}
            key={column.id}
            loadMoreLabel={loadMoreLabel}
            onLoadMore={onLoadMore}
            onOpenCard={onOpenCard}
            onToggleCollapsed={() => setLaneState(toggleLaneCollapse(reconciled, column, boardHasCards))}
            selectedCardId={selectedCardId}
          />
        )
      })}
    </div>
  )
}

export function ActivityBoard(props: ActivityBoardProps) {
  const { t } = useI18n()
  const { model, onLoadMore, onOpenCard, selectedCardId } = props
  const collapsible = props.layout === 'collapsible-lanes'

  return (
    <div
      aria-label={`${model.scopeLabel} activity board`}
      className={cn('min-w-0', collapsible && 'flex h-full min-h-0 flex-col')}
      data-source={model.source}
    >
      {model.stale && <p role="status">{t.operations.dataStale}</p>}
      {collapsible ? (
        <CollapsibleLaneStrip {...props} loadMoreLabel={t.operations.loadMore} />
      ) : (
        <div className="grid min-w-0 grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
          {model.columns.map(column => (
            <VirtualCardColumn
              column={column}
              key={column.id}
              loadMoreLabel={t.operations.loadMore}
              onLoadMore={onLoadMore}
              onOpenCard={onOpenCard}
              selectedCardId={selectedCardId}
            />
          ))}
        </div>
      )}
    </div>
  )
}
