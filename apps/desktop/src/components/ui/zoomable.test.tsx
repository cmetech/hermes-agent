// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { Zoomable } from './zoomable'

describe('Zoomable', () => {
  afterEach(cleanup)

  it('uses the full stage as the resting frame for contain content and resets back to that fit', () => {
    render(
      <Zoomable
        fit="contain"
        label="Open test diagram"
        overlay={<svg aria-label="Expanded diagram" viewBox="0 0 800 400" />}
      >
        <svg aria-label="Inline diagram" viewBox="0 0 800 400" />
      </Zoomable>
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open test diagram' }))

    const dialog = screen.getByRole('dialog')
    const content = dialog.querySelector<HTMLElement>('[data-zoom-pan-content]')

    expect(content).not.toBeNull()
    expect(content?.classList.contains('size-full')).toBe(true)
    expect(content?.style.transform).toBe('translate(0px, 0px) scale(1)')

    fireEvent.click(within(dialog).getByRole('button', { name: 'Zoom in' }))
    expect(content?.style.transform).toBe('translate(0px, 0px) scale(1.25)')

    fireEvent.click(within(dialog).getByRole('button', { name: 'Reset' }))
    expect(content?.style.transform).toBe('translate(0px, 0px) scale(1)')
  })

  it('keeps natural-size content shrink-wrapped in the full viewer', () => {
    render(
      <Zoomable label="Open natural content">
        <div>Natural content</div>
      </Zoomable>
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open natural content' }))

    const content = screen.getByRole('dialog').querySelector<HTMLElement>('[data-zoom-pan-content]')

    expect(content).not.toBeNull()
    expect(content?.classList.contains('size-full')).toBe(false)
  })
})
