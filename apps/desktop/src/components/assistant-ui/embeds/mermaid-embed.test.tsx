// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as SvgImage from '@/lib/svg-image'

import MermaidRenderer from './mermaid-embed'

const mermaidMock = vi.hoisted(() => ({
  initialize: vi.fn(),
  render: vi.fn()
}))

vi.mock('mermaid', () => ({ default: mermaidMock }))
vi.mock('./use-is-dark', () => ({ useIsDark: () => false }))
vi.mock('@/lib/svg-image', async importOriginal => ({
  ...(await importOriginal<typeof SvgImage>()),
  copySvgAsPng: vi.fn()
}))

const INTRINSIC_SVG =
  '<svg height="48" style="max-width: 180px;" viewBox="0 0 180 48" width="100%"><g><rect height="40" width="172" /></g></svg>'

describe('MermaidRenderer viewport fitting', () => {
  beforeEach(() => {
    mermaidMock.initialize.mockReset()
    mermaidMock.render.mockReset()
    mermaidMock.render.mockResolvedValue({ svg: INTRINSIC_SVG })
  })

  afterEach(cleanup)

  it('overrides Mermaid intrinsic dimensions and centres workflow diagrams before paint', async () => {
    const { container } = render(
      <MermaidRenderer code="flowchart LR\nA --> B" presentation="workflow" streaming={false} />
    )

    await waitFor(() => expect(container.querySelector('svg')).not.toBeNull())
    const diagram = container.querySelector('svg')

    expect(diagram?.getAttribute('preserveAspectRatio')).toBe('xMidYMid meet')
    expect(diagram?.style.getPropertyValue('width')).toBe('100%')
    expect(diagram?.style.getPropertyPriority('width')).toBe('important')
    expect(diagram?.style.getPropertyValue('height')).toBe('100%')
    expect(diagram?.style.getPropertyPriority('height')).toBe('important')
    expect(diagram?.style.getPropertyValue('max-width')).toBe('none')
    expect(diagram?.style.getPropertyPriority('max-width')).toBe('important')
    expect(diagram?.style.getPropertyValue('max-height')).toBe('none')
  })

  it('keeps chat diagrams compact but applies the same fit contract after expansion', async () => {
    const { container } = render(<MermaidRenderer code="flowchart LR\nA --> B" streaming={false} />)

    await waitFor(() => expect(container.querySelector('svg')).not.toBeNull())
    const inlineDiagram = container.querySelector('svg')

    expect(inlineDiagram?.style.getPropertyValue('max-width')).toBe('180px')
    expect(inlineDiagram?.style.getPropertyPriority('max-width')).toBe('')

    fireEvent.click(screen.getByRole('button', { name: 'Open diagram' }))
    const expandedDiagram = screen.getByRole('dialog').querySelector('svg')

    expect(expandedDiagram?.style.getPropertyValue('width')).toBe('100%')
    expect(expandedDiagram?.style.getPropertyPriority('width')).toBe('important')
    expect(expandedDiagram?.getAttribute('preserveAspectRatio')).toBe('xMidYMid meet')
  })
})
