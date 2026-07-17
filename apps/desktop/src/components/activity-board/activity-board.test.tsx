// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { ActivityBoard } from './activity-board'
import type { ActivityBoardModel } from './types'

const model: ActivityBoardModel = {
  columns: [{
    cards: [{ ariaDescription: 'Run one, running', badges: [], exactState: 'running', health: 'healthy', id: 'one', title: 'Run one', updatedAt: 1 }],
    count: 1,
    id: 'active',
    label: 'Active',
    nextCursor: 'next'
  }],
  revision: '1',
  scopeLabel: 'Workflow',
  source: 'workflow',
  stale: false
}

afterEach(cleanup)

describe('ActivityBoard', () => {
  it('opens cards with keyboard-compatible native controls and loads bounded pages', () => {
    const open = vi.fn()
    const load = vi.fn()
    render(<ActivityBoard model={model} onLoadMore={load} onOpenCard={open} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run one, running' }))
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    expect(open).toHaveBeenCalledWith(model.columns[0]!.cards[0])
    expect(load).toHaveBeenCalledWith('active', 'next')
    expect(screen.getByRole('region', { name: 'Active, 1' })).toBeTruthy()
  })

  it('surfaces stale state without adding a generic move API', () => {
    render(<ActivityBoard model={{ ...model, stale: true }} onLoadMore={vi.fn()} onOpenCard={vi.fn()} />)
    expect(screen.getByRole('status').textContent).toContain('stale')
  })

  it('preserves card focus when another card is reordered by a delta', () => {
    const other = { ariaDescription: 'Run two, queued', badges: [], exactState: 'queued', health: 'waiting' as const, id: 'two', title: 'Run two', updatedAt: 2 }
    const { rerender } = render(<ActivityBoard model={{ ...model, columns: [{ ...model.columns[0]!, cards: [model.columns[0]!.cards[0]!, other], count: 2 }] }} onLoadMore={vi.fn()} onOpenCard={vi.fn()} />)
    const focused = screen.getByRole('button', { name: 'Run one, running' })
    focused.focus()

    rerender(<ActivityBoard model={{ ...model, revision: '2', columns: [{ ...model.columns[0]!, cards: [other, model.columns[0]!.cards[0]!], count: 2 }] }} onLoadMore={vi.fn()} onOpenCard={vi.fn()} />)

    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Run one, running' }))
  })

  it.each([320, 768, 1440])('keeps the page board width bounded at %ipx', width => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: width })
    const { container } = render(<ActivityBoard model={model} onLoadMore={vi.fn()} onOpenCard={vi.fn()} />)

    expect(container.firstElementChild?.className).toContain('min-w-0')
    expect(within(container).getByRole('region', { name: 'Active, 1' }).className).toContain('min-w-0')
  })

  it('does not add motion that ignores reduced-motion preferences', () => {
    window.matchMedia = vi.fn().mockReturnValue({ matches: true }) as never
    const { container } = render(<ActivityBoard model={model} onLoadMore={vi.fn()} onOpenCard={vi.fn()} />)

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
