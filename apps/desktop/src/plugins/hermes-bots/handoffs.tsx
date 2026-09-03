import { Button, Codicon, GlyphSpinner, host, useQuery } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'

import { backendTargetProfile } from './routing'
import type { ProfileRoute } from './types'

interface AgentDirectoryRow {
  default: string
  endpoints: string[]
  name: string
}

interface HandoffApproval {
  choices: string[]
  request_id: string
}

interface HandoffSummary {
  actions: string[]
  age_seconds: number
  approval?: HandoffApproval
  endpoint: string
  failure_code: null | string
  handoff_id: string
  mechanism: null | string
  needs_attention: boolean
  phase: string
  terminal_summary: null | {
    media_type: string
    sha256: string
    size_bytes: number
  }
}

interface HandoffEvent {
  created_at: null | string
  event_id: string
  kind: string
  phase_after: string
  phase_before: null | string
  sequence: number
}

interface HandoffEvidence extends HandoffSummary {
  events: HandoffEvent[]
}

interface InboxPayload {
  agents: AgentDirectoryRow[]
  handoffs: HandoffSummary[]
}

export interface HandoffsProps {
  profile: string
  route?: null | ProfileRoute
  unavailable?: boolean
}

const HANDOFF_QUERY = ['hermes-bots', 'agent-handoffs'] as const
let operationSequence = 0

function operationId(prefix: string): string {
  const random = globalThis.crypto?.randomUUID?.()

  return `${prefix}-${random || `${Date.now()}-${(operationSequence += 1)}`}`
}

function elapsed(seconds: number): string {
  const value = Math.max(0, Number(seconds) || 0)

  if (value < 60) {
    return `${Math.floor(value)}s`
  }

  if (value < 3600) {
    return `${Math.floor(value / 60)}m`
  }

  if (value < 86400) {
    return `${Math.floor(value / 3600)}h`
  }

  return `${Math.floor(value / 86400)}d`
}

async function requestHandoff<T>(
  route: null | ProfileRoute,
  profile: string,
  method: string,
  params: Record<string, unknown> = {}
): Promise<T> {
  if (typeof host.requestProfile !== 'function') {
    throw new Error('Profile RPC is unavailable')
  }

  const backendProfile = backendTargetProfile(route, profile)

  return host.requestProfile<T>(route || profile, method, {
    ...params,
    profile: backendProfile
  })
}

export function Handoffs({ profile, route = null, unavailable = false }: HandoffsProps) {
  const scopeKey = route ? `${route.connectionId}::${route.profile}::${route.targetProfile}` : profile
  const [open, setOpen] = useState(true)
  const [selectedId, setSelectedId] = useState('')
  const [message, setMessage] = useState('')
  const [target, setTarget] = useState('')
  const [followUp, setFollowUp] = useState('')
  const [mutation, setMutation] = useState('')
  const [mutationFailed, setMutationFailed] = useState(false)

  useEffect(() => {
    setSelectedId('')
    setTarget('')
    setMessage('')
    setFollowUp('')
    setMutationFailed(false)
  }, [scopeKey])

  const inbox = useQuery({
    queryKey: [...HANDOFF_QUERY, scopeKey],
    queryFn: async (): Promise<InboxPayload> => {
      const [directory, list] = await Promise.all([
        requestHandoff<{ agents?: AgentDirectoryRow[] }>(route, profile, 'agent_handoff.directory'),
        requestHandoff<{ handoffs?: HandoffSummary[] }>(route, profile, 'agent_handoff.list', { limit: 50 })
      ])

      return {
        agents: Array.isArray(directory?.agents) ? directory.agents : [],
        handoffs: Array.isArray(list?.handoffs) ? list.handoffs : []
      }
    },
    enabled: !unavailable,
    refetchInterval: 5000,
    retry: false,
    staleTime: 2000
  })

  const detail = useQuery({
    queryKey: [...HANDOFF_QUERY, scopeKey, selectedId, 'evidence'],
    queryFn: () =>
      requestHandoff<HandoffEvidence>(route, profile, 'agent_handoff.evidence', {
        handoff_id: selectedId,
        limit: 100
      }),
    enabled: !unavailable && Boolean(selectedId),
    retry: false
  })

  const rows = inbox.data?.handoffs || []
  const agents = inbox.data?.agents || []

  const destinations = agents.flatMap(agent =>
    (agent.endpoints.length ? agent.endpoints : [agent.default]).map(endpoint => ({
      agent: agent.name,
      endpoint
    }))
  )

  const selected = rows.find(row => row.handoff_id === selectedId) || null
  const effectiveTarget = target || destinations[0]?.endpoint || ''
  const attentionCount = rows.filter(row => row.needs_attention).length

  const refresh = async () => {
    await Promise.all([inbox.refetch(), selectedId ? detail.refetch() : Promise.resolve()])
  }

  const create = async () => {
    if (!effectiveTarget || !message.trim() || mutation) {
      return
    }

    setMutation('create')
    setMutationFailed(false)

    try {
      await requestHandoff(route, profile, 'agent_handoff.create', {
        message: message.trim(),
        request_id: operationId('desktop-create'),
        target: effectiveTarget
      })
      setMessage('')
      await refresh()
    } catch {
      setMutationFailed(true)
    } finally {
      setMutation('')
    }
  }

  const command = async (kind: string, extra: Record<string, unknown> = {}) => {
    if (!selected || mutation) {
      return
    }

    setMutation(kind)
    setMutationFailed(false)

    try {
      await requestHandoff(route, profile, 'agent_handoff.command', {
        command_id: operationId(`desktop-${kind}`),
        handoff_id: selected.handoff_id,
        kind,
        ...extra
      })
      await refresh()
    } catch {
      setMutationFailed(true)
    } finally {
      setMutation('')
    }
  }

  const submitFollowUp = async () => {
    const text = followUp.trim()

    if (!text) {
      return
    }

    await command('message', {
      correlation_id: operationId('desktop-follow-up'),
      text
    })
    setFollowUp('')
  }

  return (
    <section aria-label="Handoffs" className="mx-1.5 mb-1 rounded-md border border-(--ui-stroke-tertiary)">
      <button
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left text-[0.6875rem] font-semibold uppercase tracking-wider text-(--ui-text-tertiary)"
        onClick={() => setOpen(value => !value)}
        type="button"
      >
        <Codicon name={open ? 'chevron-down' : 'chevron-right'} />
        <span className="flex-1">Handoffs</span>
        {attentionCount ? (
          <span aria-label={`${attentionCount} handoffs need attention`} className="text-amber-600 dark:text-amber-300">
            {attentionCount}
          </span>
        ) : null}
      </button>
      {open ? (
        <div className="grid max-h-80 gap-1 overflow-y-auto border-t border-(--ui-stroke-tertiary) p-1.5 text-xs">
          {unavailable || (inbox.error && !inbox.data) ? (
            <div className="px-1 py-2 text-(--ui-text-tertiary)">Handoffs unavailable.</div>
          ) : inbox.isLoading ? (
            <div className="flex justify-center py-2">
              <GlyphSpinner className="text-(--ui-text-tertiary)" spinner="breathe" />
            </div>
          ) : (
            <>
              {inbox.error ? (
                <div className="px-1 text-[0.6875rem] text-amber-600 dark:text-amber-300">
                  Refresh failed — showing the last good inbox.
                </div>
              ) : null}
              {agents.length ? (
                <div className="grid gap-1 rounded bg-(--chrome-action-hover) p-1.5">
                  <select
                    aria-label="Handoff destination"
                    className="min-w-0 rounded border border-(--ui-stroke-tertiary) bg-transparent px-1.5 py-1"
                    onChange={event => setTarget(event.target.value)}
                    value={effectiveTarget}
                  >
                    {destinations.map(destination => (
                      <option key={`${destination.agent}:${destination.endpoint}`} value={destination.endpoint}>
                        {destination.agent} · {destination.endpoint}
                      </option>
                    ))}
                  </select>
                  <textarea
                    aria-label="Handoff message"
                    className="min-h-14 resize-y rounded border border-(--ui-stroke-tertiary) bg-transparent p-1.5"
                    maxLength={16000}
                    onChange={event => setMessage(event.target.value)}
                    placeholder="Message an agent…"
                    value={message}
                  />
                  <Button
                    disabled={!effectiveTarget || !message.trim() || Boolean(mutation)}
                    onClick={() => void create()}
                    size="sm"
                    variant="secondary"
                  >
                    Send handoff
                  </Button>
                </div>
              ) : null}
              {!rows.length ? (
                <div className="px-1 py-2 text-(--ui-text-tertiary)">No handoffs yet.</div>
              ) : (
                <div className="grid gap-1">
                  {rows.map(row => (
                    <button
                      aria-label={`Open handoff ${row.handoff_id}`}
                      className="grid gap-0.5 rounded px-1.5 py-1 text-left hover:bg-(--chrome-action-hover)"
                      key={row.handoff_id}
                      onClick={() => setSelectedId(row.handoff_id)}
                      type="button"
                    >
                      <span className="truncate font-medium">{row.endpoint}</span>
                      <span className="flex flex-wrap items-center gap-x-2 text-[0.6875rem] text-(--ui-text-tertiary)">
                        <span>{row.mechanism || 'pending'}</span>
                        <span>{row.phase}</span>
                        <span>{elapsed(row.age_seconds)}</span>
                        {row.needs_attention ? (
                          <span className="text-amber-600 dark:text-amber-300">Needs Attention</span>
                        ) : null}
                      </span>
                    </button>
                  ))}
                </div>
              )}
              {selected ? (
                <div className="grid gap-1.5 border-t border-(--ui-stroke-tertiary) px-1 pt-1.5">
                  <div className="truncate font-medium">{selected.endpoint}</div>
                  {detail.isLoading ? <GlyphSpinner className="text-(--ui-text-tertiary)" spinner="breathe" /> : null}
                  {detail.error ? <div className="text-(--ui-text-tertiary)">Evidence unavailable.</div> : null}
                  {detail.data?.terminal_summary ? (
                    <div className="text-(--ui-text-tertiary)">
                      Result: {detail.data.terminal_summary.size_bytes} B · {detail.data.terminal_summary.media_type}
                    </div>
                  ) : null}
                  {detail.data?.events?.length ? (
                    <ol aria-label="Handoff evidence" className="grid gap-0.5">
                      {detail.data.events.map(event => (
                        <li className="text-[0.6875rem] text-(--ui-text-tertiary)" key={event.event_id}>
                          {event.kind} · {event.phase_before || 'new'} → {event.phase_after}
                        </li>
                      ))}
                    </ol>
                  ) : null}
                  {detail.data?.approval && selected.actions.includes('respond') ? (
                    <div className="flex flex-wrap gap-1">
                      {detail.data.approval.choices.map(choice => (
                        <Button
                          disabled={Boolean(mutation)}
                          key={choice}
                          onClick={() =>
                            void command('respond', {
                              choice,
                              request_id: detail.data!.approval!.request_id
                            })
                          }
                          size="sm"
                          variant="secondary"
                        >
                          {choice}
                        </Button>
                      ))}
                    </div>
                  ) : null}
                  {selected.actions.includes('message') ? (
                    <div className="grid gap-1">
                      <textarea
                        aria-label="Follow-up message"
                        className="min-h-12 resize-y rounded border border-(--ui-stroke-tertiary) bg-transparent p-1.5"
                        maxLength={16000}
                        onChange={event => setFollowUp(event.target.value)}
                        value={followUp}
                      />
                      <Button
                        disabled={!followUp.trim() || Boolean(mutation)}
                        onClick={() => void submitFollowUp()}
                        size="sm"
                        variant="secondary"
                      >
                        Send follow-up
                      </Button>
                    </div>
                  ) : null}
                  <div className="flex flex-wrap gap-1">
                    {selected.actions.includes('reconcile') ? (
                      <Button
                        disabled={Boolean(mutation)}
                        onClick={() => void command('reconcile')}
                        size="sm"
                        variant="secondary"
                      >
                        Reconcile
                      </Button>
                    ) : null}
                    {selected.actions.includes('cancel') ? (
                      <Button
                        disabled={Boolean(mutation)}
                        onClick={() => void command('cancel')}
                        size="sm"
                        variant="secondary"
                      >
                        Cancel
                      </Button>
                    ) : null}
                    {selected.actions.includes('acknowledge') ? (
                      <Button
                        disabled={Boolean(mutation)}
                        onClick={() => void command('acknowledge')}
                        size="sm"
                        variant="secondary"
                      >
                        Acknowledge
                      </Button>
                    ) : null}
                  </div>
                  {mutationFailed ? (
                    <div aria-live="polite" className="text-amber-600 dark:text-amber-300">
                      Handoff action failed.
                    </div>
                  ) : null}
                </div>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </section>
  )
}
