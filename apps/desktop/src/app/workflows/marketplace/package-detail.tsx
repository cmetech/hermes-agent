import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { ExternalLink } from '@/lib/external-link'
import type {
  WorkflowMarketplaceExternalRequirements,
  WorkflowMarketplacePackageDetail,
  WorkflowMarketplacePackageResource,
  WorkflowMarketplaceResourceType
} from '@/types/hermes'
import type { PackageState } from '@/types/workflow-marketplace-lifecycle'

import { ReviewDiagnostics } from './review-sections'

export interface MarketplacePackageDetailProps {
  actions?: {
    checkForUpdates?: (origin: HTMLButtonElement) => void
    install?: (origin: HTMLButtonElement) => void
    remove?: (origin: HTMLButtonElement) => void
    reviewTrust?: (origin: HTMLButtonElement) => void
    update?: (origin: HTMLButtonElement) => void
  }
  detail: WorkflowMarketplacePackageDetail
  lifecycleUnavailable?: boolean
  lastObserved?: boolean
  packageState?: PackageState | null
}

export function marketplaceWebRepositoryHref(value: string): null | string {
  if (value.includes('[REDACTED')) {
    return null
  }

  try {
    const url = new URL(value)

    return (url.protocol === 'http:' || url.protocol === 'https:') && !url.username && !url.password ? url.href : null
  } catch {
    return null
  }
}

function resourcesWithType(
  resources: WorkflowMarketplacePackageResource[],
  types: WorkflowMarketplaceResourceType[]
): WorkflowMarketplacePackageResource[] {
  const accepted = new Set(types)

  return resources.filter(resource => resource.types.some(type => accepted.has(type)))
}

function ResourceSection({ resources, title }: { resources: WorkflowMarketplacePackageResource[]; title: string }) {
  if (!resources.length) {
    return null
  }

  return (
    <section>
      <h3 className="text-xs font-medium text-(--ui-text-primary)">{title}</h3>
      <ul className="mt-1 space-y-1 font-mono text-[0.6875rem] text-(--ui-text-secondary)">
        {resources.map(resource => (
          <li className="break-all" key={resource.path}>
            {resource.path}
          </li>
        ))}
      </ul>
    </section>
  )
}

function RequirementList({ requirements }: { requirements: WorkflowMarketplaceExternalRequirements }) {
  const { t } = useI18n()
  const copy = t.operations

  const groups = [
    [copy.workflowMarketplaceProviders, requirements.providers],
    [copy.workflowMarketplaceRuntimes, requirements.runtimes],
    [copy.workflowMarketplaceSecrets, requirements.secrets],
    [copy.workflowMarketplaceServices, requirements.services],
    [copy.workflowMarketplaceTools, requirements.tools]
  ] as const

  if (groups.every(([, values]) => values.length === 0)) {
    return <p className="text-xs text-(--ui-text-secondary)">{copy.workflowMarketplaceNoRequirements}</p>
  }

  return (
    <dl className="mt-1 grid grid-cols-[minmax(5rem,auto)_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
      {groups.map(([label, values]) =>
        values.length ? (
          <div className="contents" key={label}>
            <dt className="text-(--ui-text-tertiary)">{label}</dt>
            <dd className="flex min-w-0 flex-wrap gap-1">
              {values.map(value => (
                <Badge key={value} size="xs" variant="outline">
                  {value}
                </Badge>
              ))}
            </dd>
          </div>
        ) : null
      )}
    </dl>
  )
}

export function MarketplacePackageDetail({
  actions,
  detail,
  lifecycleUnavailable = false,
  lastObserved = false,
  packageState
}: MarketplacePackageDetailProps) {
  const { t } = useI18n()
  const copy = t.operations
  const repositoryHref = marketplaceWebRepositoryHref(detail.repository_url)

  const installed = packageState ? packageState.installed : detail.installed

  const installedRepositoryHref = installed ? marketplaceWebRepositoryHref(installed.repository_url) : null

  const commands = resourcesWithType(detail.resources, ['command'])
  const scripts = resourcesWithType(detail.resources, ['script'])
  const mcp = resourcesWithType(detail.resources, ['mcp', 'mcp_resource'])
  const other = resourcesWithType(detail.resources, ['other'])

  const compatibility = detail.workflows.flatMap(workflow =>
    workflow.compatibility.map(item => ({ ...item, workflowName: workflow.workflow_name }))
  )

  return (
    <section
      aria-label={copy.workflowMarketplaceDetailLabel(detail.display_name)}
      className="min-w-0 space-y-5"
      role="region"
    >
      <header>
        {lastObserved ? <p role="status">{copy.workflowMarketplaceLastObservedRefreshing}</p> : null}
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <h2 className="break-all text-lg font-medium text-(--ui-text-primary)">{detail.display_name}</h2>
            <p className="break-all font-mono text-[0.6875rem] text-(--ui-text-tertiary)">{detail.identifier}</p>
          </div>
          <span className="flex flex-wrap gap-1">
            <Badge variant="default">v{detail.version}</Badge>
            {installed ? <Badge variant="muted">{copy.workflowMarketplaceInstalled}</Badge> : null}
            {installed && detail.update_status === 'current' ? (
              <Badge variant="default">
                {lastObserved ? copy.workflowMarketplaceLastObservedPrefix : ''}
                {copy.workflowMarketplaceCurrent}
              </Badge>
            ) : installed && detail.update_status === 'update_available' ? (
              <Badge variant="warn">
                {lastObserved ? copy.workflowMarketplaceLastObservedPrefix : ''}
                {copy.workflowMarketplaceUpdateAvailable}
              </Badge>
            ) : null}
          </span>
        </div>
        <p className="mt-2 text-sm leading-5 text-(--ui-text-secondary)">{detail.description}</p>
        <div className="mt-2 flex flex-wrap gap-1">
          {detail.tags.map(tag => (
            <Badge key={tag} size="xs" variant="outline">
              {tag}
            </Badge>
          ))}
        </div>
        {actions ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {detail.install_status === 'not_installed' && detail.blockers.length === 0 && actions.install ? (
              <Button disabled size="sm" type="button">
                {copy.workflowMarketplaceInstallPackage}
              </Button>
            ) : null}
            {detail.install_status === 'installed' && detail.update_status === 'update_available' && actions.update ? (
              <Button disabled size="sm" type="button">
                {copy.workflowMarketplaceUpdatePackage}
              </Button>
            ) : null}
            {detail.install_status === 'installed' && detail.update_status === 'current' && actions.checkForUpdates ? (
              <Button disabled size="sm" type="button" variant="secondary">
                {copy.workflowMarketplaceCheckUpdates}
              </Button>
            ) : null}
            {detail.install_status === 'installed' && actions.reviewTrust ? (
              <Button disabled size="sm" type="button" variant="secondary">
                {copy.workflowMarketplaceReviewTrustAction}
              </Button>
            ) : null}
            {detail.install_status === 'installed' && actions.remove ? (
              <Button disabled size="sm" type="button" variant="secondary">
                {copy.workflowMarketplaceRemovePackage}
              </Button>
            ) : null}
          </div>
        ) : lifecycleUnavailable ? (
          <p className="mt-3 text-xs text-(--ui-text-tertiary)" role="status">
            {copy.workflowMarketplaceLifecycleUnavailable}
          </p>
        ) : null}
      </header>

      <section aria-label={copy.workflowMarketplaceCandidateIdentity} role="region">
        <h3 className="text-xs font-medium text-(--ui-text-primary)">{copy.workflowMarketplaceCandidateIdentity}</h3>
        <dl className="mt-1 grid grid-cols-[minmax(6rem,auto)_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
          <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplacePublisher}</dt>
          <dd className="min-w-0 break-all">{detail.publisher}</dd>
          <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceLicense}</dt>
          <dd className="min-w-0 break-all">{detail.license}</dd>
          <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceSource}</dt>
          <dd className="min-w-0 break-all">{detail.source_name}</dd>
          <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceRepository}</dt>
          <dd className="min-w-0 break-all">
            {repositoryHref ? (
              <ExternalLink href={repositoryHref}>{detail.repository_url}</ExternalLink>
            ) : (
              detail.repository_url
            )}
          </dd>
          <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceRef}</dt>
          <dd className="break-all font-mono">{detail.configured_ref ?? copy.workflowMarketplaceDefaultRef}</dd>
          <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceCommit}</dt>
          <dd className="break-all font-mono">{detail.resolved_commit}</dd>
          <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplacePackageDigest}</dt>
          <dd className="break-all font-mono">{detail.package_digest}</dd>
          <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplacePackagePath}</dt>
          <dd className="break-all font-mono">{detail.package_path}</dd>
          <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceVerified}</dt>
          <dd>{detail.verified_at}</dd>
        </dl>
      </section>

      {installed ? (
        <section aria-label={copy.workflowMarketplaceInstalledProvenance} role="region">
          <h3 className="text-xs font-medium text-(--ui-text-primary)">
            {copy.workflowMarketplaceInstalledProvenance}
          </h3>
          <dl className="mt-1 grid grid-cols-[minmax(7rem,auto)_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceInstalledIdentity}</dt>
            <dd className="break-all font-mono">
              {installed.identity.source_key}/{installed.identity.package_id}
            </dd>
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceInstalledVersionLabel}</dt>
            <dd>{installed.version}</dd>
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceInstalledSource}</dt>
            <dd className="min-w-0 break-all">{installed.source_name}</dd>
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceInstalledRepository}</dt>
            <dd className="min-w-0 break-all">
              {installedRepositoryHref ? (
                <ExternalLink href={installedRepositoryHref}>{installed.repository_url}</ExternalLink>
              ) : (
                installed.repository_url
              )}
            </dd>
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceInstalledRef}</dt>
            <dd className="break-all font-mono">{installed.configured_ref ?? copy.workflowMarketplaceDefaultRef}</dd>
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceInstalledCommit}</dt>
            <dd className="break-all font-mono">{installed.resolved_commit}</dd>
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceDistributionDigest}</dt>
            <dd className="break-all font-mono">{installed.distribution_digest}</dd>
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceInstalledAt}</dt>
            <dd>{installed.installed_at}</dd>
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceInstalledPackagePath}</dt>
            <dd className="break-all font-mono">{installed.package_path}</dd>
          </dl>
        </section>
      ) : null}

      {detail.blockers.length ? (
        <ReviewDiagnostics diagnostics={detail.blockers} title={copy.workflowMarketplaceBlockers} />
      ) : null}

      {detail.advisories.length ? (
        <ReviewDiagnostics diagnostics={detail.advisories} title={copy.workflowMarketplaceAdvisories} />
      ) : null}

      {compatibility.length ? (
        <ReviewDiagnostics diagnostics={compatibility} title={copy.workflowMarketplaceCompatibility} />
      ) : null}

      <section>
        <h3 className="text-xs font-medium text-(--ui-text-primary)">
          {copy.workflowMarketplaceWorkflowCount(detail.workflows.length)}
        </h3>
        <ul className="mt-1 space-y-2 text-xs">
          {detail.workflows.map(workflow => (
            <li key={workflow.workflow_name}>
              <span className="font-medium text-(--ui-text-primary)">{workflow.workflow_name}</span>
              <span className="ms-2 font-mono text-[0.6875rem] text-(--ui-text-tertiary)">
                {workflow.definition_path}
              </span>
              {!packageState ? (
                <Badge className="ms-2" size="xs" variant={workflow.trust_state === 'trusted' ? 'default' : 'warn'}>
                  {lastObserved ? copy.workflowMarketplaceLastObservedPrefix : ''}
                  {workflow.trust_state === 'trusted' ? copy.workflowTrusted : copy.workflowUntrusted}
                </Badge>
              ) : null}
              {workflow.companion_path ? (
                <span className="mt-0.5 block break-all font-mono text-[0.6875rem] text-(--ui-text-tertiary)">
                  {workflow.companion_path}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      </section>

      {packageState?.trust ? (
        <section aria-label={copy.workflowMarketplaceCurrentTrust} role="region">
          <h3 className="text-xs font-medium">{copy.workflowMarketplaceCurrentTrust}</h3>
          <ul>
            {packageState.trust.workflows.map(workflow => (
              <li key={workflow.definition_path}>
                <span>{workflow.workflow_name}</span>
                {' · '}
                <span>{workflow.definition_path}</span>{' '}
                <Badge variant={workflow.state === 'trusted' ? 'default' : 'warn'}>
                  {workflow.state === 'trusted' ? copy.workflowTrusted : copy.workflowUntrusted}
                </Badge>
              </li>
            ))}
          </ul>
        </section>
      ) : packageState?.state === 'absent' ? (
        <p role="status">{copy.workflowMarketplaceAbsent}</p>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2">
        <ResourceSection resources={commands} title={copy.workflowMarketplaceCommands} />
        <ResourceSection resources={scripts} title={copy.workflowMarketplaceScripts} />
        <ResourceSection resources={mcp} title={copy.workflowMarketplaceMcpResources} />
        <ResourceSection resources={other} title={copy.workflowMarketplaceOtherResources} />
      </div>

      <section>
        <h3 className="text-xs font-medium text-(--ui-text-primary)">{copy.workflowMarketplaceRequirements}</h3>
        <RequirementList requirements={detail.external_requirements} />
      </section>
    </section>
  )
}
