import { acknowledgeWorkflowNotification, failWorkflowNotification, leaseWorkflowNotifications } from '@/hermes'
import { persistString, storedString } from '@/lib/storage'

import { type NativeNotificationKind, projectNativeNotification } from './native-notifications'

const CLIENT_KEY = 'hermes:workflow-notification-client'
const PROJECTED_KEY = 'hermes:workflow-notification-projected'

function projectedIds(): string[] {
  try {
    const parsed = JSON.parse(storedString(PROJECTED_KEY) ?? '[]')
    return Array.isArray(parsed) ? parsed.filter(value => typeof value === 'string').slice(-256) : []
  } catch {
    return []
  }
}

function markProjected(notificationId: string): void {
  persistString(PROJECTED_KEY, JSON.stringify([...new Set([...projectedIds(), notificationId])].slice(-256)))
}

function clearProjected(notificationId: string): void {
  persistString(PROJECTED_KEY, JSON.stringify(projectedIds().filter(value => value !== notificationId)))
}

export function workflowNotificationClientId(): string {
  const existing = storedString(CLIENT_KEY)
  if (existing) return existing
  const created = `electron-${crypto.randomUUID()}`
  persistString(CLIENT_KEY, created)
  return created
}

function nativeKind(kind: string): NativeNotificationKind {
  if (kind === 'approval_required' || kind === 'reconciliation_required') return 'approval'
  if (kind === 'input_required') return 'input'
  if (kind === 'failure' || kind === 'stalled') return 'turnError'
  return 'backgroundDone'
}

export async function deliverWorkflowNotificationsOnce(clientId = workflowNotificationClientId()): Promise<number> {
  const page = await leaseWorkflowNotifications(clientId)

  for (const item of page.items) {
    const workflow = typeof item.payload.workflow === 'string' ? item.payload.workflow : 'Workflow'
    const count = item.coalesced_count > 1 ? ` (${item.coalesced_count} updates)` : ''
    if (!projectedIds().includes(item.notification_id)) {
      try {
        await projectNativeNotification({
          body: `${item.kind.replaceAll('_', ' ')}${count}`,
          global: true,
          kind: nativeKind(item.kind),
          title: workflow
        })
        // Persist before the network acknowledgement. If transport accepted
        // the OS notification and the ack fails, a later lease retries only
        // the receipt instead of projecting a duplicate notification.
        markProjected(item.notification_id)
      } catch (error) {
        await failWorkflowNotification(
          item.notification_id,
          clientId,
          error instanceof Error ? error.message : 'notification projection failed'
        )
        continue
      }
    }
    try {
      await acknowledgeWorkflowNotification(item.notification_id, clientId)
      clearProjected(item.notification_id)
    } catch {
      // Leave the local projected receipt durable. The server lease expires;
      // the next delivery pass retries acknowledgement without another toast.
    }
  }

  return page.items.length
}

export function startWorkflowNotificationDelivery(): () => void {
  let stopped = false
  let timer: number | undefined
  const clientId = workflowNotificationClientId()

  const schedule = (delay: number) => {
    timer = window.setTimeout(async () => {
      if (stopped) return
      try {
        await deliverWorkflowNotificationsOnce(clientId)
      } catch {
        // The durable server lease/outbox owns retry; backend outages must not
        // destabilize Desktop chat or create a competing local authority.
      } finally {
        if (!stopped) schedule(10_000)
      }
    }, delay)
  }
  schedule(0)

  return () => {
    stopped = true
    if (timer !== undefined) window.clearTimeout(timer)
  }
}
