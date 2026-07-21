import { type QueryClient, queryOptions, useQuery } from '@tanstack/react-query'

import { preflightWorkflow } from '@/lib/hermes-api'
import type { WorkflowCatalogSource } from '@/types/hermes'

const WORKFLOW_DETAIL_STALE_TIME_MS = 30_000

export function workflowDetailQueryKey(name: string, source: WorkflowCatalogSource, profile: null | string) {
  return ['workflow-detail', profile ?? 'default', source, name] as const
}

export function workflowDetailQueryOptions(name: string, source: WorkflowCatalogSource, profile: null | string) {
  return queryOptions({
    queryFn: () => preflightWorkflow(name, source, profile),
    queryKey: workflowDetailQueryKey(name, source, profile),
    retry: false,
    staleTime: WORKFLOW_DETAIL_STALE_TIME_MS
  })
}

export function useWorkflowDetailQuery(name: string, source: WorkflowCatalogSource, profile: null | string) {
  return useQuery(workflowDetailQueryOptions(name, source, profile))
}

export function cancelPendingWorkflowDetailQuery(
  client: QueryClient,
  name: string,
  source: WorkflowCatalogSource,
  profile: null | string
) {
  const queryKey = workflowDetailQueryKey(name, source, profile)
  const state = client.getQueryState(queryKey)

  if (state?.fetchStatus === 'fetching' && state.data === undefined) {
    void client.cancelQueries({ exact: true, queryKey })
  }
}
