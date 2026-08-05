import { afterEach, describe, expect, it, vi } from 'vitest'

const normalizeOrLocalPreviewTarget = vi.fn()
const openPreview = vi.fn()

vi.mock('@/lib/local-preview', () => ({
  normalizeOrLocalPreviewTarget: (...args: unknown[]) => normalizeOrLocalPreviewTarget(...args)
}))

vi.mock('@/store/preview', () => ({
  openPreview: (...args: unknown[]) => openPreview(...args)
}))

const { host } = await import('./index')

const fileTarget = { kind: 'file' as const, label: 'report.md', path: '/tmp/report.md', url: 'file:///tmp/report.md' }

afterEach(() => {
  vi.clearAllMocks()
})

describe('host.previewFile', () => {
  it('opens a preview tab for a resolvable file', async () => {
    normalizeOrLocalPreviewTarget.mockResolvedValue(fileTarget)

    await expect(host.previewFile('/tmp/report.md')).resolves.toBe(true)
    expect(normalizeOrLocalPreviewTarget).toHaveBeenCalledWith('/tmp/report.md')
    expect(openPreview).toHaveBeenCalledWith(fileTarget, 'tool-result')
  })

  // 'file-browser'/'manual' flip HTML to renderMode 'source'. A task artifact
  // must RENDER, so the tag has to stay outside that set.
  it('tags the preview as a tool result so HTML renders instead of showing source', async () => {
    normalizeOrLocalPreviewTarget.mockResolvedValue(fileTarget)

    await host.previewFile('/tmp/report.html')

    expect(openPreview.mock.calls[0][1]).toBe('tool-result')
  })

  it('reports failure for a missing or unreadable file instead of throwing', async () => {
    normalizeOrLocalPreviewTarget.mockResolvedValue(null)

    await expect(host.previewFile('/tmp/gone.md')).resolves.toBe(false)
    expect(openPreview).not.toHaveBeenCalled()
  })

  it('reports failure for an empty path without touching the resolver', async () => {
    await expect(host.previewFile('')).resolves.toBe(false)
    expect(normalizeOrLocalPreviewTarget).not.toHaveBeenCalled()
  })

  it('reports failure instead of propagating a resolver throw', async () => {
    normalizeOrLocalPreviewTarget.mockRejectedValue(new Error('no bridge'))

    await expect(host.previewFile('/tmp/report.md')).resolves.toBe(false)
  })
})
