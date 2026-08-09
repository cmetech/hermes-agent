import { getApiRequestProfile } from '@/hermes'
import { isWorkflowStructuredOutputCapabilitySummary } from '@/lib/workflow-public-codec'
import type { WorkflowCatalogPage, WorkflowCatalogSource, WorkflowDetail, WorkflowStartResponse } from '@/types/hermes'

export interface StartWorkflowRunRequest {
  catalogSource: WorkflowCatalogSource
  concurrencyPolicy: 'allow' | 'forbid' | 'queue'
  idempotencyKey: string
  scheduleAt?: string
  values: Record<string, string>
  workflow: string
}

interface ServerErrorFields {
  code: string
  message?: string
  retryable?: boolean
}

export class WorkflowApiError extends Error {
  readonly body: unknown
  readonly code: string
  readonly retryable: boolean
  readonly status: number

  constructor(response: { body: unknown; status: number }, fields?: ServerErrorFields) {
    super(fields?.message || `HTTP ${response.status}`)
    this.name = 'WorkflowApiError'
    this.body = response.body
    this.code = fields?.code || String(response.status)
    this.retryable = fields?.retryable ?? response.status >= 500
    this.status = response.status
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null
}

function serverErrorFields(body: unknown): ServerErrorFields | undefined {
  const record = asRecord(body)

  for (const key of ['detail', 'error']) {
    const envelope = asRecord(record?.[key])

    if (typeof envelope?.code !== 'string' || !envelope.code) {
      continue
    }

    return {
      code: envelope.code,
      ...(typeof envelope.message === 'string' && envelope.message ? { message: envelope.message } : {}),
      ...(typeof envelope.retryable === 'boolean' ? { retryable: envelope.retryable } : {})
    }
  }

  return undefined
}

function profileScoped(profile: string | null = getApiRequestProfile()): { profile?: string } {
  return profile ? { profile } : {}
}

async function requestWorkflowApi<T>(request: Parameters<Window['hermesDesktop']['apiStructured']>[0]): Promise<T> {
  const response = await window.hermesDesktop.apiStructured<T>(request)

  if (response.ok) {
    return response.value
  }

  throw new WorkflowApiError(response, serverErrorFields(response.body))
}

export async function listWorkflowDefinitions(
  profile: string | null = getApiRequestProfile()
): Promise<WorkflowCatalogPage> {
  const catalog = await requestWorkflowApi<WorkflowCatalogPage>({
    path: '/api/plugins/workflow/workflows',
    ...profileScoped(profile)
  })

  for (const item of catalog.items) {
    if ('error' in item || item.structured_output_capability === undefined) {
      continue
    }

    if (!isWorkflowStructuredOutputCapabilitySummary(item.structured_output_capability)) {
      throw new TypeError('Workflow structured-output capability projection is invalid.')
    }
  }

  return catalog
}

export function preflightWorkflow(
  name: string,
  source: WorkflowCatalogSource,
  profile: string | null = getApiRequestProfile()
): Promise<WorkflowDetail> {
  const query = new URLSearchParams({ catalog_source: source })

  return requestWorkflowApi<WorkflowDetail>({
    path: `/api/plugins/workflow/workflows/${encodeURIComponent(name)}?${query}`,
    ...profileScoped(profile)
  })
}

export async function startWorkflowRun(
  request: StartWorkflowRunRequest,
  profile: string | null = getApiRequestProfile()
): Promise<WorkflowStartResponse> {
  if (!request.idempotencyKey.trim()) {
    throw new TypeError('idempotencyKey must not be empty')
  }

  return requestWorkflowApi<WorkflowStartResponse>({
    body: {
      catalog_source: request.catalogSource,
      concurrency_policy: request.concurrencyPolicy,
      idempotency_key: request.idempotencyKey,
      ...(request.scheduleAt ? { schedule_at: request.scheduleAt } : {}),
      values: request.values,
      workflow: request.workflow
    },
    method: 'POST',
    path: '/api/plugins/workflow/runs',
    ...profileScoped(profile)
  })
}
