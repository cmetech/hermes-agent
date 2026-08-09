// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'

const lease = vi.fn()
const ack = vi.fn()
const fail = vi.fn()
const project = vi.fn()

vi.mock('@/hermes', () => ({
  acknowledgeWorkflowNotification: (...args: unknown[]) => ack(...args),
  failWorkflowNotification: (...args: unknown[]) => fail(...args),
  leaseWorkflowNotifications: (...args: unknown[]) => lease(...args)
}))
vi.mock('./native-notifications', () => ({
  projectNativeNotification: (...args: unknown[]) => project(...args)
}))

function notification(overrides: Record<string, unknown> = {}) {
  const transitionVersion = typeof overrides.transition_version === 'number' ? overrides.transition_version : 1

  return {
    attempts: 1,
    coalesced_count: 1,
    created_at: '2026-08-08T18:00:00+00:00',
    destination: 'desktop',
    kind: 'completion',
    notification_id: 'notice-default',
    payload: {
      event_type: 'run_succeeded',
      next_actions: ['status', 'events', 'archive'],
      payload_type: 'workflow_transition',
      state_version: transitionVersion,
      status: 'succeeded',
      workflow: 'Build'
    },
    run_id: 'run-default',
    state: 'leased',
    transition_version: transitionVersion,
    updated_at: '2026-08-08T18:00:00+00:00',
    ...overrides
  }
}

describe('workflow notification delivery', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    localStorage.clear()
    ack.mockResolvedValue({})
    fail.mockResolvedValue({})
  })

  it('acknowledges only after Electron projection resolves', async () => {
    let resolveProjection!: (value: 'projected') => void

    const pending = new Promise<'projected'>(resolve => {
      resolveProjection = resolve
    })

    project.mockReturnValue(pending)
    lease.mockResolvedValue({
      items: [
        notification({
          coalesced_count: 2,
          kind: 'failure',
          notification_id: 'notice-1',
          payload: {
            event_type: 'run_failed',
            next_actions: ['status', 'events', 'resume', 'retry', 'abandon'],
            payload_type: 'workflow_transition',
            state_version: 4,
            status: 'failed',
            workflow: 'Deploy'
          },
          run_id: 'run-1',
          transition_version: 4
        })
      ],
      schema_version: 1
    })
    const { deliverWorkflowNotificationsOnce } = await import('./workflow-notifications')
    const delivery = deliverWorkflowNotificationsOnce('electron-stable')
    await Promise.resolve()
    expect(ack).not.toHaveBeenCalled()

    resolveProjection('projected')
    await delivery

    expect(project).toHaveBeenCalledWith(expect.objectContaining({ global: true, kind: 'turnError' }))
    expect(ack).toHaveBeenCalledWith('notice-1', 'electron-stable')
  })

  it('records a failed projection without acknowledging delivery', async () => {
    lease.mockResolvedValue({
      items: [
        notification({
          coalesced_count: 1,
          kind: 'approval_required',
          notification_id: 'notice-2',
          payload: {
            next_actions: ['status', 'events'],
            payload_type: 'workflow_transition',
            state_version: 5
          },
          run_id: 'run-2',
          transition_version: 5
        })
      ],
      schema_version: 1
    })
    project.mockRejectedValue(new Error('ipc unavailable'))
    const { deliverWorkflowNotificationsOnce } = await import('./workflow-notifications')

    await deliverWorkflowNotificationsOnce('electron-stable')

    expect(ack).not.toHaveBeenCalled()
    expect(fail).toHaveBeenCalledWith('notice-2', 'electron-stable', 'ipc unavailable')
  })

  it('retries a lost server receipt without projecting a duplicate toast', async () => {
    lease.mockResolvedValue({
      items: [
        notification({
          coalesced_count: 1,
          kind: 'completion',
          notification_id: 'notice-3',
          payload: {
            event_type: 'run_succeeded',
            next_actions: ['status', 'events', 'archive'],
            payload_type: 'workflow_transition',
            state_version: 6,
            status: 'succeeded',
            workflow: 'Build'
          },
          run_id: 'run-3',
          transition_version: 6
        })
      ],
      schema_version: 1
    })
    project.mockResolvedValue('projected')
    ack.mockRejectedValueOnce(new Error('receipt connection lost')).mockResolvedValueOnce({})
    const { deliverWorkflowNotificationsOnce } = await import('./workflow-notifications')

    await deliverWorkflowNotificationsOnce('electron-stable')
    await deliverWorkflowNotificationsOnce('electron-stable')

    expect(project).toHaveBeenCalledTimes(1)
    expect(ack).toHaveBeenCalledTimes(2)
    expect(fail).not.toHaveBeenCalled()
  })

  it('rejects malformed notification payloads before store or native projection', async () => {
    lease.mockResolvedValue({
      items: [
        notification({
          coalesced_count: 1,
          kind: 'made_up_kind',
          notification_id: 'notice-malformed',
          payload: {
            payload_type: 'workflow_transition',
            workflow: 'Deploy',
            status: 'failed',
            event_type: 'run_failed',
            state_version: 7,
            next_actions: ['status'],
            provider_response: 'PROMPT_COMMAND_PROVIDER_PAYLOAD_FEEDBACK_CANARY_20260808'
          },
          run_id: 'run-malformed',
          transition_version: 7
        })
      ],
      schema_version: 1
    })
    const { deliverWorkflowNotificationsOnce } = await import('./workflow-notifications')

    await expect(deliverWorkflowNotificationsOnce('electron-stable')).resolves.toBe(0)

    expect(project).not.toHaveBeenCalled()
    expect(ack).not.toHaveBeenCalled()
    expect(fail).not.toHaveBeenCalled()
  })
})
