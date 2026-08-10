import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $activeGatewayProfile } from '@/store/profile'
import type { PluginConfigurationDetail, PluginSetupActionRun } from '@/types/hermes'

const getPluginConfigurations = vi.fn()
const setPluginConfigurationEnabled = vi.fn()
const updatePluginConfiguration = vi.fn()
const clearPluginConfigurationSecret = vi.fn()
const refreshPluginReadiness = vi.fn()
const startPluginSetupAction = vi.fn()
const getPluginSetupAction = vi.fn()
const cancelPluginSetupAction = vi.fn()

vi.mock('@/hermes', () => ({
  setApiRequestProfile: vi.fn(),
  getPluginConfigurations: () => getPluginConfigurations(),
  setPluginConfigurationEnabled: (pluginId: string, enabled: boolean) =>
    setPluginConfigurationEnabled(pluginId, enabled),
  updatePluginConfiguration: (pluginId: string, body: unknown) => updatePluginConfiguration(pluginId, body),
  clearPluginConfigurationSecret: (pluginId: string, fieldId: string) =>
    clearPluginConfigurationSecret(pluginId, fieldId),
  refreshPluginReadiness: (pluginId: string) => refreshPluginReadiness(pluginId),
  startPluginSetupAction: (pluginId: string, actionId: string) => startPluginSetupAction(pluginId, actionId),
  getPluginSetupAction: (runId: string) => getPluginSetupAction(runId),
  cancelPluginSetupAction: (runId: string) => cancelPluginSetupAction(runId)
}))

vi.mock('@/store/notifications', () => ({
  notify: vi.fn(),
  notifyError: vi.fn()
}))

function detail(overrides: Partial<PluginConfigurationDetail> = {}): PluginConfigurationDetail {
  return {
    plugin_id: 'sample-connector',
    version: 1,
    enabled: false,
    readiness: { plugin_id: 'sample-connector', ready: false, reasons: ['plugin_not_enabled'] },
    fields: [
      {
        id: 'endpoint',
        label: 'Endpoint',
        type: 'string',
        storage: 'setting',
        required: true,
        advanced: false,
        readiness: true,
        documentation_url: 'https://docs.example.test/endpoint',
        value: 'https://git.example.test'
      },
      {
        id: 'token',
        label: 'Access token',
        type: 'string',
        storage: 'secret',
        required: true,
        advanced: false,
        readiness: true,
        is_set: true
      }
    ],
    setup_actions: [
      {
        id: 'connect',
        label: 'Connect',
        interactive: false,
        available: false,
        documentation_url: 'https://docs.example.test/connect'
      }
    ],
    ...overrides
  }
}

beforeEach(() => {
  $activeGatewayProfile.set('default')
  getPluginConfigurations.mockResolvedValue([detail()])
  setPluginConfigurationEnabled.mockImplementation(async (_pluginId: string, enabled: boolean) =>
    detail({ enabled, readiness: { plugin_id: 'sample-connector', ready: false, reasons: [] } })
  )
  updatePluginConfiguration.mockResolvedValue(detail())
  clearPluginConfigurationSecret.mockResolvedValue(
    detail({
      fields: detail().fields.map(field => (field.id === 'token' ? { ...field, is_set: false } : field))
    })
  )
  refreshPluginReadiness.mockResolvedValue({
    plugin_id: 'sample-connector',
    ready: false,
    reasons: ['authentication_required:token']
  })
  startPluginSetupAction.mockResolvedValue({
    run_id: 'run-one',
    plugin_id: 'sample-connector',
    action: 'connect',
    status: 'running'
  })
  getPluginSetupAction.mockResolvedValue({
    run_id: 'run-one',
    plugin_id: 'sample-connector',
    action: 'connect',
    status: 'running'
  })
  cancelPluginSetupAction.mockResolvedValue({
    run_id: 'run-one',
    plugin_id: 'sample-connector',
    action: 'connect',
    status: 'cancelled'
  })
})

afterEach(() => {
  cleanup()
  $activeGatewayProfile.set('default')
  vi.clearAllMocks()
})

describe('PluginToolsetConfigPanel', () => {
  it('shows disabled standalone connectors and enables them through the profile-scoped API', async () => {
    const { PluginToolsetConfigPanel } = await import('./plugin-toolset-config-panel')
    render(<PluginToolsetConfigPanel />)

    expect(await screen.findByText('sample-connector')).toBeTruthy()
    expect(screen.getByText('Disabled')).toBeTruthy()
    fireEvent.click(screen.getByRole('switch', { name: 'Enable sample-connector' }))

    await waitFor(() => expect(setPluginConfigurationEnabled).toHaveBeenCalledWith('sample-connector', true))
  })

  it('writes settings and secrets separately without requesting, retaining, or rendering a secret value', async () => {
    const { PluginToolsetConfigPanel } = await import('./plugin-toolset-config-panel')
    const { container } = render(<PluginToolsetConfigPanel />)

    const endpoint = await screen.findByLabelText('Endpoint')
    fireEvent.change(endpoint, { target: { value: 'https://new.example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save Endpoint' }))
    await waitFor(() =>
      expect(updatePluginConfiguration).toHaveBeenCalledWith('sample-connector', {
        settings: { endpoint: 'https://new.example.test' }
      })
    )

    const token = screen.getByLabelText('Access token') as HTMLInputElement
    expect(token.type).toBe('password')
    expect(token.value).toBe('')
    expect(container.textContent).not.toContain('credential-value')
    fireEvent.change(token, { target: { value: 'credential-value' } })
    fireEvent.click(screen.getByRole('button', { name: 'Set Access token' }))
    await waitFor(() =>
      expect(updatePluginConfiguration).toHaveBeenCalledWith('sample-connector', {
        secrets: { token: 'credential-value' }
      })
    )
    await waitFor(() => expect(token.value).toBe(''))
    expect(container.textContent).not.toContain('credential-value')
    expect(getPluginConfigurations).toHaveBeenCalledTimes(1)
  })

  it('clears a write-only secret and refreshes only backend-authored readiness', async () => {
    const { PluginToolsetConfigPanel } = await import('./plugin-toolset-config-panel')
    render(<PluginToolsetConfigPanel />)
    await screen.findByText('sample-connector')

    fireEvent.click(screen.getByRole('button', { name: 'Clear Access token' }))
    await waitFor(() => expect(clearPluginConfigurationSecret).toHaveBeenCalledWith('sample-connector', 'token'))

    fireEvent.click(screen.getByRole('button', { name: 'Refresh readiness for sample-connector' }))
    await waitFor(() => expect(refreshPluginReadiness).toHaveBeenCalledWith('sample-connector'))
    expect(await screen.findByText('Authentication required: token')).toBeTruthy()
  })

  it('renders safe documentation links and drops unsafe URLs without navigation side effects', async () => {
    getPluginConfigurations.mockResolvedValue([
      detail({
        fields: [detail().fields[0], { ...detail().fields[1], documentation_url: 'javascript:alert(1)' }]
      })
    ])
    const { PluginToolsetConfigPanel } = await import('./plugin-toolset-config-panel')
    render(<PluginToolsetConfigPanel />)

    const docs = await screen.findAllByRole('link', { name: 'Docs' })
    expect(docs).toHaveLength(2)
    expect(docs.every(link => link.getAttribute('href')?.startsWith('https://'))).toBe(true)
    expect(globalThis.document.activeElement).toBe(globalThis.document.body)
  })

  it('fails unknown field types non-destructively while keeping supported fields usable', async () => {
    getPluginConfigurations.mockResolvedValue([
      detail({
        fields: [
          { ...detail().fields[0], id: 'future', label: 'Future field', type: 'future-type' },
          detail().fields[1]
        ]
      })
    ])
    const { PluginToolsetConfigPanel } = await import('./plugin-toolset-config-panel')
    render(<PluginToolsetConfigPanel />)

    expect(await screen.findByText('Future field cannot be edited in this version.')).toBeTruthy()
    expect(screen.getByLabelText('Access token')).toBeTruthy()
  })

  it('starts, refreshes, and cancels setup actions and reports cancellation honestly', async () => {
    getPluginConfigurations.mockResolvedValue([
      detail({ enabled: true, setup_actions: [{ ...detail().setup_actions![0], available: true }] })
    ])
    const { PluginToolsetConfigPanel } = await import('./plugin-toolset-config-panel')
    render(<PluginToolsetConfigPanel />)
    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }))
    await waitFor(() => expect(startPluginSetupAction).toHaveBeenCalledWith('sample-connector', 'connect'))

    fireEvent.click(await screen.findByRole('button', { name: 'Check setup status' }))
    await waitFor(() => expect(getPluginSetupAction).toHaveBeenCalledWith('run-one'))

    fireEvent.click(screen.getByRole('button', { name: 'Cancel setup' }))
    await waitFor(() => expect(cancelPluginSetupAction).toHaveBeenCalledWith('run-one'))
    expect(await screen.findByText('Setup cancelled')).toBeTruthy()
  })

  it.each(['failed', 'timed_out'] as const)(
    'surfaces unsuccessful %s setup status without claiming success',
    async status => {
      const terminal: PluginSetupActionRun = {
        run_id: 'run-one',
        plugin_id: 'sample-connector',
        action: 'connect',
        status,
        error: status === 'failed' ? 'setup action failed' : 'setup action deadline exceeded'
      }

      getPluginConfigurations.mockResolvedValue([
        detail({ enabled: true, setup_actions: [{ ...detail().setup_actions![0], available: true }] })
      ])
      startPluginSetupAction.mockResolvedValue(terminal)
      const { PluginToolsetConfigPanel } = await import('./plugin-toolset-config-panel')
      render(<PluginToolsetConfigPanel />)

      fireEvent.click(await screen.findByRole('button', { name: 'Connect' }))
      expect(await screen.findByText(status === 'failed' ? 'Setup failed' : 'Setup timed out')).toBeTruthy()
      expect(screen.queryByText('Setup complete')).toBeNull()
    }
  )

  it('prevents an older enable response from overwriting newer disable intent', async () => {
    let resolveEnable: (value: PluginConfigurationDetail) => void = () => undefined
    setPluginConfigurationEnabled
      .mockImplementationOnce(
        () =>
          new Promise(resolve => {
            resolveEnable = resolve
          })
      )
      .mockResolvedValueOnce(detail({ enabled: false }))
    const { PluginToolsetConfigPanel } = await import('./plugin-toolset-config-panel')
    render(<PluginToolsetConfigPanel />)
    await screen.findByText('sample-connector')

    fireEvent.click(screen.getByRole('switch', { name: 'Enable sample-connector' }))
    fireEvent.click(screen.getByRole('switch', { name: 'Disable sample-connector' }))
    await waitFor(() => expect(setPluginConfigurationEnabled).toHaveBeenCalledTimes(2))
    resolveEnable(detail({ enabled: true }))

    await waitFor(() => expect(screen.getByText('Disabled')).toBeTruthy())
    expect(screen.queryByText('Enabled')).toBeNull()
  })

  it('prevents an older field response from overwriting a newer plugin mutation', async () => {
    let resolveSetting: (value: PluginConfigurationDetail) => void = () => undefined
    updatePluginConfiguration.mockImplementationOnce(
      () =>
        new Promise(resolve => {
          resolveSetting = resolve
        })
    )
    setPluginConfigurationEnabled.mockResolvedValueOnce(detail({ enabled: true }))
    const { PluginToolsetConfigPanel } = await import('./plugin-toolset-config-panel')
    render(<PluginToolsetConfigPanel />)

    fireEvent.change(await screen.findByLabelText('Endpoint'), {
      target: { value: 'https://new.example.test' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save Endpoint' }))
    fireEvent.click(screen.getByRole('switch', { name: 'Enable sample-connector' }))
    await waitFor(() => expect(setPluginConfigurationEnabled).toHaveBeenCalledTimes(1))
    resolveSetting(detail({ enabled: false }))

    await waitFor(() => expect(screen.getByText('Enabled')).toBeTruthy())
    expect(screen.queryByText('Disabled')).toBeNull()
  })

  it('discards settings, unsaved secrets, and setup run state synchronously when the active profile changes', async () => {
    getPluginConfigurations.mockResolvedValueOnce([
      detail({ enabled: true, setup_actions: [{ ...detail().setup_actions![0], available: true }] })
    ])
    const { PluginToolsetConfigPanel } = await import('./plugin-toolset-config-panel')
    render(<PluginToolsetConfigPanel />)

    expect(await screen.findByDisplayValue('https://git.example.test')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Access token'), { target: { value: 'unsaved-secret' } })
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(await screen.findByRole('button', { name: 'Cancel setup' })).toBeTruthy()

    getPluginConfigurations.mockResolvedValueOnce([
      detail({
        fields: detail().fields.map(field =>
          field.id === 'endpoint' ? { ...field, value: 'https://profile-b.example.test' } : field
        ),
        setup_actions: [{ ...detail().setup_actions![0], available: true }]
      })
    ])
    act(() => $activeGatewayProfile.set('profile-b'))

    expect(screen.queryByDisplayValue('unsaved-secret')).toBeNull()
    expect(screen.queryByDisplayValue('https://git.example.test')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Cancel setup' })).toBeNull()
    expect(await screen.findByDisplayValue('https://profile-b.example.test')).toBeTruthy()
    expect((screen.getByLabelText('Access token') as HTMLInputElement).value).toBe('')
    expect(screen.getByRole('button', { name: 'Connect' })).toBeTruthy()
  })
})
