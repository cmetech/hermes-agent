import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { profileScopeKey } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { getWorkflowMarketplaceCapabilities, listInstalledWorkflowPackages } from '@/hermes'
import type { WorkflowMarketplaceScope } from '@/hermes'
import { useI18n } from '@/i18n'
import { ExternalLink } from '@/lib/external-link'

import { marketplaceWebRepositoryHref } from './package-detail'
import { marketplaceKeys } from './query-keys'

export interface InstalledPackagesProps {
  children: ReactNode
  scope: WorkflowMarketplaceScope
}

export function InstalledPackages({ children, scope }: InstalledPackagesProps) {
  const { t } = useI18n()
  const copy = t.operations
  const scopeKey = profileScopeKey(scope)

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

  const packages = installed.data?.packages ?? []

  return (
    <div className="space-y-5">
      {packages.length ? (
        <section aria-label={copy.workflowMarketplaceInstalledPackages}>
          <h2 className="text-sm font-medium text-(--ui-text-primary)">{copy.workflowMarketplaceInstalledPackages}</h2>
          <div className="mt-2 space-y-3">
            {packages.map(item => {
              const identifier = `${item.identity.source_key}/${item.identity.package_id}`
              const repositoryHref = marketplaceWebRepositoryHref(item.repository_url)

              return (
                <article className="border-b border-(--ui-stroke-tertiary) pb-3 last:border-b-0" key={identifier}>
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
                </article>
              )
            })}
          </div>
        </section>
      ) : null}
      {children}
    </div>
  )
}
