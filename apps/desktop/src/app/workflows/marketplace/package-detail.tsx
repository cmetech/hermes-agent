import { Badge } from '@/components/ui/badge'
import { useI18n } from '@/i18n'
import { ExternalLink } from '@/lib/external-link'
import type {
  WorkflowMarketplaceExternalRequirements,
  WorkflowMarketplacePackageDetail,
  WorkflowMarketplacePackageResource,
  WorkflowMarketplaceResourceType
} from '@/types/hermes'

export interface MarketplacePackageDetailProps {
  detail: WorkflowMarketplacePackageDetail
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

export function MarketplacePackageDetail({ detail }: MarketplacePackageDetailProps) {
  const { t } = useI18n()
  const copy = t.operations
  const repositoryHref = marketplaceWebRepositoryHref(detail.repository_url)

  const installedRepositoryHref = detail.installed
    ? marketplaceWebRepositoryHref(detail.installed.repository_url)
    : null

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
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className="text-lg font-medium text-(--ui-text-primary)">{detail.display_name}</h2>
            <p className="break-all font-mono text-[0.6875rem] text-(--ui-text-tertiary)">{detail.identifier}</p>
          </div>
          <span className="flex flex-wrap gap-1">
            <Badge variant="default">v{detail.version}</Badge>
            {detail.install_status === 'installed' ? (
              <Badge variant="muted">{copy.workflowMarketplaceInstalled}</Badge>
            ) : null}
            {detail.update_status === 'current' ? (
              <Badge variant="default">{copy.workflowMarketplaceCurrent}</Badge>
            ) : detail.update_status === 'update_available' ? (
              <Badge variant="warn">{copy.workflowMarketplaceUpdateAvailable}</Badge>
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
      </header>

      <section aria-label={copy.workflowMarketplaceCandidateIdentity} role="region">
        <h3 className="text-xs font-medium text-(--ui-text-primary)">{copy.workflowMarketplaceCandidateIdentity}</h3>
        <dl className="mt-1 grid grid-cols-[minmax(6rem,auto)_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
          <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplacePublisher}</dt>
          <dd>{detail.publisher}</dd>
          <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceLicense}</dt>
          <dd>{detail.license}</dd>
          <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceSource}</dt>
          <dd>{detail.source_name}</dd>
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

      {detail.installed ? (
        <section aria-label={copy.workflowMarketplaceInstalledProvenance} role="region">
          <h3 className="text-xs font-medium text-(--ui-text-primary)">
            {copy.workflowMarketplaceInstalledProvenance}
          </h3>
          <dl className="mt-1 grid grid-cols-[minmax(7rem,auto)_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceInstalledIdentity}</dt>
            <dd className="break-all font-mono">
              {detail.installed.identity.source_key}/{detail.installed.identity.package_id}
            </dd>
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceInstalledVersionLabel}</dt>
            <dd>{detail.installed.version}</dd>
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceInstalledSource}</dt>
            <dd>{detail.installed.source_name}</dd>
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceInstalledRepository}</dt>
            <dd className="min-w-0 break-all">
              {installedRepositoryHref ? (
                <ExternalLink href={installedRepositoryHref}>{detail.installed.repository_url}</ExternalLink>
              ) : (
                detail.installed.repository_url
              )}
            </dd>
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceInstalledRef}</dt>
            <dd className="break-all font-mono">
              {detail.installed.configured_ref ?? copy.workflowMarketplaceDefaultRef}
            </dd>
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceInstalledCommit}</dt>
            <dd className="break-all font-mono">{detail.installed.resolved_commit}</dd>
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceDistributionDigest}</dt>
            <dd className="break-all font-mono">{detail.installed.distribution_digest}</dd>
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceInstalledAt}</dt>
            <dd>{detail.installed.installed_at}</dd>
            <dt className="text-(--ui-text-tertiary)">{copy.workflowMarketplaceInstalledPackagePath}</dt>
            <dd className="break-all font-mono">{detail.installed.package_path}</dd>
          </dl>
        </section>
      ) : null}

      {detail.blockers.length ? (
        <section aria-label={copy.workflowMarketplaceBlockers}>
          <h3 className="text-xs font-medium text-destructive">{copy.workflowMarketplaceBlockers}</h3>
          <ul className="mt-1 space-y-1 text-xs text-(--ui-text-secondary)">
            {detail.blockers.map(item => (
              <li key={`${item.code}:${item.message}`}>{item.message}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {detail.advisories.length ? (
        <section aria-label={copy.workflowMarketplaceAdvisories}>
          <h3 className="text-xs font-medium text-(--ui-text-primary)">{copy.workflowMarketplaceAdvisories}</h3>
          <ul className="mt-1 space-y-1 text-xs text-(--ui-text-secondary)">
            {detail.advisories.map(item => (
              <li key={`${item.code}:${item.message}`}>{item.message}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {compatibility.length ? (
        <section aria-label={copy.workflowMarketplaceCompatibility}>
          <h3 className="text-xs font-medium text-(--ui-text-primary)">{copy.workflowMarketplaceCompatibility}</h3>
          <ul className="mt-1 space-y-1 text-xs text-(--ui-text-secondary)">
            {compatibility.map(item => (
              <li key={`${item.workflowName}:${item.code}:${item.message}`}>
                <span className="font-medium text-(--ui-text-primary)">{item.workflowName}:</span> {item.message}
              </li>
            ))}
          </ul>
        </section>
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
              <Badge className="ms-2" size="xs" variant={workflow.trust_state === 'trusted' ? 'default' : 'warn'}>
                {workflow.trust_state === 'trusted' ? copy.workflowTrusted : copy.workflowUntrusted}
              </Badge>
              {workflow.companion_path ? (
                <span className="mt-0.5 block break-all font-mono text-[0.6875rem] text-(--ui-text-tertiary)">
                  {workflow.companion_path}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      </section>

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
