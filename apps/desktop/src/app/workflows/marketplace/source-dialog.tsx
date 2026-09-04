import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'

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
import type { MarketplaceOperationController } from './use-marketplace-operation'

interface SourceDraft {
  enabled: boolean
  name: string
  ref: string
  repositoryUrl: string
}

type ActionError = 'auth' | 'generic'

const EMPTY_DRAFT: SourceDraft = { enabled: true, name: '', ref: '', repositoryUrl: '' }

function errorCode(error: unknown): null | string {
  return typeof error === 'object' && error !== null && 'code' in error && typeof error.code === 'string'
    ? error.code
    : null
}

function sourceStatus(
  source: WorkflowMarketplaceSourceRecord
): 'auth' | 'disabled' | 'fresh' | 'never' | 'stale' | 'unavailable' {
  if (!source.enabled) {
    return 'disabled'
  }

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
  const actionGuardsRef = useRef(new Set<string>())
  const retryRef = useRef<null | (() => Promise<void>)>(null)
  const [draft, setDraft] = useState<SourceDraft>(EMPTY_DRAFT)
  const [editing, setEditing] = useState<null | string>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [removeName, setRemoveName] = useState<null | string>(null)
  const [busy, setBusy] = useState<null | string>(null)
  const [refreshAllBusy, setRefreshAllBusy] = useState(false)
  const [actionError, setActionError] = useState<null | ActionError>(null)

  const sources = useQuery({
    enabled: open && supported,
    queryFn: () => listWorkflowMarketplaceSources(scope),
    queryKey: marketplaceKeys.sources(scopeKey),
    retry: false
  })

  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: marketplaceKeys.sources(scopeKey) }),
      queryClient.invalidateQueries({ queryKey: marketplaceKeys.searchRoot(scopeKey) })
    ])
  }

  const runAction = async (identity: string, action: () => Promise<unknown>): Promise<void> => {
    if (actionGuardsRef.current.has(identity)) {
      return
    }

    actionGuardsRef.current.add(identity)
    setBusy(identity)
    setActionError(null)
    retryRef.current = () => runAction(identity, action)

    try {
      await action()
      retryRef.current = null
      await invalidate()
    } catch (error) {
      setActionError(errorCode(error) === 'source_authentication_failed' ? 'auth' : 'generic')
    } finally {
      actionGuardsRef.current.delete(identity)
      setBusy(current => (current === identity ? null : current))
    }
  }

  const beginAdd = () => {
    setEditing(null)
    setDraft(EMPTY_DRAFT)
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

    await runAction(`save:${editing ?? draft.name}`, action)

    if (retryRef.current === null) {
      setFormOpen(false)
    }
  }

  const refreshOne = async (name: string) => {
    await operations.start(name, () => refreshWorkflowMarketplaceSource(name, scope))
  }

  const refreshAll = async () => {
    if (refreshAllBusy) {
      return
    }

    setRefreshAllBusy(true)

    try {
      for (const source of sources.data?.sources ?? []) {
        if (source.enabled) {
          await refreshOne(source.name)
        }
      }
    } finally {
      setRefreshAllBusy(false)
    }
  }

  const statusLabel = (source: WorkflowMarketplaceSourceRecord) => {
    const status = sourceStatus(source)

    if (status === 'fresh') {
      return copy.workflowMarketplaceFresh
    }

    if (status === 'disabled') {
      return copy.workflowMarketplaceDisabled
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
                <Button onClick={beginAdd} ref={addButtonRef} size="sm" type="button">
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

              {formOpen ? (
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
                    <Button disabled={busy?.startsWith('save:')} size="sm" type="submit">
                      {copy.workflowMarketplaceSaveSource}
                    </Button>
                    <Button onClick={() => setFormOpen(false)} size="sm" type="button" variant="ghost">
                      {t.common.cancel}
                    </Button>
                  </div>
                </form>
              ) : null}

              {actionError ? (
                <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs" role="alert">
                  <p className="font-medium">
                    {actionError === 'auth'
                      ? copy.workflowMarketplaceSourceAuthTitle
                      : copy.workflowMarketplaceSafeError}
                  </p>
                  {actionError === 'auth' ? <p>{copy.workflowMarketplaceAuthGuidance}</p> : null}
                  <Button onClick={() => void retryRef.current?.()} size="sm" type="button" variant="secondary">
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
                              onClick={() => void refreshOne(source.name)}
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
                              {active.state === 'failed' ? t.common.failed : copy.workflowMarketplaceUnavailable}
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
                            disabled={busy === `enabled:${source.name}`}
                            onClick={() =>
                              void runAction(`enabled:${source.name}`, () =>
                                setWorkflowMarketplaceSourceEnabled(source.name, !source.enabled, scope)
                              )
                            }
                            size="sm"
                            type="button"
                            variant="secondary"
                          >
                            {source.enabled ? copy.workflowMarketplaceDisabled : copy.workflowMarketplaceEnabled}
                          </Button>
                          <Button
                            aria-label={copy.workflowMarketplaceRemoveSource(source.name)}
                            onClick={() => setRemoveName(source.name)}
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
        confirmLabel={copy.workflowMarketplaceRemoveSourceAction}
        description={copy.workflowMarketplaceRemoveDescription}
        destructive
        dismissOnConfirm
        onClose={() => setRemoveName(null)}
        onConfirm={async () => {
          if (removeName === null) {
            return
          }

          await runAction(`remove:${removeName}`, () => removeWorkflowMarketplaceSource(removeName, scope))

          if (retryRef.current !== null) {
            throw new Error(copy.workflowMarketplaceSafeError)
          }
        }}
        open={removeName !== null}
        title={copy.workflowMarketplaceRemoveTitle(removeName ?? '')}
      />
    </>
  )
}
