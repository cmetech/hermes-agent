// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'
import type { TrustReviewProjection } from '@/types/workflow-marketplace-lifecycle'

import { TrustReviewDialog, type TrustReviewDialogView } from './trust-review-dialog'

const DIGEST = 'a'.repeat(64)
const RISK = 'b'.repeat(64)
const REVIEW = 'c'.repeat(64)
const COMMIT = 'd'.repeat(40)
const TOKEN = 'opaque-trust-token-that-must-never-render'

function workflow(name: string) {
  return {
    approval_nodes: ['approve'],
    command_nodes: ['command-node'],
    command_resources: ['commands/run.md'],
    companion_path: `workflows/${name}.hermes.yaml`,
    compatibility: [{ code: 'runtime_missing', message: 'uv missing', severity: 'advisory' as const }],
    definition_path: `workflows/${name}.yaml`,
    external_requirements: {
      providers: ['openrouter'],
      runtimes: ['uv'],
      secrets: ['TOKEN'],
      services: ['tickets'],
      tools: ['terminal']
    },
    local_mcp_servers: ['local-mcp'],
    mcp_resource_files: ['mcp/resources.yaml'],
    mcp_resources: ['mcp/server.yaml'],
    outward_action_nodes: ['send'],
    package_digest: DIGEST,
    package_resource_set: 'package' as const,
    providers: ['openrouter'],
    remote_mcp_servers: ['remote-mcp'],
    requested_skills: ['support'],
    requested_tools: ['terminal'],
    required_secrets: ['TOKEN'],
    risk_digest: RISK,
    script_resources: ['scripts/run.py'],
    shell_or_script_nodes: ['shell'],
    trust_state: 'untrusted' as const,
    workflow_name: name
  }
}

function trustReview(workflows = [workflow('diagnostic'), workflow('collector')]): TrustReviewProjection {
  return {
    confirmation_available: true,
    distribution_digest: DIGEST,
    expires_at: '2026-09-05T12:05:00Z',
    identity: { package_id: 'laptop-support', source_key: 'company' },
    package_resources: ['scripts/run.py', 'commands/run.md'],
    package_workflows: workflows.map(item => ({
      definition_path: item.definition_path,
      workflow_name: item.workflow_name
    })),
    resolved_commit: COMMIT,
    review_digest: REVIEW,
    source_name: 'company',
    version: '2.0.0',
    workflows
  }
}

function renderDialog(
  view: TrustReviewDialogView,
  overrides: Partial<Parameters<typeof TrustReviewDialog>[0]> = {},
  locale = 'en'
) {
  return render(
    <I18nProvider configClient={null} initialLocale={locale}>
      <TrustReviewDialog
        onCancelOperation={vi.fn()}
        onClose={vi.fn()}
        onGrant={vi.fn().mockResolvedValue(undefined)}
        onPrepareAgain={vi.fn()}
        onSelectionChange={vi.fn()}
        open
        selection={{ type: 'all' }}
        view={view}
        {...overrides}
      />
    </I18nProvider>
  )
}

it.each(['ar', 'ja', 'zh', 'zh-hant'])(
  'renders localized trust uncertainty and preserves selected identity in %s',
  locale => {
    const nativeScript = locale === 'ar' ? /[\u0600-\u06ff]/ : /[\u3040-\u30ff\u3400-\u9fff]/
    const unknown = renderDialog({ kind: 'unconfirmed', recoverable: true }, {}, locale)
    expect(screen.getByRole('alert').textContent).toMatch(nativeScript)
    unknown.unmount()
    renderDialog(
      {
        kind: 'succeeded',
        selection: { type: 'one', workflow_name: 'diagnostic' },
        workflows: [{ workflow_name: 'diagnostic', definition_path: 'workflows/diagnostic.yaml', state: 'trusted' }]
      },
      {},
      locale
    )
    expect(screen.getByRole('status').textContent).toMatch(nativeScript)
    expect(screen.getByRole('status').textContent).toContain('diagnostic')
    expect(screen.getByRole('region').getAttribute('aria-label')).toMatch(nativeScript)
  }
)

function deferred() {
  let resolve!: () => void

  const promise = new Promise<void>(onResolve => {
    resolve = onResolve
  })

  return { promise, resolve }
}

afterEach(cleanup)

describe('TrustReviewDialog', () => {
  it('shows exact installed-byte identities, resources, workflow digests, risks, and no token', () => {
    renderDialog({ kind: 'review', review: trustReview() })
    const dialog = screen.getByRole('dialog', { name: 'Review trust' })

    for (const value of [
      'company/laptop-support',
      'company',
      '2.0.0',
      COMMIT,
      DIGEST,
      REVIEW,
      RISK,
      'scripts/run.py',
      'commands/run.md',
      'workflows/diagnostic.yaml',
      'workflows/diagnostic.hermes.yaml',
      'shell',
      'command-node',
      'local-mcp',
      'remote-mcp',
      'support',
      'send',
      'approve',
      'TOKEN',
      'tickets',
      'uv missing'
    ]) {
      expect(within(dialog).getAllByText(value).length).toBeGreaterThan(0)
    }

    expect(dialog.textContent).not.toContain(TOKEN)

    for (const article of within(dialog).getAllByRole('article')) {
      expect(within(article).getByText('untrusted')).toBeTruthy()
    }
  })

  it('offers only all workflows or exactly one workflow and requests a fresh review when selection changes', () => {
    const onSelectionChange = vi.fn()
    renderDialog({ kind: 'review', review: trustReview() }, { onSelectionChange })

    fireEvent.click(screen.getByRole('radio', { name: 'One workflow' }))
    expect(onSelectionChange).toHaveBeenCalledWith({ type: 'one', workflow_name: 'diagnostic' })
    expect(screen.queryByRole('checkbox')).toBeNull()
  })

  it('serializes rapid grants and reports stale review as granting nothing', async () => {
    const admission = deferred()
    const onGrant = vi.fn(() => admission.promise)
    const rendered = renderDialog({ kind: 'review', review: trustReview() }, { onGrant })
    const grant = screen.getByRole('button', { name: 'Grant trust' })

    fireEvent.click(grant)
    fireEvent.click(grant)
    expect(onGrant).toHaveBeenCalledTimes(1)
    expect((screen.getByRole('button', { name: 'Close' }) as HTMLButtonElement).disabled).toBe(true)
    await act(async () => admission.resolve())

    rendered.rerender(
      <I18nProvider configClient={null} initialLocale="en">
        <TrustReviewDialog
          onCancelOperation={vi.fn()}
          onClose={vi.fn()}
          onGrant={onGrant}
          onPrepareAgain={vi.fn()}
          onSelectionChange={vi.fn()}
          open
          selection={{ type: 'all' }}
          view={{ kind: 'stale' }}
        />
      </I18nProvider>
    )
    expect(screen.getByText('Trust was not granted. Prepare a fresh trust review.')).toBeTruthy()
  })
})
