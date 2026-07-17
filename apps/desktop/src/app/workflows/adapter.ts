import type { ActivityBoardColumn, ActivityBoardModel } from '@/components/activity-board/types'
import type { WorkflowRunSnapshot } from '@/types/hermes'

const COLUMNS = [
  ['queued', 'Queued'],
  ['active', 'Active'],
  ['attention', 'Needs attention'],
  ['completed', 'Completed'],
  ['stopped', 'Failed / stopped']
] as const

function columnId(run: WorkflowRunSnapshot): (typeof COLUMNS)[number][0] {
  if (run.status === 'queued') {return 'queued'}

  if (run.status === 'paused') {return 'attention'}

  if (run.status === 'succeeded') {return 'completed'}

  if (['failed', 'cancelled', 'abandoned', 'interrupted'].includes(run.status)) {return 'stopped'}

  return 'active'
}

function health(run: WorkflowRunSnapshot) {
  if (run.status === 'paused') {return 'attention' as const}

  if (run.status === 'failed') {return 'failed' as const}

  if (['succeeded', 'cancelled', 'abandoned'].includes(run.status)) {return 'terminal' as const}

  if (run.health.includes('wait')) {return 'waiting' as const}

  if (run.health.includes('stale')) {return 'stale' as const}

  return 'healthy' as const
}

export function workflowBoardModel(
  runs: readonly WorkflowRunSnapshot[],
  options: { nextCursor?: null | string; scopeLabel: string; stale?: boolean } = { scopeLabel: 'Workflows' }
): ActivityBoardModel {
  const columns: ActivityBoardColumn[] = COLUMNS.map(([id, label]) => {
    const selected = runs.filter(run => columnId(run) === id)

    return {
      cards: selected.map(run => ({
        ariaDescription: `${run.workflow}, ${run.status}`,
        badges: [{ label: `${run.progress.completed_nodes}/${run.progress.total_nodes}` }],
        exactState: run.status,
        health: health(run),
        id: run.run_id,
        title: run.workflow,
        updatedAt: Date.parse(run.updated_at)
      })),
      count: selected.length,
      id,
      label,
      nextCursor: options.nextCursor ?? null
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

export const WORKFLOW_NODE_COLUMNS = {
  waiting: ['pending'],
  starting: ['ready', 'claimed'],
  active: ['running', 'waiting_retry'],
  attention: ['paused'],
  done: ['succeeded', 'skipped'],
  stopped: ['failed', 'cancelled', 'interrupted']
} as const
