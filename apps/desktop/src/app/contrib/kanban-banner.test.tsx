import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../kanban/dispatcher-banner', () => ({
  DispatcherBanner: () => <div data-testid="dispatcher-banner" />
}))

import { KanbanRouteContent } from './surfaces'

describe('kanban route content', () => {
  it('puts the dispatcher banner above the board', () => {
    render(<KanbanRouteContent>{<div data-testid="board" />}</KanbanRouteContent>)
    expect(screen.getByTestId('dispatcher-banner')).toBeTruthy()
    expect(screen.getByTestId('board')).toBeTruthy()
  })
})
