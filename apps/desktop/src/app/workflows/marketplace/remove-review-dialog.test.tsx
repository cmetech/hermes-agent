// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'
import type { WorkflowMarketplaceRemoveReview } from '@/types/hermes'

import { RemoveReviewDialog, type RemoveReviewDialogView } from './remove-review-dialog'

const DIGEST = 'a'.repeat(64)
const REVIEW = 'b'.repeat(64)
const COMMIT = 'c'.repeat(40)
const TOKEN = 'opaque-remove-token-that-must-never-render'

function review(): WorkflowMarketplaceRemoveReview {
  return {
    confirmation_token: TOKEN,
    current_commit: COMMIT,
    current_version: '1.0.0',
    distribution_digest: DIGEST,
    identity: { package_id: 'laptop-support', source_key: 'company' },
    operation: 'remove',
    result: 'review_required',
    review_digest: REVIEW,
    workflow_names: ['diagnostic', 'collector']
  }
}

function renderDialog(view: RemoveReviewDialogView, overrides: Partial<Parameters<typeof RemoveReviewDialog>[0]> = {}) {
  return render(
    <I18nProvider configClient={null} initialLocale="en">
      <RemoveReviewDialog
        onCancelOperation={vi.fn()}
        onClose={vi.fn()}
        onConfirm={vi.fn().mockResolvedValue(undefined)}
        onPrepareAgain={vi.fn()}
        open
        sourceName="company"
        view={view}
        {...overrides}
      />
    </I18nProvider>
  )
}

afterEach(cleanup)

describe('RemoveReviewDialog', () => {
  it('shows exact identity and scope while preserving source, other packages, loose workflows, and independent grants', () => {
    renderDialog({ kind: 'review', review: review() })
    const dialog = screen.getByRole('dialog', { name: 'Review removal' })

    for (const value of [
      'company/laptop-support',
      'company',
      '1.0.0',
      COMMIT,
      DIGEST,
      REVIEW,
      'diagnostic',
      'collector'
    ]) {
      expect(within(dialog).getAllByText(value).length).toBeGreaterThan(0)
    }

    expect(dialog.textContent).toContain(
      'Only this namespaced package, its provenance, and installation-origin trust grants will be removed.'
    )
    expect(dialog.textContent).toContain(
      'The source definition, other packages, loose workflows, and independent or manual grants remain.'
    )
    expect(dialog.textContent).not.toContain(TOKEN)
  })

  it('serializes rapid removal confirmation', () => {
    const onConfirm = vi.fn(() => new Promise<void>(() => undefined))
    renderDialog({ kind: 'review', review: review() }, { onConfirm })
    const confirm = screen.getByRole('button', { name: 'Remove package' })

    fireEvent.click(confirm)
    fireEvent.click(confirm)
    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect((screen.getByRole('button', { name: 'Close' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it.each(['failed', 'cancelled', 'stale'] as const)('keeps the package after a %s removal', kind => {
    renderDialog({ kind, currentVersion: '1.0.0' })
    expect(screen.getByText('Version 1.0.0 remains installed.')).toBeTruthy()
    expect(screen.queryByText(/removed successfully/i)).toBeNull()
  })
})
