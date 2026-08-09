/**
 * Wiring surfaces — each pane is its own memoized component. Every surface
 * reads the reactive state it renders from at the leaf (its own atom
 * subscriptions) and reaches the controller's callbacks through the stable
 * `actions` bag, so a state change scoped to one surface (or a bare
 * wiring-controller tick) never re-renders another. This is what keeps the
 * layout tree's zones independently rendered — the whole point of the shell.
 */

import { useStore } from '@nanostores/react'
import { type ComponentProps, lazy, memo, type ReactNode, Suspense, useMemo } from 'react'
import { Navigate, Route, Routes, useParams } from 'react-router'

import { ContribBoundary } from '@/contrib/react/boundary'
import { useContributions } from '@/contrib/react/use-contributions'
import { $activeGatewayProfile } from '@/store/profile'
import { $freshDraftReady, $gatewayState } from '@/store/session'

import { ChatView } from '../chat'
import { ChatSidebar } from '../chat/sidebar'
import { DispatcherBanner } from '../kanban/dispatcher-banner'
import { TerminalPaneChrome } from '../right-sidebar/terminal/chrome'
import { contributedRoutes, KANBAN_ROUTE, NEW_CHAT_ROUTE, ROUTES_AREA, sessionRoute } from '../routes'
import { useStatusSnapshot } from '../shell/hooks/use-status-snapshot'
import { useStatusbarItems } from '../shell/hooks/use-statusbar-items'
import { ModelMenuPanel } from '../shell/model-menu-panel'
import { StatusbarControls } from '../shell/statusbar-controls'
import { StatusbarBoundary } from '../shell/statusbar-fallback'

import { latestChatActions, latestSidebarActions } from './latest-actions'
import { setStatusbarItemGroup, useStatusbarContributions } from './panes'
import type { SidebarActions, WiringActions } from './types'

// Same lazy-view split as DesktopController — pages load on demand. The
// full-page views the workspace route table mounts live here; overlay views
// (agents/settings/…) are the controller's and stay in wiring.tsx.
const ArtifactsView = lazy(async () => ({ default: (await import('../artifacts')).ArtifactsView }))
const MessagingView = lazy(async () => ({ default: (await import('../messaging')).MessagingView }))
const SkillsView = lazy(async () => ({ default: (await import('../skills')).SkillsView }))
// OTTO additive full-page views (Workflows + Kanban) — reachable at their own
// APP_ROUTES paths, rendered like every other page view in the workspace pane.
const WorkflowsView = lazy(async () => ({ default: (await import('../workflows')).WorkflowsView }))
const KanbanView = lazy(async () => ({ default: (await import('../kanban')).KanbanView }))

export function LegacySessionRedirect() {
  const { sessionId } = useParams()

  return <Navigate replace to={sessionId ? sessionRoute(sessionId) : NEW_CHAT_ROUTE} />
}

/** Kanban board content with the inert-board warning above it.
 *
 *  Exported so one wrapper serves BOTH boards: the contributed SDK plugin
 *  page (a prebuilt bundle we cannot edit) and the built-in fallback. */
export function KanbanRouteContent({ children }: { children: ReactNode }) {
  return (
    <>
      <DispatcherBanner />
      {children}
    </>
  )
}

export const SidebarSurface = memo(function SidebarSurface({
  actions,
  currentView
}: {
  actions: SidebarActions
  currentView: ComponentProps<typeof ChatSidebar>['currentView']
}) {
  const latestActions = useMemo(() => latestSidebarActions(actions), [actions])

  return <ChatSidebar currentView={currentView} {...latestActions} />
})

export const TerminalSurface = memo(function TerminalSurface() {
  return (
    <div className="relative flex h-full min-h-0 flex-col overflow-hidden bg-(--ui-terminal-surface-background)">
      <TerminalPaneChrome />
    </div>
  )
})

/** Owns the statusbar's own data hooks (status snapshot poll, contributed
 *  items) so its 15s refresh — and any statusbar-only churn — re-renders the
 *  bar alone, never the chat/sidebar/terminal. */
const StatusbarSurfaceInner = memo(function StatusbarSurfaceInner({
  actions,
  agentsOpen,
  chatOpen,
  commandCenterOpen
}: {
  actions: WiringActions
  agentsOpen: boolean
  chatOpen: boolean
  commandCenterOpen: boolean
}) {
  const gatewayState = useStore($gatewayState)
  const freshDraftReady = useStore($freshDraftReady)
  const { inferenceStatus, statusSnapshot } = useStatusSnapshot(gatewayState, actions.requestGateway)
  const extraLeftItems = useStatusbarContributions('left')
  const extraRightItems = useStatusbarContributions('right')

  const { leftStatusbarItems, statusbarItems } = useStatusbarItems({
    agentsOpen,
    chatOpen,
    commandCenterOpen,
    extraLeftItems,
    extraRightItems,
    freshDraftReady,
    gatewayState,
    inferenceStatus,
    openAgents: actions.openAgents,
    openCommandCenterSection: actions.openCommandCenterSection,
    requestGateway: actions.requestGateway,
    statusSnapshot,
    toggleCommandCenter: actions.toggleCommandCenter
  })

  return <StatusbarControls items={statusbarItems} leftItems={leftStatusbarItems} />
})

/** Containment: a throwing status item must degrade to the minimal fallback
 *  bar (version still visible, crash logged), never silently remove the whole
 *  footer. See statusbar-fallback.tsx. */
export function StatusbarSurface(props: ComponentProps<typeof StatusbarSurfaceInner>) {
  return (
    <StatusbarBoundary>
      <StatusbarSurfaceInner {...props} />
    </StatusbarBoundary>
  )
}

/** The workspace pane: the real route table (chat + full-page views + plugin
 *  routes). Subscribes to `$gatewayState` and ROUTES_AREA itself; the gateway
 *  instance + voice cap arrive as props so a reconnect/config load re-renders
 *  only this surface. ChatView subscribes to its own session atoms, so
 *  streaming never round-trips through the controller. */
export const ChatRoutesSurface = memo(function ChatRoutesSurface({
  actions,
  maxVoiceRecordingSeconds
}: {
  actions: WiringActions
  maxVoiceRecordingSeconds?: number
}) {
  const activeGatewayProfile = useStore($activeGatewayProfile)
  const gatewayState = useStore($gatewayState)
  useContributions(ROUTES_AREA)
  const routeContributions = contributedRoutes()
  // Upstream's SDK kanban plugin (v0.20.0, opt-in) registers its own richer
  // `/kanban` board page. When it is enabled, the built-in operations board
  // stands down — otherwise this static route shadows the contributed one
  // (same path, earlier in the table) and BOTH nav rows land on the old page.
  const kanbanContributed = routeContributions.some(route => route.path === KANBAN_ROUTE)

  // Recapture the live gateway instance whenever the connection state flips.
  // getGateway reads a controller ref, so gatewayState is the intentional
  // re-eval trigger (not a value the computation itself reads).
  const gateway = useMemo(
    () => actions.getGateway(),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [actions, gatewayState]
  )

  const modelMenuContent = useMemo(
    () =>
      gatewayState === 'open' ? (
        <ModelMenuPanel
          gateway={gateway || undefined}
          onSelectModel={actions.selectModel}
          profile={activeGatewayProfile}
          requestGateway={actions.requestGateway}
        />
      ) : null,
    [actions, activeGatewayProfile, gateway, gatewayState]
  )

  const chatActions = useMemo(() => latestChatActions(actions), [actions])

  const chatView = (
    <ChatView
      gateway={gateway}
      maxVoiceRecordingSeconds={maxVoiceRecordingSeconds}
      modelMenuContent={modelMenuContent}
      {...chatActions}
    />
  )

  // FULL-PAGE views (not chat) mark the zone body `data-zone-no-header`: a
  // page is not a tab-able surface, so the zone's double-click header toggle
  // stands down while one is showing (see onZoneDoubleClick).
  const page = (view: ReactNode) => (
    <div className="contents" data-zone-no-header>
      <Suspense fallback={null}>{view}</Suspense>
    </div>
  )

  return (
    <Routes>
      <Route element={chatView} index />
      <Route element={chatView} path=":sessionId" />
      <Route element={page(<SkillsView setStatusbarItemGroup={setStatusbarItemGroup} />)} path="skills" />
      <Route element={page(<MessagingView setStatusbarItemGroup={setStatusbarItemGroup} />)} path="messaging" />
      <Route element={page(<ArtifactsView setStatusbarItemGroup={setStatusbarItemGroup} />)} path="artifacts" />
      <Route element={page(<WorkflowsView />)} path="workflows" />
      {!kanbanContributed && (
        <Route element={page(<KanbanRouteContent><KanbanView /></KanbanRouteContent>)} path="kanban" />
      )}
      <Route element={null} path="agents" />
      <Route element={null} path="command-center" />
      <Route element={null} path="cron" />
      <Route element={null} path="profiles" />
      <Route element={null} path="settings" />
      <Route element={null} path="starmap" />
      <Route element={null} path="webhooks" />
      {/* Registry-contributed pages (core features + plugins) render in the
          workspace pane like any built-in view — behind the same blast wall
          as every other contribution mount. */}
      {routeContributions.map(route => {
        const content = <ContribBoundary id={route.key}>{route.render()}</ContribBoundary>

        return (
          <Route
            element={page(
              route.path === KANBAN_ROUTE
                ? <KanbanRouteContent>{content}</KanbanRouteContent>
                : content
            )}
            key={route.key}
            path={route.path.slice(1)}
          />
        )
      })}
      <Route element={<Navigate replace to={NEW_CHAT_ROUTE} />} path="new" />
      <Route element={<LegacySessionRedirect />} path="sessions/:sessionId" />
      <Route element={<Navigate replace to={NEW_CHAT_ROUTE} />} path="*" />
    </Routes>
  )
})
