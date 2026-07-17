// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, expect, it, vi } from 'vitest'

import { ActivityBoard } from './activity-board'
import type { ActivityBoardModel } from './types'

beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect', {
    configurable: true,
    value: () => ({ bottom: 600, height: 600, left: 0, right: 320, toJSON: () => {}, top: 0, width: 320, x: 0, y: 0 })
  })
  globalThis.ResizeObserver = class {
    private callback: ResizeObserverCallback

    constructor(callback: ResizeObserverCallback) {
      this.callback = callback
    }

    disconnect() {}
    observe(target: Element) {
      this.callback([{ contentRect: target.getBoundingClientRect(), target } as ResizeObserverEntry], this as unknown as ResizeObserver)
    }
    unobserve() {}
  }
})

afterEach(cleanup)

it('virtualizes one thousand rendered cards instead of mounting the full column', async () => {
  const cards = Array.from({ length: 1000 }, (_, index) => ({
    ariaDescription: `Run ${index}, running`,
    badges: [],
    exactState: 'running',
    health: 'healthy' as const,
    id: `run-${index}`,
    title: `Run ${index}`,
    updatedAt: index
  }))

  const model: ActivityBoardModel = {
    columns: [{ cards, count: cards.length, id: 'active', label: 'Active', nextCursor: null }],
    revision: '1000',
    scopeLabel: 'Workflow',
    source: 'workflow',
    stale: false
  }

  render(<ActivityBoard model={model} onLoadMore={vi.fn()} onOpenCard={vi.fn()} />)

  await waitFor(() => expect(screen.getAllByRole('button').length).toBeGreaterThan(0))
  expect(screen.getAllByRole('button').length).toBeLessThan(100)
  expect(screen.getByRole('region', { name: 'Active, 1000' })).toBeTruthy()
})
