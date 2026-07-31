// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'
import type { WorkflowArtifactPreview, WorkflowTypedArtifact } from '@/types/hermes'

import { TypedArtifactView } from './typed-artifact-view'

const getWorkflowArtifactPreview = vi.fn()
const downloadWorkflowArtifact = vi.fn()
const cancelWorkflowArtifactDownload = vi.fn()
const apiRequestState = vi.hoisted(() => ({ profile: 'default' as null | string }))

vi.mock('@/hermes', () => ({
  cancelWorkflowArtifactDownload: (...args: unknown[]) => cancelWorkflowArtifactDownload(...args),
  downloadWorkflowArtifact: (...args: unknown[]) => downloadWorkflowArtifact(...args),
  getApiRequestProfile: () => apiRequestState.profile,
  getWorkflowArtifactPreview: (...args: unknown[]) => getWorkflowArtifactPreview(...args)
}))

function artifact(overrides: Partial<WorkflowTypedArtifact> = {}): WorkflowTypedArtifact {
  return {
    attempt_id: 'attempt-7',
    integrity_status: 'verified',
    media_type: 'application/json',
    node_id: 'produce-report',
    output_type: 'DiagnosticReport',
    produced_at: '2026-07-30T12:00:00Z',
    publication_id: 'publication / opaque',
    recovery_status: 'verified',
    schema_fingerprint: 'schema-fingerprint',
    session_id: 'session-9',
    sha256: 'a'.repeat(64),
    size_bytes: 1_024,
    ...overrides
  }
}

function view(artifacts: WorkflowTypedArtifact[], runId = 'run / one') {
  return (
    <I18nProvider configClient={null} initialLocale="en">
      <TypedArtifactView artifacts={artifacts} runId={runId} />
    </I18nProvider>
  )
}

function renderView(artifacts: WorkflowTypedArtifact[], runId = 'run / one') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(<QueryClientProvider client={client}>{view(artifacts, runId)}</QueryClientProvider>)
}

beforeEach(() => {
  apiRequestState.profile = 'default'
  cancelWorkflowArtifactDownload.mockReset()
  cancelWorkflowArtifactDownload.mockResolvedValue({ cancelled: true })
  downloadWorkflowArtifact.mockReset()
  downloadWorkflowArtifact.mockResolvedValue({
    filename: 'diagnostic.json',
    mediaType: 'application/json',
    sizeBytes: 1_024,
    status: 'saved'
  })
  getWorkflowArtifactPreview.mockReset()
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('TypedArtifactView', () => {
  it('renders confirmed metadata and fetches complete canonical JSON only after explicit preview selection', async () => {
    const preview: WorkflowArtifactPreview = {
      bytes_returned: 13,
      content: { answer: 42 },
      media_type: 'application/json',
      publication_id: 'publication / opaque',
      size_bytes: 13,
      truncated: false
    }

    getWorkflowArtifactPreview.mockResolvedValue(preview)

    renderView([artifact()])

    expect(screen.getByText('DiagnosticReport')).toBeTruthy()
    expect(screen.getByText('application/json')).toBeTruthy()
    expect(screen.getByText('produce-report')).toBeTruthy()
    expect(screen.getByText('attempt-7')).toBeTruthy()
    expect(screen.getByText('1,024 bytes')).toBeTruthy()
    expect(screen.getByText('a'.repeat(64))).toBeTruthy()
    expect(screen.getByText('schema-fingerprint')).toBeTruthy()
    expect(screen.getByText('2026-07-30T12:00:00Z')).toBeTruthy()
    expect(screen.getByText('session-9')).toBeTruthy()
    expect(screen.getAllByText('verified')).toHaveLength(2)
    expect(getWorkflowArtifactPreview).not.toHaveBeenCalled()

    const download = screen.getByRole('button', {
      name: 'Download artifact: DiagnosticReport · produce-report · publication / opaque'
    })

    expect(download.getAttribute('href')).toBeNull()
    fireEvent.click(download)
    await waitFor(() =>
      expect(downloadWorkflowArtifact).toHaveBeenCalledWith(
        'run / one',
        'publication / opaque',
        'default',
        expect.stringMatching(/^workflow-artifact-/)
      )
    )

    fireEvent.click(screen.getByRole('button', { name: /Preview artifact:/ }))

    await waitFor(() => expect(getWorkflowArtifactPreview).toHaveBeenCalledWith('run / one', 'publication / opaque'))
    expect((await screen.findByRole('region', { name: 'Artifact preview' })).textContent).toContain(
      '{\n  "answer": 42\n}'
    )
  })

  it('keeps unknown media download-only and tolerates missing optional metadata', () => {
    renderView([
      artifact({
        attempt_id: undefined,
        integrity_status: undefined,
        media_type: 'application/x-future-artifact',
        node_id: undefined,
        output_type: undefined,
        produced_at: undefined,
        recovery_status: undefined,
        schema_fingerprint: undefined,
        session_id: undefined,
        sha256: undefined,
        size_bytes: undefined
      })
    ])

    expect(screen.getByText('application/x-future-artifact')).toBeTruthy()
    expect(screen.getByText('Preview unavailable for this media type. Download remains available.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Preview artifact:/ })).toBeNull()
    expect(screen.getByRole('button', { name: /Download artifact:/ })).toBeTruthy()
    expect(getWorkflowArtifactPreview).not.toHaveBeenCalled()
  })

  it('does not format incomplete JSON and keeps preview failures inside the artifact surface', async () => {
    getWorkflowArtifactPreview.mockResolvedValueOnce({
      bytes_returned: 0,
      content: null,
      media_type: 'application/json',
      publication_id: 'json-large',
      size_bytes: 70_000,
      truncated: true
    } satisfies WorkflowArtifactPreview)

    renderView([artifact({ publication_id: 'json-large' })])
    fireEvent.click(screen.getByRole('button', { name: /Preview artifact:/ }))

    expect(
      await screen.findByText('The bounded preview is incomplete. Download the artifact to inspect it.')
    ).toBeTruthy()
    expect(screen.queryByText('{')).toBeNull()

    cleanup()
    getWorkflowArtifactPreview.mockRejectedValueOnce(new Error('preview unavailable'))
    renderView([artifact({ media_type: 'text/markdown; charset=utf-8', publication_id: 'text-failure' })])
    fireEvent.click(screen.getByRole('button', { name: /Preview artifact:/ }))

    expect(await screen.findByText('Could not load artifact preview')).toBeTruthy()
    expect(screen.getByText('DiagnosticReport')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Download artifact:/ })).toBeTruthy()
  })

  it('keeps pending, cancellation, and retryable download errors inside the artifact row', async () => {
    let resolveDownload!: (value: { status: 'cancelled' }) => void
    downloadWorkflowArtifact.mockImplementationOnce(
      () =>
        new Promise(resolve => {
          resolveDownload = resolve
        })
    )

    renderView([artifact()])
    fireEvent.click(screen.getByRole('button', { name: /Download artifact:/ }))
    expect(await screen.findByRole('button', { name: /Downloading artifact:/ })).toBeTruthy()

    resolveDownload({ status: 'cancelled' })
    expect(await screen.findByText('Download canceled.')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Download artifact:/ })).toBeTruthy()

    downloadWorkflowArtifact.mockRejectedValueOnce(new Error('gateway unavailable'))
    fireEvent.click(screen.getByRole('button', { name: /Download artifact:/ }))

    expect(await screen.findByText('Could not download artifact')).toBeTruthy()
    expect(screen.getByText('The download failed. Try again.')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Download artifact:/ })).toBeTruthy()
  })

  it('gives repeated artifact rows unique accessible actions and announces async errors', async () => {
    downloadWorkflowArtifact.mockRejectedValue(new Error('network failed'))
    renderView([
      artifact({ node_id: 'producer-a', publication_id: 'publication-a' }),
      artifact({ node_id: 'producer-b', publication_id: 'publication-b' })
    ])

    expect(
      screen.getByRole('button', { name: 'Preview artifact: DiagnosticReport · producer-a · publication-a' })
    ).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Download artifact: DiagnosticReport · producer-a · publication-a' })
    ).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Download artifact: DiagnosticReport · producer-b · publication-b' })
    ).toBeTruthy()

    fireEvent.click(
      screen.getByRole('button', { name: 'Download artifact: DiagnosticReport · producer-b · publication-b' })
    )
    expect((await screen.findByRole('alert')).textContent).toContain('Could not download artifact')
  })

  it('cancels and ignores stale download settlement when profile and run context change', async () => {
    let resolveOld!: (value: { status: 'cancelled' }) => void

    const oldRequest = new Promise<{ status: 'cancelled' }>(resolve => {
      resolveOld = resolve
    })

    downloadWorkflowArtifact
      .mockReturnValueOnce(oldRequest)
      .mockResolvedValueOnce({ filename: 'new.json', mediaType: 'application/json', sizeBytes: 2, status: 'saved' })

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    const rendered = render(
      <QueryClientProvider client={client}>
        {view([artifact({ publication_id: 'old-publication' })], 'old-run')}
      </QueryClientProvider>
    )

    fireEvent.click(screen.getByRole('button', { name: /Download artifact:.*old-publication/ }))
    await waitFor(() => expect(downloadWorkflowArtifact).toHaveBeenCalledTimes(1))

    apiRequestState.profile = 'new-profile'
    rendered.rerender(
      <QueryClientProvider client={client}>
        {view([artifact({ node_id: 'new-producer', publication_id: 'new-publication' })], 'new-run')}
      </QueryClientProvider>
    )

    const newAction = await screen.findByRole('button', { name: /Download artifact:.*new-publication/ })
    expect((newAction as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(newAction)
    await waitFor(() => expect(downloadWorkflowArtifact).toHaveBeenCalledTimes(2))
    expect(cancelWorkflowArtifactDownload).toHaveBeenCalledTimes(1)

    resolveOld({ status: 'cancelled' })
    await waitFor(() => expect(screen.queryByText('Download canceled.')).toBeNull())
    expect(
      (screen.getByRole('button', { name: /Download artifact:.*new-publication/ }) as HTMLButtonElement).disabled
    ).toBe(false)
  })

  it('isolates preview state by selection so a stale response cannot replace newer intent', async () => {
    let resolveFirst!: (value: WorkflowArtifactPreview) => void

    const first = new Promise<WorkflowArtifactPreview>(resolve => {
      resolveFirst = resolve
    })

    getWorkflowArtifactPreview.mockImplementation((_runId: string, publicationId: string) =>
      publicationId === 'first'
        ? first
        : Promise.resolve({
            bytes_returned: 6,
            content: 'second',
            media_type: 'text/markdown; charset=utf-8',
            publication_id: 'second',
            size_bytes: 6,
            truncated: false
          } satisfies WorkflowArtifactPreview)
    )

    renderView([
      artifact({ media_type: 'text/markdown; charset=utf-8', publication_id: 'first' }),
      artifact({ media_type: 'text/markdown; charset=utf-8', publication_id: 'second' })
    ])
    const rows = screen.getAllByRole('listitem')
    fireEvent.click(within(rows[0]!).getByRole('button', { name: /Preview artifact:/ }))
    fireEvent.click(within(rows[1]!).getByRole('button', { name: /Preview artifact:/ }))

    expect(await screen.findByText('second')).toBeTruthy()
    resolveFirst({
      bytes_returned: 5,
      content: 'first',
      media_type: 'text/markdown; charset=utf-8',
      publication_id: 'first',
      size_bytes: 5,
      truncated: false
    })
    await waitFor(() => expect(getWorkflowArtifactPreview).toHaveBeenCalledTimes(2))
    expect(screen.queryByText('first')).toBeNull()
    expect(screen.getByText('second')).toBeTruthy()
  })
})
