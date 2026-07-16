import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

// Radix Select calls scrollIntoView on its items when the content opens; jsdom
// doesn't implement it (nor hasPointerCapture / releasePointerCapture), so stub
// them to let the dropdown open in tests.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

const getGlobalModelInfo = vi.fn()
const getGlobalModelOptions = vi.fn()
const getAuxiliaryModels = vi.fn()
const getMoaModels = vi.fn()
const setModelAssignment = vi.fn()
const getRecommendedDefaultModel = vi.fn()
const saveMoaModels = vi.fn()
const setEnvVar = vi.fn()
const getHermesConfigRecord = vi.fn()
const saveHermesConfig = vi.fn()
const startManualProviderOAuth = vi.fn()
const startManualLocalEndpoint = vi.fn()
const profileSwitch = vi.hoisted(() => ({ callback: null as null | (() => void) }))

vi.mock('@/hermes', () => ({
  getGlobalModelInfo: () => getGlobalModelInfo(),
  getGlobalModelOptions: (options?: unknown) => getGlobalModelOptions(options),
  getAuxiliaryModels: () => getAuxiliaryModels(),
  getMoaModels: () => getMoaModels(),
  setModelAssignment: (body: unknown) => setModelAssignment(body),
  getRecommendedDefaultModel: (slug: string) => getRecommendedDefaultModel(slug),
  saveMoaModels: (body: unknown) => saveMoaModels(body),
  setEnvVar: (key: string, value: string) => setEnvVar(key, value),
  getHermesConfigRecord: () => getHermesConfigRecord(),
  saveHermesConfig: (config: unknown) => saveHermesConfig(config)
}))

vi.mock('@/store/onboarding', () => ({
  startManualLocalEndpoint: () => startManualLocalEndpoint(),
  startManualProviderOAuth: (slug: string) => startManualProviderOAuth(slug)
}))

vi.mock('../hooks/use-on-profile-switch', () => ({
  useOnProfileSwitch: (callback: () => void) => {
    profileSwitch.callback = callback
  }
}))

vi.mock('../hooks/use-config-record', () => ({
  invalidateHermesConfig: vi.fn(),
  setHermesConfigCache: vi.fn(),
  useHermesConfigRecord: () => {
    getHermesConfigRecord()

    return { data: { agent: { reasoning_effort: 'medium', service_tier: 'normal' } } }
  }
}))

beforeEach(() => {
  profileSwitch.callback = null
  getGlobalModelInfo.mockResolvedValue({ provider: 'nous', model: 'hermes-4' })
  getGlobalModelOptions.mockResolvedValue({
    providers: [
      {
        name: 'Nous',
        slug: 'nous',
        models: ['hermes-4', 'hermes-4-mini'],
        authenticated: true,
        capabilities: { 'hermes-4': { reasoning: true, fast: true } }
      }
    ]
  })
  getAuxiliaryModels.mockResolvedValue({
    main: { provider: 'nous', model: 'hermes-4' },
    tasks: [{ task: 'vision', provider: 'auto', model: '', base_url: '' }]
  })
  getMoaModels.mockResolvedValue(null)
  setModelAssignment.mockResolvedValue({ provider: 'nous', model: 'hermes-4', gateway_tools: [] })
  getRecommendedDefaultModel.mockResolvedValue({ provider: 'nous', model: 'hermes-4', free_tier: null })
  setEnvVar.mockResolvedValue({ ok: true })
  getHermesConfigRecord.mockResolvedValue({ agent: { reasoning_effort: 'medium', service_tier: 'normal' } })
  saveHermesConfig.mockResolvedValue({ ok: true })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderModelSettings() {
  const { ModelSettings } = await import('./model-settings')

  return render(<ModelSettings />)
}

const ALL_SUPPORTED = {
  completion: 'supported',
  reasoning: 'supported',
  tools: 'supported',
  vision: 'supported'
} as const

function gatewayProvider(overrides: Record<string, unknown> = {}): {
  authenticated: boolean
  capability_status: string
  capabilities: Record<string, unknown>
  key_env: string
  models: string[]
  name: string
  slug: string
} {
  return {
    authenticated: true,
    capability_status: 'ready',
    capabilities: {
      'gateway-good': {
        evidence: {
          tools: {
            reference: 'https://evidence.example/private-catalog'
          }
        },
        fast: false,
        reasoning: true,
        selection_mode: 'explicit',
        verified: ALL_SUPPORTED
      },
      'gateway-no-tools': {
        fast: false,
        reasoning: true,
        selection_mode: 'explicit',
        verified: { ...ALL_SUPPORTED, tools: 'unsupported' }
      },
      'gateway-tools-unknown': {
        fast: false,
        reasoning: true,
        selection_mode: 'explicit',
        verified: { ...ALL_SUPPORTED, reasoning: 'unknown', tools: 'unknown' }
      },
      'gateway-no-vision': {
        fast: false,
        reasoning: true,
        selection_mode: 'explicit',
        verified: { ...ALL_SUPPORTED, vision: 'unsupported' }
      }
    },
    key_env: 'OTTO_API_KEY',
    models: ['gateway-good', 'gateway-no-tools', 'gateway-tools-unknown', 'gateway-no-vision'],
    name: 'Gateway',
    slug: 'gateway',
    ...overrides
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(res => {
    resolve = res
  })

  return { promise, resolve }
}

describe('ModelSettings', () => {
  it('loads the current main model and lists configured providers only', async () => {
    await renderModelSettings()

    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalled())
    await waitFor(() => expect(getGlobalModelOptions).toHaveBeenCalled())

    // Open the provider Select — only configured providers should be listed.
    const triggers = await screen.findAllByRole('combobox')
    fireEvent.click(triggers[0])

    // "Nous" shows in both the trigger and the open list.
    expect((await screen.findAllByText('Nous')).length).toBeGreaterThan(0)
    expect(screen.queryByText(/DeepSeek/)).toBeNull()
  })

  it('writes the profile default speed (service_tier) when the fast switch is toggled', async () => {
    await renderModelSettings()
    await waitFor(() => expect(getHermesConfigRecord).toHaveBeenCalled())

    const fastSwitch = await screen.findByRole('switch')
    fireEvent.click(fastSwitch)

    await waitFor(() =>
      expect(saveHermesConfig).toHaveBeenCalledWith(
        expect.objectContaining({ agent: expect.objectContaining({ service_tier: 'fast' }) })
      )
    )
  })

  it('hides the reasoning/speed defaults when the main model reports no capabilities', async () => {
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        {
          name: 'Nous',
          slug: 'nous',
          models: ['hermes-4'],
          authenticated: true,
          capabilities: { 'hermes-4': { reasoning: false, fast: false } }
        }
      ]
    })

    await renderModelSettings()
    await waitFor(() => expect(getHermesConfigRecord).toHaveBeenCalled())

    expect(screen.queryByRole('switch')).toBeNull()
  })

  it('renders the auxiliary task rows', async () => {
    await renderModelSettings()

    expect(await screen.findByText('Vision')).toBeTruthy()
    expect(screen.getAllByText('auto · use main model').length).toBeGreaterThan(0)
  })

  it('assigns an auxiliary task to the main model via setModelAssignment', async () => {
    await renderModelSettings()

    // One "Set to main" button per task slot; the first is Vision.
    const setToMainButtons = await screen.findAllByRole('button', { name: 'Set to main' })
    fireEvent.click(setToMainButtons[0])

    await waitFor(() =>
      expect(setModelAssignment).toHaveBeenCalledWith({
        model: 'hermes-4',
        provider: 'nous',
        scope: 'auxiliary',
        task: 'vision'
      })
    )
  })

  it('warns when a main switch leaves auxiliary tasks pinned to another provider', async () => {
    setModelAssignment.mockResolvedValueOnce({
      provider: 'openrouter',
      model: 'anthropic/claude-opus-4.7',
      gateway_tools: [],
      stale_aux: [{ task: 'compression', provider: 'nous', model: 'hermes-4' }]
    })

    await renderModelSettings()
    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalled())

    const applyButton = await screen.findByRole('button', { name: 'Apply' })
    fireEvent.click(applyButton)

    // The switch-time notice names the pinned provider and offers a reset.
    expect(await screen.findByText(/still run on/)).toBeTruthy()
    expect(screen.getByText('nous')).toBeTruthy()
  })

  it('shows a persistent banner when a loaded aux slot mismatches the main provider', async () => {
    getAuxiliaryModels.mockResolvedValueOnce({
      main: { provider: 'nous', model: 'hermes-4' },
      tasks: [{ task: 'curator', provider: 'openrouter', model: 'anthropic/claude-opus-4.7', base_url: '' }]
    })

    await renderModelSettings()

    // Banner present on load, no switch required.
    expect(await screen.findByText(/still run on/)).toBeTruthy()
  })

  it('shows Gateway auto first and keeps unsupported or unknown live main models visible but disabled', async () => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'gateway', model: 'gateway-good' })
    getGlobalModelOptions.mockResolvedValueOnce({ providers: [gatewayProvider()] })

    await renderModelSettings()

    const triggers = await screen.findAllByRole('combobox')
    fireEvent.click(triggers[1])

    const options = await screen.findAllByRole('option')
    expect(options.map(option => option.textContent?.trim())).toEqual([
      'autoAutomatic routing',
      'gateway-good',
      'gateway-no-toolsDoes not support tools',
      'gateway-tools-unknownTool support is not verified',
      'gateway-no-vision'
    ])
    expect(screen.getByRole('option', { name: 'gateway-good' }).getAttribute('aria-disabled')).not.toBe('true')
    expect(screen.getByRole('option', { name: /gateway-no-tools/ }).getAttribute('aria-disabled')).toBe('true')
    expect(screen.getByRole('option', { name: /gateway-tools-unknown/ }).getAttribute('aria-disabled')).toBe('true')
    expect(screen.queryByText(/evidence\.example/)).toBeNull()
  })

  it('preserves a current unsupported Gateway model and marks it for review without changing it', async () => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'gateway', model: 'gateway-no-tools' })
    getGlobalModelOptions.mockResolvedValueOnce({ providers: [gatewayProvider()] })

    await renderModelSettings()

    const triggers = await screen.findAllByRole('combobox')
    expect(triggers[1].textContent).toContain('gateway-no-tools')
    fireEvent.click(triggers[1])

    expect(
      screen.getByRole('option', { name: /gateway-no-tools.*Needs review/ }).getAttribute('aria-disabled')
    ).not.toBe('true')
    expect(setModelAssignment).not.toHaveBeenCalled()
  })

  it.each([
    ['authentication-required', 'Configure OTTO_API_KEY in Keys to use Gateway models.'],
    ['gateway-upgrade-required', 'Update Gateway to load verified model capabilities.'],
    ['gateway-unreachable', 'Gateway is unavailable. Refresh the model catalog and try again.'],
    ['catalog-empty', 'Gateway returned no concrete models. Refresh the model catalog and try again.'],
    ['capability-response-invalid', 'Gateway returned an incompatible capability response.'],
    ['unknown', 'Gateway model status is unknown. Refresh the model catalog and try again.']
  ])('renders the distinct Gateway readiness message for %s', async (status, message) => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'gateway', model: 'auto' })
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        gatewayProvider({
          authenticated: status !== 'authentication-required',
          capabilities: {},
          capability_status: status,
          models: []
        })
      ]
    })

    await renderModelSettings()

    expect(await screen.findByText(message)).toBeTruthy()
  })

  it('uses vision eligibility for Vision but ordinary auxiliary eligibility for text tasks', async () => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'gateway', model: 'gateway-good' })
    getGlobalModelOptions.mockResolvedValueOnce({ providers: [gatewayProvider()] })
    getAuxiliaryModels.mockResolvedValueOnce({
      main: { provider: 'gateway', model: 'gateway-good' },
      tasks: [
        { task: 'vision', provider: 'auto', model: '', base_url: '' },
        { task: 'compression', provider: 'auto', model: '', base_url: '' }
      ]
    })

    await renderModelSettings()

    const changeButtons = await screen.findAllByRole('button', { name: 'Change' })
    fireEvent.click(changeButtons[0])
    let triggers = screen.getAllByRole('combobox')
    fireEvent.click(triggers.at(-1)!)

    expect(
      screen.getByRole('option', { name: /gateway-no-vision.*Does not support vision/ }).getAttribute('aria-disabled')
    ).toBe('true')
    expect(screen.queryByRole('option', { name: /^auto/ })).toBeNull()

    fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Escape' })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Change' })[1])
    triggers = screen.getAllByRole('combobox')
    fireEvent.click(triggers.at(-1)!)

    expect(screen.getByRole('option', { name: 'gateway-no-vision' }).getAttribute('aria-disabled')).not.toBe('true')
    expect(screen.queryByRole('option', { name: /^auto/ })).toBeNull()
  })

  it('keeps provider=auto and an empty model as the inherited auxiliary assignment', async () => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'gateway', model: 'gateway-good' })
    getGlobalModelOptions.mockResolvedValueOnce({ providers: [gatewayProvider()] })
    getAuxiliaryModels.mockResolvedValueOnce({
      main: { provider: 'gateway', model: 'gateway-good' },
      tasks: [{ task: 'vision', provider: 'auto', model: '', base_url: '' }]
    })

    await renderModelSettings()

    expect((await screen.findAllByText('auto · use main model')).length).toBeGreaterThan(0)
    expect(setModelAssignment).not.toHaveBeenCalled()
  })

  it('shows reasoning defaults only for verified contract support while preserving legacy behavior', async () => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'gateway', model: 'gateway-tools-unknown' })
    getGlobalModelOptions.mockResolvedValueOnce({ providers: [gatewayProvider()] })

    const { unmount } = await renderModelSettings()
    await waitFor(() => expect(getHermesConfigRecord).toHaveBeenCalled())
    expect(screen.queryByText('Reasoning')).toBeNull()

    unmount()
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'legacy', model: 'legacy-model' })
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        {
          name: 'Legacy',
          slug: 'legacy',
          models: ['legacy-model'],
          authenticated: true,
          capabilities: { 'legacy-model': { reasoning: true, fast: false } }
        }
      ]
    })

    await renderModelSettings()
    expect(await screen.findByText('Reasoning')).toBeTruthy()
  })

  it('forces a fresh catalog and prevents an old profile response from repainting after a profile switch', async () => {
    const oldOptions = deferred<{ providers: ReturnType<typeof gatewayProvider>[] }>()
    getGlobalModelInfo
      .mockResolvedValueOnce({ provider: 'gateway', model: 'gateway-good' })
      .mockResolvedValueOnce({ provider: 'legacy', model: 'legacy-model' })
    getGlobalModelOptions.mockReturnValueOnce(oldOptions.promise).mockResolvedValueOnce({
      providers: [
        {
          name: 'Legacy',
          slug: 'legacy',
          models: ['legacy-model'],
          authenticated: true,
          capabilities: { 'legacy-model': { reasoning: true, fast: false } }
        }
      ]
    })

    await renderModelSettings()
    await waitFor(() => expect(profileSwitch.callback).not.toBeNull())
    profileSwitch.callback?.()

    expect(await screen.findByText('legacy-model')).toBeTruthy()
    expect(getGlobalModelOptions).toHaveBeenNthCalledWith(1, { refresh: true })
    expect(getGlobalModelOptions).toHaveBeenNthCalledWith(2, { refresh: true })

    oldOptions.resolve({ providers: [gatewayProvider()] })
    await waitFor(() => expect(screen.queryByText('gateway-good')).toBeNull())
    expect(screen.getByText('legacy-model')).toBeTruthy()
  })

  it('preserves a draft Gateway provider and model when manually refreshing its readiness', async () => {
    const legacyProvider = {
      name: 'Legacy',
      slug: 'legacy',
      models: ['legacy-model'],
      authenticated: true,
      capabilities: { 'legacy-model': { reasoning: true, fast: false } }
    }

    getGlobalModelInfo.mockResolvedValue({ provider: 'legacy', model: 'legacy-model' })
    getGlobalModelOptions
      .mockResolvedValueOnce({
        providers: [
          legacyProvider,
          gatewayProvider({
            capabilities: {},
            capability_status: 'gateway-unreachable',
            models: []
          })
        ]
      })
      .mockResolvedValueOnce({
        providers: [legacyProvider, gatewayProvider()]
      })

    await renderModelSettings()

    let triggers = await screen.findAllByRole('combobox')
    fireEvent.click(triggers[0])
    fireEvent.click(screen.getByRole('option', { name: 'Gateway' }))

    expect(await screen.findByText('Gateway is unavailable. Refresh the model catalog and try again.')).toBeTruthy()

    triggers = screen.getAllByRole('combobox')
    fireEvent.click(triggers[1])
    fireEvent.click(screen.getByRole('option', { name: /auto.*Automatic routing/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Refresh models' }))

    await waitFor(() => expect(getGlobalModelOptions).toHaveBeenCalledTimes(2))
    triggers = screen.getAllByRole('combobox')
    expect(triggers[0].textContent).toContain('Gateway')
    expect(triggers[1].textContent).toContain('auto')

    fireEvent.click(triggers[1])
    expect(screen.getByRole('option', { name: 'gateway-good' })).toBeTruthy()
  })

  it('shows a localized warning when an unchanged grandfathered assignment is accepted as a no-op', async () => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'gateway', model: 'gateway-no-tools' })
    getGlobalModelOptions.mockResolvedValueOnce({ providers: [gatewayProvider()] })
    setModelAssignment.mockResolvedValueOnce({
      ok: true,
      provider: 'gateway',
      model: 'gateway-no-tools',
      gateway_tools: [],
      selection_warning: {
        code: 'grandfathered-model-assignment',
        reason: 'tools-unsupported',
        message: 'backend prose must not be shown'
      }
    })

    await renderModelSettings()
    fireEvent.click(await screen.findByRole('button', { name: 'Apply' }))

    expect(
      await screen.findByText(
        'This saved model is grandfathered and was left unchanged. Choose a verified model when you are ready.'
      )
    ).toBeTruthy()
    expect(screen.queryByText('backend prose must not be shown')).toBeNull()
  })
})
