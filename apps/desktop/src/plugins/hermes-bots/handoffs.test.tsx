import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ProfileRoute } from './types'

const { requestProfile } = vi.hoisted(() => ({ requestProfile: vi.fn() }))

vi.mock('@hermes/plugin-sdk', async () => {
  const { useQuery } = await import('@tanstack/react-query')

  return {
    Button: (props: React.ComponentProps<'button'>) => <button {...props} />,
    Codicon: ({ name }: { name: string }) => <span data-icon={name} />,
    GlyphSpinner: () => <span aria-label="Loading handoffs" />,
    host: { requestProfile },
    useQuery
  }
})

const { Handoffs } = await import('./handoffs')

const route: ProfileRoute = {
  connectionId: 'remote-a',
  mode: 'remote',
  profile: 'reviewer',
  targetProfile: 'backend-reviewer'
}

const row = {
  actions: ['message', 'cancel', 'acknowledge'],
  age_seconds: 125,
  created_at: '2026-09-02T00:00:00Z',
  endpoint: 'hermes://peer/spark/worker',
  failure_code: null,
  handoff_id: 'handoff-1',
  mechanism: 'peer_runs',
  needs_attention: true,
  next_observation_at: null,
  phase: 'active',
  terminal_summary: null,
  updated_at: '2026-09-02T00:02:05Z'
}

function mount(props: { profile?: string; route?: ProfileRoute | null } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )

  const view = render(
    <Handoffs profile={props.profile || 'reviewer'} route={props.route === undefined ? route : props.route} />,
    { wrapper }
  )

  return { ...view, client }
}

function answer(method: string, overrides: Record<string, unknown> = {}) {
  if (method === 'agent_handoff.directory') {
    return {
      agents: [
        {
          default: 'hermes://peer/spark/worker',
          endpoints: ['hermes://peer/spark/worker', 'hermes://local/worker'],
          name: 'worker'
        }
      ],
      ...overrides
    }
  }

  if (method === 'agent_handoff.list') {
    return { handoffs: [row], ...overrides }
  }

  if (method === 'agent_handoff.evidence') {
    return {
      ...row,
      events: [
        {
          actor: 'service',
          created_at: '2026-09-02T00:00:00Z',
          data: { authorization: 'Bearer private' },
          event_id: 'event-1',
          handoff_id: 'handoff-1',
          kind: 'created',
          phase_after: 'prepared',
          phase_before: null,
          sequence: 1
        }
      ],
      has_more: false,
      next_after_sequence: 1,
      raw_error: 'private remote failure',
      result: 'private result',
      ...overrides
    }
  }

  return { ...row, ...overrides }
}

beforeEach(() => {
  vi.clearAllMocks()
  requestProfile.mockImplementation(async (_route, method) => answer(method))
})

describe('profile-scoped polling', () => {
  it('loads directory and inbox in parallel through the selected profile route', async () => {
    mount()

    expect(screen.getByLabelText('Loading handoffs')).toBeTruthy()
    await screen.findByText('hermes://peer/spark/worker')

    expect(requestProfile).toHaveBeenCalledWith(route, 'agent_handoff.directory', {
      profile: 'backend-reviewer'
    })
    expect(requestProfile).toHaveBeenCalledWith(route, 'agent_handoff.list', {
      limit: 50,
      profile: 'backend-reviewer'
    })
    expect(requestProfile.mock.calls.every(([, method]) => String(method).startsWith('agent_handoff.'))).toBe(true)
  })

  it('renders empty and unavailable states without throwing outside the feature', async () => {
    requestProfile.mockImplementation(async (_route, method) =>
      answer(method, method.endsWith('.list') ? { handoffs: [] } : { agents: [] })
    )
    const empty = mount()

    await screen.findByText('No handoffs yet.')
    empty.unmount()

    requestProfile.mockRejectedValue(new Error('gateway unavailable'))
    mount({ route: null })

    await screen.findByText('Handoffs unavailable.')
  })

  it('drops a late response from the previously selected profile', async () => {
    let resolveAlpha: ((value: unknown) => void) | undefined

    const alpha = new Promise(resolve => {
      resolveAlpha = resolve
    })

    requestProfile.mockImplementation(async (_route, method, params) => {
      if (params.profile === 'alpha') {
        return alpha
      }

      return method.endsWith('.directory')
        ? { agents: [] }
        : { handoffs: [{ ...row, endpoint: 'hermes://local/beta', handoff_id: 'beta' }] }
    })
    const view = mount({ profile: 'alpha', route: null })

    view.rerender(<Handoffs profile="beta" route={null} />)
    await screen.findByText('hermes://local/beta')
    resolveAlpha?.(
      requestProfile.mock.calls[0][1].endsWith('.directory')
        ? { agents: [] }
        : { handoffs: [{ ...row, endpoint: 'hermes://local/alpha' }] }
    )

    await waitFor(() => expect(screen.queryByText('hermes://local/alpha')).toBeNull())
  })
})

describe('safe inbox and inspector', () => {
  it('shows bounded handoff facts and omits raw remote fields', async () => {
    requestProfile.mockImplementation(async (_route, method) =>
      answer(method, {
        authorization: 'Bearer private',
        raw_error: 'private remote failure',
        result: 'private result'
      })
    )
    mount()

    await screen.findByText('hermes://peer/spark/worker')
    expect(screen.getByText('peer_runs')).toBeTruthy()
    expect(screen.getByText('active')).toBeTruthy()
    expect(screen.getByText('2m')).toBeTruthy()
    expect(screen.getByText('Needs Attention')).toBeTruthy()
    expect(screen.queryByText(/Bearer private|private remote failure|private result/)).toBeNull()
  })

  it('fetches normalized evidence only after opening a row', async () => {
    requestProfile.mockImplementation(async (_route, method) =>
      answer(method, {
        terminal_summary: { media_type: 'text/plain', sha256: 'a'.repeat(64), size_bytes: 42 }
      })
    )
    mount()
    await screen.findByText('hermes://peer/spark/worker')
    expect(requestProfile.mock.calls.some(([, method]) => method === 'agent_handoff.evidence')).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: /open handoff/i }))

    await screen.findByText(/created/)
    expect(requestProfile).toHaveBeenCalledWith(route, 'agent_handoff.evidence', {
      handoff_id: 'handoff-1',
      limit: 100,
      profile: 'backend-reviewer'
    })
    expect(screen.getByText('Result: 42 B · text/plain')).toBeTruthy()
    expect(screen.queryByText(/Bearer private|private remote failure|private result/)).toBeNull()
  })
})

describe('closed mutations', () => {
  it('creates only through agent_handoff.create with the closed payload', async () => {
    requestProfile.mockImplementation(async (_route, method) =>
      answer(method, method.endsWith('.list') ? { handoffs: [] } : {})
    )
    mount()
    await screen.findByRole('option', { name: /hermes:\/\/local\/worker/ })

    fireEvent.change(screen.getByLabelText('Handoff destination'), {
      target: { value: 'hermes://local/worker' }
    })
    fireEvent.change(screen.getByLabelText('Handoff message'), { target: { value: 'Please inspect this.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send handoff' }))

    await waitFor(() =>
      expect(requestProfile.mock.calls.some(([, method]) => method === 'agent_handoff.create')).toBe(true)
    )
    const call = requestProfile.mock.calls.find(([, method]) => method === 'agent_handoff.create')!
    expect(call[0]).toEqual(route)
    expect(call[2]).toMatchObject({
      message: 'Please inspect this.',
      profile: 'backend-reviewer',
      target: 'hermes://local/worker'
    })
    expect(Object.keys(call[2]).sort()).toEqual(['message', 'profile', 'request_id', 'target'])
  })

  it('uses only advertised approval choices and refreshes after acknowledgement', async () => {
    const approval = {
      ...row,
      actions: ['respond', 'cancel', 'acknowledge'],
      approval: { choices: ['once', 'deny'], request_id: 'approval-1' },
      phase: 'needs_input'
    }

    let acknowledged = false
    requestProfile.mockImplementation(async (_route, method, params) => {
      if (method === 'agent_handoff.list') {
        return { handoffs: [{ ...approval, needs_attention: !acknowledged }] }
      }

      if (method === 'agent_handoff.evidence') {
        return { ...approval, events: [], has_more: false, next_after_sequence: 0 }
      }

      if (method === 'agent_handoff.command' && params.kind === 'acknowledge') {
        acknowledged = true
      }

      return answer(method)
    })
    mount()
    await screen.findByText('Needs Attention')
    fireEvent.click(screen.getByRole('button', { name: /open handoff/i }))

    expect(await screen.findByRole('button', { name: 'once' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'deny' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'always' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'once' }))
    await waitFor(() =>
      expect(requestProfile).toHaveBeenCalledWith(
        route,
        'agent_handoff.command',
        expect.objectContaining({
          choice: 'once',
          handoff_id: 'handoff-1',
          kind: 'respond',
          profile: 'backend-reviewer',
          request_id: 'approval-1'
        })
      )
    )

    fireEvent.click(screen.getByRole('button', { name: 'Acknowledge' }))
    await waitFor(() => expect(screen.queryByText('Needs Attention')).toBeNull())
    const commandCalls = requestProfile.mock.calls.filter(([, method]) => method === 'agent_handoff.command')
    expect(commandCalls.every(([, , params]) => !('actor' in params) && !('route' in params))).toBe(true)
  })

  it('sends a correlated follow-up through only the closed command payload', async () => {
    mount()
    await screen.findByText('hermes://peer/spark/worker')
    fireEvent.click(screen.getByRole('button', { name: /open handoff/i }))
    await screen.findByLabelText('Follow-up message')

    fireEvent.change(screen.getByLabelText('Follow-up message'), { target: { value: 'One more detail.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send follow-up' }))

    await waitFor(() =>
      expect(requestProfile.mock.calls.some(([, method]) => method === 'agent_handoff.command')).toBe(true)
    )
    const call = requestProfile.mock.calls.find(([, method]) => method === 'agent_handoff.command')!
    expect(call[2]).toMatchObject({
      handoff_id: 'handoff-1',
      kind: 'message',
      profile: 'backend-reviewer',
      text: 'One more detail.'
    })
    expect(Object.keys(call[2]).sort()).toEqual([
      'command_id',
      'correlation_id',
      'handoff_id',
      'kind',
      'profile',
      'text'
    ])
  })
})
