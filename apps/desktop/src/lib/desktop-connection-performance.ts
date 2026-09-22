export type DesktopConnectionMeasure = 'connection.initial' | 'profile.activation' | 'source.activation'
export type DesktopConnectionMeasureOutcome = 'cancelled' | 'failed' | 'ready' | 'superseded'

interface DesktopPerformanceTimeline {
  clearMarks(name: string): void
  mark(name: string): void
  measure(name: string, startMark: string, endMark: string): void
}

let nextMeasureId = 0

function browserPerformanceTimeline(): DesktopPerformanceTimeline | null {
  if (
    typeof performance === 'undefined' ||
    typeof performance.mark !== 'function' ||
    typeof performance.measure !== 'function' ||
    typeof performance.clearMarks !== 'function'
  ) {
    return null
  }

  return performance
}

/**
 * Record one local-only Desktop connection duration. Names contain only the
 * operation, a process-local sequence, and a bounded outcome; scope identity,
 * endpoints, credentials, and raw errors never enter the performance timeline.
 */
export function beginDesktopConnectionMeasure(
  operation: DesktopConnectionMeasure,
  timeline: DesktopPerformanceTimeline | null = browserPerformanceTimeline()
): (outcome: DesktopConnectionMeasureOutcome) => void {
  if (!timeline) {
    return () => undefined
  }

  const id = ++nextMeasureId
  const prefix = `hermes.desktop.${operation}.${id}`
  const start = `${prefix}.start`
  let finished = false

  timeline.mark(start)

  return outcome => {
    if (finished) {
      return
    }

    finished = true
    const end = `${prefix}.${outcome}`

    timeline.mark(end)
    timeline.measure(`hermes.desktop.${operation}.${outcome}`, start, end)
    timeline.clearMarks(start)
    timeline.clearMarks(end)
  }
}

export function _resetDesktopConnectionPerformanceForTests(): void {
  nextMeasureId = 0
}
