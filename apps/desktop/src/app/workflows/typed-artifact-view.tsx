import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ErrorBanner, ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { LogView } from '@/components/ui/log-view'
import {
  cancelWorkflowArtifactDownload,
  downloadWorkflowArtifact,
  getApiRequestProfile,
  getWorkflowArtifactPreview
} from '@/hermes'
import { useI18n } from '@/i18n'
import type { WorkflowArtifactPreview, WorkflowTypedArtifact } from '@/types/hermes'

interface TypedArtifactViewProps {
  artifacts: WorkflowTypedArtifact[]
  runId: string
}

interface MetadataRow {
  label: string
  value: null | string
}

interface DownloadFeedback {
  contextKey: string
  publicationId: string
  requestId: string
  status: 'cancelled' | 'error'
}

interface ActiveDownload {
  contextKey: string
  generation: number
  publicationId: string
  requestId: string
}

export function isWorkflowTypedArtifact(item: unknown): item is WorkflowTypedArtifact {
  if (!item || typeof item !== 'object') {
    return false
  }

  const publicationId = (item as { publication_id?: unknown }).publication_id

  return typeof publicationId === 'string' && publicationId.trim().length > 0
}

function readableValue(value: null | string | undefined, fallback: string): string {
  return typeof value === 'string' && value.length > 0 ? value : fallback
}

function previewableMediaType(mediaType: null | string | undefined): boolean {
  const normalized = mediaType?.toLowerCase()

  return normalized === 'application/json' || Boolean(normalized?.startsWith('text/'))
}

function previewMatchesArtifact(preview: WorkflowArtifactPreview, artifact: WorkflowTypedArtifact): boolean {
  return (
    preview.publication_id === artifact.publication_id &&
    preview.media_type === artifact.media_type &&
    Number.isSafeInteger(preview.bytes_returned) &&
    preview.bytes_returned >= 0 &&
    Number.isSafeInteger(preview.size_bytes) &&
    preview.size_bytes >= preview.bytes_returned &&
    typeof preview.truncated === 'boolean'
  )
}

function accessibleIdentityPart(value: null | string | undefined, fallback: string): string {
  const normalized = value?.trim()

  return (normalized || fallback).slice(0, 80)
}

export function TypedArtifactView({ artifacts, runId }: TypedArtifactViewProps) {
  const { locale, t } = useI18n()
  const copy = t.operations
  const profile = getApiRequestProfile() ?? 'default'
  const contextKey = `${profile}\u0000${runId}`
  const activeDownloadRef = useRef<ActiveDownload | null>(null)
  const contextRef = useRef(contextKey)
  const generationRef = useRef(0)
  const requestSequenceRef = useRef(0)
  contextRef.current = contextKey
  const [downloadFeedback, setDownloadFeedback] = useState<DownloadFeedback | null>(null)
  const [downloading, setDownloading] = useState<ActiveDownload | null>(null)
  const [selectedPublicationId, setSelectedPublicationId] = useState<null | string>(null)

  useEffect(() => {
    setDownloadFeedback(null)
    setDownloading(null)
    setSelectedPublicationId(null)

    return () => {
      const active = activeDownloadRef.current

      if (active?.contextKey === contextKey) {
        activeDownloadRef.current = null
        generationRef.current += 1
        void cancelWorkflowArtifactDownload(active.requestId)
      }
    }
  }, [contextKey])

  const handleDownload = async (artifact: WorkflowTypedArtifact) => {
    const publicationId = artifact.publication_id
    const activeProfile = getApiRequestProfile() ?? 'default'
    const activeContextKey = `${activeProfile}\u0000${runId}`
    const requestId = `workflow-artifact-${Date.now()}-${++requestSequenceRef.current}`
    const generation = ++generationRef.current
    const active: ActiveDownload = { contextKey: activeContextKey, generation, publicationId, requestId }

    const previous = activeDownloadRef.current

    if (previous && previous.contextKey !== activeContextKey) {
      void cancelWorkflowArtifactDownload(previous.requestId)
    }

    setDownloadFeedback(null)
    setDownloading(active)
    activeDownloadRef.current = active

    const isCurrent = () =>
      activeDownloadRef.current?.requestId === requestId &&
      generationRef.current === generation &&
      contextRef.current === activeContextKey

    try {
      const result = await downloadWorkflowArtifact(runId, publicationId, activeProfile, requestId)

      if (isCurrent() && result.status === 'cancelled') {
        setDownloadFeedback({ contextKey: activeContextKey, publicationId, requestId, status: 'cancelled' })
      }
    } catch (error) {
      if (isCurrent() && !(error instanceof DOMException && error.name === 'AbortError')) {
        setDownloadFeedback({ contextKey: activeContextKey, publicationId, requestId, status: 'error' })
      }
    } finally {
      if (isCurrent()) {
        activeDownloadRef.current = null
        setDownloading(null)
      }
    }
  }

  const selectedArtifact = useMemo(
    () => artifacts.find(artifact => artifact.publication_id === selectedPublicationId) ?? null,
    [artifacts, selectedPublicationId]
  )

  const previewEnabled = selectedArtifact !== null && previewableMediaType(selectedArtifact.media_type)

  const preview = useQuery({
    enabled: previewEnabled,
    queryFn: () => getWorkflowArtifactPreview(runId, selectedArtifact!.publication_id),
    queryKey: ['workflow-artifact-preview', profile, runId, selectedArtifact?.publication_id],
    staleTime: 5_000
  })

  return (
    <div className="space-y-4">
      <div className="divide-y divide-(--ui-stroke-tertiary)" role="list">
        {artifacts.map((artifact, index) => {
          const size =
            typeof artifact.size_bytes === 'number' &&
            Number.isSafeInteger(artifact.size_bytes) &&
            artifact.size_bytes >= 0
              ? copy.artifactSizeBytes(new Intl.NumberFormat(locale).format(artifact.size_bytes))
              : copy.artifactUnavailable

          const metadata: MetadataRow[] = [
            {
              label: copy.artifactOutputType,
              value: readableValue(artifact.output_type, copy.artifactUnavailable)
            },
            {
              label: copy.artifactMediaType,
              value: readableValue(artifact.media_type, copy.artifactUnavailable)
            },
            {
              label: copy.artifactProducer,
              value: readableValue(artifact.node_id, copy.artifactUnavailable)
            },
            {
              label: copy.artifactWinningAttempt,
              value: readableValue(artifact.attempt_id, copy.artifactUnavailable)
            },
            { label: copy.artifactSize, value: size },
            { label: copy.artifactSha256, value: readableValue(artifact.sha256, copy.artifactUnavailable) },
            { label: copy.artifactSchemaFingerprint, value: artifact.schema_fingerprint ?? null },
            { label: copy.artifactProducedAt, value: artifact.produced_at ?? null },
            { label: copy.artifactSession, value: artifact.session_id ?? null },
            {
              label: copy.artifactIntegrity,
              value: readableValue(artifact.integrity_status, copy.artifactUnavailable)
            },
            {
              label: copy.artifactRecovery,
              value: readableValue(artifact.recovery_status, copy.artifactUnavailable)
            }
          ]

          const canPreview = previewableMediaType(artifact.media_type)

          const identity = [
            accessibleIdentityPart(artifact.output_type, copy.artifactUnavailable),
            accessibleIdentityPart(artifact.node_id, copy.artifactUnavailable),
            accessibleIdentityPart(artifact.publication_id, copy.artifactUnavailable)
          ].join(' · ')

          const currentDownload = downloading?.contextKey === contextKey ? downloading : null
          const isDownloading = currentDownload?.publicationId === artifact.publication_id

          const feedback =
            downloadFeedback?.contextKey === contextKey && downloadFeedback.publicationId === artifact.publication_id
              ? downloadFeedback
              : null

          return (
            <section
              aria-label={copy.artifactLabel(identity)}
              className="grid gap-3 py-4 first:pt-0 last:pb-0"
              key={`${artifact.publication_id}:${index}`}
              role="listitem"
            >
              <dl className="grid grid-cols-[minmax(8rem,auto)_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
                {metadata.map(row =>
                  row.value === null ? null : (
                    <div className="contents" key={row.label}>
                      <dt className="text-(--ui-text-secondary)">{row.label}</dt>
                      <dd className="min-w-0 break-all font-mono text-xs text-(--ui-text-primary)">{row.value}</dd>
                    </div>
                  )
                )}
              </dl>

              <div className="flex flex-wrap items-center gap-2">
                {canPreview ? (
                  <Button
                    aria-label={copy.artifactPreviewFor(identity)}
                    aria-pressed={selectedPublicationId === artifact.publication_id}
                    onClick={() => setSelectedPublicationId(artifact.publication_id)}
                    size="sm"
                    type="button"
                    variant="secondary"
                  >
                    {copy.artifactPreview}
                  </Button>
                ) : (
                  <span className="text-xs text-(--ui-text-tertiary)">{copy.artifactDownloadOnly}</span>
                )}
                <Button
                  aria-label={
                    isDownloading ? copy.artifactDownloadingFor(identity) : copy.artifactDownloadFor(identity)
                  }
                  disabled={currentDownload !== null}
                  onClick={() => void handleDownload(artifact)}
                  size="sm"
                  type="button"
                  variant="secondary"
                >
                  {isDownloading ? copy.artifactDownloading : copy.artifactDownload}
                </Button>
              </div>

              {feedback?.status === 'cancelled' ? (
                <p aria-live="polite" className="text-xs text-(--ui-text-tertiary)" role="status">
                  {copy.artifactDownloadCancelled}
                </p>
              ) : feedback?.status === 'error' ? (
                <div aria-live="assertive" role="alert">
                  <ErrorBanner>
                    <span className="grid gap-1">
                      <span className="font-medium">{copy.artifactDownloadErrorTitle}</span>
                      <span>{copy.artifactDownloadErrorDescription}</span>
                    </span>
                  </ErrorBanner>
                </div>
              ) : null}
            </section>
          )
        })}
      </div>

      {selectedArtifact && previewEnabled ? (
        <section aria-label={copy.artifactPreviewRegion} className="min-w-0" role="region">
          {preview.isPending ? (
            <Loader className="py-4" label={copy.artifactPreviewLoading} type="lemniscate-bloom" />
          ) : preview.isError ? (
            <ErrorState
              className="py-4"
              description={copy.artifactPreviewErrorDescription}
              title={copy.artifactPreviewErrorTitle}
            >
              <Button onClick={() => void preview.refetch()} size="sm" type="button" variant="secondary">
                {copy.retry}
              </Button>
            </ErrorState>
          ) : preview.data && previewMatchesArtifact(preview.data, selectedArtifact) ? (
            selectedArtifact.media_type === 'application/json' ? (
              preview.data.truncated || preview.data.bytes_returned !== preview.data.size_bytes ? (
                <p className="text-sm text-(--ui-text-tertiary)">{copy.artifactPreviewIncomplete}</p>
              ) : Object.prototype.hasOwnProperty.call(preview.data, 'content') ? (
                <LogView className="max-h-80">{JSON.stringify(preview.data.content, null, 2)}</LogView>
              ) : (
                <ErrorState
                  className="py-4"
                  description={copy.artifactPreviewErrorDescription}
                  title={copy.artifactPreviewErrorTitle}
                />
              )
            ) : typeof preview.data.content === 'string' ? (
              <div className="space-y-2">
                <LogView className="max-h-80">{preview.data.content}</LogView>
                {preview.data.truncated ? (
                  <p className="text-xs text-(--ui-text-tertiary)">{copy.artifactTextPreviewTruncated}</p>
                ) : null}
              </div>
            ) : (
              <ErrorState
                className="py-4"
                description={copy.artifactPreviewErrorDescription}
                title={copy.artifactPreviewErrorTitle}
              />
            )
          ) : (
            <ErrorState
              className="py-4"
              description={copy.artifactPreviewErrorDescription}
              title={copy.artifactPreviewErrorTitle}
            />
          )}
        </section>
      ) : null}
    </div>
  )
}
