import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { profileScopeKey } from '@/api/client'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { SearchField } from '@/components/ui/search-field'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  getWorkflowMarketplaceCapabilities,
  getWorkflowMarketplaceOperation,
  inspectWorkflowPackage,
  isWorkflowMarketplaceUnsupportedError,
  listInstalledWorkflowPackages,
  listWorkflowMarketplaceSources,
  searchWorkflowPackages
} from '@/hermes'
import type { WorkflowMarketplaceScope } from '@/hermes'
import { useMediaQuery } from '@/hooks/use-media-query'
import { useI18n } from '@/i18n'
import { ChevronLeft } from '@/lib/icons'
import type { WorkflowMarketplaceOperation, WorkflowMarketplacePackageDetail } from '@/types/hermes'

import { installedProvenanceErrorKind, InstalledProvenanceNotice } from './installed-packages'
import { MarketplacePackageDetail } from './package-detail'
import { MarketplacePackageList, type MarketplacePackageSelection } from './package-list'
import { marketplaceKeys } from './query-keys'

const PAGE_LIMIT = 50
const NARROW_MARKETPLACE_QUERY = '(max-width: 39.999rem)'

interface ScopedFilters {
  offset: number
  query: string
  scopeKey: string
  source: null | string
}

interface ScopedSelection extends MarketplacePackageSelection {
  identifier: string
  scopeKey: string
}

function errorStatus(error: unknown): null | number {
  if (typeof error !== 'object' || error === null || !('status' in error)) {
    return null
  }

  return typeof error.status === 'number' ? error.status : null
}

function errorCode(error: unknown): null | string {
  if (typeof error !== 'object' || error === null || !('code' in error)) {
    return null
  }

  return typeof error.code === 'string' ? error.code : null
}

function detailFrom(operation: WorkflowMarketplaceOperation | undefined): null | WorkflowMarketplacePackageDetail {
  return operation?.kind === 'package_detail' &&
    operation.state === 'succeeded' &&
    operation.result.type === 'package_detail'
    ? operation.result.value
    : null
}

function capabilityErrorKind(error: unknown): 'auth' | 'error' | 'unsupported' {
  if (isWorkflowMarketplaceUnsupportedError(error)) {
    return 'unsupported'
  }

  return errorStatus(error) === 401 || errorStatus(error) === 403 ? 'auth' : 'error'
}

function operationNeedsPolling(operation: WorkflowMarketplaceOperation | undefined): boolean {
  return operation?.state === 'pending' || operation?.state === 'running'
}

function operationWasLost(error: unknown): boolean {
  return errorCode(error) === 'marketplace_operation_not_found'
}

export interface WorkflowMarketplaceViewProps {
  scope: WorkflowMarketplaceScope
}

export function WorkflowMarketplaceView({ scope }: WorkflowMarketplaceViewProps) {
  const { t } = useI18n()
  const copy = t.operations
  const scopeKey = profileScopeKey(scope)
  const narrow = useMediaQuery(NARROW_MARKETPLACE_QUERY)
  const rootRef = useRef<HTMLElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const backButtonRef = useRef<HTMLButtonElement>(null)
  const selectionOriginRef = useRef<HTMLButtonElement | null>(null)
  const previousScopeKeyRef = useRef(scopeKey)
  const [filterState, setFilterState] = useState<ScopedFilters>({ offset: 0, query: '', scopeKey, source: null })
  const [selectionState, setSelectionState] = useState<null | ScopedSelection>(null)

  const filters = filterState.scopeKey === scopeKey ? filterState : { offset: 0, query: '', scopeKey, source: null }

  const selection = selectionState?.scopeKey === scopeKey ? selectionState : null

  const capabilities = useQuery({
    queryFn: () => getWorkflowMarketplaceCapabilities(scope),
    queryKey: marketplaceKeys.capabilities(scopeKey),
    retry: false
  })

  const supportsSearch = capabilities.data?.capabilities.includes('search') === true
  const supportsSources = capabilities.data?.capabilities.includes('sources') === true
  const supportsInstalled = capabilities.data?.capabilities.includes('installed') === true

  const sources = useQuery({
    enabled: supportsSources,
    queryFn: () => listWorkflowMarketplaceSources(scope),
    queryKey: marketplaceKeys.sources(scopeKey),
    retry: false
  })

  const installed = useQuery({
    enabled: supportsInstalled,
    queryFn: () => listInstalledWorkflowPackages(scope),
    queryKey: marketplaceKeys.installed(scopeKey),
    retry: false
  })

  const packages = useQuery({
    enabled: supportsSearch,
    queryFn: () =>
      searchWorkflowPackages(filters.query, scope, {
        limit: PAGE_LIMIT,
        offset: filters.offset,
        ...(filters.source ? { source: filters.source } : {})
      }),
    queryKey: marketplaceKeys.search(scopeKey, {
      limit: PAGE_LIMIT,
      offset: filters.offset,
      query: filters.query,
      source: filters.source
    }),
    retry: false
  })

  const detailRequest = useQuery({
    enabled: selection !== null,
    queryFn: () => inspectWorkflowPackage(selection!.sourceName, selection!.packageId, scope),
    queryKey: selection
      ? marketplaceKeys.detail(scopeKey, selection.sourceName, selection.packageId)
      : marketplaceKeys.detail(scopeKey, '', ''),
    retry: false
  })

  const operationId = detailRequest.data?.id ?? null
  const needsDetailPolling = operationNeedsPolling(detailRequest.data)

  const detailOperation = useQuery({
    enabled: Boolean(operationId && needsDetailPolling),
    queryFn: () => getWorkflowMarketplaceOperation(operationId!, scope),
    queryKey: marketplaceKeys.operation(scopeKey, operationId ?? 'none'),
    refetchInterval: query => {
      if (query.state.status === 'error') {
        return false
      }

      const operation = query.state.data

      return operation &&
        (operation.state === 'succeeded' || operation.state === 'failed' || operation.state === 'cancelled')
        ? false
        : 500
    },
    retry: false
  })

  const effectiveOperation = needsDetailPolling ? (detailOperation.data ?? detailRequest.data) : detailRequest.data
  const detail = detailFrom(effectiveOperation)

  const detailFailed =
    detailRequest.isError ||
    detailOperation.isError ||
    effectiveOperation?.state === 'failed' ||
    effectiveOperation?.state === 'cancelled'

  const detailLoading = selection !== null && !detail && !detailFailed
  const items = packages.data?.items ?? []
  const installedPackages = installed.data?.packages ?? []
  const selectedStillVisible = selection ? items.some(item => item.identifier === selection.identifier) : false
  const stale = items.some(item => item.state === 'stale')

  // eslint-disable-next-line no-restricted-syntax -- resets ephemeral filters and selection at a backend authority boundary
  useEffect(() => {
    const changed = previousScopeKeyRef.current !== scopeKey

    previousScopeKeyRef.current = scopeKey
    setFilterState({ offset: 0, query: '', scopeKey, source: null })
    setSelectionState(null)
    selectionOriginRef.current = null

    if (changed) {
      requestAnimationFrame(() => {
        const active = document.activeElement

        if (!active || active === document.body || !active.isConnected) {
          searchRef.current?.focus()
        }
      })
    }
  }, [scopeKey])

  // eslint-disable-next-line no-restricted-syntax -- reconciles ephemeral selection with a freshly authoritative page
  useEffect(() => {
    if (selection && packages.data && !packages.isFetching && !selectedStillVisible) {
      setSelectionState(null)
      selectionOriginRef.current = null
      requestAnimationFrame(() => {
        const active = document.activeElement

        if (!active || active === document.body || !active.isConnected) {
          searchRef.current?.focus()
        }
      })
    }
  }, [packages.data, packages.isFetching, selectedStillVisible, selection])

  useEffect(() => {
    if (!filters.source || !sources.data) {
      return
    }

    const selectedSource = sources.data.sources.find(item => item.name === filters.source)

    if (!selectedSource?.enabled) {
      setFilterState({ offset: 0, query: filters.query, scopeKey, source: null })
    }
  }, [filters.query, filters.source, scopeKey, sources.data])

  useEffect(() => {
    if (narrow && selection) {
      backButtonRef.current?.focus()
    }
  }, [narrow, selection])

  const selectPackage = (next: MarketplacePackageSelection, origin: HTMLButtonElement) => {
    const item = items.find(candidate => candidate.source_name === next.sourceName && candidate.id === next.packageId)

    if (!item) {
      return
    }

    selectionOriginRef.current = origin
    setSelectionState({ ...next, identifier: item.identifier, scopeKey })
  }

  const backToPackages = () => {
    const origin = selectionOriginRef.current
    const identifier = selection?.identifier

    setSelectionState(null)
    requestAnimationFrame(() => {
      const restored = identifier
        ? Array.from(rootRef.current?.querySelectorAll<HTMLButtonElement>('[data-marketplace-package]') ?? []).find(
            item => item.dataset.marketplacePackage === identifier
          )
        : null

      if (restored) {
        restored.focus()
      } else if (origin?.isConnected) {
        origin.focus()
      } else {
        searchRef.current?.focus()
      }
    })
  }

  const retryDetail = () => {
    if (needsDetailPolling && detailOperation.isError && !operationWasLost(detailOperation.error)) {
      void detailOperation.refetch()
    } else {
      void detailRequest.refetch()
    }
  }

  if (capabilities.isPending) {
    return (
      <div
        aria-label={copy.workflowMarketplaceCapabilityLoading}
        className="grid min-h-48 place-items-center"
        role="status"
      >
        <Loader aria-hidden className="size-10 text-primary/70" role="presentation" type="lemniscate-bloom" />
      </div>
    )
  }

  if (capabilities.isError || !supportsSearch) {
    const kind = capabilities.isError ? capabilityErrorKind(capabilities.error) : 'unsupported'

    return (
      <div className="grid min-h-48 place-items-center" role="alert">
        <ErrorState
          description={
            kind === 'unsupported'
              ? copy.workflowMarketplaceUpgradeDescription
              : kind === 'auth'
                ? copy.workflowMarketplaceAuthDescription
                : copy.workflowMarketplaceErrorDescription
          }
          title={
            kind === 'unsupported'
              ? copy.workflowMarketplaceUpgradeTitle
              : kind === 'auth'
                ? copy.workflowMarketplaceAuthTitle
                : copy.workflowMarketplaceErrorTitle
          }
        >
          {kind !== 'unsupported' ? (
            <Button onClick={() => void capabilities.refetch()} size="sm" type="button" variant="secondary">
              {copy.workflowCatalogRetry}
            </Button>
          ) : null}
        </ErrorState>
      </div>
    )
  }

  const searchError = packages.isError
  const searchErrorCode = errorCode(packages.error)

  const searchErrorTitle =
    searchErrorCode === 'source_authentication_failed'
      ? copy.workflowMarketplaceSourceAuthTitle
      : searchErrorCode === 'source_disabled'
        ? copy.workflowMarketplaceSourceDisabledTitle
        : searchErrorCode === 'source_unavailable'
          ? copy.workflowMarketplaceSourceUnavailable
          : copy.workflowMarketplaceCatalogErrorTitle

  const installedNotice = !supportsInstalled ? (
    <InstalledProvenanceNotice context="browse" kind="unsupported" />
  ) : installed.isPending ? (
    <InstalledProvenanceNotice context="browse" kind="loading" />
  ) : installed.isError ? (
    <InstalledProvenanceNotice
      context="browse"
      kind={installedProvenanceErrorKind(installed.error)}
      onRetry={() => void installed.refetch()}
    />
  ) : null

  const detailPanel = selection ? (
    <div className="min-h-0 overflow-y-auto px-4 py-3 [scrollbar-gutter:stable]">
      {narrow ? (
        <Button onClick={backToPackages} ref={backButtonRef} size="sm" type="button" variant="secondary">
          <ChevronLeft />
          {copy.workflowMarketplaceBack}
        </Button>
      ) : null}
      {detailLoading ? (
        <div
          aria-label={copy.workflowMarketplaceDetailLoading}
          className="grid min-h-48 place-items-center"
          role="status"
        >
          <Loader aria-hidden className="size-9 text-primary/70" role="presentation" type="lemniscate-bloom" />
        </div>
      ) : detailFailed ? (
        <div className="grid min-h-48 place-items-center" role="alert">
          <ErrorState
            description={copy.workflowMarketplaceDetailErrorDescription}
            title={copy.workflowMarketplaceDetailErrorTitle}
          >
            <Button onClick={retryDetail} size="sm" type="button" variant="secondary">
              {copy.workflowCatalogRetry}
            </Button>
          </ErrorState>
        </div>
      ) : detail ? (
        <div className={narrow ? 'mt-3' : undefined}>
          <MarketplacePackageDetail detail={detail} />
        </div>
      ) : null}
    </div>
  ) : (
    <div className="grid min-h-48 place-items-center">
      <EmptyState
        className="min-h-36"
        description={copy.workflowMarketplaceSelectDescription}
        title={copy.workflowMarketplaceSelectTitle}
      />
    </div>
  )

  return (
    <section
      className="flex h-full min-h-0 flex-col"
      onKeyDown={event => {
        if (event.key === 'Escape' && narrow && selection) {
          event.stopPropagation()
          backToPackages()
        }
      }}
      ref={rootRef}
    >
      {!(narrow && selection) ? (
        <div className="flex shrink-0 flex-wrap items-center gap-3 pb-3">
          <SearchField
            aria-label={copy.workflowMarketplaceSearch}
            inputRef={searchRef}
            loading={packages.isFetching && !packages.isPending}
            onChange={query => setFilterState({ offset: 0, query, scopeKey, source: filters.source })}
            placeholder={copy.workflowMarketplaceSearch}
            role="searchbox"
            value={filters.query}
          />
          <Select
            onValueChange={value =>
              setFilterState({ offset: 0, query: filters.query, scopeKey, source: value === '__all__' ? null : value })
            }
            value={filters.source ?? '__all__'}
          >
            <SelectTrigger aria-label={copy.workflowMarketplaceSource} className="w-44" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">{copy.workflowMarketplaceAllSources}</SelectItem>
              {(sources.data?.sources ?? []).map(source => (
                <SelectItem disabled={!source.enabled} key={source.name} value={source.name}>
                  {source.enabled ? source.name : copy.workflowMarketplaceDisabledSource(source.name)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : null}

      {stale && !(narrow && selection) ? (
        <p
          aria-label={copy.workflowMarketplaceStaleLabel}
          className="mb-3 text-xs text-amber-600 dark:text-amber-300"
          role="status"
        >
          {copy.workflowMarketplaceStaleDescription}
        </p>
      ) : null}

      {installedNotice && !(narrow && selection) ? <div className="mb-3">{installedNotice}</div> : null}

      {narrow && selection ? (
        <div className="min-h-0 flex-1">{detailPanel}</div>
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-1 sm:grid-cols-[minmax(15rem,0.8fr)_minmax(0,1.2fr)]">
          <div className="min-h-0 overflow-y-auto pe-3 [scrollbar-gutter:stable]">
            {packages.isPending ? (
              <div
                aria-label={copy.workflowMarketplaceLoading}
                className="grid min-h-48 place-items-center"
                role="status"
              >
                <Loader aria-hidden className="size-9 text-primary/70" role="presentation" type="lemniscate-bloom" />
              </div>
            ) : searchError ? (
              <div className="grid min-h-48 place-items-center" role="alert">
                <ErrorState description={copy.workflowMarketplaceCatalogErrorDescription} title={searchErrorTitle}>
                  <Button onClick={() => void packages.refetch()} size="sm" type="button" variant="secondary">
                    {copy.workflowCatalogRetry}
                  </Button>
                </ErrorState>
              </div>
            ) : items.length === 0 ? (
              <EmptyState
                className="min-h-36"
                description={
                  filters.query ? copy.workflowMarketplaceNoMatchDescription : copy.workflowMarketplaceEmptyDescription
                }
                title={filters.query ? copy.workflowMarketplaceNoMatchTitle : copy.workflowMarketplaceEmptyTitle}
              />
            ) : (
              <MarketplacePackageList
                installedPackages={installedPackages}
                items={items}
                onSelect={selectPackage}
                selectedIdentifier={selection?.identifier ?? null}
              />
            )}
            {!packages.isPending && !searchError && (filters.offset > 0 || packages.data?.next_offset !== null) ? (
              <nav
                aria-label={copy.workflowMarketplacePagination}
                className="mt-3 flex items-center justify-between gap-2"
              >
                <Button
                  disabled={filters.offset === 0}
                  onClick={() =>
                    setFilterState({ ...filters, offset: Math.max(0, filters.offset - PAGE_LIMIT), scopeKey })
                  }
                  size="sm"
                  type="button"
                  variant="secondary"
                >
                  {copy.workflowMarketplacePreviousPage}
                </Button>
                <span className="text-xs text-(--ui-text-tertiary)">
                  {copy.workflowMarketplacePage(Math.floor(filters.offset / PAGE_LIMIT) + 1)}
                </span>
                <Button
                  disabled={packages.data?.next_offset === null}
                  onClick={() =>
                    setFilterState({ ...filters, offset: packages.data?.next_offset ?? filters.offset, scopeKey })
                  }
                  size="sm"
                  type="button"
                  variant="secondary"
                >
                  {copy.workflowMarketplaceNextPage}
                </Button>
              </nav>
            ) : null}
          </div>
          <div className="min-h-0 border-s border-(--ui-stroke-tertiary)">{detailPanel}</div>
        </div>
      )}
    </section>
  )
}

export { InstalledPackages } from './installed-packages'
export { MarketplacePackageDetail } from './package-detail'
export { MarketplacePackageList } from './package-list'
export { marketplaceKeys } from './query-keys'
