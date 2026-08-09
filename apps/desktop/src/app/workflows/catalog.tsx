import { useQuery } from '@tanstack/react-query'
import { useId } from 'react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { Tip } from '@/components/ui/tooltip'
import { getApiRequestProfile } from '@/hermes'
import { useI18n } from '@/i18n'
import { ExternalLink } from '@/lib/external-link'
import { listWorkflowDefinitions } from '@/lib/hermes-api'
import { Eye, Play } from '@/lib/icons'
import type { WorkflowDefinition, WorkflowDefinitionError } from '@/types/hermes'

import {
  desktopWorkflowLanguageLabel,
  desktopWorkflowRunDisabledReason,
  isDesktopProviderCapabilityProjection,
  workflowTrustAllowsRun
} from './catalog-run-policy'

const WORKFLOW_DOCS_URL =
  'https://github.com/cmetech/hermes-agent/blob/base/website/docs/user-guide/features/workflows.md'

export interface WorkflowCatalogProps {
  onRunWorkflow?: (workflow: WorkflowDefinition) => void
  onViewWorkflow?: (workflow: WorkflowDefinition) => void
}

function isCatalogError(item: WorkflowDefinition | WorkflowDefinitionError): item is WorkflowDefinitionError {
  return 'error' in item
}

function CatalogErrorRow({ item }: { item: WorkflowDefinitionError }) {
  const { t } = useI18n()

  const error =
    item.error === 'catalog_capacity'
      ? t.operations.workflowCatalogCapacityError
      : t.operations.workflowCatalogInvalidDefinition

  return (
    <tr>
      <td className="px-2.5 py-3 align-middle" colSpan={7}>
        <span className="font-medium text-(--ui-text-primary)">{item.name}</span>
        <span className="ml-2 text-(--ui-text-secondary)">{error}</span>
      </td>
    </tr>
  )
}

function CatalogRow({
  item,
  onRunWorkflow,
  onViewWorkflow
}: {
  item: WorkflowDefinition
  onRunWorkflow?: (workflow: WorkflowDefinition) => void
  onViewWorkflow?: (workflow: WorkflowDefinition) => void
}) {
  const { t } = useI18n()
  const runReasonId = useId()

  const runDisabledReason = desktopWorkflowRunDisabledReason(item, t.operations, 'catalog')

  const inputCount = item.inputs.length

  return (
    <tr>
      <td className="px-2.5 py-2 align-middle font-medium text-(--ui-text-primary)">{item.name}</td>
      <td className="px-2.5 py-2 align-middle font-mono text-(--ui-text-secondary)">{item.version}</td>
      <td className="px-2.5 py-2 align-middle">
        <span className="block truncate text-(--ui-text-secondary)" title={item.description}>
          {item.description}
        </span>
        {item.requires_ai ? (
          <span className="mt-0.5 block text-[0.625rem] text-(--ui-text-tertiary)">
            {t.operations.workflowRequiresAi}
          </span>
        ) : null}
      </td>
      <td className="px-2.5 py-2 align-middle">
        <Badge variant={workflowTrustAllowsRun(item.trust_state) ? 'default' : 'destructive'}>
          {item.trust_state === 'verified_bundled'
            ? t.operations.workflowVerifiedBundle
            : item.trust_state === 'trusted'
              ? t.operations.workflowTrusted
              : t.operations.workflowUntrusted}
        </Badge>
      </td>
      <td className="px-2.5 py-2 align-middle">
        <Badge variant={item.supported_inputs.supported ? 'muted' : 'warn'}>
          {inputCount === 0 ? t.operations.workflowNoInputs : t.operations.workflowInputCount(inputCount)}
        </Badge>
      </td>
      <td className="px-2.5 py-2 align-middle text-(--ui-text-secondary)">
        <div className="flex flex-wrap items-center gap-1">
          <span>
            {item.source === 'showcase'
              ? t.operations.workflowSourceBundled
              : item.source === 'profile'
                ? t.operations.workflowSourceProfile
                : t.operations.workflowSourceProject}
          </span>
          {item.language ? (
            <Badge variant={item.language.legacy ? 'muted' : 'default'}>
              {desktopWorkflowLanguageLabel(item.language, t.operations)}
            </Badge>
          ) : null}
          {item.compatibility?.runnable === false ? (
            <Badge variant="warn">{t.operations.workflowIncompatible}</Badge>
          ) : null}
          {isDesktopProviderCapabilityProjection(item.provider_capability, 'catalog') ? (
            <Badge variant={item.provider_capability.level === 'degraded' ? 'warn' : 'muted'}>
              {t.operations.workflowProviderReadiness}: {item.provider_capability.level}
            </Badge>
          ) : null}
        </div>
      </td>
      <td className="px-2.5 py-2 align-middle">
        <div className="flex items-center gap-1">
          <Tip label={t.operations.workflowView}>
            <Button
              aria-label={t.operations.workflowView}
              onClick={() => onViewWorkflow?.(item)}
              size="icon-xs"
              type="button"
              variant="ghost"
            >
              <Eye />
            </Button>
          </Tip>
          {runDisabledReason ? (
            <Tip label={runDisabledReason}>
              <span
                aria-disabled="true"
                aria-label={t.operations.workflowRunUnavailable}
                className="inline-flex rounded-[4px] outline-none focus-visible:ring-[0.1875rem] focus-visible:ring-ring/50"
                role="note"
                tabIndex={0}
              >
                <Button
                  aria-describedby={runReasonId}
                  aria-label={t.operations.workflowRun}
                  disabled
                  size="icon-xs"
                  type="button"
                  variant="ghost"
                >
                  <Play />
                </Button>
                <span className="sr-only" id={runReasonId}>
                  {runDisabledReason}
                </span>
              </span>
            </Tip>
          ) : (
            <Tip label={t.operations.workflowRun}>
              <Button
                aria-label={t.operations.workflowRun}
                onClick={() => onRunWorkflow?.(item)}
                size="icon-xs"
                type="button"
                variant="ghost"
              >
                <Play />
              </Button>
            </Tip>
          )}
        </div>
      </td>
    </tr>
  )
}

export function WorkflowCatalog({ onRunWorkflow, onViewWorkflow }: WorkflowCatalogProps) {
  const { t } = useI18n()
  const requestProfile = getApiRequestProfile()
  const profile = requestProfile ?? 'default'

  const catalog = useQuery({
    queryFn: () => listWorkflowDefinitions(requestProfile),
    queryKey: ['workflow-catalog', profile]
  })

  if (catalog.isLoading) {
    return (
      <div aria-label={t.operations.workflowCatalogLoading} className="grid min-h-48 place-items-center" role="status">
        <Loader aria-hidden className="size-10 text-primary/70" role="presentation" type="lemniscate-bloom" />
      </div>
    )
  }

  if (catalog.isError) {
    return (
      <div className="grid min-h-48 place-items-center" role="alert">
        <ErrorState
          description={t.operations.workflowCatalogErrorDescription}
          title={t.operations.workflowCatalogErrorTitle}
        >
          <Button onClick={() => void catalog.refetch()} size="sm" type="button" variant="secondary">
            {t.operations.workflowCatalogRetry}
          </Button>
        </ErrorState>
      </div>
    )
  }

  const items = catalog.data?.items ?? []

  if (items.length === 0) {
    return (
      <div>
        <EmptyState
          className="min-h-36"
          description={t.operations.workflowCatalogEmptyDescription}
          title={t.operations.workflowCatalogEmptyTitle}
        />
        <p className="text-center text-xs text-(--ui-text-secondary)">
          <ExternalLink href={WORKFLOW_DOCS_URL}>{t.operations.workflowCatalogDocs}</ExternalLink>
        </p>
      </div>
    )
  }

  return (
    <div>
      {catalog.data?.truncated ? (
        <div className="mb-2">
          <Alert aria-label={t.operations.workflowCatalogPartialLabel} role="status" variant="warning">
            <AlertDescription>{t.operations.workflowCatalogPartialDescription}</AlertDescription>
          </Alert>
        </div>
      ) : null}
      <div className="max-w-full overflow-x-auto">
        <table
          aria-label={t.operations.workflowCatalog}
          className="w-full min-w-[52rem] table-fixed text-left text-[length:var(--conversation-caption-font-size)]"
        >
          <thead className="border-b border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) text-[0.625rem] uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
            <tr>
              <th className="w-[16%] px-2.5 py-1.5 font-medium" scope="col">
                {t.operations.workflowColumnName}
              </th>
              <th className="w-[9%] px-2.5 py-1.5 font-medium" scope="col">
                {t.operations.workflowColumnVersion}
              </th>
              <th className="w-[28%] px-2.5 py-1.5 font-medium" scope="col">
                {t.operations.workflowColumnDescription}
              </th>
              <th className="w-[10%] px-2.5 py-1.5 font-medium" scope="col">
                {t.operations.workflowColumnTrust}
              </th>
              <th className="w-[10%] px-2.5 py-1.5 font-medium" scope="col">
                {t.operations.workflowColumnInputs}
              </th>
              <th className="w-[12%] px-2.5 py-1.5 font-medium" scope="col">
                {t.operations.workflowColumnSource}
              </th>
              <th className="w-[15%] px-2.5 py-1.5 font-medium" scope="col">
                {t.operations.workflowColumnActions}
              </th>
            </tr>
          </thead>
          <tbody>
            {items.map((item, index) =>
              isCatalogError(item) ? (
                <CatalogErrorRow item={item} key={`${item.name}-${item.error}-${index}`} />
              ) : (
                <CatalogRow
                  item={item}
                  key={`${item.name}-${item.source}-${item.version}`}
                  onRunWorkflow={onRunWorkflow}
                  onViewWorkflow={onViewWorkflow}
                />
              )
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
