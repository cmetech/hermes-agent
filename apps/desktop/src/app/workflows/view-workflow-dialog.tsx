import { lazy, Suspense, useId, useMemo, useState } from 'react'

import { RichBoundary } from '@/components/assistant-ui/embeds/rich-boundary'
import { CodeCard, CodeCardBody, CodeCardHeader, CodeCardTitle } from '@/components/chat/code-card'
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
                  <CodeCardHeader>
                    <CodeCardTitle>{copy.workflowViewDefinitionTitle}</CodeCardTitle>
                    <CopyButton
                      appearance="icon"
                      buttonSize="icon-xs"
                      label={copy.workflowViewCopyDefinition}
                      text={definitionJson}
                    />
                  </CodeCardHeader>
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
