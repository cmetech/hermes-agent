import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Loader, LOADER_TYPES } from './loader'

// OTTO: these tests exist to stop a specific freeze coming back.
//
// The loader used to drive its animation from a requestAnimationFrame loop that
// rebuilt a 241-point SVG path and issued ~314 DOM attribute writes every frame.
// On a slow renderer (a corporate laptop, very likely rasterizing in software)
// each frame overran its budget, rAF re-queued immediately, and the main thread
// was never idle. Electron logged `webContents became unresponsive` and the app
// froze — while the spinner was on screen, which meant the loading indicator was
// what prevented the load from finishing.
//
// The animation is therefore declarative: described once, run by the browser. A
// declarative animation has no JS loop and cannot starve the main thread.
//
// This is the guard against a SILENT revert. An upstream merge that rewrites
// loader.tsx could drop our delegation with no conflict, no build error and no
// type error; the app would compile, look identical, and freeze again. Asserting
// on behaviour rather than on source text keeps that detectable however upstream
// restructures the file.
//
// Design: docs/plans/2026-07-26-desktop-loader-offload-design.md
describe('Loader', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it.each(LOADER_TYPES)('schedules no animation frames for %s', type => {
    const requestAnimationFrame = vi.fn(() => 0)
    vi.stubGlobal('requestAnimationFrame', requestAnimationFrame)

    render(<Loader type={type} />)

    expect(requestAnimationFrame).not.toHaveBeenCalled()
  })

  // The trail used to be produced by advancing a head along the curve each frame
  // and placing each particle tailOffset * trailSpan behind it. Declaratively,
  // that spacing is a distinct negative animation-delay per particle: each one
  // starts mid-cycle, so at any instant they are strung out along the path.
  it('spaces the trail with a distinct negative delay per particle', () => {
    const { container } = render(<Loader type="rose-curve" />)

    const delays = Array.from(container.querySelectorAll('circle'), circle =>
      (circle as SVGCircleElement).style.animationDelay
    )

    expect(delays.length).toBeGreaterThan(1)
    expect(new Set(delays).size).toBe(delays.length)
    // The head is at 0ms and every follower starts earlier in the cycle.
    expect(delays.every(delay => delay === '0ms' || delay.startsWith('-'))).toBe(true)
  })

  // particleFor derives opacity and radius from the particle index alone, so
  // they are constant for the component's lifetime. Upstream rewrote both on
  // every frame anyway -- half the per-frame DOM traffic. They are static
  // attributes now, and the fade ramp they encode has to survive that.
  it('renders the fade ramp as static attributes', () => {
    const { container } = render(<Loader type="rose-curve" />)

    const opacities = Array.from(container.querySelectorAll('circle'), circle =>
      Number(circle.getAttribute('opacity'))
    )

    expect(opacities.length).toBeGreaterThan(1)
    expect(opacities[0]).toBeGreaterThan(opacities[opacities.length - 1])
    expect(opacities.every((value, index) => index === 0 || value <= opacities[index - 1])).toBe(true)
  })

  // Some corporate Windows images set this system-wide, which makes it a real
  // code path here rather than only an accessibility nicety. Motion goes; the
  // status role and its label must not.
  it('drops the animation under prefers-reduced-motion but keeps the status role', () => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      addEventListener: () => {},
      matches: query.includes('prefers-reduced-motion'),
      removeEventListener: () => {}
    }))

    const { container, getByRole } = render(<Loader type="rose-orbit" />)

    expect(getByRole('status').getAttribute('aria-label')).toBe('Loading')

    const group = container.querySelector('g') as SVGGElement
    expect(group.style.animation).toBe('')

    const animated = Array.from(container.querySelectorAll('circle')).filter(
      circle => (circle as SVGCircleElement).style.animation !== ''
    )

    expect(animated).toHaveLength(0)
  })
})
