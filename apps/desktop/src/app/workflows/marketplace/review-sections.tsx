import { Badge } from '@/components/ui/badge'
import { useI18n } from '@/i18n'
import type {
  WorkflowMarketplaceDiagnostic,
  WorkflowMarketplaceExternalRequirements,
  WorkflowMarketplaceTrustReviewItem
} from '@/types/hermes'
import type { WorkflowTrustReviewItem } from '@/types/workflow-marketplace-lifecycle'

export function ReviewFacts({ items }: { items: ReadonlyArray<readonly [string, null | string]> }) {
  return (
    <dl className="grid grid-cols-[minmax(7rem,auto)_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
      {items.map(([label, value]) => (
        <div className="contents" key={label}>
          <dt className="text-(--ui-text-tertiary)">{label}</dt>
          <dd className="min-w-0 break-all font-mono">{value ?? '—'}</dd>
        </div>
      ))}
    </dl>
  )
}

export function ReviewValueList({ empty = false, values }: { empty?: boolean; values: readonly string[] }) {
  const { t } = useI18n()

  if (!values.length) {
    return empty ? (
      <p className="text-xs text-(--ui-text-tertiary)">{t.operations.workflowMarketplaceNoChanges}</p>
    ) : null
  }

  return (
    <ul className="mt-1 space-y-1 text-xs text-(--ui-text-secondary)">
      {values.map(value => (
        <li className="break-all font-mono" key={value}>
          {value}
        </li>
      ))}
    </ul>
  )
}

export function ReviewDiagnostics({
  diagnostics,
  title
}: {
  diagnostics: readonly WorkflowMarketplaceDiagnostic[]
  title: string
}) {
  if (!diagnostics.length) {
    return null
  }

  return (
    <section aria-label={title}>
      <h3 className="text-xs font-medium text-(--ui-text-primary)">{title}</h3>
      <ul className="mt-1 space-y-1 text-xs text-(--ui-text-secondary)">
        {diagnostics.map(item => (
          <li key={`${item.code}:${item.message}`}>
            <Badge className="me-1" size="xs" variant={item.severity === 'blocker' ? 'destructive' : 'warn'}>
              {item.code}
            </Badge>
            {item.message}
          </li>
        ))}
      </ul>
    </section>
  )
}

export function ReviewRequirements({ requirements }: { requirements: WorkflowMarketplaceExternalRequirements }) {
  const { t } = useI18n()
  const copy = t.operations

  const groups = [
    [copy.workflowMarketplaceProviders, requirements.providers],
    [copy.workflowMarketplaceRuntimes, requirements.runtimes],
    [copy.workflowMarketplaceSecrets, requirements.secrets],
    [copy.workflowMarketplaceServices, requirements.services],
    [copy.workflowMarketplaceTools, requirements.tools]
  ] as const

  return (
    <section>
      <h3 className="text-xs font-medium text-(--ui-text-primary)">{copy.workflowMarketplaceRequirements}</h3>
      {groups.every(([, values]) => values.length === 0) ? (
        <p className="text-xs text-(--ui-text-secondary)">{copy.workflowMarketplaceNoRequirements}</p>
      ) : (
        <dl className="mt-1 grid grid-cols-[minmax(6rem,auto)_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
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
      )}
    </section>
  )
}

export function WorkflowRiskReview({
  showTrustState = false,
  workflow
}: {
  showTrustState?: boolean
  workflow: WorkflowMarketplaceTrustReviewItem | WorkflowTrustReviewItem
}) {
  const { t } = useI18n()
  const copy = t.operations

  const groups = [
    [copy.workflowMarketplaceShellScriptNodes, workflow.shell_or_script_nodes],
    [copy.workflowMarketplaceScriptResources, workflow.script_resources],
    [copy.workflowMarketplaceCommandNodes, workflow.command_nodes],
    [copy.workflowMarketplaceCommandResources, workflow.command_resources],
    [copy.workflowMarketplaceLocalMcp, workflow.local_mcp_servers],
    [copy.workflowMarketplaceRemoteMcp, workflow.remote_mcp_servers],
    [copy.workflowMarketplaceMcpDefinitions, workflow.mcp_resources],
    [copy.workflowMarketplaceMcpResourceFiles, workflow.mcp_resource_files],
    [copy.workflowMarketplaceTools, workflow.requested_tools],
    [copy.workflowMarketplaceRequestedSkills, workflow.requested_skills],
    [copy.workflowMarketplaceOutwardActions, workflow.outward_action_nodes],
    [copy.workflowMarketplaceApprovalNodes, workflow.approval_nodes],
    [copy.workflowMarketplaceProviders, workflow.providers],
    [copy.workflowMarketplaceRequiredSecrets, workflow.required_secrets]
  ] as const

  return (
    <article className="grid gap-3 rounded-md border border-(--ui-stroke-tertiary) p-3">
      {showTrustState ? (
        <div className="flex items-center justify-between gap-2">
          <h4 className="text-sm font-medium text-(--ui-text-primary)">{workflow.workflow_name}</h4>
          <Badge variant={workflow.trust_state === 'trusted' ? 'default' : 'warn'}>
            {workflow.trust_state === 'trusted' ? copy.workflowTrusted : copy.workflowUntrusted}
          </Badge>
        </div>
      ) : (
        <h4 className="text-sm font-medium text-(--ui-text-primary)">{workflow.workflow_name}</h4>
      )}
      <ReviewFacts
        items={[
          [copy.workflowMarketplaceDefinitionPath, workflow.definition_path],
          [copy.workflowMarketplaceCompanionPath, workflow.companion_path],
          [copy.workflowMarketplacePackageDigest, workflow.package_digest],
          [copy.workflowMarketplaceRiskDigest, workflow.risk_digest]
        ]}
      />
      {groups.map(([label, values]) =>
        values.length ? (
          <section key={label}>
            <h5 className="text-xs font-medium text-(--ui-text-primary)">{label}</h5>
            <ReviewValueList values={values} />
          </section>
        ) : null
      )}
      <ReviewRequirements requirements={workflow.external_requirements} />
      <ReviewDiagnostics diagnostics={workflow.compatibility} title={copy.workflowMarketplaceCompatibility} />
    </article>
  )
}
