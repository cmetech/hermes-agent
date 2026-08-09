import type {
  WorkflowArtifactEvidence,
  WorkflowAttemptEvidence,
  WorkflowAttentionPage,
  WorkflowEventPage,
  WorkflowEvidenceItem,
  WorkflowEvidencePage,
  WorkflowRunPage,
  WorkflowRunSnapshot,
  WorkflowStructuredOutputCapabilitySummary,
  WorkflowTimelineEvent
} from '@/types/hermes'

const RUN_KEYS = new Set([
  'action',
  'admission_disposition',
  'archived_at',
  'archive_version',
  'artifacts',
  'attempts',
  'blocked_by_run_id',
  'blocking_reason',
  'completed_at',
  'coordinator',
  'created_at',
  'current_nodes',
  'definition_digest',
  'event_sequence',
  'execution_mode',
  'health',
  'last_error',
  'last_semantic_progress_at',
  'next_actions',
  'next_retry_at',
  'nodes',
  'pending_interaction',
  'presentation_state',
  'previous_node',
  'progress',
  'provider_resolution_sha256',
  'provenance',
  'queue_position',
  'restored_to_history',
  'run_id',
  'schedule_at',
  'schema_version',
  'started_at',
  'state_version',
  'status',
  'status_authoritative',
  'trigger',
  'updated_at',
  'warnings',
  'workflow',
  'workflow_version'
])

const ACTIONS = new Set([
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

const HEALTH = new Set([
  'coordinator_unavailable',
  'healthy',
  'interrupted',
  'retry_wait',
  'stalled',
  'storage_degraded',
  'terminal',
  'user_wait',
  'waiting'
])

const NODE_STATES = new Set([
  'cancelled',
  'claimed',
  'failed',
  'interrupted',
  'paused',
  'pending',
  'ready',
  'running',
  'skipped',
  'succeeded',
  'waiting_resolution',
  'waiting_retry'
])

const INTERACTION_TYPES = new Set([
  'approval',
  'capability',
  'loop_input',
  'loop_signal_confirmation',
  'reconcile',
  'workflow_approval'
])

const PROVENANCE_SOURCES = new Set(['api', 'background_agent', 'chat', 'cli', 'cron', 'desktop'])
const PROVENANCE_ASSURANCE = new Set(['legacy_unknown', 'local_admin_claim', 'system_schedule', 'verified_adapter'])
const ATTEMPT_ERROR_CODES = new Set(['execution_integrity', 'package_mcp_unavailable'])

const EVIDENCE_KINDS = new Set([
  'artifacts',
  'attempts',
  'cleanup',
  'coordinator',
  'interactions',
  'logs',
  'notifications',
  'outputs',
  'recovery',
  'timeline'
])

const STRUCTURED_OUTPUT_STRATEGIES = new Set([
  'native_json_mode',
  'native_json_schema',
  'prompt_json_schema',
  'unsupported'
])

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function exact(value: Record<string, unknown>, keys: Set<string>): boolean {
  return Object.keys(value).every(key => keys.has(key))
}

function finiteInt(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}

export function isWorkflowStructuredOutputCapabilitySummary(
  value: unknown
): value is WorkflowStructuredOutputCapabilitySummary {
  if (
    !record(value) ||
    !exact(value, new Set(['mixed', 'summaries', 'summaries_truncated', 'summary_count'])) ||
    typeof value.mixed !== 'boolean' ||
    typeof value.summaries_truncated !== 'boolean' ||
    !finiteInt(value.summary_count) ||
    value.summary_count < 1 ||
    value.summary_count > 1_000_000 ||
    !Array.isArray(value.summaries) ||
    value.summaries.length < 1 ||
    value.summaries.length > 16
  ) {
    return false
  }

  if (value.mixed !== (value.summary_count > 1)) {
    return false
  }

  if (
    value.summaries_truncated
      ? value.summary_count <= 16 || value.summaries.length !== 16
      : value.summary_count !== value.summaries.length
  ) {
    return false
  }

  return value.summaries.every(
    summary =>
      record(summary) &&
      exact(summary, new Set(['adapter_version', 'api_mode', 'provider', 'strategy'])) &&
      finiteInt(summary.adapter_version) &&
      summary.adapter_version >= 1 &&
      summary.adapter_version <= 1_000_000 &&
      typeof summary.api_mode === 'string' &&
      summary.api_mode.length <= 64 &&
      typeof summary.provider === 'string' &&
      summary.provider.length <= 64 &&
      STRUCTURED_OUTPUT_STRATEGIES.has(String(summary.strategy))
  )
}

function optionalString(value: unknown): boolean {
  return value === undefined || value === null || typeof value === 'string'
}

function optionalFiniteInt(value: unknown): boolean {
  return value === undefined || value === null || finiteInt(value)
}

function optionalBoolean(value: unknown): boolean {
  return value === undefined || value === null || typeof value === 'boolean'
}

function optionalDigest(value: unknown): boolean {
  return value === undefined || value === null || (typeof value === 'string' && /^[0-9a-f]{64}$/.test(value))
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === 'string')
}

function closedError(value: unknown): boolean {
  return (
    record(value) &&
    exact(value, new Set(['code', 'message'])) &&
    value.code === 'workflow_operation_failed' &&
    value.message === 'Workflow operation failed.'
  )
}

function pendingInteraction(value: unknown): boolean {
  if (!record(value) || !exact(value, new Set(['interaction_id', 'iteration', 'max_iterations', 'node_id', 'type']))) {
    return false
  }

  return (
    optionalString(value.interaction_id) &&
    INTERACTION_TYPES.has(String(value.type)) &&
    optionalString(value.node_id) &&
    optionalFiniteInt(value.iteration) &&
    optionalFiniteInt(value.max_iterations)
  )
}

function retry(value: unknown): boolean {
  const keys = new Set([
    'additional_provider_attempts',
    'capped',
    'effective_total_attempts',
    'remaining_attempts',
    'requested_retries',
    'requested_total_attempts',
    'retry_consumed'
  ])

  return (
    record(value) &&
    exact(value, keys) &&
    [...keys].filter(key => key !== 'capped').every(key => finiteInt(value[key])) &&
    typeof value.capped === 'boolean'
  )
}

function costBudget(value: unknown): boolean {
  if (
    !record(value) ||
    !exact(value, new Set(['max_budget_usd', 'overage_usd', 'remaining_usd', 'settled_cost_usd', 'settlement_count']))
  ) {
    return false
  }

  return (
    ['max_budget_usd', 'overage_usd', 'remaining_usd', 'settled_cost_usd'].every(key => optionalString(value[key])) &&
    optionalFiniteInt(value.settlement_count)
  )
}

function attempt(value: unknown): value is WorkflowAttemptEvidence {
  if (
    !record(value) ||
    !exact(
      value,
      new Set([
        'attempt_id',
        'completed_at',
        'cost_budget',
        'error',
        'error_code',
        'item_type',
        'next_attempt_at',
        'node_id',
        'provider_authority',
        'retry',
        'started_at',
        'state'
      ])
    )
  ) {
    return false
  }

  return (
    value.item_type === 'attempt' &&
    typeof value.node_id === 'string' &&
    typeof value.attempt_id === 'string' &&
    NODE_STATES.has(String(value.state)) &&
    retry(value.retry) &&
    (value.error === undefined || value.error === null || closedError(value.error)) &&
    (value.error_code === undefined || ATTEMPT_ERROR_CODES.has(String(value.error_code))) &&
    optionalString(value.started_at) &&
    optionalString(value.completed_at) &&
    optionalString(value.next_attempt_at) &&
    (value.provider_authority === undefined ||
      value.provider_authority === null ||
      (record(value.provider_authority) &&
        exact(value.provider_authority, new Set(['authority_digest', 'manifest_digest'])) &&
        optionalDigest(value.provider_authority.authority_digest) &&
        optionalDigest(value.provider_authority.manifest_digest))) &&
    (value.cost_budget === undefined || value.cost_budget === null || costBudget(value.cost_budget))
  )
}

function artifact(value: unknown): value is WorkflowArtifactEvidence {
  if (
    !record(value) ||
    !exact(
      value,
      new Set([
        'attempt_id',
        'integrity_status',
        'item_type',
        'media_type',
        'node_id',
        'output_type',
        'produced_at',
        'publication_id',
        'recovery_status',
        'schema_fingerprint',
        'sha256',
        'size_bytes'
      ])
    )
  ) {
    return false
  }

  const typedPublication =
    value.integrity_status === 'verified' &&
    value.recovery_status === 'verified' &&
    typeof value.publication_id === 'string' &&
    /^[0-9a-f]{32}$/.test(value.publication_id)

  const legacyArtifact =
    value.integrity_status === 'legacy_unverified' &&
    value.recovery_status === 'projection_recovered' &&
    (value.publication_id === undefined || value.publication_id === null)

  return (
    value.item_type === 'artifact' &&
    (typedPublication || legacyArtifact) &&
    ['attempt_id', 'media_type', 'node_id', 'output_type', 'produced_at', 'schema_fingerprint', 'sha256'].every(key =>
      optionalString(value[key])
    ) &&
    optionalFiniteInt(value.size_bytes) &&
    optionalDigest(value.schema_fingerprint) &&
    optionalDigest(value.sha256)
  )
}

function event(value: unknown): value is WorkflowTimelineEvent {
  if (
    !record(value) ||
    !exact(
      value,
      new Set([
        'actor',
        'attempt_id',
        'channel',
        'decision',
        'event_type',
        'interaction_id',
        'item_type',
        'node_id',
        'outcome',
        'payload_truncated',
        'reason_code',
        'run_id',
        'sequence',
        'timestamp'
      ])
    )
  ) {
    return false
  }

  return (
    value.item_type === 'timeline_event' &&
    finiteInt(value.sequence) &&
    typeof value.timestamp === 'string' &&
    typeof value.run_id === 'string' &&
    typeof value.event_type === 'string' &&
    optionalBoolean(value.payload_truncated) &&
    ['actor', 'attempt_id', 'channel', 'decision', 'interaction_id', 'node_id', 'outcome', 'reason_code'].every(key =>
      optionalString(value[key])
    )
  )
}

function evidenceItem(value: unknown): value is WorkflowEvidenceItem {
  if (!record(value) || typeof value.item_type !== 'string') {
    return false
  }

  if (value.item_type === 'timeline_event') {
    return event(value)
  }

  if (value.item_type === 'attempt') {
    return attempt(value)
  }

  if (value.item_type === 'artifact') {
    return artifact(value)
  }

  const keys: Record<string, Set<string>> = {
    cleanup: new Set(['bytes', 'files', 'item_type', 'outcome', 'sequence']),
    coordinator: new Set(['health', 'item_type', 'status']),
    interaction: new Set([
      'actor',
      'channel',
      'decision',
      'event_type',
      'interaction_id',
      'item_type',
      'iteration',
      'max_iterations',
      'next_actions',
      'node_id',
      'outcome',
      'sequence',
      'state_version',
      'type'
    ]),
    log: new Set(['attempt_id', 'bytes_returned', 'item_type', 'node_id', 'stream', 'truncated']),
    notification: new Set(['item_type', 'kind', 'notification_id', 'state', 'transition_version']),
    output: new Set(['available', 'item_type', 'node_id']),
    recovery: new Set([
      'attempt_id',
      'cache_fingerprint_sha256',
      'item_type',
      'missing_session_sha256',
      'node_id',
      'outcome',
      'provider',
      'provider_attempts_before_recovery',
      'recovery_kind',
      'registry_generation',
      'runtime_profile',
      'source'
    ])
  }

  const allowed = keys[value.item_type]

  if (!allowed || !exact(value, allowed)) {
    return false
  }

  switch (value.item_type) {
    case 'cleanup':
      return (
        finiteInt(value.bytes) &&
        finiteInt(value.files) &&
        finiteInt(value.sequence) &&
        typeof value.outcome === 'string'
      )

    case 'coordinator':
      return typeof value.health === 'string' && typeof value.status === 'string'

    case 'interaction':
      return (
        ['actor', 'channel', 'decision', 'event_type', 'interaction_id', 'node_id', 'outcome', 'type'].every(key =>
          optionalString(value[key])
        ) &&
        optionalFiniteInt(value.iteration) &&
        optionalFiniteInt(value.max_iterations) &&
        optionalFiniteInt(value.sequence) &&
        optionalFiniteInt(value.state_version) &&
        (value.next_actions === undefined ||
          (strings(value.next_actions) && value.next_actions.every(action => ACTIONS.has(action))))
      )

    case 'log':
      return (
        typeof value.attempt_id === 'string' &&
        finiteInt(value.bytes_returned) &&
        typeof value.node_id === 'string' &&
        ['stderr', 'stdout'].includes(String(value.stream)) &&
        typeof value.truncated === 'boolean'
      )

    case 'notification':
      return (
        typeof value.kind === 'string' &&
        typeof value.notification_id === 'string' &&
        typeof value.state === 'string' &&
        finiteInt(value.transition_version)
      )

    case 'output':
      return value.available === true && typeof value.node_id === 'string'

    case 'recovery':
      return (
        typeof value.node_id === 'string' &&
        typeof value.outcome === 'string' &&
        ['persistent_session', 'process'].includes(String(value.recovery_kind)) &&
        [
          'attempt_id',
          'cache_fingerprint_sha256',
          'missing_session_sha256',
          'provider',
          'runtime_profile',
          'source'
        ].every(key => optionalString(value[key])) &&
        optionalFiniteInt(value.provider_attempts_before_recovery) &&
        optionalFiniteInt(value.registry_generation)
      )

    default:
      return false
  }
}

function node(value: unknown): boolean {
  return (
    record(value) &&
    exact(
      value,
      new Set([
        'approval_rework_attempts',
        'attempt_count',
        'attempts',
        'completed_at',
        'depends_on',
        'error',
        'id',
        'next_attempt_at',
        'pending_interaction',
        'retry_consumed',
        'started_at',
        'state'
      ])
    ) &&
    typeof value.id === 'string' &&
    NODE_STATES.has(String(value.state)) &&
    strings(value.depends_on) &&
    finiteInt(value.attempt_count) &&
    Array.isArray(value.attempts) &&
    value.attempts.every(attempt) &&
    (value.pending_interaction === undefined ||
      value.pending_interaction === null ||
      pendingInteraction(value.pending_interaction)) &&
    optionalFiniteInt(value.approval_rework_attempts) &&
    optionalFiniteInt(value.retry_consumed) &&
    optionalString(value.completed_at) &&
    optionalString(value.next_attempt_at) &&
    optionalString(value.started_at) &&
    (value.error === undefined || value.error === null || closedError(value.error))
  )
}

function coordinator(value: unknown): boolean {
  return (
    record(value) &&
    exact(value, new Set(['epoch', 'heartbeat_at', 'lease_expires_at', 'reason_code', 'status'])) &&
    typeof value.status === 'string' &&
    optionalString(value.reason_code) &&
    optionalFiniteInt(value.epoch) &&
    optionalString(value.heartbeat_at) &&
    optionalString(value.lease_expires_at)
  )
}

function provenance(value: unknown): boolean {
  return (
    record(value) &&
    exact(value, new Set(['admitted_at', 'assurance', 'source'])) &&
    PROVENANCE_SOURCES.has(String(value.source)) &&
    PROVENANCE_ASSURANCE.has(String(value.assurance)) &&
    optionalString(value.admitted_at)
  )
}

export function decodeWorkflowRun(value: unknown): null | WorkflowRunSnapshot {
  if (!record(value) || !exact(value, RUN_KEYS)) {
    return null
  }

  if (
    (value.schema_version !== undefined && value.schema_version !== 1) ||
    (value.action !== undefined && !ACTIONS.has(String(value.action))) ||
    typeof value.run_id !== 'string' ||
    typeof value.workflow !== 'string' ||
    typeof value.updated_at !== 'string' ||
    (value.status_authoritative !== undefined && typeof value.status_authoritative !== 'boolean') ||
    !finiteInt(value.state_version) ||
    !STATUSES.has(String(value.status)) ||
    !HEALTH.has(String(value.health)) ||
    !strings(value.next_actions) ||
    !value.next_actions.every(action => ACTIONS.has(action)) ||
    !record(value.progress) ||
    !exact(value.progress, new Set(['completed_nodes', 'kind', 'total_nodes'])) ||
    value.progress.kind !== 'graph' ||
    !finiteInt(value.progress.completed_nodes) ||
    !finiteInt(value.progress.total_nodes) ||
    (value.nodes !== undefined && (!record(value.nodes) || !Object.values(value.nodes).every(node))) ||
    (value.artifacts !== undefined && (!Array.isArray(value.artifacts) || !value.artifacts.every(artifact))) ||
    !optionalFiniteInt(value.attempts) ||
    (value.current_nodes !== undefined && !strings(value.current_nodes))
  ) {
    return null
  }

  if (
    value.pending_interaction !== undefined &&
    value.pending_interaction !== null &&
    !pendingInteraction(value.pending_interaction)
  ) {
    return null
  }

  if (value.last_error !== undefined && value.last_error !== null && !closedError(value.last_error)) {
    return null
  }

  if (value.coordinator !== undefined && value.coordinator !== null && !coordinator(value.coordinator)) {
    return null
  }

  if (value.provenance !== undefined && value.provenance !== null && !provenance(value.provenance)) {
    return null
  }

  if (value.warnings !== undefined && value.warnings !== null && !strings(value.warnings)) {
    return null
  }

  if (!optionalDigest(value.definition_digest) || !optionalDigest(value.provider_resolution_sha256)) {
    return null
  }

  if (
    !optionalFiniteInt(value.archive_version) ||
    !optionalFiniteInt(value.event_sequence) ||
    !optionalFiniteInt(value.queue_position)
  ) {
    return null
  }

  if (!optionalBoolean(value.restored_to_history)) {
    return null
  }

  if (
    ![
      'admission_disposition',
      'archived_at',
      'blocked_by_run_id',
      'blocking_reason',
      'completed_at',
      'created_at',
      'execution_mode',
      'last_semantic_progress_at',
      'next_retry_at',
      'presentation_state',
      'previous_node',
      'schedule_at',
      'started_at',
      'trigger',
      'workflow_version'
    ].every(key => optionalString(value[key]))
  ) {
    return null
  }

  return value as unknown as WorkflowRunSnapshot
}

export function decodeWorkflowRunPage(value: unknown): null | WorkflowRunPage {
  if (!record(value) || !exact(value, new Set(['next_cursor', 'runs', 'schema_version']))) {
    return null
  }

  if (value.schema_version !== 1 || !optionalString(value.next_cursor) || !Array.isArray(value.runs)) {
    return null
  }

  const runs = value.runs.map(decodeWorkflowRun)

  if (runs.some(run => run === null)) {
    return null
  }

  return { next_cursor: value.next_cursor as null | string, runs: runs as WorkflowRunSnapshot[], schema_version: 1 }
}

export function decodeWorkflowEventPage(value: unknown): null | WorkflowEventPage {
  if (!record(value) || !exact(value, new Set(['cursor_reset', 'events', 'next_cursor', 'schema_version']))) {
    return null
  }

  if (
    value.schema_version !== 1 ||
    typeof value.cursor_reset !== 'boolean' ||
    !finiteInt(value.next_cursor) ||
    !Array.isArray(value.events) ||
    !value.events.every(event)
  ) {
    return null
  }

  return value as unknown as WorkflowEventPage
}

export function decodeWorkflowEvidencePage(value: unknown): null | WorkflowEvidencePage {
  if (
    !record(value) ||
    !exact(value, new Set(['items', 'kind', 'next_cursor', 'schema_version', 'truncated', 'warnings']))
  ) {
    return null
  }

  if (
    value.schema_version !== 1 ||
    !EVIDENCE_KINDS.has(String(value.kind)) ||
    !finiteInt(value.next_cursor) ||
    typeof value.truncated !== 'boolean' ||
    !Array.isArray(value.items) ||
    !value.items.every(evidenceItem) ||
    (value.warnings !== undefined && !strings(value.warnings))
  ) {
    return null
  }

  return value as unknown as WorkflowEvidencePage
}

export function decodeWorkflowAttentionPage(value: unknown): null | WorkflowAttentionPage {
  if (!record(value) || !exact(value, new Set(['items', 'next_cursor', 'schema_version']))) {
    return null
  }

  if (value.schema_version !== 1 || !optionalString(value.next_cursor) || !Array.isArray(value.items)) {
    return null
  }

  const itemKeys = new Set([
    'cause',
    'health',
    'interaction',
    'kind',
    'next_actions',
    'node_id',
    'origin',
    'run_id',
    'state_version',
    'status',
    'updated_at',
    'workflow'
  ])

  const valid = value.items.every(item => {
    if (!record(item) || !exact(item, itemKeys)) {
      return false
    }

    const interaction = item.interaction

    const validInteraction =
      interaction === undefined ||
      interaction === null ||
      pendingInteraction(interaction) ||
      (record(interaction) &&
        exact(interaction, new Set(['kind', 'notification_id', 'type'])) &&
        interaction.type === 'notification' &&
        typeof interaction.kind === 'string' &&
        typeof interaction.notification_id === 'string')

    return (
      typeof item.cause === 'string' &&
      HEALTH.has(String(item.health)) &&
      typeof item.kind === 'string' &&
      strings(item.next_actions) &&
      item.next_actions.every(action => ACTIONS.has(action)) &&
      optionalString(item.node_id) &&
      typeof item.origin === 'string' &&
      typeof item.run_id === 'string' &&
      finiteInt(item.state_version) &&
      STATUSES.has(String(item.status)) &&
      typeof item.updated_at === 'string' &&
      typeof item.workflow === 'string' &&
      validInteraction
    )
  })

  return valid ? (value as unknown as WorkflowAttentionPage) : null
}

export function formatWorkflowEvidenceItem(item: WorkflowEvidenceItem): string {
  switch (item.item_type) {
    case 'attempt': {
      const error = item.error ? ` · ${item.error.code}: ${item.error.message}` : ''

      return `${item.node_id} · ${item.attempt_id} · ${item.state ?? 'unknown'}${error}`
    }

    case 'timeline_event':
      return [String(item.sequence), item.event_type, item.node_id, item.attempt_id, item.actor, item.channel]
        .filter(Boolean)
        .join(' · ')

    case 'interaction':
      return [
        item.event_type ?? item.type ?? 'interaction',
        item.node_id,
        item.interaction_id,
        item.actor,
        item.channel
      ]
        .filter(Boolean)
        .join(' · ')

    case 'artifact':
      return [
        item.publication_id,
        item.output_type,
        item.media_type,
        item.size_bytes === undefined ? null : `${item.size_bytes} bytes`
      ]
        .filter(Boolean)
        .join(' · ')

    case 'log':
      return `${item.node_id} · ${item.attempt_id} · ${item.stream} · ${item.bytes_returned} bytes`

    case 'output':
      return `${item.node_id} · output available`

    case 'cleanup':
      return `${item.outcome} · ${item.files} files · ${item.bytes} bytes`

    case 'coordinator':
      return `${item.status} · ${item.health}`

    case 'notification':
      return `${item.kind} · ${item.state}`

    case 'recovery':
      return `${item.node_id} · ${item.recovery_kind} · ${item.outcome}`
  }
}
