import { Fragment, lazy, Suspense, useId, useMemo, useState } from 'react'

import { RichBoundary } from '@/components/assistant-ui/embeds/rich-boundary'
import { CodeCard, CodeCardBody } from '@/components/chat/code-card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { CopyButton } from '@/components/ui/copy-button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { useI18n } from '@/i18n'
import { WorkflowApiError } from '@/lib/hermes-api'
import { Eye, Play } from '@/lib/icons'
import type { WorkflowDefinition } from '@/types/hermes'

import { desktopWorkflowLanguageLabel, desktopWorkflowRunDisabledReason } from './catalog-run-policy'
import { useWorkflowDetailQuery } from './detail-query'

const MermaidRenderer = lazy(() => import('@/components/assistant-ui/embeds/mermaid-embed'))

type ViewMode = 'definition' | 'diagram'

interface DependencySourceView {
  catalogSource: string
  precedence: number
  workflow: string
}

export interface ViewWorkflowDialogProps {
  onClose: () => void
  onRun: () => void
  profile: null | string
  workflow: WorkflowDefinition
}

function stableJsonValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(stableJsonValue)
  }

  if (typeof value !== 'object' || value === null) {
    return value
  }

  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([key, item]) => [key, stableJsonValue(item)])
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function dependencySources(value: unknown): DependencySourceView[] {
  if (!Array.isArray(value)) {
    return []
  }

  return value.flatMap(item => {
    if (!isRecord(item) || typeof item.catalog_source !== 'string' || !Number.isFinite(item.precedence)) {
      return []
    }

    const workflow =
      typeof item.workflow_name === 'string' && item.workflow_name.length > 0
        ? item.workflow_name
        : typeof item.package_key === 'string' && item.package_key.length > 0
          ? item.package_key
          : null

    return workflow ? [{ catalogSource: item.catalog_source, precedence: Number(item.precedence), workflow }] : []
  })
}

function ignoredPolicyFields(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return []
  }

  return Array.from(
    new Set(
      value.flatMap(item =>
        isRecord(item) && Array.isArray(item.fields)
          ? item.fields.filter((field): field is string => typeof field === 'string' && field.length > 0)
          : []
      )
    )
  )
}

export function stableWorkflowDefinitionJson(definition: Record<string, unknown>): string {
  return JSON.stringify(stableJsonValue(definition), null, 2)
}

function DiagramSourcePreview({ code, muted = false }: { code: string; muted?: boolean }) {
  return (
    <pre
      className={`max-h-[50dvh] max-w-full overflow-auto p-3 font-mono text-[0.7rem] leading-relaxed whitespace-pre-wrap wrap-anywhere ${muted ? 'text-(--ui-text-tertiary)' : 'text-(--ui-text-primary)'}`}
    >
      {code}
    </pre>
  )
}

function Diagram({ code }: { code: string }) {
  const fallback = <DiagramSourcePreview code={code} />

  return (
    <div className="max-h-[55dvh] max-w-full overflow-auto" data-workflow-view-scroll>
      <RichBoundary fallback={fallback} resetKey={code}>
        <Suspense fallback={<DiagramSourcePreview code={code} muted />}>
          <MermaidRenderer code={code} streaming={false} />
        </Suspense>
      </RichBoundary>
    </div>
  )
}

export function ViewWorkflowDialog({ onClose, onRun, profile, workflow }: ViewWorkflowDialogProps) {
  const { t } = useI18n()
  const copy = t.operations
  const runReasonId = useId()
  const [mode, setMode] = useState<ViewMode>('diagram')
  const detail = useWorkflowDetailQuery(workflow.name, workflow.source, profile)

  const definitionJson = useMemo(
    () => (detail.data ? stableWorkflowDefinitionJson(detail.data.definition) : ''),
    [detail.data]
  )

  const modes = useMemo(
    () => [
      { id: 'diagram' as const, label: copy.workflowViewDiagram },
      { id: 'definition' as const, label: copy.workflowViewDefinition }
    ],
    [copy.workflowViewDefinition, copy.workflowViewDiagram]
  )

  const runDisabledReason = detail.isError
    ? copy.workflowViewRunError
    : !detail.data
      ? copy.workflowViewRunLoading
      : desktopWorkflowRunDisabledReason(detail.data, copy, 'detail')

  const compilation = isRecord(detail.data?.compilation) ? detail.data.compilation : null
  const sources = dependencySources(compilation?.sources)
  const counts = isRecord(compilation?.counts) ? compilation.counts : null

  const dependencyCounts = [
    [copy.workflowDependencyPackages, counts?.dependency_packages],
    [copy.workflowExpandedNodes, counts?.expanded_nodes],
    [copy.workflowExpandedEdges, counts?.expanded_edges],
    [copy.workflowIncludeDepth, compilation?.include_depth]
  ].filter((item): item is [string, number] => typeof item[1] === 'number' && Number.isFinite(item[1]))

  const compositeDigest =
    typeof compilation?.composite_digest === 'string' && compilation.composite_digest.length > 0
      ? compilation.composite_digest
      : null

  const ignoredFields = ignoredPolicyFields(compilation?.ignored_policies)

  const hasDependencyDetails =
    sources.length > 0 || dependencyCounts.length > 0 || compositeDigest !== null || ignoredFields.length > 0

  const errorDescription =
    detail.error instanceof WorkflowApiError && detail.error.code === 'workflow_not_found'
      ? copy.workflowViewNotFound
      : copy.workflowViewErrorDescription

  return (
    <Dialog onOpenChange={open => !open && onClose()} open>
      <DialogContent className="min-w-0 overflow-x-hidden sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle icon={Eye}>{copy.workflowViewTitle(workflow.name)}</DialogTitle>
          <DialogDescription>{copy.workflowViewDescription}</DialogDescription>
        </DialogHeader>
        {detail.isPending ? (
          <div aria-label={copy.workflowViewLoading} className="grid min-h-40 place-items-center" role="status">
            <Loader aria-hidden className="size-9 text-primary/70" role="presentation" type="lemniscate-bloom" />
          </div>
        ) : null}
        {detail.isError ? (
          <ErrorState description={errorDescription} title={copy.workflowViewErrorTitle}>
            <Button onClick={() => void detail.refetch()} size="sm" type="button" variant="secondary">
              {copy.workflowRunRetry}
            </Button>
          </ErrorState>
        ) : null}
        {detail.data ? (
          <div className="grid min-h-0 min-w-0 gap-3 overflow-x-hidden">
            {detail.data.language ? (
              <Alert variant={detail.data.language.legacy ? 'warning' : 'default'}>
                <AlertDescription>
                  <Badge variant={detail.data.language.legacy ? 'muted' : 'default'}>
                    {desktopWorkflowLanguageLabel(detail.data.language, copy)}
                  </Badge>
                  {detail.data.language.legacy ? <p>{copy.workflowLanguageLegacyDescription}</p> : null}
                  {detail.data.language.normalizer_version !== undefined ? (
                    <p>
                      {copy.workflowLanguageNormalizer} {detail.data.language.normalizer_version}
                    </p>
                  ) : null}
                  {detail.data.language.normalized_definition_digest ? (
                    <p className="flex flex-wrap gap-1">
                      <span>{copy.workflowLanguageDigest}</span>
                      <span className="font-mono" title={detail.data.language.normalized_definition_digest}>
                        {detail.data.language.normalized_definition_digest.slice(0, 12)}…
                      </span>
                    </p>
                  ) : null}
                </AlertDescription>
              </Alert>
            ) : null}
            {hasDependencyDetails ? (
              <section aria-label={copy.workflowDependencies} className="grid min-w-0 gap-2">
                <h3 className="text-sm font-medium">{copy.workflowDependencies}</h3>
                {sources.length > 0 ? (
                  <div className="grid gap-1">
                    <p className="text-xs text-(--ui-text-secondary)">{copy.workflowDependencySources}</p>
                    <ul className="grid gap-1 text-xs">
                      {sources.map(source => (
                        <li
                          className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1"
                          key={`${source.catalogSource}:${source.precedence}:${source.workflow}`}
                        >
                          <span className="font-medium">{source.workflow}</span>
                          <Badge variant="muted">
                            {source.catalogSource === 'profile'
                              ? copy.workflowSourceProfile
                              : source.catalogSource === 'project'
                                ? copy.workflowSourceProject
                                : source.catalogSource === 'showcase'
                                  ? copy.workflowSourceBundled
                                  : source.catalogSource}
                          </Badge>
                          <span className="text-(--ui-text-secondary)">
                            <span>{copy.workflowDependencyPrecedence}</span>: {source.precedence}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {dependencyCounts.length > 0 || compositeDigest ? (
                  <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-4 gap-y-1 text-xs">
                    {dependencyCounts.map(([label, value]) => (
                      <Fragment key={label}>
                        <dt className="text-(--ui-text-secondary)">{label}</dt>
                        <dd>{value}</dd>
                      </Fragment>
                    ))}
                    {compositeDigest ? (
                      <>
                        <dt className="text-(--ui-text-secondary)">{copy.workflowCompositeDigest}</dt>
                        <dd className="break-all font-mono">{compositeDigest}</dd>
                      </>
                    ) : null}
                  </dl>
                ) : null}
                {ignoredFields.length > 0 ? (
                  <div className="grid gap-1">
                    <p className="text-xs text-(--ui-text-secondary)">{copy.workflowIgnoredPolicies}</p>
                    <div className="flex flex-wrap gap-1">
                      {ignoredFields.map(field => (
                        <Badge key={field} variant="warn">
                          {copy.workflowIgnoredPolicyField(field.replaceAll('_', ' '))}
                        </Badge>
                      ))}
                    </div>
                  </div>
                ) : null}
              </section>
            ) : null}
            <SegmentedControl onChange={setMode} options={modes} value={mode} />
            {mode === 'diagram' ? (
              detail.data.topology.mermaid ? (
                <Diagram code={detail.data.topology.mermaid} />
              ) : (
                <div className="max-h-[55dvh] max-w-full overflow-auto" data-workflow-view-scroll>
                  <p className="mb-2 text-xs font-medium text-(--ui-text-secondary)">
                    {detail.data.topology.omitted?.startsWith('topology_mermaid_too_')
                      ? copy.workflowViewDiagramTooLarge
                      : copy.workflowViewDiagramOmitted}
                  </p>
                  <DiagramSourcePreview code={detail.data.topology.text} />
                </div>
              )
            ) : (
              <div className="grid min-h-0 min-w-0 gap-2" data-workflow-view-scroll>
                <p className="text-xs text-(--ui-text-secondary)">{copy.workflowViewDefinitionCaption}</p>
                <CodeCard>
                  {/* Upstream v0.20.0 removed CodeCardHeader/CodeCardTitle (headerless
                      code cards); keep the title + copy affordance as an inline row. */}
                  <div className="flex items-center justify-between gap-2 border-b border-border px-2 py-1.5">
                    <span className="flex min-w-0 items-center gap-1.5 truncate text-[length:var(--conversation-tool-font-size)] font-medium leading-(--conversation-line-height) text-foreground/80">
                      {copy.workflowViewDefinitionTitle}
                    </span>
                    <CopyButton
                      appearance="icon"
                      buttonSize="icon-xs"
                      label={copy.workflowViewCopyDefinition}
                      text={definitionJson}
                    />
                  </div>
                  <CodeCardBody className="max-h-[48dvh] overflow-auto select-text">
                    <pre data-testid="workflow-definition-json">{definitionJson}</pre>
                  </CodeCardBody>
                </CodeCard>
              </div>
            )}
            <p className="text-sm leading-relaxed text-(--ui-text-secondary)">{detail.data.description}</p>
          </div>
        ) : null}
        <DialogFooter className={runDisabledReason ? 'sm:items-center sm:justify-between' : undefined}>
          {runDisabledReason ? (
            <p className="text-xs leading-relaxed text-(--ui-text-secondary)" id={runReasonId}>
              {runDisabledReason}
            </p>
          ) : null}
          <Button
            aria-describedby={runDisabledReason ? runReasonId : undefined}
            disabled={Boolean(runDisabledReason)}
            onClick={onRun}
            size="sm"
            type="button"
          >
            <Play />
            {copy.workflowRun}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
