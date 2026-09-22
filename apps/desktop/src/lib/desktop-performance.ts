export interface DesktopRoutePerformanceLog {
  event: 'desktop.route.slow'
  route: string
  settledMs: number
  visibleMs: number | null
}

interface DesktopPerformanceTimeline {
  clearMarks(name: string): void
  mark(name: string): void
  measure(name: string, startMark: string, endMark: string): void
}

interface DesktopRoutePerformanceAdapters {
  log(entry: DesktopRoutePerformanceLog): void
  now(): number
  timeline: DesktopPerformanceTimeline | null
}

interface ActiveRouteMeasure {
  id: number
  intentAt: number
  intentMark: string
  route: string
  visibleAt: number | null
  visibleMark: string | null
}

export interface DesktopRoutePerformance {
  noteIntent(to: string): void
  noteSettled(to: string): void
  noteVisible(to: string): void
}

const SLOW_ROUTE_MS = 500

function normalizedRoute(to: string): string {
  const cut = to.search(/[?#]/)
  const pathname = cut === -1 ? to : to.slice(0, cut)

  // Only app-internal pathnames are valid measurement identities. This keeps
  // endpoints and other URL-shaped data out even if a caller misuses the API.
  return pathname.startsWith('/') && !pathname.startsWith('//') ? pathname : '/'
}

function browserTimeline(): DesktopPerformanceTimeline | null {
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

function defaultAdapters(): DesktopRoutePerformanceAdapters {
  return {
    log: entry => console.info('[desktop-performance]', entry),
    now: () => (typeof performance === 'undefined' ? Date.now() : performance.now()),
    timeline: browserTimeline()
  }
}

let nextRouteMeasureId = 0

export function createDesktopRoutePerformance(
  adapters: DesktopRoutePerformanceAdapters = defaultAdapters()
): DesktopRoutePerformance {
  let active: ActiveRouteMeasure | null = null

  const clearActiveMarks = () => {
    if (!active || !adapters.timeline) {
      return
    }

    adapters.timeline.clearMarks(active.intentMark)

    if (active.visibleMark) {
      adapters.timeline.clearMarks(active.visibleMark)
    }
  }

  return {
    noteIntent(to) {
      clearActiveMarks()

      const route = normalizedRoute(to)
      const id = ++nextRouteMeasureId
      const intentMark = `hermes.desktop.route.${id}.intent:${route}`

      active = {
        id,
        intentAt: adapters.now(),
        intentMark,
        route,
        visibleAt: null,
        visibleMark: null
      }
      adapters.timeline?.mark(intentMark)
    },

    noteVisible(to) {
      const route = normalizedRoute(to)

      if (!active || active.route !== route || active.visibleAt !== null) {
        return
      }

      active.visibleAt = adapters.now()
      active.visibleMark = `hermes.desktop.route.${active.id}.visible:${route}`
      adapters.timeline?.mark(active.visibleMark)
      adapters.timeline?.measure(`hermes.desktop.route.visible:${route}`, active.intentMark, active.visibleMark)
    },

    noteSettled(to) {
      const route = normalizedRoute(to)

      if (!active || active.route !== route) {
        return
      }

      const finished = active
      const settledAt = adapters.now()
      const settledMark = `hermes.desktop.route.${finished.id}.settled:${route}`
      const settledMs = Math.max(0, Math.round(settledAt - finished.intentAt))

      const visibleMs =
        finished.visibleAt === null ? null : Math.max(0, Math.round(finished.visibleAt - finished.intentAt))

      adapters.timeline?.mark(settledMark)
      adapters.timeline?.measure(`hermes.desktop.route.settled:${route}`, finished.intentMark, settledMark)

      if (settledMs >= SLOW_ROUTE_MS) {
        adapters.log({ event: 'desktop.route.slow', route, settledMs, visibleMs })
      }

      clearActiveMarks()
      adapters.timeline?.clearMarks(settledMark)
      active = null
    }
  }
}

let desktopRoutePerformance = createDesktopRoutePerformance()

export function noteDesktopRouteIntent(to: string): void {
  desktopRoutePerformance.noteIntent(to)
}

export function noteDesktopRouteVisible(to: string): void {
  desktopRoutePerformance.noteVisible(to)
}

export function noteDesktopRouteSettled(to: string): void {
  desktopRoutePerformance.noteSettled(to)
}

export function _resetDesktopRoutePerformanceForTests(): void {
  nextRouteMeasureId = 0
  desktopRoutePerformance = createDesktopRoutePerformance()
}
