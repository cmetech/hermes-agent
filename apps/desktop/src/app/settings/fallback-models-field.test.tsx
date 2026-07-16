import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

// Radix Select calls scrollIntoView / pointer-capture APIs jsdom lacks.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

const getGlobalModelOptions = vi.fn()

vi.mock('@/hermes', () => ({
  getGlobalModelOptions: () => getGlobalModelOptions()
}))

beforeEach(() => {
  getGlobalModelOptions.mockResolvedValue({
    providers: [
      {
        name: 'Gateway',
        slug: 'gateway',
        models: ['auto', 'gateway-good', 'gateway-no-completion', 'gateway-no-tools', 'gateway-tools-unknown'],
        capability_status: 'ready',
        capabilities: {
          auto: {
            selection_mode: 'automatic',
            verified: {
              completion: 'supported',
              reasoning: 'supported',
              tools: 'supported',
              vision: 'supported'
            }
          },
          'gateway-good': {
            selection_mode: 'explicit',
            verified: {
              completion: 'supported',
              reasoning: 'supported',
              tools: 'supported',
              vision: 'supported'
            }
          },
          'gateway-no-tools': {
            selection_mode: 'explicit',
            verified: {
              completion: 'supported',
              reasoning: 'supported',
              tools: 'unsupported',
              vision: 'supported'
            }
          },
          'gateway-no-completion': {
            selection_mode: 'explicit',
            verified: {
              completion: 'unsupported',
              reasoning: 'supported',
              tools: 'supported',
              vision: 'supported'
            }
          },
          'gateway-retired': {
            selection_mode: 'explicit',
            verified: {
              completion: 'supported',
              reasoning: 'supported',
              tools: 'supported',
              vision: 'supported'
            }
          },
          'gateway-tools-unknown': {
            selection_mode: 'explicit',
            verified: {
              completion: 'supported',
              reasoning: 'supported',
              tools: 'unknown',
              vision: 'supported'
            }
          }
        }
      },
      { name: 'GitHub Copilot', slug: 'copilot', models: ['gpt-5-mini', 'gpt-5.4-mini'] },
      { name: 'OpenAI Codex', slug: 'openai-codex', models: ['gpt-5.4-mini'] },
      { name: 'Nous', slug: 'nous', models: ['hermes-4'] }
    ]
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderField(value: unknown, onChange = vi.fn()) {
  const { FallbackModelsField } = await import('./fallback-models-field')
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  render(
    <QueryClientProvider client={client}>
      <FallbackModelsField onChange={onChange} value={value} />
    </QueryClientProvider>
  )

  return onChange
}

async function renderFieldWithRerender(value: unknown, onChange = vi.fn()) {
  const { FallbackModelsField } = await import('./fallback-models-field')
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  const view = render(
    <QueryClientProvider client={client}>
      <FallbackModelsField onChange={onChange} value={value} />
    </QueryClientProvider>
  )

  return (next: unknown) =>
    view.rerender(
      <QueryClientProvider client={client}>
        <FallbackModelsField onChange={onChange} value={next} />
      </QueryClientProvider>
    )
}

const CHAIN = [
  { provider: 'copilot', model: 'gpt-5-mini' },
  { provider: 'openai-codex', model: 'gpt-5.4-mini' }
]

describe('FallbackModelsField', () => {
  it('renders each {provider, model} entry as its own row (never "[object Object]")', async () => {
    await renderField(CHAIN)

    // One Remove control per entry proves the object list became rows — the old
    // generic `list` input stringified the array to "[object Object]".
    expect(screen.getAllByLabelText('Remove')).toHaveLength(2)
    expect(screen.getByText('Add fallback')).toBeTruthy()
    expect(screen.queryByText(/\[object Object\]/)).toBeNull()
    await waitFor(() => expect(getGlobalModelOptions).toHaveBeenCalled())
  })

  it('removing a row emits the remaining entries', async () => {
    const onChange = await renderField(CHAIN)

    fireEvent.click(screen.getAllByLabelText('Remove')[0])

    expect(onChange.mock.calls.at(-1)?.[0]).toEqual([{ provider: 'openai-codex', model: 'gpt-5.4-mini' }])
  })

  it('adding a blank row does not persist a partial entry', async () => {
    const onChange = await renderField(CHAIN)

    fireEvent.click(screen.getByText('Add fallback'))

    // The new empty row stays in the UI but only complete pairs are emitted.
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual(CHAIN)
    expect(screen.getAllByLabelText('Remove')).toHaveLength(3)
  })

  it('shows an empty-state hint when there are no fallbacks', async () => {
    await renderField([])

    expect(screen.getByText(/No fallback models/)).toBeTruthy()
    expect(screen.queryAllByLabelText('Remove')).toHaveLength(0)
  })

  it('resyncs rows when persisted config changes', async () => {
    const rerender = await renderFieldWithRerender(CHAIN)
    expect(screen.getAllByLabelText('Remove')).toHaveLength(2)

    rerender([{ provider: 'nous', model: 'hermes-4' }])

    await waitFor(() => expect(screen.getAllByLabelText('Remove')).toHaveLength(1))
  })

  it('requires verified completion and tools while excluding Gateway auto', async () => {
    await renderField([{ provider: 'gateway', model: 'gateway-good' }])
    await waitFor(() => expect(getGlobalModelOptions).toHaveBeenCalled())

    fireEvent.click(screen.getAllByRole('combobox')[1])

    expect(screen.queryByRole('option', { name: /^auto/ })).toBeNull()
    expect(screen.getByRole('option', { name: 'gateway-good' }).getAttribute('aria-disabled')).not.toBe('true')
    expect(
      screen
        .getByRole('option', { name: /gateway-no-completion.*Does not support completions/ })
        .getAttribute('aria-disabled')
    ).toBe('true')
    expect(
      screen.getByRole('option', { name: /gateway-no-tools.*Does not support tools/ }).getAttribute('aria-disabled')
    ).toBe('true')
    expect(
      screen
        .getByRole('option', { name: /gateway-tools-unknown.*Tool support is not verified/ })
        .getAttribute('aria-disabled')
    ).toBe('true')
  })

  it('keeps a saved invalid Gateway fallback visible and marked for review', async () => {
    await renderField([{ provider: 'gateway', model: 'gateway-retired' }])
    await waitFor(() => expect(getGlobalModelOptions).toHaveBeenCalled())

    const modelTrigger = screen.getAllByRole('combobox')[1]
    expect(modelTrigger.textContent).toContain('gateway-retired')
    fireEvent.click(modelTrigger)

    expect(
      screen
        .getByRole('option', { name: /gateway-retired.*No longer in the live model catalog.*Needs review/ })
        .getAttribute('aria-disabled')
    ).not.toBe('true')
  })

  it('preserves legacy provider model behavior', async () => {
    await renderField([{ provider: 'copilot', model: 'gpt-5-mini' }])
    await waitFor(() => expect(getGlobalModelOptions).toHaveBeenCalled())

    fireEvent.click(screen.getAllByRole('combobox')[1])

    expect(screen.getByRole('option', { name: 'gpt-5-mini' }).getAttribute('aria-disabled')).not.toBe('true')
    expect(screen.getByRole('option', { name: 'gpt-5.4-mini' }).getAttribute('aria-disabled')).not.toBe('true')
    expect(screen.queryByText('Needs review')).toBeNull()
  })

  it('clears the prior model when its provider changes', async () => {
    const onChange = await renderField([{ provider: 'gateway', model: 'gateway-good' }])
    await waitFor(() => expect(getGlobalModelOptions).toHaveBeenCalled())

    fireEvent.click(screen.getAllByRole('combobox')[0])
    fireEvent.click(screen.getByRole('option', { name: 'Nous' }))

    expect(screen.getAllByRole('combobox')[1].textContent).toContain('Model')
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual([])
  })
})
