// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { ActivityBoard } from './activity-board'
import type { ActivityBoardModel } from './types'

const model: ActivityBoardModel = {
  columns: [
    {
      cards: [
        {
          ariaDescription: 'Run one, running',
          badges: [],
          exactState: 'running',
          health: 'healthy',
          id: 'one',
          title: 'Run one',
          updatedAt: 1
        }
      ],
      count: 1,
      id: 'active',
      label: 'Active',
      nextCursor: 'next'
    }
  ],
  revision: '1',
  scopeLabel: 'Workflow',
  source: 'workflow',
  stale: false
}

const laneCopy = {
  collapse: (lane: string) => `Collapse ${lane}`,
  empty: 'No runs',
  expand: (lane: string) => `Expand ${lane}`
}

const collapsibleModel: ActivityBoardModel = {
  ...model,
  columns: [{ cards: [], count: 0, id: 'queued', label: 'Queued', nextCursor: null }, model.columns[0]!]
}

afterEach(cleanup)

describe('ActivityBoard', () => {
  it('opens cards with keyboard-compatible native controls and loads bounded pages', () => {
    const open = vi.fn()
    const load = vi.fn()
    render(<ActivityBoard model={model} onLoadMore={load} onOpenCard={open} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run one, running' }))
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    expect(open).toHaveBeenCalledWith(model.columns[0]!.cards[0], expect.any(HTMLButtonElement))
    expect(load).toHaveBeenCalledWith('active', 'next')
    expect(screen.getByRole('region', { name: 'Active, 1' })).toBeTruthy()
  })

  it('renders empty lanes as accessible rails and toggles them without moving cards', () => {
    render(
      <ActivityBoard
        collapseScope="board"
        laneCopy={laneCopy}
        layout="collapsible-lanes"
        model={collapsibleModel}
        onLoadMore={vi.fn()}
        onOpenCard={vi.fn()}
      />
    )

    const queued = screen.getByRole('region', { name: 'Queued, 0' })
    expect(within(queued).getByRole('button', { name: 'Expand Queued' }).getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(within(queued).getByRole('button', { name: 'Expand Queued' }))
    expect(within(queued).getByText('No runs')).toBeTruthy()
    expect(within(queued).getByRole('button', { name: 'Collapse Queued' }).getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByRole('button', { name: 'Run one, running' })).toBeTruthy()
  })

  it('expands every lane when the entire board is empty', () => {
    const empty = {
      ...collapsibleModel,
      columns: collapsibleModel.columns.map(column => ({ ...column, cards: [], count: 0 }))
    }

    render(
      <ActivityBoard
        collapseScope="history"
        laneCopy={laneCopy}
        layout="collapsible-lanes"
        model={empty}
        onLoadMore={vi.fn()}
        onOpenCard={vi.fn()}
      />
    )

    expect(screen.queryByRole('button', { name: /^Expand / })).toBeNull()
    expect(screen.getAllByText('No runs')).toHaveLength(2)
  })

  it('resets lane overrides when the collapse scope changes', () => {
    const view = render(
      <ActivityBoard
        collapseScope="board"
        laneCopy={laneCopy}
        layout="collapsible-lanes"
        model={collapsibleModel}
        onLoadMore={vi.fn()}
        onOpenCard={vi.fn()}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Collapse Active' }))
    expect(screen.getByRole('button', { name: 'Expand Active' })).toBeTruthy()
    view.rerender(
      <ActivityBoard
        collapseScope="archive"
        laneCopy={laneCopy}
        layout="collapsible-lanes"
        model={collapsibleModel}
        onLoadMore={vi.fn()}
        onOpenCard={vi.fn()}
      />
    )
    expect(screen.getByRole('button', { name: 'Collapse Active' })).toBeTruthy()
  })

  it('exposes the selected card and supplies its native button as the activation origin', () => {
    const open = vi.fn()

    render(
      <ActivityBoard
        collapseScope="board"
        laneCopy={laneCopy}
        layout="collapsible-lanes"
        model={model}
        onLoadMore={vi.fn()}
        onOpenCard={open}
        selectedCardId="one"
      />
    )

    const card = screen.getByRole('button', { name: 'Run one, running' })
    expect(card.getAttribute('aria-expanded')).toBe('true')
    fireEvent.click(card)
    expect(open).toHaveBeenCalledWith(model.columns[0]!.cards[0], card)
  })

  it('preserves the existing grid card chrome while supplying its activation origin', () => {
    const open = vi.fn()

    render(<ActivityBoard model={model} onLoadMore={vi.fn()} onOpenCard={open} />)

    const card = screen.getByRole('button', { name: 'Run one, running' })
    expect(card.className).toContain('rounded-sm')
    expect(card.className).toContain('bg-(--ui-bg-quaternary)')
    expect(card.className).not.toContain('border-l-2')
    fireEvent.click(card)
    expect(open).toHaveBeenCalledWith(model.columns[0]!.cards[0], card)
  })

  it('surfaces stale state without adding a generic move API', () => {
    render(<ActivityBoard model={{ ...model, stale: true }} onLoadMore={vi.fn()} onOpenCard={vi.fn()} />)
    expect(screen.getByRole('status').textContent).toContain('stale')
  })

  it('preserves card focus when another card is reordered by a delta', () => {
    const other = {
      ariaDescription: 'Run two, queued',
      badges: [],
      exactState: 'queued',
      health: 'waiting' as const,
      id: 'two',
      title: 'Run two',
      updatedAt: 2
    }

    const { rerender } = render(
      <ActivityBoard
        model={{ ...model, columns: [{ ...model.columns[0]!, cards: [model.columns[0]!.cards[0]!, other], count: 2 }] }}
        onLoadMore={vi.fn()}
        onOpenCard={vi.fn()}
      />
    )

    const focused = screen.getByRole('button', { name: 'Run one, running' })
    focused.focus()

    rerender(
      <ActivityBoard
        model={{
          ...model,
          revision: '2',
          columns: [{ ...model.columns[0]!, cards: [other, model.columns[0]!.cards[0]!], count: 2 }]
        }}
        onLoadMore={vi.fn()}
        onOpenCard={vi.fn()}
      />
    )

    expect(globalThis.document.activeElement).toBe(screen.getByRole('button', { name: 'Run one, running' }))
  })

  it.each([320, 768, 1440])('keeps the page board width bounded at %ipx', width => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: width })
    const { container } = render(<ActivityBoard model={model} onLoadMore={vi.fn()} onOpenCard={vi.fn()} />)

    expect(container.firstElementChild?.className).toContain('min-w-0')
    expect(within(container).getByRole('region', { name: 'Active, 1' }).className).toContain('min-w-0')
  })

  it.each([320, 768, 1440])('contains collapsible lane overflow inside the board at %ipx', width => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: width })

    const { container } = render(
      <ActivityBoard
        collapseScope="board"
        laneCopy={laneCopy}
        layout="collapsible-lanes"
        model={model}
        onLoadMore={vi.fn()}
        onOpenCard={vi.fn()}
      />
    )

    const strip = container.querySelector('[data-layout="collapsible-lanes"]')
    expect(strip?.className).toContain('overflow-x-auto')
    expect(strip?.className).toContain('flex-1')
    expect(container.firstElementChild?.className).toContain('min-w-0')
    expect(container.firstElementChild?.className).toContain('h-full')
    expect(container.querySelector('[data-lane-scroll]')?.className).toContain('overflow-y-auto')
  })

  it('does not add motion that ignores reduced-motion preferences', () => {
    window.matchMedia = vi.fn().mockReturnValue({ matches: true }) as never

    const { container } = render(
      <ActivityBoard
        collapseScope="board"
        laneCopy={laneCopy}
        layout="collapsible-lanes"
        model={model}
        onLoadMore={vi.fn()}
        onOpenCard={vi.fn()}
      />
    )

    expect(container.querySelector('[class*="animate-"]')).toBeNull()

    for (const element of container.querySelectorAll('[class*="transition-"]')) {
      expect(element.className).toContain('motion-reduce:transition-none')
    }
  })

  it.each([
    ['en', 'Data is stale. Reconnecting…', 'Load more'],
    ['ja', 'データが古くなっています。再接続中…', 'さらに読み込む'],
    ['zh', '数据已过期。正在重新连接…', '加载更多'],
    ['zh-hant', '資料已過期。正在重新連線…', '載入更多']
  ] as const)('localizes stale and paging affordances in %s', (locale, stale, loadMore) => {
    render(
      <I18nProvider configClient={null} initialLocale={locale}>
        <ActivityBoard model={{ ...model, stale: true }} onLoadMore={vi.fn()} onOpenCard={vi.fn()} />
      </I18nProvider>
    )

    expect(screen.getByRole('status').textContent).toBe(stale)
    expect(screen.getByRole('button', { name: loadMore })).toBeTruthy()
  })
})
