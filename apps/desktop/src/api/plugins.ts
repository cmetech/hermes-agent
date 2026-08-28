import type { HermesConnection, WorkflowArtifactDownloadResult } from '@/global'
import { reconnectBackoffDelayMs } from '@/lib/reconnect-backoff'
import { RECONNECT_ATTEMPT_TIMEOUT_MS, withTimeout } from '@/lib/with-timeout'
import {
  decodeWorkflowAttentionPage,
  decodeWorkflowEventPage,
  decodeWorkflowEvidencePage,
  decodeWorkflowRun,
  decodeWorkflowRunPage
} from '@/lib/workflow-public-codec'
import type {
  KanbanBoardSummary,
  KanbanTaskPage,
  PluginConfigurationDetail,
  PluginConfigurationReadiness,
  PluginSetupActionRun,
  WorkflowArtifactPreview,
  WorkflowAttentionPage,
  WorkflowCleanupPreview,
  WorkflowCleanupResult,
  WorkflowEventPage,
  WorkflowEvidenceKind,
  WorkflowEvidencePage,
  WorkflowRunListView,
  WorkflowRunPage,
  WorkflowRunSnapshot
} from '@/types/hermes'

import { connectionScoped, getApiRequestConnection, getApiRequestProfile, hermesApi, profileScoped } from './client'

/** Resolve the ACTIVE backend's connection descriptor, (connectionId,
 *  profile)-scoped — mirroring how store/profile resolves $connection: a
 *  registry agent's descriptor comes from getConnectionFor (its SOURCE
 *  connection), everything else from the profile-keyed local pool. The
 *  getConnectionFor bridge is optional (older Desktop mains); without it the
 *  profile-scoped pool lookup is the best available answer.
 *
 *  Both branches are IPC round-trips into the main process with no timeout of
 *  their own (#93454) — a wedged main-process round-trip otherwise hangs
 *  pluginSocket's connect() forever instead of falling back to the polling
 *  fallback every consumer already has. Bound the same way store/gateway's
 *  openSecondary bounds the same *For/plain pair.
 *
 *  Exported for tests. */
export async function activeConnection(): Promise<HermesConnection> {
  const getConnectionFor = window.hermesDesktop.getConnectionFor
  const connectionId = getApiRequestConnection()
  const profile = getApiRequestProfile()

  if (connectionId && getConnectionFor) {
    return withTimeout(
      getConnectionFor({ connectionId, profile }),
      RECONNECT_ATTEMPT_TIMEOUT_MS,
      `Timed out connecting to profile "${profile}"`
    )
  }

  return withTimeout(
    window.hermesDesktop.getConnection(profile),
    RECONNECT_ATTEMPT_TIMEOUT_MS,
    `Timed out connecting to profile "${profile}"`
  )
}

/** Options for a plugin REST call — mirrors the app's own `hermesDesktop.api`
 *  shape, minus the path (which is namespace-derived). */
export interface PluginRestOptions {
  method?: string
  body?: unknown
  /** Single-file multipart upload (see HermesApiRequest.upload). */
  upload?: { filename: string; contentType?: string; bytes: ArrayBuffer }
  timeoutMs?: number
}

// Normalize `path` to a leading-slash suffix relative to `/api/plugins/<id>`.
// The namespace is the boundary — reject `..` so a relative segment can't
// normalize out into another plugin's API or a core route. Check the path
// portion only (before any query/hash).
function pluginPathSuffix(caller: string, path: string): string {
  const suffix = path.startsWith('/') ? path : `/${path}`

  if (suffix.split(/[?#]/, 1)[0].split('/').includes('..')) {
    throw new Error(`${caller}: illegal path traversal in "${path}"`)
  }

  return suffix
}

/** The plugin REST door. Every call is scoped BY CONSTRUCTION to the plugin's
 *  own backend namespace — `path` is relative to `/api/plugins/<pluginId>`
 *  ('/board' → `/api/plugins/kanban/board`), so a plugin can't address another
 *  plugin's API or a core route through it. Profile-aware like every desktop
 *  REST call. Broader reach (core endpoints, another namespace) is the future
 *  declared-capability seam; today the namespace IS the boundary. */
export async function pluginRest<T>(pluginId: string, path: string, opts: PluginRestOptions = {}): Promise<T> {
  if (!window.hermesDesktop?.api) {
    throw new Error('Hermes desktop bridge unavailable')
  }

  const suffix = pluginPathSuffix('pluginRest', path)

  return hermesApi<T>({
    path: `/api/plugins/${pluginId}${suffix}`,
    method: opts.method,
    body: opts.body,
    upload: opts.upload,
    timeoutMs: opts.timeoutMs,
    ...profileScoped()
  })
}

/** The plugin WebSocket door — the live twin of `pluginRest`, scoped the same
 *  way: `path` is relative to `/api/plugins/<pluginId>` ('/events' → the
 *  plugin's own event stream). Token-mode backends auth via the same query
 *  credential the app's own sockets use; OAuth remotes resolve null (callers
 *  keep their polling fallback — every consumer must have one anyway, since a
 *  socket can drop). Auto-reconnects with backoff until disposed. */
export function pluginSocket(pluginId: string, path: string, onMessage: (data: unknown) => void): () => void {
  const suffix = pluginPathSuffix('pluginSocket', path)

  let socket: null | WebSocket = null
  let disposed = false
  let attempt = 0

  const connect = async () => {
    const connection = await activeConnection().catch(() => null)

    // No bridge / OAuth cookie auth (WS tickets are single-use, core-managed):
    // stay on the polling fallback rather than half-working.
    if (disposed || !connection || connection.authMode === 'oauth') {
      return
    }

    const base = connection.baseUrl.replace(/^http/, 'ws')
    const join = suffix.includes('?') ? '&' : '?'
    socket = new WebSocket(
      `${base}/api/plugins/${pluginId}${suffix}${join}token=${encodeURIComponent(connection.token)}`
    )

    socket.onmessage = event => {
      attempt = 0

      try {
        onMessage(JSON.parse(String(event.data)))
      } catch {
        // Non-JSON frame — plugin streams are JSON by contract; skip it.
      }
    }

    socket.onclose = () => {
      socket = null

      if (!disposed) {
        // Full-jitter exponential backoff: same rationale as the gateway
        // socket reconnect loops — an immediate-retry loop across many
        // desktop clients floods the gateway with connection attempts
        // during a restart.
        window.setTimeout(() => void connect(), reconnectBackoffDelayMs(attempt, { baseDelayMs: 500, capMs: 30_000 }))
        attempt += 1
      }
    }
  }

  void connect()

  return () => {
    disposed = true
    socket?.close()
  }
}

// Fork-owned workflow and kanban surfaces use the same authenticated plugin
// namespace as SDK plugins, but expose typed core UI helpers through @/hermes.
export async function listWorkflowRuns(cursor?: string, view: WorkflowRunListView = 'board'): Promise<WorkflowRunPage> {
  const query = new URLSearchParams({ view })

  if (cursor) {
    query.set('cursor', cursor)
  }

  const decoded = decodeWorkflowRunPage(
    await hermesApi<unknown>({ path: `/api/plugins/workflow/runs?${query}`, ...profileScoped() })
  )

  if (decoded === null) {
    throw new Error('Hermes returned an invalid workflow run page')
  }

  return decoded
}

export function previewWorkflowCleanup(olderThan = '7d'): Promise<WorkflowCleanupPreview> {
  return hermesApi({
    path: `/api/plugins/workflow/cleanup/preview?older_than=${encodeURIComponent(olderThan)}`,
    ...profileScoped()
  })
}

export function executeWorkflowCleanup(confirmationToken: string, olderThan = '7d'): Promise<WorkflowCleanupResult> {
  return hermesApi({
    body: { confirmation_token: confirmationToken, older_than: olderThan },
    method: 'POST',
    path: '/api/plugins/workflow/cleanup/execute',
    ...profileScoped()
  })
}

export function leaseWorkflowNotifications(clientId: string): Promise<unknown> {
  return hermesApi({
    path: `/api/plugins/workflow/notifications/lease?client_id=${encodeURIComponent(clientId)}`,
    ...profileScoped()
  })
}

export function acknowledgeWorkflowNotification(notificationId: string, clientId: string): Promise<unknown> {
  return hermesApi({
    body: { client_id: clientId },
    method: 'POST',
    path: `/api/plugins/workflow/notifications/${encodeURIComponent(notificationId)}/ack`,
    ...profileScoped()
  })
}

export function failWorkflowNotification(notificationId: string, clientId: string, error: string): Promise<unknown> {
  return hermesApi({
    body: { client_id: clientId, error },
    method: 'POST',
    path: `/api/plugins/workflow/notifications/${encodeURIComponent(notificationId)}/fail`,
    ...profileScoped()
  })
}

export async function getWorkflowRun(runId: string): Promise<WorkflowRunSnapshot> {
  const decoded = decodeWorkflowRun(
    await hermesApi<unknown>({ path: `/api/plugins/workflow/runs/${encodeURIComponent(runId)}`, ...profileScoped() })
  )

  if (decoded === null) {
    throw new Error('Hermes returned an invalid workflow run')
  }

  return decoded
}

export async function listWorkflowAttention(): Promise<WorkflowAttentionPage> {
  const decoded = decodeWorkflowAttentionPage(
    await hermesApi<unknown>({ path: '/api/plugins/workflow/attention', ...profileScoped() })
  )

  if (decoded === null) {
    throw new Error('Hermes returned an invalid workflow attention page')
  }

  return decoded
}

export async function listWorkflowEvents(runId: string, after = 0): Promise<WorkflowEventPage> {
  const decoded = decodeWorkflowEventPage(
    await hermesApi<unknown>({
      path: `/api/plugins/workflow/runs/${encodeURIComponent(runId)}/events?after=${after}&wait_seconds=0`,
      ...profileScoped()
    })
  )

  if (decoded === null) {
    throw new Error('Hermes returned an invalid workflow event page')
  }

  return decoded
}

export async function getWorkflowEvidence(
  runId: string,
  kind: WorkflowEvidenceKind,
  after = 0
): Promise<WorkflowEvidencePage> {
  const query = new URLSearchParams({ after: String(after), kind })

  const decoded = decodeWorkflowEvidencePage(
    await hermesApi<unknown>({
      path: `/api/plugins/workflow/runs/${encodeURIComponent(runId)}/evidence?${query}`,
      ...profileScoped()
    })
  )

  if (decoded === null) {
    throw new Error('Hermes returned an invalid workflow evidence page')
  }

  return decoded
}

function workflowArtifactUrl(runId: string, publicationId: string, action: 'download' | 'preview'): string {
  return (
    `/api/plugins/workflow/runs/${encodeURIComponent(runId)}/artifacts/` +
    `${encodeURIComponent(publicationId)}/${action}`
  )
}

export function getWorkflowArtifactPreview(runId: string, publicationId: string): Promise<WorkflowArtifactPreview> {
  return hermesApi({ path: workflowArtifactUrl(runId, publicationId, 'preview'), ...profileScoped() })
}

export function downloadWorkflowArtifact(
  runId: string,
  publicationId: string,
  profile: null | string,
  requestId: string
): Promise<WorkflowArtifactDownloadResult> {
  return window.hermesDesktop.downloadWorkflowArtifact({
    path: workflowArtifactUrl(runId, publicationId, 'download'),
    ...(profile === null ? {} : { profile }),
    requestId
  })
}

export function cancelWorkflowArtifactDownload(requestId: string): Promise<{ cancelled: boolean }> {
  return window.hermesDesktop.cancelWorkflowArtifactDownload(requestId)
}

export async function mutateWorkflowRun(
  runId: string,
  action: string,
  body: Record<string, unknown>
): Promise<WorkflowRunSnapshot> {
  const decoded = decodeWorkflowRun(
    await hermesApi<unknown>({
      path: `/api/plugins/workflow/runs/${encodeURIComponent(runId)}/${encodeURIComponent(action)}`,
      method: 'POST',
      body,
      ...profileScoped()
    })
  )

  if (decoded === null) {
    throw new Error('Hermes returned an invalid workflow run mutation')
  }

  return decoded
}

export function getKanbanBoardSummary(board: string): Promise<KanbanBoardSummary> {
  return hermesApi({ path: `/api/plugins/kanban/board/summary?board=${encodeURIComponent(board)}`, ...profileScoped() })
}

export function listKanbanTasks(board: string, status?: string, cursor?: string): Promise<KanbanTaskPage> {
  const query = new URLSearchParams({ board })

  if (status) {
    query.set('status', status)
  }

  if (cursor) {
    query.set('cursor', cursor)
  }

  return hermesApi({ path: `/api/plugins/kanban/tasks?${query}`, ...profileScoped() })
}

export class PluginConfigurationApiError extends Error {
  readonly body: unknown
  readonly code?: string
  readonly status: number

  constructor(response: { body: unknown; status: number }) {
    const body =
      typeof response.body === 'object' && response.body !== null
        ? (response.body as Record<string, unknown>)
        : undefined

    const detail =
      typeof body?.detail === 'object' && body.detail !== null ? (body.detail as Record<string, unknown>) : undefined

    super(typeof detail?.message === 'string' ? detail.message : `HTTP ${response.status}`)
    this.name = 'PluginConfigurationApiError'
    this.body = response.body
    this.code = typeof detail?.code === 'string' ? detail.code : undefined
    this.status = response.status
  }
}

async function requestPluginConfigurationApi<T>(
  request: Parameters<Window['hermesDesktop']['apiStructured']>[0]
): Promise<T> {
  const response = await window.hermesDesktop.apiStructured<T>({ ...connectionScoped(), ...request })

  if (response.ok) {
    return response.value
  }
  throw new PluginConfigurationApiError(response)
}

export function isPluginConfigurationRouteMissingError(error: unknown): boolean {
  if (!(error instanceof PluginConfigurationApiError) || error.status !== 404 || error.code) {
    return false
  }

  if (typeof error.body !== 'object' || error.body === null) {
    return false
  }

  const detail = (error.body as Record<string, unknown>).detail

  return typeof detail === 'string' && (detail === 'Not Found' || /^No such API endpoint:/i.test(detail))
}

export function getPluginConfigurations(): Promise<PluginConfigurationDetail[]> {
  return requestPluginConfigurationApi({ ...profileScoped(), path: '/api/plugin-configurations' })
}

export function setPluginConfigurationEnabled(pluginId: string, enabled: boolean): Promise<PluginConfigurationDetail> {
  return requestPluginConfigurationApi({
    ...profileScoped(),
    path: `/api/plugin-configurations/${encodeURIComponent(pluginId)}/enabled`,
    method: 'PUT',
    body: { enabled }
  })
}

export function updatePluginConfiguration(
  pluginId: string,
  body: { secrets?: Record<string, string>; settings?: Record<string, unknown> }
): Promise<PluginConfigurationDetail> {
  return requestPluginConfigurationApi({
    ...profileScoped(),
    path: `/api/plugin-configurations/${encodeURIComponent(pluginId)}`,
    method: 'PUT',
    body
  })
}

export function clearPluginConfigurationSecret(pluginId: string, fieldId: string): Promise<PluginConfigurationDetail> {
  return requestPluginConfigurationApi({
    ...profileScoped(),
    path: `/api/plugin-configurations/${encodeURIComponent(pluginId)}/secrets/${encodeURIComponent(fieldId)}`,
    method: 'DELETE'
  })
}

export function refreshPluginReadiness(pluginId: string): Promise<PluginConfigurationReadiness> {
  return requestPluginConfigurationApi({
    ...profileScoped(),
    path: `/api/plugin-configurations/${encodeURIComponent(pluginId)}/readiness`,
    method: 'POST',
    body: {}
  })
}

export function startPluginSetupAction(pluginId: string, actionId: string): Promise<PluginSetupActionRun> {
  return requestPluginConfigurationApi({
    ...profileScoped(),
    path: `/api/plugin-configurations/${encodeURIComponent(pluginId)}/actions/${encodeURIComponent(actionId)}`,
    method: 'POST',
    body: {}
  })
}

export function getPluginSetupAction(runId: string): Promise<PluginSetupActionRun> {
  return requestPluginConfigurationApi({
    ...profileScoped(),
    path: `/api/plugin-configurations/actions/${encodeURIComponent(runId)}`
  })
}

export function cancelPluginSetupAction(runId: string): Promise<PluginSetupActionRun> {
  return requestPluginConfigurationApi({
    ...profileScoped(),
    path: `/api/plugin-configurations/actions/${encodeURIComponent(runId)}`,
    method: 'DELETE'
  })
}
