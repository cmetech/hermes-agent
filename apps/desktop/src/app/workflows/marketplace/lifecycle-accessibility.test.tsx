// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { InstallReviewDialog, type InstallReviewDialogView } from './install-review-dialog'
import { RemoveReviewDialog } from './remove-review-dialog'
import { TrustReviewDialog } from './trust-review-dialog'

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

const common = {
  onCancelOperation: vi.fn(),
  onClose: vi.fn(),
  onConfirm: vi.fn(async () => undefined),
  onPrepareAgain: vi.fn(),
  onReviewTrust: vi.fn()
}

function renderInstall(view: InstallReviewDialogView) {
  return render(
    <I18nProvider configClient={null} initialLocale="en">
      <InstallReviewDialog {...common} open view={view} />
    </I18nProvider>
  )
}

describe('lifecycle dialog focus and announcements', () => {
  it('moves focus to a safe status target when the focused action disappears', async () => {
    const rendered = renderInstall({
      canPrepareAgain: true,
      canRetry: false,
      canRetryCheck: false,
      kind: 'terminal',
      mode: 'install',
      presentation: {
        canPrepareAgain: true,
        kind: 'unconfirmed',
        message: 'State could not be confirmed.',
        retryAction: 'check'
      }
    })

    const prepareAgain = screen.getByRole('button', { name: 'Prepare again' })
    prepareAgain.focus()

    rendered.rerender(
      <I18nProvider configClient={null} initialLocale="en">
        <InstallReviewDialog
          {...common}
          open
          view={{ cancellable: true, kind: 'progress', mode: 'install', phase: 'fetching', progress: 25 }}
        />
      </I18nProvider>
    )

    const dialog = screen.getByRole('dialog')
    await waitFor(() => expect(dialog.contains(globalThis.document.activeElement)).toBe(true))
    expect(globalThis.document.activeElement).toBe(
      within(dialog)
        .getAllByRole('button', { name: 'Close' })
        .find(button => button.dataset.slot === 'button')
    )
  })

  it('does not churn a surviving safe control across committed, cancelled, known-failure and unconfirmed views', async () => {
    const rendered = renderInstall({
      cancellable: true,
      kind: 'progress',
      mode: 'install',
      phase: 'committing',
      progress: 80
    })

    const close = within(screen.getByRole('dialog'))
      .getAllByRole('button', { name: 'Close' })
      .find(button => button.dataset.slot === 'button')!

    await waitFor(() => expect(globalThis.document.activeElement).toBe(close))

    const views: InstallReviewDialogView[] = [
      { kind: 'succeeded', mode: 'install', trustRequired: false, version: '1.0.0' },
      {
        canPrepareAgain: true,
        canRetry: false,
        canRetryCheck: false,
        kind: 'terminal',
        mode: 'install',
        presentation: { canPrepareAgain: true, kind: 'cancelled', message: 'Cancelled.', retryAction: 'check' }
      },
      {
        canPrepareAgain: true,
        canRetry: false,
        canRetryCheck: false,
        kind: 'terminal',
        mode: 'install',
        presentation: { canPrepareAgain: true, kind: 'check_error', message: 'Install failed.', retryAction: 'check' }
      },
      {
        canPrepareAgain: true,
        canRetry: true,
        canRetryCheck: false,
        kind: 'terminal',
        mode: 'install',
        presentation: { canPrepareAgain: true, kind: 'unconfirmed', message: 'State unknown.', retryAction: 'check' }
      }
    ]

    for (const view of views) {
      rendered.rerender(
        <I18nProvider configClient={null} initialLocale="en">
          <InstallReviewDialog {...common} open view={view} />
        </I18nProvider>
      )
      expect(globalThis.document.activeElement).toBe(close)
    }
  })

  it('uses one bounded announcement for each progress, success, failure and trust-success state', () => {
    const progress = renderInstall({
      cancellable: true,
      kind: 'progress',
      mode: 'install',
      phase: 'fetching',
      progress: 25
    })

    expect(screen.getAllByRole('status')).toHaveLength(1)
    expect(screen.queryByRole('alert')).toBeNull()
    progress.unmount()

    const success = renderInstall({ kind: 'succeeded', mode: 'install', trustRequired: false, version: '1.0.0' })
    expect(screen.getAllByRole('status')).toHaveLength(1)
    expect(screen.queryByRole('alert')).toBeNull()
    success.unmount()

    const failure = renderInstall({
      canPrepareAgain: true,
      canRetry: true,
      canRetryCheck: false,
      kind: 'terminal',
      mode: 'install',
      presentation: { canPrepareAgain: true, kind: 'unconfirmed', message: 'State unknown.', retryAction: 'check' }
    })

    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.queryByRole('status')).toBeNull()
    failure.unmount()

    render(
      <I18nProvider configClient={null} initialLocale="en">
        <TrustReviewDialog
          onCancelOperation={vi.fn()}
          onClose={vi.fn()}
          onGrant={vi.fn(async () => undefined)}
          onPrepareAgain={vi.fn()}
          onSelectionChange={vi.fn()}
          open
          selection={{ type: 'all' }}
          view={{ kind: 'succeeded', selection: { type: 'all' }, workflows: [] }}
        />
      </I18nProvider>
    )
    expect(screen.getAllByRole('status')).toHaveLength(1)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it.each(['install', 'remove', 'trust'] as const)('starts %s on the safe Close control', async kind => {
    if (kind === 'install') {
      renderInstall({ cancellable: true, kind: 'progress', mode: 'update', phase: 'fetching', progress: 25 })
    } else if (kind === 'remove') {
      render(
        <I18nProvider configClient={null} initialLocale="en">
          <RemoveReviewDialog
            {...common}
            open
            sourceName="company"
            view={{ cancellable: true, kind: 'progress', phase: 'fetching', progress: 25 }}
          />
        </I18nProvider>
      )
    } else {
      render(
        <I18nProvider configClient={null} initialLocale="en">
          <TrustReviewDialog
            onCancelOperation={vi.fn()}
            onClose={vi.fn()}
            onGrant={vi.fn(async () => undefined)}
            onPrepareAgain={vi.fn()}
            onSelectionChange={vi.fn()}
            open
            selection={{ type: 'all' }}
            view={{ cancellable: true, kind: 'progress', phase: 'fetching', progress: 25 }}
          />
        </I18nProvider>
      )
    }

    const modal = screen.getByRole('dialog')

    const close = within(modal)
      .getAllByRole('button', { name: 'Close' })
      .find(button => button.dataset.slot === 'button')!

    await waitFor(() => expect(globalThis.document.activeElement).toBe(close))

    fireEvent.keyDown(close, { key: 'Tab', shiftKey: true })
    expect(modal.contains(globalThis.document.activeElement)).toBe(true)
  })
})
