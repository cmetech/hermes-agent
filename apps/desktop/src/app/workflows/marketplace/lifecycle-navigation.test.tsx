// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { type ReactNode, useState } from 'react'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import type * as Hermes from '@/hermes'
import { I18nProvider } from '@/i18n'

import { InstallReviewDialog } from './install-review-dialog'
import { LIFECYCLE_DIALOG_ATTRIBUTE, marketplaceEscapeIsOwned } from './lifecycle-dialog-behavior'
import { createLifecycleHarness, lifecycleScope, renderLifecycleHarness } from './lifecycle-test-harness'
import { RemoveReviewDialog } from './remove-review-dialog'
import { TrustReviewDialog } from './trust-review-dialog'

import { WorkflowMarketplaceView } from './index'

const api = vi.hoisted(() => ({
  add: vi.fn(),
  capabilities: vi.fn(),
  inspect: vi.fn(),
  installed: vi.fn(),
  listOperations: vi.fn(),
  refresh: vi.fn(),
  search: vi.fn(),
  sources: vi.fn()
}))

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof Hermes>()),
  addWorkflowMarketplaceSource: (...args: unknown[]) => api.add(...args),
  getWorkflowMarketplaceCapabilities: (...args: unknown[]) => api.capabilities(...args),
  getWorkflowMarketplaceOperation: (...args: unknown[]) => api.inspect(...args),
  inspectWorkflowPackage: (...args: unknown[]) => api.inspect(...args),
  isWorkflowMarketplaceUnsupportedError: () => false,
  listInstalledWorkflowPackages: (...args: unknown[]) => api.installed(...args),
  listWorkflowMarketplaceOperations: (...args: unknown[]) => api.listOperations(...args),
  listWorkflowMarketplaceSources: (...args: unknown[]) => api.sources(...args),
  refreshWorkflowMarketplaceSource: (...args: unknown[]) => api.refresh(...args),
  searchWorkflowPackages: (...args: unknown[]) => api.search(...args)
}))

const originalHasPointerCapture = Element.prototype.hasPointerCapture
const originalReleasePointerCapture = Element.prototype.releasePointerCapture
const originalScrollIntoView = Element.prototype.scrollIntoView

beforeAll(() => {
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
  Element.prototype.scrollIntoView = vi.fn()
})

afterAll(() => {
  Element.prototype.hasPointerCapture = originalHasPointerCapture
  Element.prototype.releasePointerCapture = originalReleasePointerCapture
  Element.prototype.scrollIntoView = originalScrollIntoView
})

afterEach(cleanup)

function NarrowParent({ children, onBack }: { children: ReactNode; onBack: () => void }) {
  return (
    <section
      data-testid="narrow-marketplace"
      onKeyDown={event => {
        if (event.key === 'Escape') {
          onBack()
        }
      }}
    >
      <p>Package detail selected</p>
      {children}
    </section>
  )
}

const common = {
  onCancelOperation: vi.fn(),
  onConfirm: vi.fn(async () => undefined),
  onGrant: vi.fn(async () => undefined),
  onPrepareAgain: vi.fn(),
  onSelectionChange: vi.fn()
}

function dialog(kind: 'install' | 'update' | 'remove' | 'trust', onClose: () => void) {
  if (kind === 'remove') {
    return (
      <RemoveReviewDialog
        {...common}
        onClose={onClose}
        open
        sourceName="company"
        view={{ cancellable: true, kind: 'progress', phase: 'fetching', progress: 25 }}
      />
    )
  }

  if (kind === 'trust') {
    return (
      <TrustReviewDialog
        {...common}
        onClose={onClose}
        open
        selection={{ type: 'all' }}
        view={{ cancellable: true, kind: 'progress', phase: 'fetching', progress: 25 }}
      />
    )
  }

  return (
    <InstallReviewDialog
      {...common}
      onClose={onClose}
      onReviewTrust={vi.fn()}
      open
      view={{ cancellable: true, kind: 'progress', mode: kind, phase: 'fetching', progress: 25 }}
    />
  )
}

describe('Marketplace lifecycle portal navigation ownership', () => {
  it('recognizes both prevented and marker-owned Escape events without a document-wide modal guess', () => {
    const modal = globalThis.document.createElement('div')
    modal.setAttribute(LIFECYCLE_DIALOG_ATTRIBUTE, '')
    modal.dataset.state = 'open'
    const target = globalThis.document.createElement('button')
    modal.append(target)
    globalThis.document.body.append(modal)

    expect(marketplaceEscapeIsOwned({ defaultPrevented: true, target: globalThis.document.body })).toBe(true)
    expect(marketplaceEscapeIsOwned({ defaultPrevented: false, target })).toBe(true)
    expect(marketplaceEscapeIsOwned({ defaultPrevented: false, target: globalThis.document.body })).toBe(false)

    modal.remove()
  })

  it.each(['install', 'update', 'remove', 'trust'] as const)(
    'lets one descendant Escape close only the %s dialog',
    async kind => {
      const onBack = vi.fn()

      function Example() {
        const [open, setOpen] = useState(true)

        return (
          <I18nProvider configClient={null} initialLocale="en">
            <NarrowParent onBack={onBack}>{open ? dialog(kind, () => setOpen(false)) : null}</NarrowParent>
          </I18nProvider>
        )
      }

      render(<Example />)
      const modal = screen.getByRole('dialog')

      const close = within(modal)
        .getAllByRole('button', { name: 'Close' })
        .find(button => button.dataset.slot === 'button')!

      await waitFor(() => expect(globalThis.document.activeElement).toBe(close))
      fireEvent.keyDown(close, { bubbles: true, cancelable: true, key: 'Escape' })

      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
      expect(onBack).not.toHaveBeenCalled()
      expect(screen.getByText('Package detail selected')).toBeTruthy()
    }
  )

  it('keeps deferred admission immune to portal Escape and parent navigation, then closes once released', async () => {
    let release!: () => void

    const admission = new Promise<void>(resolve => {
      release = resolve
    })

    const onBack = vi.fn()
    const onClose = vi.fn()

    render(
      <I18nProvider configClient={null} initialLocale="en">
        <NarrowParent onBack={onBack}>
          <InstallReviewDialog
            {...common}
            onClose={onClose}
            onConfirm={() => admission}
            onReviewTrust={vi.fn()}
            open
            view={{
              kind: 'review',
              mode: 'install',
              review: {
                assessment: {
                  advisories: [],
                  blockers: [],
                  external_requirements: { providers: [], runtimes: [], secrets: [], services: [], tools: [] },
                  package_digest: 'a'.repeat(64),
                  package_resources: [],
                  review_digest: 'c'.repeat(64),
                  workflow_names: []
                },
                candidate_digest: 'a'.repeat(64),
                candidate_version: '1.0.0',
                configured_ref: 'main',
                file_changes: [],
                identity: { package_id: 'laptop-support', source_key: 'company' },
                operation: 'install',
                package_path: 'packages/laptop-support',
                repository_url: 'https://example.test/company/workflows.git',
                resolved_commit: 'b'.repeat(40),
                review_digest: 'c'.repeat(64),
                result: 'review_required',
                source_name: 'company',
                workflow_reviews: []
              }
            }}
          />
        </NarrowParent>
      </I18nProvider>
    )

    fireEvent.click(screen.getByRole('button', { name: 'Confirm install' }))
    fireEvent.keyDown(screen.getByRole('dialog'), { bubbles: true, cancelable: true, key: 'Escape' })
    const overlay = globalThis.document.querySelector('[data-slot="dialog-overlay"]')!
    fireEvent.pointerDown(overlay)
    fireEvent.click(overlay)
    expect(onClose).not.toHaveBeenCalled()
    expect(onBack).not.toHaveBeenCalled()

    await act(async () => release())
    fireEvent.keyDown(screen.getByRole('dialog'), { bubbles: true, cancelable: true, key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
    expect(onBack).not.toHaveBeenCalled()
  })

  it('starts rendered inspect and refresh intents only through exact V2 supervision', async () => {
    api.capabilities.mockResolvedValue({
      capabilities: ['sources', 'search', 'installed', 'operations'],
      profile: 'support',
      schema_version: 1
    })
    api.sources.mockResolvedValue({
      profile: 'support',
      sources: [
        {
          attempted_at: null,
          diagnostic_code: null,
          enabled: true,
          message: null,
          name: 'company',
          ref: 'main',
          refresh_state: 'fresh',
          repository_url: 'https://example.test/company/workflows.git',
          resolved_commit: 'b'.repeat(40),
          verified_at: '2026-09-04T00:00:00Z',
          verified_package_count: 1
        }
      ]
    })
    api.installed.mockResolvedValue({ packages: [], profile: 'support' })
    api.search.mockResolvedValue({
      items: [
        {
          configured_ref: 'main',
          contract_version: 1,
          description: 'Diagnostics and support workflows.',
          display_name: 'Laptop Support',
          id: 'laptop-support',
          identifier: 'company/laptop-support',
          license: 'MIT',
          package_digest: 'a'.repeat(64),
          package_path: 'packages/laptop-support',
          publisher: 'Example Company',
          repository_url: 'https://example.test/company/workflows.git',
          resolved_commit: 'b'.repeat(40),
          source_name: 'company',
          state: 'fresh',
          tags: ['support'],
          verified_at: '2026-09-04T00:00:00Z',
          version: '1.0.0'
        }
      ],
      limit: 50,
      next_offset: null,
      offset: 0,
      profile: 'support',
      query: '',
      source: null
    })
    api.listOperations.mockResolvedValue({ limit: 100, offset: 0, operations: [], profile: 'support' })
    api.inspect.mockRejectedValue(new Error('V1 inspection must not run'))
    api.refresh.mockRejectedValue(new Error('V1 refresh must not run'))

    const harness = createLifecycleHarness()
    await harness.bind()
    harness.route('inspect', 'service inspect')
    harness.route('refresh', 'service refresh')
    renderLifecycleHarness(<WorkflowMarketplaceView scope={lifecycleScope} />, harness)

    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))
    await screen.findByText('Laptop Support')
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))

    await waitFor(() => {
      const starts = harness.calls.filter(call => call.type === 'start').map(call => call.input)
      expect(starts).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            kind: 'inspect',
            subject: { identity: { package_id: 'laptop-support', source_key: 'company' }, type: 'package' }
          }),
          expect.objectContaining({ kind: 'refresh', subject: { source_name: 'company', type: 'source' } })
        ])
      )
    })
    expect(api.inspect).not.toHaveBeenCalled()
    expect(api.refresh).not.toHaveBeenCalled()
  })

  it('keeps V1 catalog and source CRUD usable while disabling V2 inspect and refresh controls', async () => {
    api.capabilities.mockResolvedValue({
      capabilities: ['sources', 'search', 'installed', 'operations'],
      profile: 'support',
      schema_version: 1
    })
    api.sources.mockResolvedValue({
      profile: 'support',
      sources: [
        {
          attempted_at: null,
          diagnostic_code: null,
          enabled: true,
          message: null,
          name: 'company',
          ref: 'main',
          refresh_state: 'fresh',
          repository_url: 'https://example.test/company/workflows.git',
          resolved_commit: 'b'.repeat(40),
          verified_at: '2026-09-04T00:00:00Z',
          verified_package_count: 1
        }
      ]
    })
    api.installed.mockResolvedValue({ packages: [], profile: 'support' })
    api.search.mockResolvedValue({
      items: [
        {
          configured_ref: 'main',
          contract_version: 1,
          description: 'Diagnostics and support workflows.',
          display_name: 'Laptop Support',
          id: 'laptop-support',
          identifier: 'company/laptop-support',
          license: 'MIT',
          package_digest: 'a'.repeat(64),
          package_path: 'packages/laptop-support',
          publisher: 'Example Company',
          repository_url: 'https://example.test/company/workflows.git',
          resolved_commit: 'b'.repeat(40),
          source_name: 'company',
          state: 'fresh',
          tags: ['support'],
          verified_at: '2026-09-04T00:00:00Z',
          version: '1.0.0'
        }
      ],
      limit: 50,
      next_offset: null,
      offset: 0,
      profile: 'support',
      query: '',
      source: null
    })
    api.add.mockResolvedValue({ profile: 'support' })
    api.inspect.mockClear()
    api.refresh.mockClear()

    const harness = createLifecycleHarness()
    harness.failCapabilities('marketplace_lifecycle_unsupported')
    await harness.supervisor.reconcileScope(lifecycleScope)
    renderLifecycleHarness(<WorkflowMarketplaceView scope={lifecycleScope} />, harness)

    expect(await screen.findByRole('option', { name: /Laptop Support/ })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Refresh' })).toBeNull()
    expect(screen.getByText('Upgrade Hermes to manage and refresh workflow sources.')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Manage Sources' }))
    const sources = await screen.findByRole('dialog', { name: 'Workflow sources' })
    expect(within(sources).getByRole('button', { name: 'Refresh all' }).hasAttribute('disabled')).toBe(true)
    fireEvent.click(within(sources).getByRole('button', { name: 'Add source' }))
    fireEvent.change(within(sources).getByLabelText('Source name'), { target: { value: 'team' } })
    fireEvent.change(within(sources).getByLabelText('Repository URL'), {
      target: { value: 'https://example.test/team/workflows.git' }
    })
    fireEvent.click(within(sources).getByRole('button', { name: 'Save source' }))
    await waitFor(() =>
      expect(api.add).toHaveBeenCalledWith(
        {
          enabled: true,
          name: 'team',
          ref: null,
          repositoryUrl: 'https://example.test/team/workflows.git'
        },
        lifecycleScope
      )
    )
    expect(api.inspect).not.toHaveBeenCalled()
    expect(api.refresh).not.toHaveBeenCalled()
  })
})
