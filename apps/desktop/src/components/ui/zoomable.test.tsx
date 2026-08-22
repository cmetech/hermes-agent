// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { Zoomable } from './zoomable'

afterEach(cleanup)

describe('Zoomable', () => {
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

  it('opens the full-view overlay when the trigger is clicked', () => {
    render(
      <Zoomable label="Open diagram" overlay={<div data-testid="overlay">Expanded diagram</div>}>
        <div>Inline diagram</div>
      </Zoomable>
    )

    expect(screen.queryByTestId('overlay')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Open diagram' }))
    expect(screen.getByTestId('overlay')).toBeTruthy()
  })

  it('gives the full-view stage flex space inside the fixed-height dialog', () => {
    render(
      <Zoomable label="Open diagram" overlay={<div data-testid="overlay">Expanded diagram</div>}>
        <div>Inline diagram</div>
      </Zoomable>
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open diagram' }))

    const dialog = screen.getByRole('dialog')
    const body = dialog.firstElementChild

    // jsdom does not compute flex layout, so height stays 0 either way. The
    // contract that actually failed in Electron is: the body must grow
    // (`flex-1`) inside the h-[85vh] shell, and the pan/zoom stage must too.
    expect(body?.classList.contains('flex-1')).toBe(true)
    expect(body?.classList.contains('min-h-0')).toBe(true)

    const stage = body?.querySelector('.flex-1.overflow-hidden')

    expect(stage).toBeTruthy()
    expect(stage?.contains(screen.getByTestId('overlay'))).toBe(true)
  })
})
