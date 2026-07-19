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
        {
          coalesced_count: 2,
          kind: 'failure',
          notification_id: 'notice-1',
          payload: { workflow: 'Deploy' },
          run_id: 'run-1',
          transition_version: 4
        }
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
        {
          coalesced_count: 1,
          kind: 'approval_required',
          notification_id: 'notice-2',
          payload: {},
          run_id: 'run-2',
          transition_version: 5
        }
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
        {
          coalesced_count: 1,
          kind: 'completion',
          notification_id: 'notice-3',
          payload: { workflow: 'Build' },
          run_id: 'run-3',
          transition_version: 6
        }
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
})
