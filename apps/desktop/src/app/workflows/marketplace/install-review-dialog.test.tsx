// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'
import type { WorkflowMarketplaceInstallReview, WorkflowMarketplaceUpdateReview } from '@/types/hermes'

import { InstallReviewDialog, type InstallReviewDialogView } from './install-review-dialog'

const DIGEST = 'a'.repeat(64)
const OLD_DIGEST = 'b'.repeat(64)
const REVIEW_DIGEST = 'c'.repeat(64)
const COMMIT = 'd'.repeat(40)
const OLD_COMMIT = 'e'.repeat(40)
const TOKEN = 'opaque-confirmation-token-that-must-never-render'

function workflowReview() {
  return {
    approval_nodes: ['approve-ticket'],
    command_nodes: ['interpret-report'],
    command_resources: ['commands/interpret.md'],
    companion_path: 'workflows/diagnostic.hermes.yaml',
    compatibility: [{ code: 'runtime_missing', message: 'uv is not installed', severity: 'advisory' as const }],
    definition_path: 'workflows/diagnostic.yaml',
    external_requirements: {
      providers: ['openrouter'],
      runtimes: ['uv'],
      secrets: ['SUPPORT_TOKEN'],
      services: ['ticketing'],
      tools: ['terminal']
    },
    local_mcp_servers: ['local-files'],
    mcp_resource_files: ['mcp/resources.yaml'],
    mcp_resources: ['support-mcp'],
    outward_action_nodes: ['create-ticket'],
    package_digest: DIGEST,
    package_resource_set: 'package' as const,
    providers: ['openrouter'],
    remote_mcp_servers: ['support-remote'],
    requested_skills: ['incident-response'],
    requested_tools: ['terminal'],
    required_secrets: ['SUPPORT_TOKEN'],
    risk_digest: OLD_DIGEST,
    script_resources: ['scripts/collect.py'],
    shell_or_script_nodes: ['collect'],
    trust_state: 'untrusted' as const,
    workflow_name: 'diagnostic'
  }
}

function installReview(blocked = false): WorkflowMarketplaceInstallReview {
  return {
    assessment: {
      advisories: [{ code: 'provider_missing', message: 'Provider setup is required.', severity: 'advisory' }],
      blockers: blocked
        ? [{ code: 'package_invalid', message: 'Package structure is invalid.', severity: 'blocker' }]
        : [],
      external_requirements: workflowReview().external_requirements,
      package_digest: DIGEST,
      package_resources: ['commands/interpret.md', 'scripts/collect.py'],
      review_digest: REVIEW_DIGEST,
      workflow_names: ['diagnostic']
    },
    candidate_digest: DIGEST,
    candidate_version: '2.0.0',
    confirmation_token: TOKEN,
    configured_ref: 'release',
    file_changes: [
      {
        candidate_digest: DIGEST,
        kind: 'added',
        old_digest: null,
        old_path: null,
        path: 'workflows/diagnostic.yaml'
      }
    ],
    identity: { package_id: 'laptop-support', source_key: 'company' },
    operation: 'install',
    package_path: 'packages/laptop-support',
    repository_url: 'https://example.test/team/workflows.git',
    resolved_commit: COMMIT,
    result: 'review_required',
    review_digest: REVIEW_DIGEST,
    source_name: 'company',
    workflow_reviews: [workflowReview()]
  }
}

function updateReview(): WorkflowMarketplaceUpdateReview {
  const empty = { added: [] as string[], removed: [] as string[] }

  return {
    ...installReview(),
    candidate_commit: COMMIT,
    compatibility_changes: {
      added: [{ code: 'runtime_missing', severity: 'advisory', workflow_name: 'diagnostic' }],
      removed: []
    },
    confirmation_token: TOKEN,
    file_changes: [
      {
        candidate_digest: DIGEST,
        kind: 'renamed',
        old_digest: OLD_DIGEST,
        old_path: 'scripts/old.py',
        path: 'scripts/collect.py'
      }
    ],
    old_commit: OLD_COMMIT,
    old_digest: OLD_DIGEST,
    old_version: '1.0.0',
    operation: 'update',
    requirement_changes: {
      providers: { added: ['openrouter'], removed: [] },
      runtimes: empty,
      secrets: { added: ['SUPPORT_TOKEN'], removed: [] },
      services: empty,
      tools: { added: ['terminal'], removed: [] }
    },
    result: 'update_available',
    risk_changes: {
      added: [{ package_digest: DIGEST, risk_digest: OLD_DIGEST, workflow_name: 'diagnostic' }],
      removed: []
    },
    source_name: 'company',
    workflow_changes: { added: ['diagnostic'], removed: ['legacy'] }
  }
}

function renderDialog(
  view: InstallReviewDialogView,
  overrides: Partial<Parameters<typeof InstallReviewDialog>[0]> = {},
  locale = 'en'
) {
  return render(
    <I18nProvider configClient={null} initialLocale={locale}>
      <InstallReviewDialog
        onCancelOperation={vi.fn()}
        onClose={vi.fn()}
        onConfirm={vi.fn().mockResolvedValue(undefined)}
        onPrepareAgain={vi.fn()}
        onReviewTrust={vi.fn()}
        open
        view={view}
        {...overrides}
      />
    </I18nProvider>
  )
}

function deferred() {
  let resolve!: () => void

  const promise = new Promise<void>(onResolve => {
    resolve = onResolve
  })

  return { promise, resolve }
}

afterEach(cleanup)

describe('InstallReviewDialog', () => {
  it.each([
    ['ar', ['مضاف', 'معدّل', 'محذوف', 'أُعيدت تسميته'], ['مانع', 'تنبيه']],
    ['ja', ['追加', '変更', '削除', '名前変更'], ['阻害要因', '注意事項']],
    ['zh', ['已添加', '已修改', '已移除', '已重命名'], ['阻止项', '提示']],
    ['zh-hant', ['已新增', '已修改', '已移除', '已重新命名'], ['阻擋項目', '提醒']]
  ])(
    'localizes update change kinds and severities in %s without translating publisher facts or diagnostic codes',
    (locale, kinds, severities) => {
      const review = updateReview()
      review.file_changes = (['added', 'modified', 'removed', 'renamed'] as const).map(kind => ({
        kind,
        path: `publisher/${kind}.yaml`,
        old_path: kind === 'renamed' ? 'publisher/old.yaml' : null,
        candidate_digest: kind === 'removed' ? null : DIGEST,
        old_digest: kind === 'added' ? null : OLD_DIGEST
      }))
      review.compatibility_changes = {
        added: [{ workflow_name: 'publisher-added', code: 'runtime_missing', severity: 'blocker' }],
        removed: [{ workflow_name: 'publisher-removed', code: 'provider_missing', severity: 'advisory' }]
      }
      renderDialog({ kind: 'review', mode: 'update', review }, {}, locale)

      for (const [index, change] of review.file_changes.entries()) {
        const row = screen.getByText(change.path).closest('li')!
        expect(row.textContent).toContain(kinds[index])
        expect(row.querySelector('span')?.textContent).not.toMatch(/added|modified|removed|renamed/)
      }

      expect(screen.getByText('publisher/old.yaml')).toBeTruthy()

      for (const [index, identity] of [
        ...review.compatibility_changes.added,
        ...review.compatibility_changes.removed
      ].entries()) {
        const row = screen.getByText(text => text.includes(identity.workflow_name) && text.includes(identity.code))
        expect(row.textContent).toContain(severities[index])
        expect(row.textContent).not.toMatch(/blocker|advisory/)
      }

      for (const value of ['SUPPORT_TOKEN', 'openrouter', '2.0.0', DIGEST]) {
        expect(screen.getAllByText(value).length).toBeGreaterThan(0)
      }
    }
  )

  it('localizes the retry-check action without confusing it with preparation', () => {
    renderDialog(
      {
        kind: 'terminal',
        mode: 'update',
        canPrepareAgain: false,
        canRetry: false,
        canRetryCheck: true,
        presentation: { kind: 'check_error', message: '', canPrepareAgain: false, retryAction: 'check' }
      },
      { onRetryCheck: vi.fn() },
      'ja'
    )
    expect(screen.queryByRole('button', { name: 'Retry check' })).toBeNull()
    const buttons = screen.getAllByRole('button')
    expect(
      buttons.every(button =>
        /[\u3040-\u30ff\u3400-\u9fff]/.test(button.textContent || button.getAttribute('aria-label') || '')
      )
    ).toBe(true)
  })

  it('renders every exact install fact and risk surface without exposing the confirmation token', () => {
    renderDialog({ kind: 'review', mode: 'install', review: installReview() })

    const dialog = screen.getByRole('dialog', { name: 'Review installation' })

    for (const value of [
      'company',
      'https://example.test/team/workflows.git',
      'release',
      COMMIT,
      'packages/laptop-support',
      '2.0.0',
      DIGEST,
      REVIEW_DIGEST,
      'workflows/diagnostic.yaml',
      'commands/interpret.md',
      'scripts/collect.py',
      'collect',
      'interpret-report',
      'local-files',
      'support-remote',
      'terminal',
      'incident-response',
      'create-ticket',
      'approve-ticket',
      'SUPPORT_TOKEN',
      'ticketing',
      'openrouter',
      'uv is not installed',
      'Provider setup is required.'
    ]) {
      expect(within(dialog).getAllByText(value).length).toBeGreaterThan(0)
    }

    expect(dialog.textContent).not.toContain(TOKEN)
    expect((screen.getByRole('button', { name: 'Confirm install' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('blocks structural findings, keeps advisories nonblocking, and serializes rapid confirmation', async () => {
    const admission = deferred()
    const onConfirm = vi.fn(() => admission.promise)

    const rendered = renderDialog({ kind: 'review', mode: 'install', review: installReview(true) }, { onConfirm })

    expect((screen.getByRole('button', { name: 'Confirm install' }) as HTMLButtonElement).disabled).toBe(true)

    rendered.rerender(
      <I18nProvider configClient={null} initialLocale="en">
        <InstallReviewDialog
          onCancelOperation={vi.fn()}
          onClose={vi.fn()}
          onConfirm={onConfirm}
          onPrepareAgain={vi.fn()}
          onReviewTrust={vi.fn()}
          open
          view={{ kind: 'review', mode: 'install', review: installReview() }}
        />
      </I18nProvider>
    )

    const confirm = screen.getByRole('button', { name: 'Confirm install' })
    fireEvent.click(confirm)
    fireEvent.click(confirm)
    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect((screen.getByRole('button', { name: 'Close' }) as HTMLButtonElement).disabled).toBe(true)
    await act(async () => admission.resolve())
  })

  it('renders the full update comparison without treating a generic failure as rollback evidence', () => {
    const rendered = renderDialog({ kind: 'review', mode: 'update', review: updateReview() })
    const dialog = screen.getByRole('dialog', { name: 'Review update' })

    for (const value of [OLD_COMMIT, COMMIT, OLD_DIGEST, DIGEST, 'scripts/old.py', 'scripts/collect.py', 'legacy']) {
      expect(within(dialog).getAllByText(value).length).toBeGreaterThan(0)
    }

    rendered.rerender(
      <I18nProvider configClient={null} initialLocale="en">
        <InstallReviewDialog
          onCancelOperation={vi.fn()}
          onClose={vi.fn()}
          onConfirm={vi.fn().mockResolvedValue(undefined)}
          onPrepareAgain={vi.fn()}
          onReviewTrust={vi.fn()}
          open
          view={{ kind: 'failed', mode: 'update', previousVersion: '1.0.0', recoverable: true }}
        />
      </I18nProvider>
    )

    expect(screen.getByText(/State could not be confirmed/)).toBeTruthy()
    expect(screen.queryByText(/remains installed|Nothing new was installed/)).toBeNull()
    expect(screen.queryByText(/rolled back/i)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Prepare again' })).toBeNull()
  })

  it('keeps the component-level Review trust handoff disabled without integration authority', () => {
    const onReviewTrust = vi.fn()
    renderDialog({ kind: 'succeeded', mode: 'install', version: '2.0.0', trustRequired: true }, { onReviewTrust })

    expect(screen.getByText('Installed — trust required to run')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Review trust' }))
    expect(onReviewTrust).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Review trust' }).hasAttribute('disabled')).toBe(true)
  })

  it('provides responsive safe focus and Escape except during confirm admission', async () => {
    const onClose = vi.fn()
    const admission = deferred()
    renderDialog(
      { kind: 'review', mode: 'install', review: installReview() },
      { onClose, onConfirm: () => admission.promise }
    )

    const dialog = screen.getByRole('dialog', { name: 'Review installation' })

    const footerClose = within(dialog)
      .getAllByRole('button', { name: 'Close' })
      .find(button => button.dataset.slot === 'button')!

    await waitFor(() => expect(globalThis.document.activeElement).toBe(footerClose))
    expect(dialog.className).toContain('w-[min(92vw,54rem)]')

    fireEvent.click(screen.getByRole('button', { name: 'Confirm install' }))
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()

    await act(async () => admission.resolve())
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
  })
})
