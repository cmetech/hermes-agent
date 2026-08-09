import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
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
const startManualLocalEndpoint = vi.fn()
const startManualOnboarding = vi.fn()
const startManualProviderOAuth = vi.fn()
const profileSwitch = vi.hoisted(() => ({ callback: null as null | (() => void) }))
let apiRequestProfile: null | string = null
let profileSwitchHandler: (() => void) | null = null

vi.mock('@/hermes', () => ({
  getGlobalModelInfo: () => getGlobalModelInfo(),
  getGlobalModelOptions: (options?: unknown) => getGlobalModelOptions(options),
  getApiRequestProfile: () => apiRequestProfile,
  getAuxiliaryModels: () => getAuxiliaryModels(),
  getMoaModels: () => getMoaModels(),
  setModelAssignment: (body: unknown) => setModelAssignment(body),
  getRecommendedDefaultModel: (slug: string) => getRecommendedDefaultModel(slug),
  saveMoaModels: (body: unknown, profile?: null | string) => saveMoaModels(body, profile),
  setEnvVar: (key: string, value: string) => setEnvVar(key, value),
  getHermesConfigRecord: () => getHermesConfigRecord(),
  saveHermesConfig: (config: unknown) => saveHermesConfig(config),
  setApiRequestProfile: () => {}
}))

vi.mock('@/store/onboarding', () => ({
  startManualLocalEndpoint: () => startManualLocalEndpoint(),
  startManualOnboarding: () => startManualOnboarding(),
  startManualProviderOAuth: (slug: string) => startManualProviderOAuth(slug)
}))

vi.mock('../hooks/use-on-profile-switch', () => ({
  useOnProfileSwitch: (callback: () => void) => {
    profileSwitch.callback = callback
    profileSwitchHandler = callback
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
  apiRequestProfile = null
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
  saveMoaModels.mockImplementation(async body => body)
  setEnvVar.mockResolvedValue({ ok: true })
  getHermesConfigRecord.mockResolvedValue({ agent: { reasoning_effort: 'medium', service_tier: 'normal' } })
  saveHermesConfig.mockResolvedValue({ ok: true })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.useRealTimers()
  profileSwitchHandler = null
})

async function renderModelSettings() {
  const { ModelSettings } = await import('./model-settings')
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    // The aux-task deep-link highlight reads useSearchParams, so the page
    // needs a router context in tests (the app provides HashRouter at root).
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ModelSettings />
      </QueryClientProvider>
    </MemoryRouter>
  )
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
  let reject!: (reason?: unknown) => void

  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })

  return { promise, reject, resolve }
}

function moaPreset(overrides: Record<string, unknown> = {}) {
  return {
    active_preset: 'default',
    aggregator: { provider: 'gateway', model: 'gateway-tools-unknown' },
    aggregator_temperature: 0.2,
    default_preset: 'default',
    enabled: true,
    max_tokens: 2048,
    presets: {
      default: {
        aggregator: { provider: 'gateway', model: 'gateway-tools-unknown' },
        aggregator_temperature: 0.2,
        enabled: true,
        max_tokens: 2048,
        reference_models: [{ provider: 'gateway', model: 'gateway-no-completion' }],
        reference_temperature: 0.7
      }
    },
    reference_models: [{ provider: 'gateway', model: 'gateway-no-completion' }],
    reference_temperature: 0.7,
    ...overrides
  }
}

function namedMoaPreset(name: string) {
  const base = moaPreset()

  return {
    ...base,
    active_preset: name,
    default_preset: name,
    presets: { [name]: base.presets.default }
  }
}

function rowComboboxes(title: string): HTMLElement[] {
  const titleNode = screen.getByText(title)
  const row = titleNode.parentElement?.parentElement

  if (!row) {
    throw new Error(`Could not find settings row for ${title}`)
  }

  return within(row).getAllByRole('combobox')
}

describe('ModelSettings', () => {
  it('loads the current main model and lists configured providers only', async () => {
    await renderModelSettings()

    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalled())
    await waitFor(() => expect(getGlobalModelOptions).toHaveBeenCalled())
    expect(getGlobalModelOptions).toHaveBeenCalledWith(undefined)

    // Open the provider Select — only configured providers should be listed.
    const triggers = await screen.findAllByRole('combobox')
    fireEvent.click(triggers[0])

    // "Nous" shows in both the trigger and the open list.
    expect((await screen.findAllByText('Nous')).length).toBeGreaterThan(0)
    expect(screen.queryByText(/DeepSeek/)).toBeNull()
  })

  it.each(['custom', 'local', 'custom:lab'])(
    'opens local endpoint setup when %s has no inventory row',
    async provider => {
      getGlobalModelInfo.mockResolvedValueOnce({ provider, model: '' })
      getGlobalModelOptions.mockResolvedValueOnce({ providers: [] })

      await renderModelSettings()

      const providerSelect = (await screen.findAllByRole('combobox'))[0]

      expect(providerSelect.textContent).toContain(provider)
      expect(screen.queryByText(/undefined/)).toBeNull()
      expect(screen.queryByText(/signs in through your browser/)).toBeNull()

      fireEvent.click(await screen.findByRole('button', { name: 'Set up provider' }))

      expect(startManualLocalEndpoint).toHaveBeenCalledOnce()
      expect(startManualOnboarding).not.toHaveBeenCalled()
      expect(startManualProviderOAuth).not.toHaveBeenCalled()
    }
  )

  it('opens the generic provider picker for an unknown provider with no inventory row', async () => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'retired-provider', model: '' })
    getGlobalModelOptions.mockResolvedValueOnce({ providers: [] })

    await renderModelSettings()

    fireEvent.click(await screen.findByRole('button', { name: 'Set up provider' }))

    expect(startManualOnboarding).toHaveBeenCalledOnce()
    expect(startManualLocalEndpoint).not.toHaveBeenCalled()
    expect(startManualProviderOAuth).not.toHaveBeenCalled()
  })

  it('deep-links a known OAuth provider row into its setup flow', async () => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'anthropic', model: '' })
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        {
          name: 'Anthropic',
          slug: 'anthropic',
          models: [],
          authenticated: false,
          auth_type: 'oauth'
        }
      ]
    })

    await renderModelSettings()

    fireEvent.click(await screen.findByRole('button', { name: 'Set up Anthropic' }))

    expect(startManualProviderOAuth).toHaveBeenCalledWith('anthropic')
    expect(startManualLocalEndpoint).not.toHaveBeenCalled()
    expect(startManualOnboarding).not.toHaveBeenCalled()
  })

  it('replaces the selected provider and model when the active profile changes', async () => {
    getGlobalModelInfo
      .mockResolvedValueOnce({ provider: 'custom', model: 'local-a' })
      .mockResolvedValueOnce({ provider: 'nous', model: 'hermes-4' })
    getGlobalModelOptions
      .mockResolvedValueOnce({
        providers: [
          {
            name: 'Custom A',
            slug: 'custom',
            models: ['local-a'],
            authenticated: true
          }
        ]
      })
      .mockResolvedValueOnce({
        providers: [
          {
            name: 'Nous',
            slug: 'nous',
            models: ['hermes-4'],
            authenticated: true,
            capabilities: { 'hermes-4': { reasoning: true, fast: true } }
          }
        ]
      })

    await renderModelSettings()
    expect((await screen.findAllByRole('combobox'))[0].textContent).toContain('Custom A')

    await act(async () => {
      profileSwitchHandler?.()
    })

    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getAllByRole('combobox')[0].textContent).toContain('Nous'))
    expect(screen.queryByRole('button', { name: 'Set up provider' })).toBeNull()
  })

  it('preserves a user-defined provider endpoint when applying the main model', async () => {
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        {
          name: 'Nous',
          slug: 'nous',
          models: ['hermes-4'],
          authenticated: true
        },
        {
          name: 'Ollama',
          slug: 'local-ollama',
          models: ['qwen3:latest'],
          authenticated: true,
          is_user_defined: true,
          api_url: 'http://localhost:11434/v1'
        }
      ]
    })
    setModelAssignment.mockResolvedValueOnce({
      provider: 'local-ollama',
      model: 'qwen3:latest',
      gateway_tools: []
    })

    await renderModelSettings()

    const providerSelect = (await screen.findAllByRole('combobox'))[0]
    fireEvent.click(providerSelect)
    fireEvent.click(await screen.findByRole('option', { name: 'Ollama' }))

    const modelSelect = (await screen.findAllByRole('combobox'))[1]
    fireEvent.click(modelSelect)
    fireEvent.click(await screen.findByRole('option', { name: 'qwen3:latest' }))

    fireEvent.click(await screen.findByRole('button', { name: 'Apply' }))

    await waitFor(() =>
      expect(setModelAssignment).toHaveBeenCalledWith({
        model: 'qwen3:latest',
        provider: 'local-ollama',
        scope: 'main',
        base_url: 'http://localhost:11434/v1'
      })
    )
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

  it('restores an auxiliary task to inherited main-model routing', async () => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'gateway', model: 'auto' })
    getGlobalModelOptions.mockResolvedValueOnce({ providers: [gatewayProvider()] })
    getAuxiliaryModels.mockResolvedValueOnce({
      main: { provider: 'gateway', model: 'auto' },
      tasks: [{ task: 'vision', provider: 'gateway', model: 'gateway-good', base_url: '' }]
    })

    await renderModelSettings()

    // One "Set to main" button per task slot; the first is Vision.
    const setToMainButtons = await screen.findAllByRole('button', { name: 'Set to main' })
    fireEvent.click(setToMainButtons[0])

    await waitFor(() =>
      expect(setModelAssignment).toHaveBeenCalledWith({
        model: '',
        provider: 'auto',
        scope: 'auxiliary',
        task: 'vision'
      })
    )
  })

  it('keeps a local main model inherited instead of pinning its endpoint into an aux slot', async () => {
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        {
          name: 'Ollama',
          slug: 'local-ollama',
          models: ['qwen3:latest'],
          authenticated: true,
          is_user_defined: true,
          api_url: 'http://localhost:11434/v1'
        }
      ]
    })
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'local-ollama', model: 'qwen3:latest' })
    getAuxiliaryModels.mockResolvedValueOnce({
      main: { provider: 'local-ollama', model: 'qwen3:latest' },
      tasks: [{ task: 'vision', provider: 'auto', model: '', base_url: '' }]
    })

    await renderModelSettings()

    const setToMainButtons = await screen.findAllByRole('button', { name: 'Set to main' })
    fireEvent.click(setToMainButtons[0])

    await waitFor(() =>
      expect(setModelAssignment).toHaveBeenCalledWith({
        model: '',
        provider: 'auto',
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

  it('uses cached catalogs and prevents an old profile response from repainting after a profile switch', async () => {
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
    expect(getGlobalModelOptions).toHaveBeenNthCalledWith(1, undefined)
    expect(getGlobalModelOptions).toHaveBeenNthCalledWith(2, undefined)

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
    expect(getGlobalModelOptions).toHaveBeenNthCalledWith(1, undefined)
    expect(getGlobalModelOptions).toHaveBeenNthCalledWith(2, { refresh: true })
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

  it('uses reference and aggregator eligibility while preserving saved invalid MoA slots', async () => {
    const provider = gatewayProvider({
      capabilities: {
        ...gatewayProvider().capabilities,
        auto: {
          selection_mode: 'automatic',
          verified: ALL_SUPPORTED
        },
        'gateway-completion-only': {
          selection_mode: 'explicit',
          verified: { ...ALL_SUPPORTED, tools: 'unsupported' }
        },
        'gateway-no-completion': {
          selection_mode: 'explicit',
          verified: { ...ALL_SUPPORTED, completion: 'unsupported' }
        }
      },
      models: ['auto', 'gateway-good', 'gateway-completion-only', 'gateway-tools-unknown']
    })

    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'gateway', model: 'gateway-good' })
    getGlobalModelOptions.mockResolvedValueOnce({ providers: [provider] })
    getMoaModels.mockResolvedValueOnce(moaPreset())

    await renderModelSettings()
    await screen.findByText('Reference 1')

    fireEvent.click(rowComboboxes('Reference 1')[1])
    expect(screen.queryByRole('option', { name: /^auto/ })).toBeNull()
    expect(screen.getByRole('option', { name: 'gateway-completion-only' }).getAttribute('aria-disabled')).not.toBe(
      'true'
    )
    expect(
      screen.getByRole('option', { name: /gateway-no-completion.*Needs review/ }).getAttribute('aria-disabled')
    ).not.toBe('true')

    fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Escape' })
    fireEvent.click(rowComboboxes('Aggregator')[1])
    expect(screen.queryByRole('option', { name: /^auto/ })).toBeNull()
    expect(
      screen
        .getByRole('option', { name: /gateway-completion-only.*Does not support tools/ })
        .getAttribute('aria-disabled')
    ).toBe('true')
    expect(
      screen.getByRole('option', { name: /gateway-tools-unknown.*Needs review/ }).getAttribute('aria-disabled')
    ).not.toBe('true')
  })

  it('keeps a newly selected live MoA model whose completion metadata is unverified', async () => {
    const provider = gatewayProvider({
      capabilities: {
        ...gatewayProvider().capabilities,
        'gateway-completion-unknown': {
          selection_mode: 'explicit',
          verified: { ...ALL_SUPPORTED, completion: 'unknown' }
        }
      },
      models: [...gatewayProvider().models, 'gateway-completion-unknown']
    })

    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'gateway', model: 'gateway-good' })
    getGlobalModelOptions.mockResolvedValueOnce({ providers: [provider] })
    getMoaModels.mockResolvedValueOnce(moaPreset())

    await renderModelSettings()
    await screen.findByText('Reference 1')
    vi.useFakeTimers()

    fireEvent.click(rowComboboxes('Reference 1')[1])
    const option = screen.getByRole('option', { name: 'gateway-completion-unknown' })
    expect(option.getAttribute('aria-disabled')).not.toBe('true')
    fireEvent.click(option)
    await act(async () => vi.advanceTimersByTimeAsync(600))

    expect(saveMoaModels).toHaveBeenCalledTimes(1)
    expect(rowComboboxes('Reference 1')[1].textContent).toContain('gateway-completion-unknown')
  })

  it('renders MoA selection warnings without removing the preserved slots', async () => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'gateway', model: 'gateway-good' })
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        gatewayProvider({
          capabilities: {
            ...gatewayProvider().capabilities,
            'gateway-no-completion': {
              selection_mode: 'explicit',
              verified: { ...ALL_SUPPORTED, completion: 'unsupported' }
            }
          }
        })
      ]
    })
    getMoaModels.mockResolvedValueOnce(moaPreset())
    saveMoaModels.mockResolvedValueOnce(
      moaPreset({
        selection_warnings: [
          {
            message: 'backend prose must not be shown',
            preset: 'default',
            reason: 'completion-unsupported',
            slot: 'reference:0'
          }
        ]
      })
    )

    await renderModelSettings()
    fireEvent.click(await screen.findByRole('button', { name: 'Set default' }))

    expect(
      await screen.findByText(
        'This saved model is grandfathered and was left unchanged. Choose a verified model when you are ready.'
      )
    ).toBeTruthy()
    expect(screen.getByText('Reference 1')).toBeTruthy()
    expect(screen.getByText('Aggregator')).toBeTruthy()
    expect(screen.getByText(/gateway · gateway-no-completion/)).toBeTruthy()
    expect(screen.queryByText('backend prose must not be shown')).toBeNull()
  })

  it('cancels a pending MoA autosave when the active profile switches', async () => {
    apiRequestProfile = 'profile-a'
    getGlobalModelInfo.mockResolvedValue({ provider: 'gateway', model: 'gateway-good' })
    getGlobalModelOptions.mockResolvedValue({ providers: [gatewayProvider()] })
    getMoaModels.mockResolvedValueOnce(namedMoaPreset('profile-a')).mockResolvedValueOnce(namedMoaPreset('profile-b'))

    await renderModelSettings()
    await screen.findByText('Reference 1')
    vi.useFakeTimers()

    fireEvent.click(screen.getByRole('button', { name: 'Add reference model' }))
    apiRequestProfile = 'profile-b'
    profileSwitch.callback?.()
    await act(async () => vi.advanceTimersByTimeAsync(600))

    expect(saveMoaModels).not.toHaveBeenCalled()
  })

  it('pins an in-flight MoA autosave to its origin and ignores its late success after a profile switch', async () => {
    const oldSave = deferred<ReturnType<typeof namedMoaPreset>>()
    apiRequestProfile = 'profile-a'
    getGlobalModelInfo.mockResolvedValue({ provider: 'gateway', model: 'gateway-good' })
    getGlobalModelOptions.mockResolvedValue({ providers: [gatewayProvider()] })
    getMoaModels.mockResolvedValueOnce(namedMoaPreset('profile-a')).mockResolvedValueOnce(namedMoaPreset('profile-b'))
    saveMoaModels.mockReturnValueOnce(oldSave.promise)

    await renderModelSettings()
    await screen.findByText('Reference 1')
    vi.useFakeTimers()

    fireEvent.click(screen.getByRole('button', { name: 'Add reference model' }))
    await act(async () => vi.advanceTimersByTimeAsync(600))
    expect(saveMoaModels).toHaveBeenCalledWith(expect.any(Object), 'profile-a')

    apiRequestProfile = 'profile-b'
    await act(async () => {
      profileSwitch.callback?.()
      await Promise.resolve()
    })
    expect(screen.getAllByText('profile-b')).toHaveLength(2)

    await act(async () => oldSave.resolve(namedMoaPreset('old-response')))
    expect(screen.getAllByText('profile-b')).toHaveLength(2)
    expect(screen.queryByText('old-response')).toBeNull()
  })

  it('ignores a late MoA autosave error from the previous profile', async () => {
    const oldSave = deferred<ReturnType<typeof namedMoaPreset>>()
    apiRequestProfile = 'profile-a'
    getGlobalModelInfo.mockResolvedValue({ provider: 'gateway', model: 'gateway-good' })
    getGlobalModelOptions.mockResolvedValue({ providers: [gatewayProvider()] })
    getMoaModels.mockResolvedValueOnce(namedMoaPreset('profile-a')).mockResolvedValueOnce(namedMoaPreset('profile-b'))
    saveMoaModels.mockReturnValueOnce(oldSave.promise)

    await renderModelSettings()
    await screen.findByText('Reference 1')
    vi.useFakeTimers()
    fireEvent.click(screen.getByRole('button', { name: 'Add reference model' }))
    await act(async () => vi.advanceTimersByTimeAsync(600))

    apiRequestProfile = 'profile-b'
    await act(async () => {
      profileSwitch.callback?.()
      await Promise.resolve()
    })
    await act(async () => oldSave.reject(new Error('profile-a save failed')))

    expect(screen.queryByText('profile-a save failed')).toBeNull()
    expect(screen.getAllByText('profile-b')).toHaveLength(2)
  })
})

describe('ModelSettings MoA preset editor', () => {
  const moaConfig = () => ({
    default_preset: 'default',
    active_preset: '',
    presets: {
      default: {
        reference_models: [
          { provider: 'nous', model: 'hermes-4' },
          { provider: 'openrouter', model: 'deepseek/deepseek-v4-pro' }
        ],
        aggregator: { provider: 'openrouter', model: 'anthropic/claude-opus-4.8' },
        reference_temperature: 0,
        aggregator_temperature: 0,
        max_tokens: 4096,
        enabled: true
      }
    },
    reference_models: [
      { provider: 'nous', model: 'hermes-4' },
      { provider: 'openrouter', model: 'deepseek/deepseek-v4-pro' }
    ],
    aggregator: { provider: 'openrouter', model: 'anthropic/claude-opus-4.8' },
    reference_temperature: 0,
    aggregator_temperature: 0,
    max_tokens: 4096,
    enabled: true
  })

  beforeEach(() => {
    getGlobalModelOptions.mockResolvedValue({
      providers: [
        {
          name: 'Nous',
          slug: 'nous',
          models: ['hermes-4', 'hermes-4-mini'],
          authenticated: true,
          capabilities: { 'hermes-4': { reasoning: true, fast: true } }
        },
        {
          name: 'OpenRouter',
          slug: 'openrouter',
          models: ['deepseek/deepseek-v4-pro', 'anthropic/claude-opus-4.8'],
          authenticated: true
        }
      ]
    })
    getMoaModels.mockResolvedValue(moaConfig())
    saveMoaModels.mockImplementation((body: unknown) => Promise.resolve(body))
  })

  async function openReferenceEditor() {
    await renderModelSettings()
    expect(await screen.findByText('Reference 1')).toBeTruthy()
  }

  function slotSelects() {
    // Combobox order in the MoA section (last 7 on the page): preset select,
    // then provider+model per reference (2 refs), then aggregator
    // provider+model. Reference 1's pair is therefore at -6 / -5.
    const all = screen.getAllByRole('combobox')

    return { ref1Provider: all.at(-6)!, ref1Model: all.at(-5)! }
  }

  it('holds the autosave while a slot is half-filled (provider changed, model pending)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    try {
      await openReferenceEditor()

      fireEvent.click(slotSelects().ref1Provider)
      fireEvent.click(await screen.findByRole('option', { name: 'OpenRouter' }))

      // Model was cleared by the provider change → config incomplete → the
      // debounced autosave must NOT fire, even well past the 600ms window.
      await vi.advanceTimersByTimeAsync(2000)
      expect(saveMoaModels).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('saves once the model pick completes the slot', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    try {
      await openReferenceEditor()

      fireEvent.click(slotSelects().ref1Provider)
      fireEvent.click(await screen.findByRole('option', { name: 'OpenRouter' }))
      await vi.advanceTimersByTimeAsync(700)

      fireEvent.click(slotSelects().ref1Model)
      fireEvent.click(await screen.findByRole('option', { name: 'anthropic/claude-opus-4.8' }))
      await vi.advanceTimersByTimeAsync(700)

      expect(saveMoaModels).toHaveBeenCalledTimes(1)
      const sent = saveMoaModels.mock.calls[0][0] as ReturnType<typeof moaConfig>
      expect(sent.presets.default.reference_models[0]).toEqual({
        provider: 'openrouter',
        model: 'anthropic/claude-opus-4.8'
      })
      // The untouched slots ride along unchanged — nothing reverts to defaults.
      expect(sent.presets.default.reference_models[1]).toEqual({
        provider: 'openrouter',
        model: 'deepseek/deepseek-v4-pro'
      })
      expect(sent.presets.default.aggregator).toEqual({
        provider: 'openrouter',
        model: 'anthropic/claude-opus-4.8'
      })
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not clear the model or save when the same provider is re-selected', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    try {
      await openReferenceEditor()

      fireEvent.click(slotSelects().ref1Provider)
      fireEvent.click(await screen.findByRole('option', { name: 'Nous' }))
      await vi.advanceTimersByTimeAsync(700)

      // Radix treats re-picking the current value as a no-op (no
      // onValueChange), so nothing changes: no save, model still shown.
      expect(saveMoaModels).not.toHaveBeenCalled()
      expect(screen.getByText('nous · hermes-4')).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })

  it('autosaves the selected preset when its enabled switch is toggled', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    try {
      await openReferenceEditor()

      await act(async () => {
        fireEvent.click(screen.getByRole('switch', { name: 'Enabled' }))
        await vi.advanceTimersByTimeAsync(700)
      })

      expect(saveMoaModels).toHaveBeenCalledWith(
        expect.objectContaining({
          presets: expect.objectContaining({
            default: expect.objectContaining({ enabled: false })
          })
        }),
        null
      )
    } finally {
      vi.useRealTimers()
    }
  })

  it('saves a disabled reference model without removing it (per-slot enabled toggle)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    try {
      await openReferenceEditor()

      await act(async () => {
        fireEvent.click(screen.getByRole('switch', { name: 'Disable reference 1' }))
        await vi.advanceTimersByTimeAsync(700)
      })

      expect(saveMoaModels).toHaveBeenCalledWith(
        expect.objectContaining({
          presets: expect.objectContaining({
            default: expect.objectContaining({
              reference_models: [
                expect.objectContaining({ provider: 'nous', model: 'hermes-4', enabled: false }),
                expect.objectContaining({ provider: 'openrouter', model: 'deepseek/deepseek-v4-pro' })
              ]
            })
          })
        }),
        null
      )
    } finally {
      vi.useRealTimers()
    }
  })
})
