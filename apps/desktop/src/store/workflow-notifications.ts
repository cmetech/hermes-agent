import { acknowledgeWorkflowNotification, failWorkflowNotification, leaseWorkflowNotifications } from '@/hermes'
import { persistString, storedString } from '@/lib/storage'
import type {
  WorkflowDeliveryDecisionNotificationPayload,
  WorkflowNotification,
  WorkflowNotificationAction,
  WorkflowNotificationInteraction,
  WorkflowNotificationPage,
  WorkflowProjectionRecoveryNotificationPayload,
  WorkflowTransitionNotificationPayload
} from '@/types/hermes'

import { type NativeNotificationKind, projectNativeNotification } from './native-notifications'

const CLIENT_KEY = 'hermes:workflow-notification-client'
const PROJECTED_KEY = 'hermes:workflow-notification-projected'

const ACTIONS = new Set<WorkflowNotificationAction>([
  'abandon',
  'approve',
  'archive',
  'cancel',
  'events',
  'provide-input',
  'reconcile',
  'reject',
  'restore',
  'resume',
  'retry',
  'status'
])

const KINDS = new Set([
  'approval_required',
  'cancellation',
  'completion',
  'failure',
  'input_required',
  'reconciliation_required',
  'retry',
  'stalled'
])

const STATES = new Set(['dead', 'delivered', 'leased', 'pending', 'pruned', 'suppressed'])

const STATUSES = new Set([
  'abandoned',
  'cancelled',
  'failed',
  'interrupted',
  'paused',
  'queued',
  'recovery_pending',
  'running',
  'succeeded',
  'waiting_retry'
])

const EVENT_TYPES = new Set([
  'cancel_reconciliation_required',
  'cleanup_failed',
  'coordinator_stalled',
  'loop_input_required',
  'loop_signal_confirmation_required',
  'node_approval_required',
  'node_reconciliation_required',
  'node_retry_scheduled',
  'run_cancelled',
  'run_failed',
  'run_paused',
  'run_reconciliation_required',
  'run_retry_waiting',
  'run_stalled',
  'run_succeeded',
  'workflow_approval_required'
])

const INTERACTION_TYPES = new Set([
  'approval',
  'loop_input',
  'loop_signal_confirmation',
  'reconcile',
  'workflow_approval'
])

const CODES = new Set([
  'cleanup_failed',
  'host_pressure',
  'persistent_session_registry_update_pending',
  'provider_capability_drift',
  'schedule_overlap_forbidden',
  'schedule_revalidation_failed',
  'workflow_operation_failed'
])

const MISMATCH_FIELDS = new Set([
  'api_mode',
  'base_url_trust_class',
  'endpoint_sha256',
  'model',
  'provider',
  'registration_provenance_digest'
])

const DECISIONS = new Set([
  'dead_letter_retried',
  'delivery_outcome_uncertain',
  'delivery_pruned',
  'terminal_dead_letter'
])

const DELIVERY_REASONS = new Set([
  'adapter_send_failed',
  'adapter_send_timeout',
  'adapter_unavailable',
  'bad_format',
  'delivery_store_unavailable',
  'forbidden',
  'gateway_loop_unavailable',
  'invalid_text',
  'not_found',
  'notification delivery failed',
  'outcome_uncertain',
  'permanent_failure',
  'projection_failed',
  'rate_limited',
  'retryable_failure',
  'too_long',
  'transient',
  'unauthorized',
  'unknown'
])

const LOGICAL_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,255}$/

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function hasExactKeys(value: Record<string, unknown>, allowed: ReadonlySet<string>): boolean {
  return Object.keys(value).every(key => allowed.has(key))
}

function isBoundedInteger(value: unknown, minimum = 0, maximum = 1_000_000): value is number {
  return Number.isInteger(value) && typeof value === 'number' && value >= minimum && value <= maximum
}

function isOptionalMember(value: unknown, members: ReadonlySet<string>): boolean {
  return value === undefined || (typeof value === 'string' && members.has(value))
}

function isOptionalTimestamp(value: unknown): boolean {
  return (
    value === undefined ||
    (typeof value === 'string' && value.length > 0 && value.length <= 64 && Number.isFinite(Date.parse(value)))
  )
}

function isLogicalIdentifier(value: unknown, maximum = 256): value is string {
  return typeof value === 'string' && value.length <= maximum && LOGICAL_IDENTIFIER.test(value)
}

function isActionList(value: unknown): value is WorkflowNotificationAction[] {
  return (
    Array.isArray(value) &&
    value.length <= ACTIONS.size &&
    new Set(value).size === value.length &&
    value.every(item => typeof item === 'string' && ACTIONS.has(item as WorkflowNotificationAction))
  )
}

function isInteraction(value: unknown): value is WorkflowNotificationInteraction {
  if (!isRecord(value) || !hasExactKeys(value, new Set(['type', 'interaction_id', 'iteration', 'max_iterations']))) {
    return false
  }

  return (
    typeof value.type === 'string' &&
    INTERACTION_TYPES.has(value.type) &&
    (value.interaction_id === undefined || isLogicalIdentifier(value.interaction_id, 128)) &&
    (value.iteration === undefined || isBoundedInteger(value.iteration, 1, 100)) &&
    (value.max_iterations === undefined || isBoundedInteger(value.max_iterations, 1, 100)) &&
    (value.iteration === undefined || value.max_iterations === undefined || value.iteration <= value.max_iterations)
  )
}

function isTransitionPayload(value: unknown): value is WorkflowTransitionNotificationPayload {
  if (
    !isRecord(value) ||
    !hasExactKeys(
      value,
      new Set([
        'payload_type',
        'workflow',
        'status',
        'event_type',
        'node_id',
        'interaction',
        'code',
        'mismatched_fields',
        'state_version',
        'next_actions'
      ])
    )
  ) {
    return false
  }

  return (
    value.payload_type === 'workflow_transition' &&
    isBoundedInteger(value.state_version, 0, 1_000_000_000) &&
    isActionList(value.next_actions) &&
    (value.workflow === undefined || isLogicalIdentifier(value.workflow, 128)) &&
    isOptionalMember(value.status, STATUSES) &&
    isOptionalMember(value.event_type, EVENT_TYPES) &&
    (value.node_id === undefined || isLogicalIdentifier(value.node_id, 128)) &&
    (value.interaction === undefined || isInteraction(value.interaction)) &&
    isOptionalMember(value.code, CODES) &&
    (value.mismatched_fields === undefined ||
      (Array.isArray(value.mismatched_fields) &&
        value.mismatched_fields.length <= MISMATCH_FIELDS.size &&
        new Set(value.mismatched_fields).size === value.mismatched_fields.length &&
        value.mismatched_fields.every(item => typeof item === 'string' && MISMATCH_FIELDS.has(item))))
  )
}

function isDecisionPayload(value: unknown): value is WorkflowDeliveryDecisionNotificationPayload {
  if (
    !isRecord(value) ||
    !hasExactKeys(
      value,
      new Set([
        'payload_type',
        'decision',
        'error',
        'attempts',
        'previous_attempts',
        'previous_error',
        'authority_scope',
        'delivery_state',
        'delivered_at',
        'dismissed_at',
        'state_version',
        'next_actions'
      ])
    )
  ) {
    return false
  }

  return (
    value.payload_type === 'delivery_decision' &&
    typeof value.decision === 'string' &&
    DECISIONS.has(value.decision) &&
    isBoundedInteger(value.state_version, 0, 1_000_000_000) &&
    isActionList(value.next_actions) &&
    (value.error === undefined || (typeof value.error === 'string' && DELIVERY_REASONS.has(value.error))) &&
    (value.previous_error === undefined ||
      (typeof value.previous_error === 'string' && DELIVERY_REASONS.has(value.previous_error))) &&
    (value.attempts === undefined || isBoundedInteger(value.attempts)) &&
    (value.previous_attempts === undefined || isBoundedInteger(value.previous_attempts)) &&
    (value.authority_scope === undefined || isLogicalIdentifier(value.authority_scope)) &&
    isOptionalMember(value.delivery_state, STATES) &&
    isOptionalTimestamp(value.delivered_at) &&
    isOptionalTimestamp(value.dismissed_at)
  )
}

function isRecoveryPayload(value: unknown): value is WorkflowProjectionRecoveryNotificationPayload {
  return (
    isRecord(value) &&
    hasExactKeys(value, new Set(['payload_type', 'code', 'state_version', 'next_actions'])) &&
    value.payload_type === 'projection_recovery' &&
    value.code === 'notification_projection_invalid' &&
    isBoundedInteger(value.state_version, 0, 1_000_000_000) &&
    isActionList(value.next_actions)
  )
}

function isNotification(value: unknown): value is WorkflowNotification {
  if (
    !isRecord(value) ||
    !hasExactKeys(
      value,
      new Set([
        'notification_id',
        'run_id',
        'kind',
        'destination',
        'transition_version',
        'coalesced_count',
        'payload',
        'state',
        'created_at',
        'updated_at',
        'lease_owner',
        'lease_expires_at',
        'delivered_at',
        'dismissed_at',
        'attempts',
        'last_error'
      ])
    )
  ) {
    return false
  }

  return (
    isLogicalIdentifier(value.notification_id) &&
    isLogicalIdentifier(value.run_id) &&
    typeof value.kind === 'string' &&
    KINDS.has(value.kind) &&
    (value.destination === 'desktop' || value.destination === 'gateway:opaque') &&
    isBoundedInteger(value.transition_version, 0, 1_000_000_000) &&
    isBoundedInteger(value.coalesced_count, 1) &&
    (isTransitionPayload(value.payload) || isDecisionPayload(value.payload) || isRecoveryPayload(value.payload)) &&
    typeof value.state === 'string' &&
    STATES.has(value.state) &&
    isOptionalTimestamp(value.created_at) &&
    isOptionalTimestamp(value.updated_at) &&
    (value.lease_owner === undefined || isLogicalIdentifier(value.lease_owner)) &&
    isOptionalTimestamp(value.lease_expires_at) &&
    isOptionalTimestamp(value.delivered_at) &&
    isOptionalTimestamp(value.dismissed_at) &&
    isBoundedInteger(value.attempts) &&
    (value.last_error === undefined ||
      (typeof value.last_error === 'string' && DELIVERY_REASONS.has(value.last_error))) &&
    value.payload.state_version === value.transition_version
  )
}

export function decodeWorkflowNotificationPage(value: unknown): WorkflowNotificationPage | null {
  if (!isRecord(value) || !hasExactKeys(value, new Set(['schema_version', 'items']))) {
    return null
  }

  if (value.schema_version !== 1 || !Array.isArray(value.items) || value.items.length > 100) {
    return null
  }

  if (!value.items.every(isNotification)) {
    return null
  }

  return { items: value.items, schema_version: 1 }
}

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

  if (existing) {
    return existing
  }

  const created = `electron-${crypto.randomUUID()}`
  persistString(CLIENT_KEY, created)

  return created
}

function nativeKind(kind: string): NativeNotificationKind {
  if (kind === 'approval_required' || kind === 'reconciliation_required') {
    return 'approval'
  }

  if (kind === 'input_required') {
    return 'input'
  }

  if (kind === 'failure' || kind === 'stalled') {
    return 'turnError'
  }

  return 'backgroundDone'
}

export async function deliverWorkflowNotificationsOnce(clientId = workflowNotificationClientId()): Promise<number> {
  const page = decodeWorkflowNotificationPage(await leaseWorkflowNotifications(clientId))

  if (!page) {
    return 0
  }

  for (const item of page.items) {
    const workflow =
      item.payload.payload_type === 'workflow_transition' && item.payload.workflow ? item.payload.workflow : 'Workflow'

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
      if (stopped) {
        return
      }

      try {
        await deliverWorkflowNotificationsOnce(clientId)
      } catch {
        // The durable server lease/outbox owns retry; backend outages must not
        // destabilize Desktop chat or create a competing local authority.
      } finally {
        if (!stopped) {
          schedule(10_000)
        }
      }
    }, delay)
  }

  schedule(0)

  return () => {
    stopped = true

    if (timer !== undefined) {
      window.clearTimeout(timer)
    }
  }
}
