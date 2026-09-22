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

function memoizedLoader<TModule extends Awaited<ReturnType<DesktopLazyPageLoader>>>(
  load: () => Promise<TModule>
): () => Promise<TModule> {
  let pending: Promise<TModule> | null = null

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

const loadArtifacts = memoizedLoader(async () => ({ default: (await import('./artifacts')).ArtifactsView }))
const loadMessaging = memoizedLoader(async () => ({ default: (await import('./messaging')).MessagingView }))
const loadSkills = memoizedLoader(async () => ({ default: (await import('./skills')).SkillsView }))
const loadWorkflows = memoizedLoader(async () => ({ default: (await import('./workflows')).WorkflowsView }))
const loadKanban = memoizedLoader(async () => ({ default: (await import('./kanban')).KanbanView }))
const loadAgents = memoizedLoader(async () => ({ default: (await import('./agents')).AgentsView }))

const loadCommandCenter = memoizedLoader(async () => ({
  default: (await import('./command-center')).CommandCenterView
}))

const loadCron = memoizedLoader(async () => ({ default: (await import('./cron')).CronView }))
const loadWebhooks = memoizedLoader(async () => ({ default: (await import('./webhooks')).WebhooksView }))
const loadProfiles = memoizedLoader(async () => ({ default: (await import('./profiles')).ProfilesView }))
const loadSettings = memoizedLoader(async () => ({ default: (await import('./settings')).SettingsView }))
const loadStarmap = memoizedLoader(async () => ({ default: (await import('./starmap')).StarmapView }))

const registry = createDesktopLazyPageRegistry([
  { route: ARTIFACTS_ROUTE, load: loadArtifacts },
  { route: MESSAGING_ROUTE, load: loadMessaging },
  { route: SKILLS_ROUTE, load: loadSkills },
  { route: WORKFLOWS_ROUTE, load: loadWorkflows },
  { route: KANBAN_ROUTE, load: loadKanban },
  { route: AGENTS_ROUTE, load: loadAgents },
  { route: COMMAND_CENTER_ROUTE, load: loadCommandCenter },
  { route: CRON_ROUTE, load: loadCron },
  { route: WEBHOOKS_ROUTE, load: loadWebhooks },
  { route: PROFILES_ROUTE, load: loadProfiles },
  { route: SETTINGS_ROUTE, load: loadSettings },
  { route: STARMAP_ROUTE, load: loadStarmap }
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

export const ArtifactsView = lazy(loadArtifacts)
export const MessagingView = lazy(loadMessaging)
export const SkillsView = lazy(loadSkills)
export const WorkflowsView = lazy(loadWorkflows)
export const KanbanView = lazy(loadKanban)
export const AgentsView = lazy(loadAgents)
export const CommandCenterView = lazy(loadCommandCenter)
export const CronView = lazy(loadCron)
export const WebhooksView = lazy(loadWebhooks)
export const ProfilesView = lazy(loadProfiles)
export const SettingsView = lazy(loadSettings)
export const StarmapView = lazy(loadStarmap)

export function prefetchDesktopRoute(to: string): Promise<void> | null {
  return registry.prefetch(to)
}
