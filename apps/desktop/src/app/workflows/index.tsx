import { useStore } from '@nanostores/react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'

import { ActivityBoard } from '@/components/activity-board/activity-board'
import type { ActivityBoardCard } from '@/components/activity-board/types'
import { PageLoader } from '@/components/page-loader'
import { Button } from '@/components/ui/button'
import {
  executeWorkflowCleanup,
  getApiRequestProfile,
  getWorkflowRun,
  listWorkflowAttention,
  listWorkflowEvents,
  listWorkflowRuns,
  mutateWorkflowRun,
  previewWorkflowCleanup
} from '@/hermes'
import { useI18n } from '@/i18n'
import { notify } from '@/store/notifications'
import { ensureGatewayProfile } from '@/store/profile'
import type { WorkflowDefinition, WorkflowEventPage, WorkflowRunPage, WorkflowRunView } from '@/types/hermes'

import { PAGE_INSET_X } from '../layout-constants'

import { workflowBoardModel } from './adapter'
import { AttentionInbox } from './attention-inbox'
import { WorkflowCatalog } from './catalog'
import { cancelPendingWorkflowDetailQuery } from './detail-query'
import { ReviewRunDialog } from './review-run-dialog'
import { $workflowSelectedRunId, selectWorkflowRun } from './store'
import { ViewWorkflowDialog } from './view-workflow-dialog'
import { WorkflowRunDrawer } from './workflow-run-drawer'
import { WorkflowViewHeader } from './workflow-view-header'

function isConflict(error: unknown): boolean {
  if (typeof error === 'object' && error !== null && 'statusCode' in error) {
    return (error as { statusCode?: unknown }).statusCode === 409
  }

  return error instanceof Error && /^409(?:\D|$)/.test(error.message)
}

export function loadWorkflowRunPage(view: WorkflowRunView, cursor?: string): Promise<WorkflowRunPage> {
  if (view === 'workflows') {
    return Promise.reject(new Error('The workflows catalog view does not list workflow runs.'))
  }

  return listWorkflowRuns(cursor, view)
}

export function WorkflowsView() {
  const { t } = useI18n()
  const requestProfile = getApiRequestProfile()
  const profile = requestProfile ?? 'default'
  const queryClient = useQueryClient()
  const selectedRunId = useStore($workflowSelectedRunId)
  const actionInFlight = useRef(false)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const pageRef = useRef<HTMLElement>(null)
  const mounted = useRef(true)
  const reviewGeneration = useRef(0)
  const runReturnFocusRef = useRef<HTMLButtonElement | null>(null)
  const [actionPending, setActionPending] = useState(false)
  const [isVisible, setIsVisible] = useState(() => document.visibilityState === 'visible')
  const [view, setView] = useState<WorkflowRunView>('workflows')

  const [viewIntent, setViewIntent] = useState<null | {
    profile: string
    returnFocusTo: HTMLElement | null
    workflow: WorkflowDefinition
  }>(null)

  const [reviewIntent, setReviewIntent] = useState<null | {
    generation: number
    profile: string
    returnFocusTo: HTMLElement | null
    workflow: WorkflowDefinition
  }>(null)

  const closeReview = () => {
    reviewGeneration.current += 1

    if (reviewIntent) {
      cancelPendingWorkflowDetailQuery(
        queryClient,
        reviewIntent.workflow.name,
        reviewIntent.workflow.source,
        reviewIntent.profile
      )
    }

    setReviewIntent(null)
  }

  const activeElement = () => (document.activeElement instanceof HTMLElement ? document.activeElement : null)

  const openReviewForProfile = (
    workflow: WorkflowDefinition,
    intentProfile: string,
    returnFocusTo = activeElement()
  ) => {
    reviewGeneration.current += 1
    setViewIntent(null)
    setReviewIntent({ generation: reviewGeneration.current, profile: intentProfile, returnFocusTo, workflow })
  }

  const openReview = (workflow: WorkflowDefinition) => openReviewForProfile(workflow, profile)

  const openView = (workflow: WorkflowDefinition) => {
    setReviewIntent(null)
    setViewIntent({ profile, returnFocusTo: activeElement(), workflow })
  }

  const closeView = () => {
    if (viewIntent) {
      cancelPendingWorkflowDetailQuery(
        queryClient,
        viewIntent.workflow.name,
        viewIntent.workflow.source,
        viewIntent.profile
      )
    }

    setViewIntent(null)
  }

  // eslint-disable-next-line no-restricted-syntax -- mount/generation lifecycle guards, not an atom mirror
  useEffect(() => {
    mounted.current = true

    return () => {
      mounted.current = false
      reviewGeneration.current += 1
    }
  }, [])

  useEffect(() => {
    const onVisibilityChange = () => setIsVisible(document.visibilityState === 'visible')

    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => document.removeEventListener('visibilitychange', onVisibilityChange)
  }, [])

  const runs = useInfiniteQuery({
    enabled: view !== 'workflows',
    getNextPageParam: (page: WorkflowRunPage) => page.next_cursor ?? undefined,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => loadWorkflowRunPage(view, pageParam as string | undefined),
    queryKey: ['workflow-runs', profile, view],
    refetchInterval: () => (isVisible ? 20_000 : false)
  })

  const attention = useQuery({
    enabled: view !== 'workflows',
    queryFn: listWorkflowAttention,
    queryKey: ['workflow-attention', profile],
    refetchInterval: () => (isVisible ? 20_000 : false)
  })

  const selected = useQuery({
    enabled: view !== 'workflows' && Boolean(selectedRunId),
    queryFn: () => getWorkflowRun(selectedRunId!),
    queryKey: ['workflow-run', profile, selectedRunId],
    refetchInterval: () => (isVisible ? 20_000 : false)
  })

  const eventQueryKey = useMemo(() => ['workflow-events', profile, selectedRunId] as const, [profile, selectedRunId])

  const events = useQuery({
    enabled: view !== 'workflows' && Boolean(selectedRunId) && isVisible,
    queryFn: async () => {
      const previous = queryClient.getQueryData<WorkflowEventPage>(eventQueryKey)
      const page = await listWorkflowEvents(selectedRunId!, previous?.next_cursor ?? 0)

      if (!previous || page.cursor_reset) {
        return page
      }

      return { ...page, events: [...previous.events, ...page.events].slice(-200) }
    },
    queryKey: eventQueryKey,
    refetchInterval: () => (isVisible ? 1_000 : false)
  })

  useEffect(() => {
    if (!isVisible || !selectedRunId) {
      void queryClient.cancelQueries({ exact: true, queryKey: eventQueryKey })
    }

    return () => {
      void queryClient.cancelQueries({ exact: true, queryKey: eventQueryKey })
    }
  }, [eventQueryKey, isVisible, queryClient, selectedRunId])

  const mutation = useMutation({
    mutationFn: ({ action, body = {} }: { action: string; body?: Record<string, unknown> }) =>
      mutateWorkflowRun(selectedRunId!, action, {
        ...body,
        expected_version: selected.data?.state_version ?? -1,
        interaction_id: selected.data?.pending_interaction?.interaction_id
      }),
    onError: async error => {
      if (!isConflict(error)) {
        return
      }

      await queryClient.refetchQueries({
        exact: true,
        queryKey: ['workflow-run', profile, selectedRunId]
      })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['workflow-runs', profile] }),
        queryClient.invalidateQueries({ queryKey: ['workflow-attention', profile] }),
        queryClient.invalidateQueries({ queryKey: eventQueryKey })
      ])
    },
    onSuccess: run => {
      queryClient.setQueryData(['workflow-run', profile, selectedRunId], run)
      void queryClient.invalidateQueries({ queryKey: ['workflow-runs', profile] })
      void queryClient.invalidateQueries({ queryKey: ['workflow-attention', profile] })
    }
  })

  const cleanupPreview = useMutation({ mutationFn: () => previewWorkflowCleanup('7d') })

  const cleanupExecute = useMutation({
    mutationFn: (token: string) => executeWorkflowCleanup(token, '7d'),
    onSuccess: () => {
      cleanupPreview.reset()
      void queryClient.invalidateQueries({ queryKey: ['workflow-runs', profile] })
    }
  })

  const mutateRun = (action: string, body?: Record<string, unknown>) => {
    if (actionInFlight.current) {
      return
    }

    actionInFlight.current = true
    setActionPending(true)
    mutation.mutate(
      { action, body },
      {
        onSettled: () => {
          actionInFlight.current = false
          setActionPending(false)
        }
      }
    )
  }

  const pages = (runs.data?.pages ?? []) as WorkflowRunPage[]
  const runItems = pages.flatMap(page => page.runs)
  const nextCursor = pages.at(-1)?.next_cursor ?? null

  const model = useMemo(
    () =>
      workflowBoardModel(runItems, {
        nextCursor,
        scheduledLabel: t.operations.workflowScheduled,
        scopeLabel: t.operations.workflows,
        stale: runs.isError
      }),
    [nextCursor, runItems, runs.isError, t.operations.workflowScheduled, t.operations.workflows]
  )

  const loadedRunCount = model.columns.reduce((total, column) => total + column.cards.length, 0)

  const openRunId = (runId: string, origin?: HTMLButtonElement) => {
    runReturnFocusRef.current = origin ?? null
    selectWorkflowRun(runId)
  }

  const openRun = (card: ActivityBoardCard, origin?: HTMLButtonElement) => openRunId(card.id, origin)

  const closeRunDrawer = () => {
    const target = runReturnFocusRef.current
    const restoreFocus = pageRef.current?.contains(activeElement()) ?? false

    runReturnFocusRef.current = null
    selectWorkflowRun(null)

    if (!restoreFocus) {
      return
    }

    requestAnimationFrame(() => {
      if (target?.isConnected) {
        target.focus()
      } else {
        headingRef.current?.focus()
      }
    })
  }

  const changeView = (next: WorkflowRunView) => {
    runReturnFocusRef.current = null
    selectWorkflowRun(null)
    setView(next)
  }

  if (view !== 'workflows' && runs.isError && !runs.data) {
    return <p className={PAGE_INSET_X}>{t.operations.workflowUnavailable}</p>
  }

  return (
    <main
      aria-busy={
        runs.isFetching ||
        attention.isFetching ||
        selected.isFetching ||
        mutation.isPending ||
        cleanupPreview.isPending ||
        cleanupExecute.isPending
      }
      className={`relative flex h-full min-h-0 min-w-0 flex-col overflow-hidden py-6 ${PAGE_INSET_X}`}
      ref={pageRef}
    >
      <WorkflowViewHeader
        headingRef={headingRef}
        loadedRunCount={loadedRunCount}
        onViewChange={changeView}
        view={view}
      />
      {view === 'workflows' ? (
        <div className="mt-4 min-h-0 flex-1 overflow-y-auto">
          <WorkflowCatalog onRunWorkflow={openReview} onViewWorkflow={openView} />
        </div>
      ) : (
        <div className="mt-4 flex min-h-0 flex-1 flex-col overflow-hidden" data-workflow-run-view>
          {runs.isLoading && !runs.data ? (
            <PageLoader className="min-h-0 flex-1" />
          ) : (
            <>
              <AttentionInbox items={attention.data?.items ?? []} onOpenRun={openRunId} />
              <div className="min-h-0 flex-1" data-workflow-board-shell>
                <ActivityBoard
                  collapseScope={view}
                  laneCopy={{
                    collapse: t.operations.workflowLaneCollapse,
                    empty: t.operations.workflowLaneEmpty,
                    expand: t.operations.workflowLaneExpand
                  }}
                  layout="collapsible-lanes"
                  model={model}
                  onLoadMore={() => void runs.fetchNextPage()}
                  onOpenCard={openRun}
                  selectedCardId={selectedRunId}
                />
              </div>
              {(view === 'history' || view === 'archive') && (
                <section aria-label={t.operations.cleanup} className="mt-6 shrink-0 border-t border-(--ui-border) pt-4">
                  <h2 className="text-sm font-medium">{t.operations.cleanup}</h2>
                  <p className="mt-1 text-xs text-(--ui-text-secondary)">{t.operations.cleanupExplanation}</p>
                  <Button
                    className="mt-3"
                    disabled={cleanupPreview.isPending || cleanupExecute.isPending}
                    onClick={() => cleanupPreview.mutate()}
                    size="sm"
                    variant="secondary"
                  >
                    {t.operations.inspectCleanupImpact}
                  </Button>
                  {cleanupPreview.data && (
                    <div className="mt-3 text-sm" role="status">
                      <p>{t.operations.cleanupImpact(cleanupPreview.data.run_ids.length, cleanupPreview.data.files)}</p>
                      {cleanupPreview.data.confirmation_token ? (
                        <Button
                          className="mt-2"
                          disabled={cleanupExecute.isPending}
                          onClick={() => cleanupExecute.mutate(cleanupPreview.data!.confirmation_token!)}
                          size="sm"
                          variant="destructive"
                        >
                          {t.operations.executeCleanup}
                        </Button>
                      ) : (
                        <p className="mt-1 text-(--ui-text-secondary)">{t.operations.cleanupBlocked}</p>
                      )}
                    </div>
                  )}
                </section>
              )}
            </>
          )}
        </div>
      )}
      {view !== 'workflows' && selectedRunId ? (
        <WorkflowRunDrawer
          actionsDisabled={actionPending || mutation.isPending || selected.isError}
          error={selected.error}
          events={events.data?.events}
          loading={selected.isLoading}
          onAction={mutateRun}
          onClose={closeRunDrawer}
          run={selected.data ?? null}
          selectedRunId={selectedRunId}
        />
      ) : null}
      {reviewIntent ? (
        <ReviewRunDialog
          onClose={closeReview}
          onRunLocated={async (runId, disposition, scheduled) => {
            await ensureGatewayProfile(reviewIntent.profile)

            if (!mounted.current || reviewGeneration.current !== reviewIntent.generation) {
              return
            }

            notify({
              kind: 'success',
              message:
                disposition === 'existing'
                  ? t.operations.workflowRunAlreadyRunning
                  : scheduled
                    ? t.operations.workflowScheduled
                    : t.operations.workflowRunStarted
            })
            selectWorkflowRun(runId)
            setView('board')
            void queryClient.invalidateQueries({
              queryKey: ['workflow-runs', reviewIntent.profile]
            })
            closeReview()
          }}
          profile={reviewIntent.profile}
          returnFocusTo={reviewIntent.returnFocusTo}
          workflow={reviewIntent.workflow}
        />
      ) : null}
      {viewIntent ? (
        <ViewWorkflowDialog
          onClose={closeView}
          onRun={() => openReviewForProfile(viewIntent.workflow, viewIntent.profile, viewIntent.returnFocusTo)}
          profile={viewIntent.profile}
          workflow={viewIntent.workflow}
        />
      ) : null}
    </main>
  )
}
