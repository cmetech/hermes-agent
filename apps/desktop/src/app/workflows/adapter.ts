import type { ActivityBoardColumn, ActivityBoardModel } from '@/components/activity-board/types'
import type { WorkflowRunSnapshot } from '@/types/hermes'

const COLUMNS = [
  ['queued', 'Queued', 'var(--ui-blue)'],
  ['active', 'Active', 'var(--ui-green)'],
  ['attention', 'Needs attention', 'var(--ui-yellow)'],
  ['completed', 'Completed', 'var(--ui-text-tertiary)'],
  ['stopped', 'Failed / stopped', 'var(--ui-red)']
] as const

const ORIGIN_ICONS: Record<string, string> = {
  api: 'globe',
  background_agent: 'robot',
  chat: 'comment-discussion',
  cli: 'terminal',
  cron: 'calendar',
  desktop: 'device-desktop'
}

const ATTENTION_HEALTH = new Set(['coordinator_unavailable', 'stalled', 'storage_degraded'])

function columnId(run: WorkflowRunSnapshot): (typeof COLUMNS)[number][0] {
  if (run.status === 'queued') {
    return 'queued'
  }

  if (run.status === 'paused') {
    return 'attention'
  }

  if (ATTENTION_HEALTH.has(run.health)) {
    return 'attention'
  }

  if (run.status === 'succeeded') {
    return 'completed'
  }

  if (['failed', 'cancelled', 'abandoned', 'interrupted'].includes(run.status)) {
    return 'stopped'
  }

  return 'active'
}

function health(run: WorkflowRunSnapshot) {
  if (run.status === 'paused') {
    return 'attention' as const
  }

  if (run.status === 'failed') {
    return 'failed' as const
  }

  if (ATTENTION_HEALTH.has(run.health)) {
    return 'failed' as const
  }

  if (['succeeded', 'cancelled', 'abandoned'].includes(run.status)) {
    return 'terminal' as const
  }

  if (run.health.includes('wait')) {
    return 'waiting' as const
  }

  if (run.health.includes('stale')) {
    return 'stale' as const
  }

  return 'healthy' as const
}

function exactState(run: WorkflowRunSnapshot, scheduledLabel = 'Scheduled'): string {
  return run.presentation_state === 'scheduled_wait' ? scheduledLabel : run.status
}

export function workflowBoardModel(
  runs: readonly WorkflowRunSnapshot[],
  options: { nextCursor?: null | string; scheduledLabel?: string; scopeLabel: string; stale?: boolean } = {
    scopeLabel: 'Workflows'
  }
): ActivityBoardModel {
  const columns: ActivityBoardColumn[] = COLUMNS.map(([id, label, tone]) => {
    const selected = runs.filter(run => columnId(run) === id)

    return {
      cards: selected.map(run => ({
        ariaDescription: [
          run.workflow,
          exactState(run, options.scheduledLabel),
          run.health,
          run.provenance?.source,
          run.provenance?.assurance
        ]
          .filter(Boolean)
          .join(', '),
        badges: [
          ...(run.provenance
            ? [
                {
                  icon: ORIGIN_ICONS[run.provenance.source],
                  label: run.provenance.source,
                  tone: 'muted' as const
                }
              ]
            : []),
          ...(ATTENTION_HEALTH.has(run.health)
            ? [
                {
                  label: run.health.replaceAll('_', ' '),
                  tone: 'danger' as const
                }
              ]
            : []),
          ...(run.current_nodes?.[0] ? [{ label: run.current_nodes[0], tone: 'notice' as const }] : []),
          { label: `${run.progress.completed_nodes}/${run.progress.total_nodes}` }
        ],
        exactState: exactState(run, options.scheduledLabel),
        health: health(run),
        id: run.run_id,
        title: run.workflow,
        updatedAt: Date.parse(run.updated_at)
      })),
      count: selected.length,
      id,
      label,
      nextCursor: options.nextCursor ?? null,
      tone
    }
  })

  return {
    columns,
    revision: runs.map(run => `${run.run_id}:${run.state_version}`).join('|'),
    scopeLabel: options.scopeLabel,
    source: 'workflow',
    stale: options.stale ?? false
  }
}
