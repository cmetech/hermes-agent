// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setApiRequestProfile } from '@/hermes'

interface WorkflowApiModule {
  listWorkflowDefinitions: (profile?: string | null) => Promise<unknown>
  preflightWorkflow: (
    name: string,
    source: 'profile' | 'project' | 'showcase',
    profile?: string | null
  ) => Promise<unknown>
  startWorkflowRun: (
    request: {
      catalogSource: 'profile' | 'project' | 'showcase'
      concurrencyPolicy: 'allow' | 'forbid' | 'queue'
      idempotencyKey: string
      scheduleAt?: string
      values: Record<string, string>
      workflow: string
    },
    profile?: string | null
  ) => Promise<unknown>
}

async function workflowApi(): Promise<WorkflowApiModule> {
  return import('./hermes-api')
}

describe('workflow catalog authenticated API', () => {
  let apiStructured: ReturnType<typeof vi.fn>

  beforeEach(() => {
    apiStructured = vi.fn().mockResolvedValue({
      ok: true,
      value: { items: [], truncated: false }
    })
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { apiStructured }
    })
  })

  afterEach(() => {
    setApiRequestProfile(null)
    vi.restoreAllMocks()
    Reflect.deleteProperty(window, 'hermesDesktop')
  })

  it('lists definitions through the structured authenticated profile channel', async () => {
    const catalog = { items: [], truncated: false }
    apiStructured.mockResolvedValue({ ok: true, value: catalog })
    setApiRequestProfile('remote-profile')
    const { listWorkflowDefinitions } = await workflowApi()

    await expect(listWorkflowDefinitions()).resolves.toBe(catalog)
    expect(apiStructured).toHaveBeenCalledWith({
      path: '/api/plugins/workflow/workflows',
      profile: 'remote-profile'
    })
  })

  it('rejects a malformed backend structured-output catalog projection', async () => {
    apiStructured.mockResolvedValue({
      ok: true,
      value: {
        items: [
          {
            name: 'structured',
            structured_output_capability: {
              mixed: false,
              private_extra: 'rejected',
              summaries: [],
              summaries_truncated: false,
              summary_count: 1
            }
          }
        ],
        truncated: false
      }
    })
    const { listWorkflowDefinitions } = await workflowApi()

    await expect(listWorkflowDefinitions()).rejects.toThrow(
      'Workflow structured-output capability projection is invalid.'
    )
  })

  it('uses an explicitly captured profile instead of mutable ambient profile state', async () => {
    setApiRequestProfile('profile-b')
    const { listWorkflowDefinitions, preflightWorkflow, startWorkflowRun } = await workflowApi()

    await listWorkflowDefinitions('profile-a')
    await preflightWorkflow('deploy', 'project', 'profile-a')
    await startWorkflowRun(
      {
        catalogSource: 'project',
        concurrencyPolicy: 'queue',
        idempotencyKey: 'captured-profile',
        values: {},
        workflow: 'deploy'
      },
      'profile-a'
    )

    expect(apiStructured.mock.calls.map(([request]) => request.profile)).toEqual([
      'profile-a',
      'profile-a',
      'profile-a'
    ])
  })

  it('preflights an encoded workflow name with GET semantics', async () => {
    const detail = { name: 'deploy safe' }
    apiStructured.mockResolvedValue({ ok: true, value: detail })
    const { preflightWorkflow } = await workflowApi()

    await expect(preflightWorkflow('deploy safe/blue', 'showcase')).resolves.toBe(detail)
    expect(apiStructured).toHaveBeenCalledWith({
      path: '/api/plugins/workflow/workflows/deploy%20safe%2Fblue?catalog_source=showcase'
    })
  })

  it('starts a run with the exact snake-case server body and no provenance fields', async () => {
    const response = { error: null, ok: true, result: { run_id: 'run-1' }, schema_version: 1 }
    apiStructured.mockResolvedValue({ ok: true, value: response })
    setApiRequestProfile('writer')
    const { startWorkflowRun } = await workflowApi()

    await expect(
      startWorkflowRun({
        catalogSource: 'showcase',
        concurrencyPolicy: 'forbid',
        idempotencyKey: 'desktop-request-1',
        values: { subject: 'safe' },
        workflow: 'deploy'
      })
    ).resolves.toBe(response)

    expect(apiStructured).toHaveBeenCalledWith({
      body: {
        catalog_source: 'showcase',
        concurrency_policy: 'forbid',
        idempotency_key: 'desktop-request-1',
        values: { subject: 'safe' },
        workflow: 'deploy'
      },
      method: 'POST',
      path: '/api/plugins/workflow/runs',
      profile: 'writer'
    })
  })

  it('adds only the canonical schedule field for a one-shot scheduled run', async () => {
    const { startWorkflowRun } = await workflowApi()

    await startWorkflowRun({
      catalogSource: 'profile',
      concurrencyPolicy: 'queue',
      idempotencyKey: 'scheduled-desktop-request',
      scheduleAt: '2099-01-02T03:04:05Z',
      values: {},
      workflow: 'deploy'
    })

    expect(apiStructured).toHaveBeenCalledWith({
      body: {
        catalog_source: 'profile',
        concurrency_policy: 'queue',
        idempotency_key: 'scheduled-desktop-request',
        schedule_at: '2099-01-02T03:04:05Z',
        values: {},
        workflow: 'deploy'
      },
      method: 'POST',
      path: '/api/plugins/workflow/runs'
    })
  })

  it.each(['', '   '])('refuses to send a run with an empty idempotency key (%j)', async idempotencyKey => {
    const { startWorkflowRun } = await workflowApi()

    await expect(
      startWorkflowRun({
        catalogSource: 'project',
        concurrencyPolicy: 'queue',
        idempotencyKey,
        values: {},
        workflow: 'deploy'
      })
    ).rejects.toThrow(/idempotencyKey/)
    expect(apiStructured).not.toHaveBeenCalled()
  })

  it('preserves a FastAPI detail code on a 409 response', async () => {
    apiStructured.mockResolvedValue({
      ok: false,
      status: 409,
      body: {
        detail: { code: 'idempotency_conflict', message: 'Already admitted', retryable: false }
      }
    })
    const { startWorkflowRun } = await workflowApi()

    await expect(
      startWorkflowRun({
        catalogSource: 'project',
        concurrencyPolicy: 'queue',
        idempotencyKey: 'duplicate',
        values: {},
        workflow: 'deploy'
      })
    ).rejects.toMatchObject({
      code: 'idempotency_conflict',
      message: 'Already admitted',
      retryable: false,
      status: 409
    })
  })

  it('preserves a plugin error code on a retryable 503 response', async () => {
    apiStructured.mockResolvedValue({
      ok: false,
      status: 503,
      body: {
        error: { code: 'workflow_catalog_unavailable', message: 'Try again', retryable: true }
      }
    })
    const { listWorkflowDefinitions } = await workflowApi()

    await expect(listWorkflowDefinitions()).rejects.toMatchObject({
      code: 'workflow_catalog_unavailable',
      message: 'Try again',
      retryable: true,
      status: 503
    })
  })

  it('prefers detail over error and uses all fields from the selected envelope', async () => {
    apiStructured.mockResolvedValue({
      ok: false,
      status: 409,
      body: {
        detail: { code: 'detail_code', message: 'Detail message', retryable: false },
        error: { code: 'error_code', message: 'Error message', retryable: true }
      }
    })
    const { preflightWorkflow } = await workflowApi()

    await expect(preflightWorkflow('deploy', 'project')).rejects.toMatchObject({
      code: 'detail_code',
      message: 'Detail message',
      retryable: false
    })
  })

  it('uses the error envelope when detail has no code, then falls back to status', async () => {
    const { preflightWorkflow } = await workflowApi()

    apiStructured.mockResolvedValueOnce({
      ok: false,
      status: 409,
      body: {
        detail: { message: 'Incomplete detail' },
        error: { code: 'plugin_conflict', message: 'Plugin conflict', retryable: false }
      }
    })
    await expect(preflightWorkflow('deploy', 'project')).rejects.toMatchObject({ code: 'plugin_conflict' })

    apiStructured.mockResolvedValueOnce({ ok: false, status: 429, body: { detail: { message: 'Slow down' } } })
    await expect(preflightWorkflow('deploy', 'project')).rejects.toMatchObject({
      code: '429',
      message: 'HTTP 429',
      retryable: false,
      status: 429
    })
  })

  it('propagates structured-channel transport failures without parsing message text', async () => {
    const transportError = new Error('503: {"detail":{"code":"must_not_parse"}}')
    apiStructured.mockRejectedValue(transportError)
    const { listWorkflowDefinitions } = await workflowApi()

    try {
      await listWorkflowDefinitions()
      throw new Error('expected transport rejection')
    } catch (error) {
      expect(error).toBe(transportError)
      expect(error).not.toHaveProperty('code')
    }
  })
})
