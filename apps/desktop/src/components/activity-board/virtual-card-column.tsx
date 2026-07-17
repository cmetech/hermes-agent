import { useVirtualizer } from '@tanstack/react-virtual'
import { useRef } from 'react'

import { Button } from '@/components/ui/button'

import type { ActivityBoardCard, ActivityBoardColumn } from './types'

interface VirtualCardColumnProps {
  column: ActivityBoardColumn
  loadMoreLabel: string
  onLoadMore: (columnId: string, cursor: string) => void
  onOpenCard: (card: ActivityBoardCard) => void
}

export function VirtualCardColumn({ column, loadMoreLabel, onLoadMore, onOpenCard }: VirtualCardColumnProps) {
  const parent = useRef<HTMLDivElement>(null)

  const virtual = useVirtualizer({
    count: column.cards.length,
    estimateSize: () => 82,
    getScrollElement: () => parent.current,
    initialRect: { height: 600, width: 320 },
    overscan: 8
  })

  const virtualRows = virtual.getVirtualItems()

  const rows = column.cards.length > 50
    ? virtualRows.length > 0
      ? virtualRows
      : column.cards.slice(0, 16).map((_, index) => ({ index, start: index * 82 }))
    : column.cards.map((_, index) => ({ index }))

  return (
    <section aria-label={`${column.label}, ${column.count}`} className="min-w-0" data-column={column.id}>
      <h2 className="mb-2 text-sm font-medium text-(--ui-text-secondary)">
        {column.label} <span className="text-(--ui-text-tertiary)">{column.count}</span>
      </h2>
      <div className="max-h-[65dvh] overflow-y-auto overscroll-contain" ref={parent}>
        <div
          className="relative space-y-2"
          style={column.cards.length > 50 ? { height: `${virtual.getTotalSize()}px` } : undefined}
        >
          {rows.map(row => {
            const card = column.cards[row.index]

            if (!card) {return null}
            const virtualRow = 'start' in row ? row : null

            return (
              <button
                aria-label={card.ariaDescription}
                className="block w-full rounded-sm bg-(--ui-bg-quaternary) p-3 text-left focus-visible:outline focus-visible:outline-(--ui-accent)"
                key={card.id}
                onClick={() => onOpenCard(card)}
                style={virtualRow ? { position: 'absolute', transform: `translateY(${virtualRow.start}px)`, width: '100%' } : undefined}
                type="button"
              >
                <span className="block truncate text-sm font-medium">{card.title}</span>
                <span className="mt-1 block text-xs text-(--ui-text-tertiary)">{card.exactState}</span>
                {card.badges.length > 0 && (
                  <span className="mt-2 flex flex-wrap gap-1 text-xs text-(--ui-text-secondary)">
                    {card.badges.map(badge => <span key={`${badge.label}:${badge.tone}`}>{badge.label}</span>)}
                  </span>
                )}
              </button>
            )
          })}
        </div>
        {column.nextCursor && (
          <Button className="motion-reduce:transition-none" onClick={() => onLoadMore(column.id, column.nextCursor!)} size="xs" variant="text">
            {loadMoreLabel}
          </Button>
        )}
      </div>
    </section>
  )
}
