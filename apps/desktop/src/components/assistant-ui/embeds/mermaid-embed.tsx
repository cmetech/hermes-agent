'use client'

import mermaid from 'mermaid'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import { Zoomable } from '@/components/ui/zoomable'
import { copySvgAsPng, normalizeSvgSize } from '@/lib/svg-image'
import { cn } from '@/lib/utils'

import type { RichFenceProps } from './types'
import { useIsDark } from './use-is-dark'

let lastTheme: 'dark' | 'default' | null = null

interface MermaidRendererProps extends RichFenceProps {
  presentation?: 'inline' | 'workflow'
}

// Re-initialise only on first use / theme flip. `securityLevel: 'strict'` makes
// mermaid sanitise label HTML and drop click handlers, so the rendered SVG is
// safe to inject.
function ensureInit(dark: boolean) {
  const theme = dark ? 'dark' : 'default'

  if (theme === lastTheme) {
    return
  }

  mermaid.initialize({ fontFamily: 'inherit', securityLevel: 'strict', startOnLoad: false, theme })
  lastTheme = theme
}

function SourcePreview({ code, muted }: { code: string; muted?: boolean }) {
  return (
    <pre
      className={cn(
        'overflow-auto p-3 font-mono text-[0.7rem] leading-relaxed whitespace-pre-wrap wrap-anywhere',
        muted ? 'text-muted-foreground/70' : 'text-foreground/90'
      )}
    >
      {code}
    </pre>
  )
}

function MermaidMarkup({ className, fit, svg }: { className?: string; fit?: boolean; svg: string }) {
  const container = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    if (!fit || !container.current) {
      return
    }

    const diagram = container.current.querySelector('svg')

    if (!diagram) {
      return
    }

    // Mermaid emits an intrinsic `max-width` inline. That is useful in chat,
    // but it wins over a responsive workflow canvas when Windows display
    // scaling makes the Electron CSS viewport narrower than the `sm`
    // breakpoint. Apply the fit contract directly to the generated SVG so
    // the result is independent of stylesheet order and platform DPI.
    diagram.setAttribute('preserveAspectRatio', 'xMidYMid meet')
    diagram.style.setProperty('width', '100%', 'important')
    diagram.style.setProperty('height', '100%', 'important')
    diagram.style.setProperty('max-width', 'none', 'important')
    diagram.style.setProperty('max-height', 'none', 'important')
  }, [fit, svg])

  return <div className={className} dangerouslySetInnerHTML={{ __html: svg }} ref={container} />
}

// Lazy chunk (pulls in mermaid). Renders ```mermaid fences as diagrams; shows
// the source while the message streams (partial syntax throws) and falls back
// to source on parse failure.
export default function MermaidRenderer({ code, streaming, presentation = 'inline' }: MermaidRendererProps) {
  const isDark = useIsDark()
  const [svg, setSvg] = useState('')
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (streaming) {
      return
    }

    let cancelled = false

    setFailed(false)

    void (async () => {
      try {
        ensureInit(isDark)
        const id = `mmd-${Math.random().toString(36).slice(2)}`
        const result = await mermaid.render(id, code)

        if (!cancelled) {
          setSvg(normalizeSvgSize(result.svg))
        }
      } catch {
        if (!cancelled) {
          setFailed(true)
          setSvg('')
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [code, isDark, streaming])

  if (streaming) {
    return <SourcePreview code={code} muted />
  }

  if (failed) {
    return <SourcePreview code={code} />
  }

  if (!svg) {
    return <SourcePreview code={code} muted />
  }

  const workflowPresentation = presentation === 'workflow'

  // The full view gives the SVG a stage-sized viewport, so Mermaid's viewBox
  // performs the resting fit/centre operation before pan/zoom transforms apply.
  // Chat diagrams remain compact; the dedicated workflow view gets a larger
  // stable viewport instead of inheriting the transcript-oriented 33dvh cap.
  return (
    <Zoomable
      fit="contain"
      label="Open diagram"
      onCopy={() => copySvgAsPng(svg)}
      overlay={
        <MermaidMarkup
          className="size-full p-6 pb-16 [&_svg]:block [&_svg]:size-full [&_svg]:!max-h-none [&_svg]:!max-w-none"
          fit
          svg={svg}
        />
      }
    >
      <MermaidMarkup
        className={cn(
          'overflow-hidden p-3 [&_svg]:mx-auto [&_svg]:block [&_svg]:max-w-full',
          workflowPresentation
            ? 'h-[clamp(16rem,50dvh,34rem)] [&_svg]:size-full [&_svg]:!max-h-none [&_svg]:!max-w-none'
            : '[&_svg]:h-auto [&_svg]:max-h-[33dvh]'
        )}
        fit={workflowPresentation}
        svg={svg}
      />
    </Zoomable>
  )
}
