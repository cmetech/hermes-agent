/**
 * The attachment row is the ONLY way a non-technical user reaches a completed
 * task's output file: `kanban_complete` auto-attaches it, and the drawer is
 * where they look for it. It used to render as inert text, so the filename was
 * visible and unreachable. These tests pin the row as a real control.
 */

import { host, type PluginContext } from '@hermes/plugin-sdk'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { bindOs, revealAttachment } from './api'
import { AttachmentsSection } from './drawer'
import plugin from './plugin'
import type { KanbanAttachment } from './types'

afterEach(() => {
  cleanup()
  bindOs(null)
  vi.clearAllMocks()
})

const attachment = (over: Partial<KanbanAttachment> = {}): KanbanAttachment => ({
  id: 1,
  filename: 'report.md',
  stored_path: '/home/u/.hermes/kanban/attachments/t-1/report.md',
  ...over
})

/** Enough of a PluginContext for `plugin.register` to run headlessly. */
const fakeContext = (os: { revealPath: (path: string) => Promise<boolean> }): PluginContext =>
  ({
    source: 'plugin:kanban',
    register: () => () => undefined,
    registerMany: () => () => undefined,
    onDispose: () => undefined,
    rest: () => Promise.resolve({} as never),
    socket: () => () => undefined,
    os: { ...os, notify: () => undefined, openExternal: async () => false, writeClipboard: async () => false },
    storage: { get: <T,>(_key: string, fallback: T) => fallback, set: () => undefined, remove: () => undefined },
    i18n: { register: () => () => undefined, t: (key: string) => key }
  }) as unknown as PluginContext

const renderSection = (attachments: KanbanAttachment[]) =>
  render(<AttachmentsSection attachments={attachments} onUpload={vi.fn()} pending={false} />)

describe('attachment reveal door', () => {
  it('is a no-op that reports failure when no OS door is bound', async () => {
    await expect(revealAttachment('/tmp/report.md')).resolves.toBe(false)
  })

  it('hands the stored path to the OS door', async () => {
    const revealPath = vi.fn().mockResolvedValue(true)

    bindOs({ revealPath })

    await expect(revealAttachment('/tmp/report.md')).resolves.toBe(true)
    expect(revealPath).toHaveBeenCalledWith('/tmp/report.md')
  })
})

describe('attachments section', () => {
  it('still shows an attachment with no stored path, but not as a dead control', () => {
    renderSection([attachment({ stored_path: '' })])

    expect(screen.getByText('report.md')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /report\.md/ })).toBeNull()
  })

  it('treats a missing stored path the same as an empty one', () => {
    renderSection([attachment({ stored_path: undefined })])

    expect(screen.getByText('report.md')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /report\.md/ })).toBeNull()
  })

  // The OS door resolves false rather than throwing (no Electron shell, older
  // build). Swallowing that would put us right back at "clicking does nothing".
  it('says so when the file manager could not be opened', async () => {
    const notify = vi.spyOn(host, 'notify').mockReturnValue('toast-id')

    bindOs({ revealPath: vi.fn().mockResolvedValue(false) })
    renderSection([attachment()])

    fireEvent.click(screen.getByRole('button', { name: 'revealAttachment' }))
    await waitFor(() => expect(notify).toHaveBeenCalled())

    expect(notify.mock.calls[0][0]).toMatchObject({ kind: 'warning' })
  })
})

// Without this the whole feature is inert in the real app while every test
// above still passes: the row renders, the click fires, and `revealAttachment`
// has no door to call.
describe('plugin registration', () => {
  it('binds the OS door so the drawer can reveal attachments', async () => {
    const revealPath = vi.fn().mockResolvedValue(true)

    plugin.register(fakeContext({ revealPath }))

    await expect(revealAttachment('/tmp/report.md')).resolves.toBe(true)
    expect(revealPath).toHaveBeenCalledWith('/tmp/report.md')
  })
})

describe('attachment row controls', () => {
  it('opens the file in the preview rail when the filename is activated', async () => {
    const previewFile = vi.spyOn(host, 'previewFile').mockResolvedValue(true)
    const revealPath = vi.fn().mockResolvedValue(true)

    bindOs({ revealPath })
    renderSection([attachment()])

    fireEvent.click(screen.getByRole('button', { name: /report\.md/ }))
    await waitFor(() => expect(previewFile).toHaveBeenCalled())

    expect(previewFile).toHaveBeenCalledWith('/home/u/.hermes/kanban/attachments/t-1/report.md')
    // Viewing must not also throw the user into the OS file manager.
    expect(revealPath).not.toHaveBeenCalled()
  })

  it('keeps a separate control that reveals the file in the file manager', () => {
    const revealPath = vi.fn().mockResolvedValue(true)
    const previewFile = vi.spyOn(host, 'previewFile').mockResolvedValue(true)

    bindOs({ revealPath })
    renderSection([attachment()])

    // Plugin tests can't register locale bundles (@/i18n is lint-fenced for
    // plugins), so k.revealAttachment(...) yields its raw key here.
    fireEvent.click(screen.getByRole('button', { name: 'revealAttachment' }))

    expect(revealPath).toHaveBeenCalledWith('/home/u/.hermes/kanban/attachments/t-1/report.md')
    expect(previewFile).not.toHaveBeenCalled()
  })

  it('says so when the file could not be opened', async () => {
    vi.spyOn(host, 'previewFile').mockResolvedValue(false)
    const notify = vi.spyOn(host, 'notify').mockReturnValue('toast-id')

    bindOs({ revealPath: vi.fn().mockResolvedValue(true) })
    renderSection([attachment()])

    fireEvent.click(screen.getByRole('button', { name: /report\.md/ }))
    await waitFor(() => expect(notify).toHaveBeenCalled())

    expect(notify.mock.calls[0][0]).toMatchObject({ kind: 'warning' })
  })

  it('renders neither control without a stored path', () => {
    renderSection([attachment({ stored_path: '' })])

    expect(screen.getByText('report.md')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /report\.md/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'revealAttachment' })).toBeNull()
  })
})
