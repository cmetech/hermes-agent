import { useQuery } from '@tanstack/react-query'
import { useRef } from 'react'
import type { ReactNode } from 'react'

import { profileScopeKey } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  getWorkflowMarketplaceCapabilities,
  isWorkflowMarketplaceUnsupportedError,
  listInstalledWorkflowPackages
} from '@/hermes'
import type { WorkflowMarketplaceScope } from '@/hermes'
import { useI18n } from '@/i18n'
import { ExternalLink } from '@/lib/external-link'

import { marketplaceWebRepositoryHref } from './package-detail'
import { marketplaceKeys } from './query-keys'
import { useWorkflowPackageLifecycle } from './use-package-lifecycle'

export interface InstalledPackagesProps {
  children: ReactNode
  scope: WorkflowMarketplaceScope
}

type InstalledProvenanceContext = 'browse' | 'installed'
type InstalledProvenanceNoticeKind = 'auth' | 'error' | 'loading' | 'unsupported'

export interface InstalledProvenanceNoticeProps {
  context: InstalledProvenanceContext
  kind: InstalledProvenanceNoticeKind
  onRetry?: () => void
}

function errorStatus(error: unknown): null | number {
  if (typeof error !== 'object' || error === null || !('status' in error)) {
    return null
  }

  return typeof error.status === 'number' ? error.status : null
}

export function installedProvenanceErrorKind(error: unknown): 'auth' | 'error' | 'unsupported' {
  if (isWorkflowMarketplaceUnsupportedError(error)) {
    return 'unsupported'
  }

  return errorStatus(error) === 401 || errorStatus(error) === 403 ? 'auth' : 'error'
}

export function InstalledProvenanceNotice({ context, kind, onRetry }: InstalledProvenanceNoticeProps) {
  const { t } = useI18n()
  const copy = t.operations
  const browse = context === 'browse'

  if (kind === 'loading') {
    const label = browse
      ? copy.workflowMarketplaceInstalledStatusLoading
      : copy.workflowMarketplaceInstalledProvenanceLoading

    return (
      <p aria-label={label} className="text-xs text-(--ui-text-tertiary)" role="status">
        {label}
      </p>
    )
  }

  const title = browse
    ? kind === 'auth'
      ? copy.workflowMarketplaceInstalledStatusAuthTitle
      : copy.workflowMarketplaceInstalledStatusUnavailableTitle
    : kind === 'auth'
      ? copy.workflowMarketplaceInstalledProvenanceAuthTitle
      : kind === 'error'
        ? copy.workflowMarketplaceInstalledProvenanceErrorTitle
        : copy.workflowMarketplaceInstalledProvenanceUnavailableTitle

  const description = browse
    ? kind === 'auth'
      ? copy.workflowMarketplaceInstalledStatusAuthDescription
      : kind === 'error'
        ? copy.workflowMarketplaceInstalledStatusErrorDescription
        : copy.workflowMarketplaceInstalledStatusUnsupportedDescription
    : kind === 'auth'
      ? copy.workflowMarketplaceInstalledProvenanceAuthDescription
      : kind === 'error'
        ? copy.workflowMarketplaceInstalledProvenanceErrorDescription
        : copy.workflowMarketplaceInstalledProvenanceUnsupportedDescription

  return (
    <div
      aria-label={title}
      className="rounded-md border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) px-3 py-2 text-xs"
      role={kind === 'unsupported' ? 'status' : 'alert'}
    >
      <p className="font-medium text-(--ui-text-primary)">{title}</p>
      <p className="mt-0.5 text-(--ui-text-secondary)">{description}</p>
      {onRetry && kind !== 'unsupported' ? (
        <Button className="mt-2" onClick={onRetry} size="sm" type="button" variant="secondary">
          {copy.workflowCatalogRetry}
        </Button>
      ) : null}
    </div>
  )
}

export function InstalledPackages({ children, scope }: InstalledPackagesProps) {
  const { t } = useI18n()
  const copy = t.operations
  const scopeKey = profileScopeKey(scope)
  const focusFallbackRef = useRef<HTMLDivElement>(null)
  const lifecycle = useWorkflowPackageLifecycle(scope, focusFallbackRef)

  const capabilities = useQuery({
    queryFn: () => getWorkflowMarketplaceCapabilities(scope),
    queryKey: marketplaceKeys.capabilities(scopeKey),
    retry: false
  })

  const installed = useQuery({
    enabled: capabilities.data?.capabilities.includes('installed') === true,
    queryFn: () => listInstalledWorkflowPackages(scope),
    queryKey: marketplaceKeys.installed(scopeKey),
    retry: false
  })

  const supportsInstalled = capabilities.data?.capabilities.includes('installed') === true
  const supportsOperations = capabilities.data?.capabilities.includes('operations') === true
  const supportsTransactions = capabilities.data?.capabilities.includes('transactions') === true
  const supportsUpdates = capabilities.data?.capabilities.includes('updates') === true
  const supportsTrust = capabilities.data?.capabilities.includes('trust') === true
  const supportsLifecycle = supportsOperations && supportsTransactions
  const packages = installed.data?.packages ?? []

  const notice = capabilities.isPending ? (
    <InstalledProvenanceNotice context="installed" kind="loading" />
  ) : capabilities.isError ? (
    <InstalledProvenanceNotice
      context="installed"
      kind={installedProvenanceErrorKind(capabilities.error)}
      onRetry={() => void capabilities.refetch()}
    />
  ) : !supportsInstalled ? (
    <InstalledProvenanceNotice context="installed" kind="unsupported" />
  ) : installed.isPending ? (
    <InstalledProvenanceNotice context="installed" kind="loading" />
  ) : installed.isError ? (
    <InstalledProvenanceNotice
      context="installed"
      kind={installedProvenanceErrorKind(installed.error)}
      onRetry={() => void installed.refetch()}
    />
  ) : null

  return (
    <div className="space-y-5" ref={focusFallbackRef} tabIndex={-1}>
      {notice}
      {packages.length ? (
        <section aria-label={copy.workflowMarketplaceInstalledPackages}>
          <h2 className="text-sm font-medium text-(--ui-text-primary)">{copy.workflowMarketplaceInstalledPackages}</h2>
          <div className="mt-2 space-y-3">
            {packages.map(item => {
              const identifier = `${item.identity.source_key}/${item.identity.package_id}`
              const repositoryHref = marketplaceWebRepositoryHref(item.repository_url)

              return (
                <article
                  aria-label={copy.workflowMarketplaceInstalledPackageLabel(identifier)}
                  className="border-b border-(--ui-stroke-tertiary) pb-3 last:border-b-0"
                  key={identifier}
                >
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <h3 className="break-all font-mono text-xs font-medium text-(--ui-text-primary)">{identifier}</h3>
                    <span className="flex flex-wrap gap-1">
                      <Badge variant="default">v{item.version}</Badge>
                      {item.orphaned_source ? (
                        <Badge variant="warn">{copy.workflowMarketplaceSourceUnavailable}</Badge>
                      ) : null}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-(--ui-text-secondary)">
                    {copy.workflowMarketplaceSource}: {item.source_name}
                    {' · '}
                    {repositoryHref ? (
                      <ExternalLink href={repositoryHref}>{item.repository_url}</ExternalLink>
                    ) : (
                      item.repository_url
                    )}
                  </p>
                  <p className="mt-1 text-xs text-(--ui-text-tertiary)">
                    {copy.workflowMarketplaceWorkflowCount(item.workflow_paths.length)}
                  </p>
                  <ul className="mt-1 space-y-0.5 font-mono text-[0.6875rem] text-(--ui-text-secondary)">
                    {item.workflow_paths.map(path => (
                      <li className="break-all" key={path}>
                        {path}
                      </li>
                    ))}
                  </ul>
                  {supportsLifecycle ? (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {supportsTrust ? (
                        <Button
                          onClick={event => lifecycle.reviewTrust(item, event.currentTarget)}
                          size="sm"
                          type="button"
                          variant="secondary"
                        >
                          {copy.workflowMarketplaceReviewTrustAction}
                        </Button>
                      ) : null}
                      {supportsUpdates ? (
                        <Button
                          onClick={event => lifecycle.checkForUpdates(item, event.currentTarget)}
                          size="sm"
                          type="button"
                          variant="secondary"
                        >
                          {copy.workflowMarketplaceCheckUpdates}
                        </Button>
                      ) : null}
                      <Button
                        onClick={event => lifecycle.remove(item, event.currentTarget)}
                        size="sm"
                        type="button"
                        variant="secondary"
                      >
                        {copy.workflowMarketplaceRemovePackage}
                      </Button>
                    </div>
                  ) : (
                    <p className="mt-2 text-xs text-(--ui-text-tertiary)" role="status">
                      {copy.workflowMarketplaceLifecycleUnavailable}
                    </p>
                  )}
                </article>
              )
            })}
          </div>
        </section>
      ) : null}
      {lifecycle.dialogs}
      {children}
    </div>
  )
}
