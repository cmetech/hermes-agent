import { type ComponentType, lazy } from 'react'

import {
  AGENTS_ROUTE,
  ARTIFACTS_ROUTE,
  COMMAND_CENTER_ROUTE,
  CRON_ROUTE,
  KANBAN_ROUTE,
  MESSAGING_ROUTE,
  PROFILES_ROUTE,
  routePathname,
  SETTINGS_ROUTE,
  SKILLS_ROUTE,
  STARMAP_ROUTE,
  WEBHOOKS_ROUTE,
  WORKFLOWS_ROUTE
} from './routes'

// Lazy pages intentionally have different public props. The registry only
// stores their module promises; each exported component retains its concrete
// loader inference at the declaration below.
type DesktopLazyPageLoader = () => Promise<{ default: ComponentType<any> }>

export interface DesktopLazyPageEntry {
  load: DesktopLazyPageLoader
  route: string
}

function memoizedLoader(load: DesktopLazyPageLoader): DesktopLazyPageLoader {
  let pending: ReturnType<DesktopLazyPageLoader> | null = null

  return () => (pending ??= load())
}

export function createDesktopLazyPageRegistry(entries: readonly DesktopLazyPageEntry[]) {
  const pages = new Map(
    entries.map(entry => {
      const load = memoizedLoader(entry.load)

      return [entry.route, { component: lazy(load), load }] as const
    })
  )

  return {
    component(to: string) {
      return pages.get(routePathname(to))?.component ?? null
    },
    prefetch(to: string): Promise<void> | null {
      const page = pages.get(routePathname(to))

      return page ? page.load().then(() => undefined) : null
    }
  }
}

const registry = createDesktopLazyPageRegistry([
  { route: ARTIFACTS_ROUTE, load: async () => ({ default: (await import('./artifacts')).ArtifactsView }) },
  { route: MESSAGING_ROUTE, load: async () => ({ default: (await import('./messaging')).MessagingView }) },
  { route: SKILLS_ROUTE, load: async () => ({ default: (await import('./skills')).SkillsView }) },
  { route: WORKFLOWS_ROUTE, load: async () => ({ default: (await import('./workflows')).WorkflowsView }) },
  { route: KANBAN_ROUTE, load: async () => ({ default: (await import('./kanban')).KanbanView }) },
  { route: AGENTS_ROUTE, load: async () => ({ default: (await import('./agents')).AgentsView }) },
  {
    route: COMMAND_CENTER_ROUTE,
    load: async () => ({ default: (await import('./command-center')).CommandCenterView })
  },
  { route: CRON_ROUTE, load: async () => ({ default: (await import('./cron')).CronView }) },
  { route: WEBHOOKS_ROUTE, load: async () => ({ default: (await import('./webhooks')).WebhooksView }) },
  { route: PROFILES_ROUTE, load: async () => ({ default: (await import('./profiles')).ProfilesView }) },
  { route: SETTINGS_ROUTE, load: async () => ({ default: (await import('./settings')).SettingsView }) },
  { route: STARMAP_ROUTE, load: async () => ({ default: (await import('./starmap')).StarmapView }) }
])

export const DESKTOP_LAZY_ROUTES = [
  ARTIFACTS_ROUTE,
  MESSAGING_ROUTE,
  SKILLS_ROUTE,
  WORKFLOWS_ROUTE,
  KANBAN_ROUTE,
  AGENTS_ROUTE,
  COMMAND_CENTER_ROUTE,
  CRON_ROUTE,
  WEBHOOKS_ROUTE,
  PROFILES_ROUTE,
  SETTINGS_ROUTE,
  STARMAP_ROUTE
] as const

export const ArtifactsView = registry.component(ARTIFACTS_ROUTE)!
export const MessagingView = registry.component(MESSAGING_ROUTE)!
export const SkillsView = registry.component(SKILLS_ROUTE)!
export const WorkflowsView = registry.component(WORKFLOWS_ROUTE)!
export const KanbanView = registry.component(KANBAN_ROUTE)!
export const AgentsView = registry.component(AGENTS_ROUTE)!
export const CommandCenterView = registry.component(COMMAND_CENTER_ROUTE)!
export const CronView = registry.component(CRON_ROUTE)!
export const WebhooksView = registry.component(WEBHOOKS_ROUTE)!
export const ProfilesView = registry.component(PROFILES_ROUTE)!
export const SettingsView = registry.component(SETTINGS_ROUTE)!
export const StarmapView = registry.component(STARMAP_ROUTE)!

export function prefetchDesktopRoute(to: string): Promise<void> | null {
  return registry.prefetch(to)
}
