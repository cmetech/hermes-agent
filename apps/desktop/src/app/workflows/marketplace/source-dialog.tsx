import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { profileScopeKey } from '@/api/client'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import {
  addWorkflowMarketplaceSource,
  listWorkflowMarketplaceSources,
  refreshWorkflowMarketplaceSource,
  removeWorkflowMarketplaceSource,
  setWorkflowMarketplaceSourceEnabled,
  updateWorkflowMarketplaceSource
} from '@/hermes'
import type { WorkflowMarketplaceScope } from '@/hermes'
import { useI18n } from '@/i18n'
import { Pencil, Plus, RefreshCw, Trash2 } from '@/lib/icons'
import type { WorkflowMarketplaceSourceRecord } from '@/types/hermes'

import { marketplaceKeys } from './query-keys'
import type { MarketplaceOperationController, MarketplaceOperationOrigin } from './use-marketplace-operation'

interface SourceDraft {
  enabled: boolean
  name: string
  ref: string
  repositoryUrl: string
}

type ActionError = 'auth' | 'generic'

interface ScopedActionFailure extends MarketplaceOperationOrigin {
  identity: string
  kind: ActionError
  retry: () => Promise<boolean>
}

interface ScopedMutation extends MarketplaceOperationOrigin {
  identity: string
  token: object
}

interface ScopedRemoval extends MarketplaceOperationOrigin {
  name: string
}

const EMPTY_DRAFT: SourceDraft = { enabled: true, name: '', ref: '', repositoryUrl: '' }

function errorCode(error: unknown): null | string {
  return typeof error === 'object' && error !== null && 'code' in error && typeof error.code === 'string'
    ? error.code
    : null
}

function sourceStatus(
  source: WorkflowMarketplaceSourceRecord
): 'auth' | 'fresh' | 'incompatible' | 'malformed' | 'never' | 'stale' | 'unavailable' {
  if (source.refresh_state === 'fresh') {
    return 'fresh'
  }

  if (source.refresh_state === 'authentication-failed') {
    return 'auth'
  }

  if (source.refresh_state === null) {
    return 'never'
  }

  if (source.refresh_state === 'stale') {
    return 'stale'
  }

  if (source.refresh_state === 'malformed') {
    return 'malformed'
  }

  if (source.refresh_state === 'incompatible') {
    return 'incompatible'
  }

  return 'unavailable'
}

export interface ManageWorkflowSourcesDialogProps {
  onClose: () => void
  open: boolean
  operations: MarketplaceOperationController
  scope: WorkflowMarketplaceScope
  supported: boolean
}

export function ManageWorkflowSourcesDialog({
  onClose,
  open,
  operations,
  scope,
  supported
}: ManageWorkflowSourcesDialogProps) {
  const { t } = useI18n()
  const copy = t.operations
  const queryClient = useQueryClient()
  const scopeKey = profileScopeKey(scope)
  const addButtonRef = useRef<HTMLButtonElement>(null)
  const mutationLedgerRef = useRef(new Map<string, ScopedMutation>())
  const mountedRef = useRef(true)
  const actionGenerationRef = useRef(0)
  const renderedScopeKeyRef = useRef(scopeKey)
  const wasOpenRef = useRef(open)
  const refreshAllGuardRef = useRef<null | MarketplaceOperationOrigin>(null)
  const [draft, setDraft] = useState<SourceDraft>(EMPTY_DRAFT)
  const [editing, setEditing] = useState<null | string>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [formScopeKey, setFormScopeKey] = useState<null | string>(null)
  const [removeSource, setRemoveSource] = useState<null | ScopedRemoval>(null)
  const [, setMutationLedgerRevision] = useState(0)
  const [refreshAllBusyScope, setRefreshAllBusyScope] = useState<null | string>(null)
  const [actionFailure, setActionFailure] = useState<null | ScopedActionFailure>(null)

  if (renderedScopeKeyRef.current !== scopeKey) {
    renderedScopeKeyRef.current = scopeKey
    actionGenerationRef.current += 1
    refreshAllGuardRef.current = null
  }

  const currentActionOrigin = (): MarketplaceOperationOrigin => ({
    generation: actionGenerationRef.current,
    scopeKey
  })

  const actionOriginIsCurrent = (origin: MarketplaceOperationOrigin) =>
    mountedRef.current &&
    renderedScopeKeyRef.current === origin.scopeKey &&
    actionGenerationRef.current === origin.generation

  // eslint-disable-next-line no-restricted-syntax -- resets ephemeral dialog state at an exact backend scope boundary
  useEffect(() => {
    setDraft(EMPTY_DRAFT)
    setEditing(null)
    setFormOpen(false)
    setFormScopeKey(null)
    setRemoveSource(null)
    setRefreshAllBusyScope(null)
    setActionFailure(null)
  }, [scopeKey])

  // eslint-disable-next-line no-restricted-syntax -- lifecycle token resets on every setup so StrictMode replay remains actionable
  useEffect(() => {
    mountedRef.current = true

    return () => {
      mountedRef.current = false
      actionGenerationRef.current += 1
      refreshAllGuardRef.current = null
    }
  }, [])

  // eslint-disable-next-line no-restricted-syntax -- detects a closed-to-open transition so persisted operations reconcile on reopen
  useEffect(() => {
    if (open && !wasOpenRef.current && supported) {
      operations.reconcile()
    }
    wasOpenRef.current = open
  }, [open, operations, supported])

  const sources = useQuery({
    enabled: open && supported,
    queryFn: () => listWorkflowMarketplaceSources(scope),
    queryKey: marketplaceKeys.sources(scopeKey),
    retry: false
  })

  const invalidate = async (origin: MarketplaceOperationOrigin) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: marketplaceKeys.sources(origin.scopeKey) }),
      queryClient.invalidateQueries({ queryKey: marketplaceKeys.searchRoot(origin.scopeKey) })
    ])
  }

  const runAction = async (
    identity: string,
    action: () => Promise<unknown>,
    origin: MarketplaceOperationOrigin = currentActionOrigin()
  ): Promise<boolean> => {
    const attempt: ScopedMutation = { ...origin, identity, token: {} }

    if (!actionOriginIsCurrent(origin) || mutationLedgerRef.current.has(origin.scopeKey)) {
      return false
    }

    mutationLedgerRef.current.set(origin.scopeKey, attempt)
    setMutationLedgerRevision(revision => revision + 1)

    try {
      if (!actionOriginIsCurrent(origin)) {
        return false
      }

      await action()
      await invalidate(origin)

      if (!actionOriginIsCurrent(origin)) {
        return false
      }

      setActionFailure(current =>
        current?.identity === identity &&
        current.scopeKey === origin.scopeKey &&
        current.generation === origin.generation
          ? null
          : current
      )

      return true
    } catch (error) {
      if (actionOriginIsCurrent(origin)) {
        setActionFailure({
          generation: origin.generation,
          identity,
          kind: errorCode(error) === 'source_authentication_failed' ? 'auth' : 'generic',
          retry: () => runAction(identity, action, origin),
          scopeKey: origin.scopeKey
        })
      }

      return false
    } finally {
      if (mutationLedgerRef.current.get(origin.scopeKey)?.token === attempt.token) {
        mutationLedgerRef.current.delete(origin.scopeKey)
      }

      if (mountedRef.current) {
        setMutationLedgerRevision(revision => revision + 1)
      }
    }
  }

  const beginAdd = () => {
    setEditing(null)
    setDraft(EMPTY_DRAFT)
    setFormScopeKey(scopeKey)
    setFormOpen(true)
  }

  const beginEdit = (source: WorkflowMarketplaceSourceRecord) => {
    setEditing(source.name)
    setDraft({
      enabled: source.enabled,
      name: source.name,
      ref: source.ref ?? '',
      repositoryUrl: source.repository_url
    })
    setFormScopeKey(scopeKey)
    setFormOpen(true)
  }

  const save = async () => {
    if (
      !draft.name ||
      !draft.repositoryUrl ||
      draft.name.length > 64 ||
      draft.repositoryUrl.length > 4096 ||
      draft.ref.length > 1024
    ) {
      return
    }

    const action = editing
      ? () =>
          updateWorkflowMarketplaceSource(
            editing,
            { enabled: draft.enabled, ref: draft.ref || null, repositoryUrl: draft.repositoryUrl },
            scope
          )
      : () =>
          addWorkflowMarketplaceSource(
            { enabled: draft.enabled, name: draft.name, ref: draft.ref || null, repositoryUrl: draft.repositoryUrl },
            scope
          )

    const saved = await runAction(`save:${editing ?? draft.name}`, action)

    if (saved) {
      setFormOpen(false)
    }
  }

  const refreshOne = async (name: string) => {
    await operations.start(name, () => refreshWorkflowMarketplaceSource(name, scope))
  }

  const refreshAll = async () => {
    const origin = operations.captureOrigin()

    if (refreshAllGuardRef.current !== null || !operations.originIsCurrent(origin)) {
      return
    }

    refreshAllGuardRef.current = origin
    setRefreshAllBusyScope(scopeKey)
    const enabledSources = (sources.data?.sources ?? []).filter(source => source.enabled)

    try {
      for (const source of enabledSources) {
        if (!operations.originIsCurrent(origin) || renderedScopeKeyRef.current !== scopeKey) {
          break
        }

        await operations.start(source.name, () => refreshWorkflowMarketplaceSource(source.name, scope), origin)

        if (!operations.originIsCurrent(origin) || renderedScopeKeyRef.current !== scopeKey) {
          break
        }
      }
    } finally {
      if (refreshAllGuardRef.current === origin) {
        refreshAllGuardRef.current = null
      }

      if (operations.originIsCurrent(origin) && renderedScopeKeyRef.current === scopeKey) {
        setRefreshAllBusyScope(null)
      }
    }
  }

  const refreshAllBusy = refreshAllBusyScope === scopeKey
  const mutationBusy = mutationLedgerRef.current.has(scopeKey)
  const visibleActionFailure =
    actionFailure?.scopeKey === scopeKey && actionFailure.generation === actionGenerationRef.current
      ? actionFailure
      : null
  const visibleFormOpen = formOpen && formScopeKey === scopeKey
  const visibleRemoval = removeSource?.scopeKey === scopeKey ? removeSource : null

  const statusLabel = (source: WorkflowMarketplaceSourceRecord) => {
    const status = sourceStatus(source)

    if (status === 'fresh') {
      return copy.workflowMarketplaceFresh
    }

    if (status === 'auth') {
      return copy.workflowMarketplaceAuthenticationRequired
    }

    if (status === 'never') {
      return copy.workflowMarketplaceNeverRefreshed
    }

    if (status === 'stale') {
      return copy.workflowMarketplaceStale
    }

    if (status === 'malformed') {
      return copy.workflowMarketplaceMalformed
    }

    if (status === 'incompatible') {
      return copy.workflowMarketplaceIncompatible
    }

    return copy.workflowMarketplaceUnavailable
  }

  return (
    <>
      <Dialog onOpenChange={value => !value && onClose()} open={open}>
        <DialogContent
          className="max-w-3xl"
          onOpenAutoFocus={event => {
            event.preventDefault()
            addButtonRef.current?.focus()
          }}
        >
          <DialogHeader>
            <DialogTitle>{copy.workflowMarketplaceSourcesTitle}</DialogTitle>
            <DialogDescription>{copy.workflowMarketplaceSourcesDescription}</DialogDescription>
          </DialogHeader>

          {!supported ? (
            <p role="status">{copy.workflowMarketplaceUnsupportedSources}</p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <Button disabled={mutationBusy} onClick={beginAdd} ref={addButtonRef} size="sm" type="button">
                  <Plus className="size-4" />
                  {copy.workflowMarketplaceAddSource}
                </Button>
                <Button
                  aria-busy={refreshAllBusy}
                  disabled={refreshAllBusy || !sources.data?.sources.some(source => source.enabled)}
                  onClick={() => void refreshAll()}
                  size="sm"
                  type="button"
                  variant="secondary"
                >
                  <RefreshCw className="size-4" />
                  {copy.workflowMarketplaceRefreshAll}
                </Button>
              </div>

              {visibleFormOpen ? (
                <form
                  className="grid gap-3 rounded-lg border border-(--ui-stroke-tertiary) p-3 sm:grid-cols-2"
                  onSubmit={event => {
                    event.preventDefault()
                    void save()
                  }}
                >
                  <label className="grid gap-1 text-xs">
                    {copy.workflowMarketplaceSourceName}
                    <Input
                      disabled={editing !== null}
                      maxLength={64}
                      onChange={event => setDraft(current => ({ ...current, name: event.target.value }))}
                      required
                      value={draft.name}
                    />
                  </label>
                  <label className="grid gap-1 text-xs sm:col-span-2">
                    {copy.workflowMarketplaceRepositoryUrl}
                    <Input
                      maxLength={4096}
                      onChange={event => setDraft(current => ({ ...current, repositoryUrl: event.target.value }))}
                      required
                      value={draft.repositoryUrl}
                    />
                  </label>
                  <label className="grid gap-1 text-xs">
                    {copy.workflowMarketplaceGitRef}
                    <Input
                      maxLength={1024}
                      onChange={event => setDraft(current => ({ ...current, ref: event.target.value }))}
                      value={draft.ref}
                    />
                  </label>
                  <label className="flex items-center gap-2 self-end text-xs">
                    <input
                      checked={draft.enabled}
                      onChange={event => setDraft(current => ({ ...current, enabled: event.target.checked }))}
                      type="checkbox"
                    />
                    {copy.workflowMarketplaceEnabled}
                  </label>
                  <div className="flex gap-2 sm:col-span-2">
                    <Button disabled={mutationBusy} size="sm" type="submit">
                      {copy.workflowMarketplaceSaveSource}
                    </Button>
                    <Button onClick={() => setFormOpen(false)} size="sm" type="button" variant="ghost">
                      {t.common.cancel}
                    </Button>
                  </div>
                </form>
              ) : null}

              {visibleActionFailure ? (
                <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs" role="alert">
                  <p className="font-medium">
                    {visibleActionFailure.kind === 'auth'
                      ? copy.workflowMarketplaceSourceAuthTitle
                      : copy.workflowMarketplaceSafeError}
                  </p>
                  {visibleActionFailure.kind === 'auth' ? <p>{copy.workflowMarketplaceAuthGuidance}</p> : null}
                  <Button
                    disabled={mutationBusy}
                    onClick={() => void visibleActionFailure.retry()}
                    size="sm"
                    type="button"
                    variant="secondary"
                  >
                    {copy.workflowCatalogRetry}
                  </Button>
                </div>
              ) : null}

              {operations.errors.__reconcile__ ? (
                <div className="flex items-center gap-2 text-xs" role="alert">
                  <span>{copy.workflowMarketplaceRefreshStatusUnavailable}</span>
                  <Button onClick={operations.reconcile} size="sm" type="button" variant="secondary">
                    {copy.workflowCatalogRetry}
                  </Button>
                </div>
              ) : null}

              {sources.isError ? (
                <div className="flex items-center gap-2 text-xs" role="alert">
                  <span>{copy.workflowMarketplaceSafeError}</span>
                  <Button
                    disabled={sources.isFetching}
                    onClick={() => void sources.refetch()}
                    size="sm"
                    type="button"
                    variant="secondary"
                  >
                    {copy.workflowCatalogRetry}
                  </Button>
                </div>
              ) : sources.isPending ? (
                <p role="status">{t.common.loading}</p>
              ) : (
                <div className="grid gap-2">
                  {sources.data.sources.map(source => {
                    const active = operations.operationForSource(source.name)
                    const recovery = operations.errors[source.name]

                    return (
                      <article
                        aria-label={copy.workflowMarketplaceSourceArticle(source.name)}
                        className="grid gap-2 rounded-lg border border-(--ui-stroke-tertiary) p-3"
                        key={source.name}
                      >
                        <div className="flex min-w-0 flex-wrap items-center gap-2">
                          <strong>{source.name}</strong>
                          {!source.enabled ? (
                            <span className="rounded-full bg-muted px-2 py-0.5 text-xs">
                              {copy.workflowMarketplaceDisabled}
                            </span>
                          ) : null}
                          <span className="rounded-full bg-muted px-2 py-0.5 text-xs">{statusLabel(source)}</span>
                          {active && (active.state === 'pending' || active.state === 'running') ? (
                            <span role="status">{`${active.phase} ${active.progress}%`}</span>
                          ) : null}
                        </div>
                        <code className="truncate text-xs">{source.repository_url}</code>
                        <dl className="grid gap-1 text-xs sm:grid-cols-2">
                          <div>
                            <dt className="inline font-medium">{copy.workflowMarketplaceRef}: </dt>
                            <dd className="inline">{source.ref ?? copy.workflowMarketplaceDefaultBranch}</dd>
                          </div>
                          <div>
                            <dt className="inline font-medium">{copy.workflowMarketplaceCommit}: </dt>
                            <dd className="inline break-all">{source.resolved_commit ?? '—'}</dd>
                          </div>
                          <div>
                            <dd>
                              {source.verified_at
                                ? copy.workflowMarketplaceLastVerified(source.verified_at)
                                : copy.workflowMarketplaceNeverVerified}
                            </dd>
                          </div>
                          <div>
                            <dd>{copy.workflowMarketplacePackageCount(source.verified_package_count)}</dd>
                          </div>
                          {source.attempted_at ? (
                            <div>
                              <dd>{copy.workflowMarketplaceLastAttempted(source.attempted_at)}</dd>
                            </div>
                          ) : null}
                        </dl>
                        {source.diagnostic_code === 'source_authentication_failed' ? (
                          <div className="text-xs">
                            <p className="font-medium">{copy.workflowMarketplaceAuthenticationRequired}</p>
                            <p>{copy.workflowMarketplaceAuthGuidance}</p>
                          </div>
                        ) : null}
                        {recovery ? (
                          <div className="flex items-center gap-2 text-xs" role="status">
                            <span>
                              {recovery === 'evicted'
                                ? copy.workflowMarketplaceOperationEvicted
                                : copy.workflowMarketplaceRefreshStatusUnavailable}
                            </span>
                            <Button
                              onClick={() =>
                                void (recovery === 'status' && active
                                  ? operations.retry(active.id)
                                  : refreshOne(source.name))
                              }
                              size="sm"
                              type="button"
                              variant="secondary"
                            >
                              {copy.workflowCatalogRetry}
                            </Button>
                          </div>
                        ) : null}
                        {active?.state === 'failed' || active?.state === 'cancelled' ? (
                          <div className="flex items-center gap-2 text-xs" role="status">
                            <span>
                              {active.state === 'failed' ? t.common.failed : copy.workflowMarketplaceCancelled}
                            </span>
                            <Button
                              onClick={() => void refreshOne(source.name)}
                              size="sm"
                              type="button"
                              variant="secondary"
                            >
                              {copy.workflowCatalogRetry}
                            </Button>
                          </div>
                        ) : null}
                        <div className="flex flex-wrap gap-2">
                          <Button
                            aria-label={copy.workflowMarketplaceRefreshSource(source.name)}
                            disabled={!source.enabled || active?.state === 'pending' || active?.state === 'running'}
                            onClick={() => void refreshOne(source.name)}
                            size="sm"
                            type="button"
                            variant="secondary"
                          >
                            <RefreshCw className="size-4" />
                          </Button>
                          {active && (active.state === 'pending' || active.state === 'running') ? (
                            <Button
                              onClick={() => void operations.cancel(active.id)}
                              size="sm"
                              type="button"
                              variant="secondary"
                            >
                              {t.common.cancel}
                            </Button>
                          ) : null}
                          <Button
                            aria-label={copy.workflowMarketplaceEditSource(source.name)}
                            disabled={mutationBusy}
                            onClick={() => beginEdit(source)}
                            size="sm"
                            type="button"
                            variant="secondary"
                          >
                            <Pencil className="size-4" />
                          </Button>
                          <Button
                            aria-label={
                              source.enabled
                                ? copy.workflowMarketplaceDisableSource(source.name)
                                : copy.workflowMarketplaceEnableSource(source.name)
                            }
                            disabled={mutationBusy}
                            onClick={() =>
                              void runAction(`enabled:${source.name}`, () =>
                                setWorkflowMarketplaceSourceEnabled(source.name, !source.enabled, scope)
                              )
                            }
                            size="sm"
                            type="button"
                            variant="secondary"
                          >
                            {source.enabled
                              ? copy.workflowMarketplaceDisableAction
                              : copy.workflowMarketplaceEnableAction}
                          </Button>
                          <Button
                            aria-label={copy.workflowMarketplaceRemoveSource(source.name)}
                            disabled={mutationBusy}
                            onClick={() => setRemoveSource({ ...currentActionOrigin(), name: source.name })}
                            size="sm"
                            type="button"
                            variant="ghost"
                          >
                            <Trash2 className="size-4" />
                          </Button>
                        </div>
                      </article>
                    )
                  })}
                </div>
              )}
            </>
          )}
          <DialogFooter>
            <Button onClick={onClose} type="button" variant="secondary">
              {t.common.close}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        busyLabel={copy.workflowMarketplaceRemovingSource}
        confirmLabel={copy.workflowMarketplaceRemoveSourceAction}
        description={copy.workflowMarketplaceRemoveDescription}
        destructive
        doneLabel={copy.workflowMarketplaceSourceRemoved}
        onClose={() => setRemoveSource(null)}
        onConfirm={async () => {
          if (visibleRemoval === null || !actionOriginIsCurrent(visibleRemoval)) {
            return
          }

          const removed = await runAction(
            `remove:${visibleRemoval.name}`,
            () => removeWorkflowMarketplaceSource(visibleRemoval.name, scope),
            visibleRemoval
          )

          if (!removed && actionOriginIsCurrent(visibleRemoval)) {
            throw new Error(copy.workflowMarketplaceSafeError)
          }
        }}
        open={visibleRemoval !== null}
        title={copy.workflowMarketplaceRemoveTitle(visibleRemoval?.name ?? '')}
      />
    </>
  )
}
