import { type CSSProperties, useMemo } from 'react'

import { cn } from '@/lib/utils'

import { buildPath, LOADER_CURVES, type LoaderProps, particleFor } from './loader'

// OTTO: the loader, animated by the browser instead of by us.
//
// The upstream implementation drove everything from a requestAnimationFrame
// loop: every frame it rebuilt a 241-point SVG path (re-parsed by the SVG
// engine) and issued four setAttribute calls per particle -- ~314 DOM writes and
// ~319 curve evaluations per frame. On a renderer without the headroom to finish
// that inside a frame budget, rAF re-queued immediately and the main thread was
// never idle: Electron logged `webContents became unresponsive` and the desktop
// app froze while a spinner was on screen.
//
// All three time-varying quantities -- rotation, path shape, particle position
// -- are declarative animations that were being expressed imperatively. Here
// they are described once and run by Chromium. Nothing executes per frame, so
// there is no JS loop that can starve the main thread; on a slow or
// software-rasterized renderer this degrades to visible jank instead of a
// freeze.
//
// Design: docs/plans/2026-07-26-desktop-loader-offload-design.md
// Guard against silent reverts: loader.test.tsx (asserts no frame is scheduled).

// `detailScale` used to breathe across 0.52..1.0 on a sine (detailScaleFor).
// Freezing it at the midpoint is what lets `d` be computed once -- and a `d`
// that never changes is what makes the rest of this declarative. The curve no
// longer pulses; that was traded away deliberately.
const FROZEN_DETAIL_SCALE = 0.76

const SPIN_ANIMATION = 'otto-loader-spin'
const TRAIL_ANIMATION = 'otto-loader-trail'

// Inline rather than in styles.css: that file is shared with upstream, and
// keeping our footprint to files upstream does not have is what makes this
// survive merges. Identical <style> content across instances is deduplicated by
// the browser's stylesheet cache.
const KEYFRAMES = `
@keyframes ${SPIN_ANIMATION} { to { transform: rotate(-360deg); } }
@keyframes ${TRAIL_ANIMATION} { to { offset-distance: 100%; } }
`

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)
}

export function NativeLoader({
  className,
  label = 'Loading',
  pathSteps = 240,
  role = 'status',
  strokeScale = 1,
  type = 'rose-curve',
  ...props
}: LoaderProps) {
  const config = LOADER_CURVES[type]
  const reduceMotion = prefersReducedMotion()

  const path = useMemo(() => buildPath(config, FROZEN_DETAIL_SCALE, pathSteps), [config, pathSteps])

  // `particleFor` derives opacity and radius from the particle's index alone, so
  // both are constant for the component's lifetime -- upstream rewrote them on
  // every frame regardless, which was half the per-frame DOM traffic. Computed
  // once here and rendered as static attributes.
  const particles = useMemo(
    () =>
      Array.from({ length: config.particleCount }, (_, index) => {
        const { opacity, radius } = particleFor(config, index, 0, FROZEN_DETAIL_SCALE, strokeScale)
        const tailOffset = index / (config.particleCount - 1)

        return {
          // A negative delay starts each particle mid-cycle, which is what
          // spaces them into a trail. Same spacing the imperative version got
          // from subtracting tailOffset * trailSpan from the head's progress.
          delayMs: -(tailOffset * config.trailSpan * config.durationMs),
          opacity,
          radius,
          // Reduced motion: hold the trail in the shape it would otherwise
          // sweep, rather than collapsing every particle onto one point.
          restDistance: tailOffset * config.trailSpan * 100
        }
      }),
    [config, strokeScale]
  )

  const groupStyle: CSSProperties =
    config.rotate && !reduceMotion
      ? {
          animation: `${SPIN_ANIMATION} ${config.rotationDurationMs}ms linear infinite`,
          transformBox: 'view-box',
          transformOrigin: '50px 50px'
        }
      : {}

  return (
    <div
      {...props}
      aria-label={props['aria-label'] ?? label}
      className={cn('inline-grid size-10 place-items-center text-primary', className)}
      role={role}
    >
      <style>{KEYFRAMES}</style>
      <svg aria-hidden="true" className="size-full overflow-visible" fill="none" viewBox="0 0 100 100">
        <g style={groupStyle}>
          <path
            d={path}
            opacity="0.1"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={config.strokeWidth * strokeScale}
          />
          {particles.map((particle, index) => (
            <circle
              cx={0}
              cy={0}
              fill="currentColor"
              key={`${type}-${index}`}
              opacity={particle.opacity.toFixed(3)}
              r={particle.radius.toFixed(2)}
              style={{
                offsetPath: `path("${path}")`,
                ...(reduceMotion
                  ? { offsetDistance: `${particle.restDistance.toFixed(2)}%` }
                  : {
                      animation: `${TRAIL_ANIMATION} ${config.durationMs}ms linear infinite`,
                      animationDelay: `${particle.delayMs.toFixed(0)}ms`
                    })
              }}
            />
          ))}
        </g>
      </svg>
    </div>
  )
}
