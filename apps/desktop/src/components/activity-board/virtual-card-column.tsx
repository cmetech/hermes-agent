import { useVirtualizer } from '@tanstack/react-virtual'
import type { MouseEvent } from 'react'
import { useRef } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'

import type { ActivityBadge, ActivityBoardCard, ActivityBoardColumn } from './types'

interface VirtualCardColumnProps {
  appearance?: 'grid' | 'lane'
  collapsed?: boolean
  collapseLabel?: string
  column: ActivityBoardColumn
  emptyLabel?: string
  expandLabel?: string
  loadMoreLabel: string
  onLoadMore: (columnId: string, cursor: string) => void
  onOpenCard: (card: ActivityBoardCard, origin?: HTMLButtonElement) => void
  onToggleCollapsed?: () => void
  selectedCardId?: null | string
}

const HEALTH_TONE: Record<ActivityBoardCard['health'], string> = {
  attention: 'var(--ui-yellow)',
  failed: 'var(--ui-red)',
  healthy: 'var(--ui-green)',
  idle: 'var(--ui-text-tertiary)',
  stale: 'var(--ui-orange)',
  terminal: 'var(--ui-text-quaternary)',
  waiting: 'var(--ui-purple)'
}

const BADGE_TONE_CLASS: Record<NonNullable<ActivityBadge['tone']>, string> = {
  danger: 'text-destructive',
  muted: 'text-(--ui-text-quaternary)',
  notice: 'text-(--ui-yellow)',
  success: 'text-(--ui-green)'
}

export function VirtualCardColumn({
  appearance = 'grid',
  collapsed = false,
  collapseLabel,
  column,
  emptyLabel,
  expandLabel,
  loadMoreLabel,
  onLoadMore,
  onOpenCard,
  onToggleCollapsed,
  selectedCardId
}: VirtualCardColumnProps) {
  const parent = useRef<HTMLDivElement>(null)
  const toggle = useRef<HTMLButtonElement>(null)
  const lane = appearance === 'lane'
  const laneTone = column.cards[0] ? HEALTH_TONE[column.cards[0].health] : 'var(--ui-text-quaternary)'

  const virtual = useVirtualizer({
    count: column.cards.length,
    estimateSize: () => 82,
    gap: 8,
    getItemKey: index => column.cards[index]?.id ?? index,
    getScrollElement: () => parent.current,
    initialRect: { height: 600, width: 320 },
    overscan: 8
  })

  const virtualRows = virtual.getVirtualItems()

  const rows =
    column.cards.length > 50
      ? virtualRows.length > 0
        ? virtualRows
        : column.cards.slice(0, 16).map((_, index) => ({ index, start: index * 82 }))
      : column.cards.map((_, index) => ({ index }))

  const toggleCollapsed = (event: MouseEvent<HTMLButtonElement>) => {
    const restoreFocus = globalThis.document.activeElement === event.currentTarget

    onToggleCollapsed?.()

    if (restoreFocus) {
      requestAnimationFrame(() => toggle.current?.focus())
    }
  }

  if (lane && collapsed) {
    return (
      <section aria-label={`${column.label}, ${column.count}`} className="h-full w-8 shrink-0" data-column={column.id}>
        <button
          aria-expanded={false}
          aria-label={expandLabel}
          className="flex h-full w-full flex-col items-center gap-1.5 rounded-lg p-2 transition-colors motion-reduce:transition-none hover:bg-(--ui-bg-quinary)"
          onClick={toggleCollapsed}
          ref={toggle}
          type="button"
        >
          <span className="grid h-5 shrink-0 place-items-center">
            <span className="size-1.5 rounded-full" style={{ backgroundColor: laneTone }} />
          </span>
          <span className="text-[0.6875rem] font-medium tracking-wide text-(--ui-text-tertiary) uppercase [writing-mode:vertical-rl]">
            {column.label}
          </span>
          {column.count > 0 && (
            <span className="text-[0.625rem] text-(--ui-text-quaternary) tabular-nums">{column.count}</span>
          )}
        </button>
      </section>
    )
  }

  return (
    <section
      aria-label={`${column.label}, ${column.count}`}
      className={
        lane
          ? 'group/col flex h-full w-64 shrink-0 flex-col rounded-lg bg-[color-mix(in_srgb,var(--ui-bg-quinary)_50%,transparent)] p-2'
          : 'min-w-0'
      }
      data-column={column.id}
    >
      {lane ? (
        <header className="mb-1.5 flex h-5 items-center gap-1.5 px-1">
          <span className="size-1.5 rounded-full" style={{ backgroundColor: laneTone }} />
          <h2 className="text-[0.6875rem] font-medium tracking-wide text-(--ui-text-tertiary) uppercase">
            {column.label}
          </h2>
          <span className="text-[0.625rem] text-(--ui-text-quaternary) tabular-nums">{column.count}</span>
          <button
            aria-expanded={true}
            aria-label={collapseLabel}
            className="ms-auto grid size-5 place-items-center rounded text-(--ui-text-tertiary) opacity-0 transition-opacity motion-reduce:transition-none hover:bg-(--chrome-action-hover) hover:text-foreground focus-visible:opacity-100 group-hover/col:opacity-100"
            onClick={toggleCollapsed}
            ref={toggle}
            type="button"
          >
            <Codicon name="chevron-left" size="0.75rem" />
          </button>
        </header>
      ) : (
        <h2 className="mb-2 text-sm font-medium text-(--ui-text-secondary)">
          {column.label} <span className="text-(--ui-text-tertiary)">{column.count}</span>
        </h2>
      )}
      <div
        className={cn('overflow-y-auto overscroll-contain', lane ? 'min-h-0 flex-1' : 'max-h-[65dvh]')}
        data-lane-scroll={lane || undefined}
        ref={parent}
      >
        {lane && column.cards.length === 0 && emptyLabel ? (
          <p className="px-1 py-2 text-[0.6875rem] text-(--ui-text-quaternary)">{emptyLabel}</p>
        ) : null}
        <div
          className={cn('relative', column.cards.length <= 50 && 'space-y-2')}
          style={column.cards.length > 50 ? { height: `${virtual.getTotalSize()}px` } : undefined}
        >
          {rows.map(row => {
            const card = column.cards[row.index]

            if (!card) {
              return null
            }

            const virtualRow = 'start' in row ? row : null
            const selected = selectedCardId === card.id

            const virtualStyle = virtualRow
              ? {
                  position: 'absolute' as const,
                  transform: `translateY(${virtualRow.start}px)`,
                  width: '100%'
                }
              : undefined

            return (
              <button
                aria-expanded={selectedCardId === undefined ? undefined : selected}
                aria-label={card.ariaDescription}
                className={cn(
                  lane
                    ? 'block w-full rounded-md border border-(--ui-stroke-tertiary) border-l-2 bg-(--ui-bg-elevated) p-2.5 text-left transition-colors motion-reduce:transition-none hover:bg-(--ui-row-hover-background) focus-visible:outline focus-visible:outline-(--ui-accent)'
                    : 'block w-full rounded-sm bg-(--ui-bg-quaternary) p-3 text-left focus-visible:outline focus-visible:outline-(--ui-accent)',
                  lane && selected && 'border-(--ui-accent) bg-(--ui-row-active-background)'
                )}
                data-activity-card-id={card.id}
                data-index={virtualRow?.index}
                key={card.id}
                onClick={event => onOpenCard(card, event.currentTarget)}
                ref={virtualRow ? virtual.measureElement : undefined}
                style={lane ? { borderLeftColor: HEALTH_TONE[card.health], ...virtualStyle } : virtualStyle}
                type="button"
              >
                <span className={cn('block truncate font-medium', lane ? 'text-[0.75rem]' : 'text-sm')}>
                  {card.title}
                </span>
                <span className={cn('mt-1 block text-(--ui-text-tertiary)', lane ? 'text-[0.6875rem]' : 'text-xs')}>
                  {card.exactState}
                </span>
                {card.badges.length > 0 && (
                  <span
                    className={cn(
                      'mt-2 flex flex-wrap gap-1 text-xs text-(--ui-text-secondary)',
                      lane && 'text-[0.6875rem]'
                    )}
                  >
                    {card.badges.map(badge => (
                      <span
                        className={cn(
                          'inline-flex items-center gap-1',
                          lane && badge.tone && BADGE_TONE_CLASS[badge.tone]
                        )}
                        key={`${badge.label}:${badge.tone}`}
                      >
                        {badge.icon && <Codicon name={badge.icon} />}
                        {badge.label}
                      </span>
                    ))}
                  </span>
                )}
              </button>
            )
          })}
        </div>
        {column.nextCursor && (
          <Button
            className="motion-reduce:transition-none"
            onClick={() => onLoadMore(column.id, column.nextCursor!)}
            size="xs"
            variant="text"
          >
            {loadMoreLabel}
          </Button>
        )}
      </div>
    </section>
  )
}
