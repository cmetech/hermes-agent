// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

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
})
