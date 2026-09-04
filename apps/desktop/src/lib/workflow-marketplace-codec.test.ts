import { describe, expect, expectTypeOf, it } from 'vitest'

import type {
  WorkflowMarketplaceFileChange,
  WorkflowMarketplaceOperation,
  WorkflowMarketplaceOperationError,
  WorkflowMarketplaceOperationForKind,
  WorkflowMarketplaceOperationKind,
  WorkflowMarketplaceOperationResult
} from '@/types/hermes'

import diagnosticCorpus from '../../../../tests/fixtures/workflow-marketplace-source-diagnostics.json'

import {
  decodeMarketplaceErrorEnvelope,
  decodeMarketplaceInstallReview,
  decodeMarketplaceOperation,
  decodeMarketplacePackageDetail,
  decodeMarketplaceRemoveReview,
  decodeMarketplaceTrustReview,
  decodeMarketplaceUpdateReview,
  decodeWorkflowMarketplaceCapabilities,
  decodeWorkflowMarketplaceInstalledPage,
  decodeWorkflowMarketplaceOperationPage,
  decodeWorkflowMarketplaceSearchPage,
  decodeWorkflowMarketplaceSourceList,
  decodeWorkflowMarketplaceSourceResponse,
  isWorkflowMarketplaceInstallIdentifier,
  isWorkflowMarketplaceRepositoryUrl,
  isWorkflowMarketplaceSourceRequestUrl
} from './workflow-marketplace-codec'

const DIGEST = '1'.repeat(64)
const RISK_DIGEST = '2'.repeat(64)
const REVIEW_DIGEST = '3'.repeat(64)
const COMMIT = '4'.repeat(40)
const TOKEN = 'confirmation-token-value-1234567890'
const NOW = '2026-09-04T00:00:00Z'

const RESULT_KIND = {
  install_review: 'install_prepare',
  installed_package: 'install_confirm',
  package_detail: 'package_detail',
  remove_review: 'remove_prepare',
  removed_package: 'remove_confirm',
  source_refresh: 'refresh',
  trust_grant: 'trust_confirm',
  trust_review: 'trust_prepare',
  trust_revoke: 'trust_revoke',
  update_checks: 'update_check',
  update_review: 'update_prepare',
  updated_package: 'update_confirm'
} as const satisfies Record<WorkflowMarketplaceOperationResult['type'], WorkflowMarketplaceOperationKind>

function identity() {
  return { package_id: 'laptop-support', source_key: 'company' }
}

function requirements() {
  return { providers: [], runtimes: ['uv'], secrets: [], services: [], tools: [] }
}

function diagnostic() {
  return { code: 'runtime_missing', message: 'uv is not installed', severity: 'advisory' }
}

function workflowReview() {
  return {
    approval_nodes: [],
    command_nodes: ['interpret'],
    command_resources: ['commands/interpret.md'],
    companion_path: null,
    compatibility: [diagnostic()],
    definition_path: 'workflows/laptop-diagnostic.yaml',
    external_requirements: requirements(),
    local_mcp_servers: [],
    mcp_resource_files: ['mcp/resources.yaml'],
    mcp_resources: ['mcp/server.yaml'],
    outward_action_nodes: [],
    package_digest: DIGEST,
    package_resource_set: 'package',
    providers: [],
    remote_mcp_servers: [],
    requested_skills: [],
    requested_tools: ['terminal'],
    required_secrets: [],
    risk_digest: RISK_DIGEST,
    script_resources: ['scripts/collect.py'],
    shell_or_script_nodes: ['collect'],
    trust_state: 'untrusted',
    workflow_name: 'laptop-diagnostic'
  }
}

function assessment() {
  return {
    advisories: [diagnostic()],
    blockers: [],
    external_requirements: requirements(),
    package_digest: DIGEST,
    package_resources: [
      'commands/interpret.md',
      'mcp/resources.yaml',
      'mcp/server.yaml',
      'scripts/collect.py',
      'workflows/laptop-diagnostic.yaml'
    ],
    review_digest: REVIEW_DIGEST,
    workflow_names: ['laptop-diagnostic']
  }
}

function installedPackage() {
  return {
    actor: 'marketplace:actor',
    configured_ref: 'main',
    contract_version: 1,
    distribution_digest: DIGEST,
    identity: identity(),
    installed_at: NOW,
    orphaned_source: false,
    package_path: 'packages/laptop-support',
    repository_url: 'ssh://git@example.test/team/workflows.git',
    resolved_commit: COMMIT,
    source_name: 'company',
    version: '1.0.0',
    workflow_paths: ['workflows/laptop-diagnostic.yaml']
  }
}

function installReview() {
  return {
    assessment: assessment(),
    candidate_digest: DIGEST,
    candidate_version: '1.0.0',
    confirmation_token: TOKEN,
    configured_ref: 'main',
    file_changes: [
      {
        candidate_digest: DIGEST,
        kind: 'added',
        old_digest: null,
        old_path: null,
        path: 'workflow-package.json'
      }
    ],
    identity: identity(),
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

function emptySetChange() {
  return { added: [], removed: [] }
}

function updateReview(result: 'unchanged' | 'update_available' = 'update_available') {
  const fileChanges: WorkflowMarketplaceFileChange[] = []

  return {
    assessment: assessment(),
    candidate_commit: COMMIT,
    candidate_digest: DIGEST,
    candidate_version: '2.0.0',
    compatibility_changes: { added: [], removed: [] },
    confirmation_token: result === 'unchanged' ? null : TOKEN,
    configured_ref: 'main',
    file_changes: fileChanges,
    identity: identity(),
    old_commit: '5'.repeat(40),
    old_digest: result === 'unchanged' ? DIGEST : '6'.repeat(64),
    old_version: '1.0.0',
    operation: 'update',
    repository_url: 'git@example.test:team/workflows.git',
    requirement_changes: {
      providers: emptySetChange(),
      runtimes: emptySetChange(),
      secrets: emptySetChange(),
      services: emptySetChange(),
      tools: emptySetChange()
    },
    result,
    review_digest: REVIEW_DIGEST,
    risk_changes: { added: [], removed: [] },
    source_name: 'company',
    workflow_changes: emptySetChange(),
    workflow_reviews: [workflowReview()]
  }
}

function removeReview() {
  return {
    confirmation_token: TOKEN,
    current_commit: COMMIT,
    current_version: '1.0.0',
    distribution_digest: DIGEST,
    identity: identity(),
    operation: 'remove',
    result: 'review_required',
    review_digest: REVIEW_DIGEST,
    workflow_names: ['laptop-diagnostic']
  }
}

function trustReview() {
  return {
    confirmation_token: TOKEN,
    distribution_digest: DIGEST,
    identity: identity(),
    package_resources: [
      'commands/interpret.md',
      'mcp/resources.yaml',
      'mcp/server.yaml',
      'scripts/collect.py',
      'workflows/laptop-diagnostic.yaml'
    ],
    resolved_commit: COMMIT,
    review_digest: REVIEW_DIGEST,
    source_name: 'company',
    version: '1.0.0',
    workflows: [workflowReview()]
  }
}

function packageDetail() {
  return {
    advisories: [diagnostic()],
    blockers: [],
    configured_ref: 'main',
    contract_version: 1,
    description: 'Support workflows',
    display_name: 'Laptop Support',
    external_requirements: requirements(),
    id: 'laptop-support',
    identifier: 'company/laptop-support',
    identity: identity(),
    install_status: 'not_installed',
    installed: null,
    license: 'MIT',
    package_digest: DIGEST,
    package_path: 'packages/laptop-support',
    publisher: 'Example',
    repository_url: 'file:///REDACTED',
    resolved_commit: COMMIT,
    resources: [
      { path: 'commands/interpret.md', types: ['command'] },
      { path: 'mcp/resources.yaml', types: ['mcp_resource'] },
      { path: 'mcp/server.yaml', types: ['mcp'] },
      { path: 'scripts/collect.py', types: ['script'] },
      { path: 'workflows/laptop-diagnostic.yaml', types: ['workflow_definition'] }
    ],
    source_name: 'company',
    source_state: 'fresh',
    tags: ['support'],
    update_status: 'not_applicable',
    verified: true,
    verified_at: NOW,
    version: '1.0.0',
    workflows: [workflowReview()]
  }
}

function operationResult(type: keyof typeof RESULT_KIND) {
  const values: Record<string, unknown> = {
    install_review: installReview(),
    installed_package: installedPackage(),
    package_detail: packageDetail(),
    remove_review: removeReview(),
    removed_package: installedPackage(),
    source_refresh: {
      diagnostic_code: null,
      message: null,
      package_count: 1,
      repository_url: 'https://example.test/team/workflows.git',
      resolved_commit: COMMIT,
      source_name: 'company',
      state: 'fresh',
      verified_at: NOW
    },
    trust_grant: { workflows: [{ state: 'trusted', workflow_name: 'laptop-diagnostic' }] },
    trust_review: trustReview(),
    trust_revoke: { revoked: 1 },
    update_checks: {
      checks: [
        {
          candidate_version: '2.0.0',
          diagnostic_code: null,
          identity: identity(),
          installed_version: '1.0.0',
          message: null,
          status: 'update_available'
        }
      ]
    },
    update_review: updateReview(),
    updated_package: { ...installedPackage(), version: '2.0.0' }
  }

  return { type, value: values[type] }
}

function operation(type: keyof typeof RESULT_KIND = 'installed_package') {
  return {
    created_at: NOW,
    error: null,
    finished_at: NOW,
    id: `wmop_${'a'.repeat(12)}_${'b'.repeat(32)}`,
    kind: RESULT_KIND[type],
    phase: 'completed',
    profile: 'support',
    progress: 100,
    result: operationResult(type),
    schema_version: 1,
    source_name: RESULT_KIND[type] === 'refresh' ? 'company' : null,
    started_at: NOW,
    state: 'succeeded',
    updated_at: NOW
  }
}

describe('workflow marketplace codec', () => {
  it('exposes an exhaustive kind/result and state-shape discriminated union', () => {
    type ActualResultTypes = {
      [Kind in WorkflowMarketplaceOperationKind]: NonNullable<
        Extract<WorkflowMarketplaceOperation, { kind: Kind; state: 'succeeded' }>['result']
      >['type']
    }

    type ExpectedResultTypes = {
      install_confirm: 'installed_package'
      install_prepare: 'install_review'
      package_detail: 'package_detail'
      refresh: 'source_refresh'
      remove_confirm: 'removed_package'
      remove_prepare: 'remove_review'
      trust_confirm: 'trust_grant'
      trust_prepare: 'trust_review'
      trust_revoke: 'trust_revoke'
      update_check: 'update_checks'
      update_confirm: 'updated_package'
      update_prepare: 'update_review'
    }

    expectTypeOf<ActualResultTypes>().toEqualTypeOf<ExpectedResultTypes>()
    expectTypeOf<Extract<WorkflowMarketplaceOperation, { kind: 'install_prepare' }>>().toEqualTypeOf<
      WorkflowMarketplaceOperationForKind<'install_prepare'>
    >()
    expectTypeOf<Extract<WorkflowMarketplaceOperation, { state: 'pending' }>['result']>().toEqualTypeOf<null>()
    expectTypeOf<Extract<WorkflowMarketplaceOperation, { state: 'running' }>['result']>().toEqualTypeOf<null>()
    expectTypeOf<Extract<WorkflowMarketplaceOperation, { state: 'failed' }>['result']>().toEqualTypeOf<null>()
    expectTypeOf<Extract<WorkflowMarketplaceOperation, { state: 'cancelled' }>['result']>().toEqualTypeOf<null>()
    expectTypeOf<
      Extract<WorkflowMarketplaceOperation, { state: 'failed' }>['error']
    >().toEqualTypeOf<WorkflowMarketplaceOperationError>()
    expectTypeOf<Extract<WorkflowMarketplaceOperation, { state: 'succeeded' }>['error']>().toEqualTypeOf<null>()
  })

  it('decodes capability, source, search, installed, and operation pages', () => {
    const source = {
      enabled: true,
      name: 'company',
      ref: 'main',
      repository_url: 'https://example.test/team/workflows.git'
    }

    const catalogPackage = {
      configured_ref: 'main',
      contract_version: 1,
      description: 'Support workflows',
      display_name: 'Laptop Support',
      id: 'laptop-support',
      identifier: 'company/laptop-support',
      license: 'MIT',
      package_digest: DIGEST,
      package_path: 'packages/laptop-support',
      publisher: 'Example',
      repository_url: 'https://example.test/team/workflows.git',
      resolved_commit: COMMIT,
      source_name: 'company',
      state: 'fresh',
      tags: ['support'],
      verified_at: NOW,
      version: '1.0.0'
    }

    expect(
      decodeWorkflowMarketplaceCapabilities({
        capabilities: ['sources', 'search', 'installed', 'updates', 'transactions', 'trust', 'operations'],
        profile: 'support',
        schema_version: 1
      })
    ).not.toBeNull()
    expect(
      decodeWorkflowMarketplaceSourceList({
        profile: 'support',
        sources: [
          {
            ...source,
            attempted_at: NOW,
            diagnostic_code: null,
            message: null,
            refresh_state: 'fresh',
            resolved_commit: COMMIT,
            verified_at: NOW,
            verified_package_count: 1
          }
        ]
      })
    ).not.toBeNull()
    expect(decodeWorkflowMarketplaceSourceResponse({ profile: 'support', source, status: 'created' })).not.toBeNull()
    expect(
      decodeWorkflowMarketplaceSearchPage({
        items: [catalogPackage],
        limit: 50,
        next_offset: null,
        offset: 0,
        profile: 'support',
        query: 'laptop',
        source: null
      })
    ).not.toBeNull()
    expect(
      decodeWorkflowMarketplaceInstalledPage({ packages: [installedPackage()], profile: 'support' })
    ).not.toBeNull()
    expect(
      decodeWorkflowMarketplaceOperationPage({
        limit: 50,
        offset: 0,
        operations: [operation()],
        profile: 'support'
      })
    ).not.toBeNull()
  })

  it.each([
    'install_review',
    'installed_package',
    'package_detail',
    'remove_review',
    'removed_package',
    'source_refresh',
    'trust_grant',
    'trust_review',
    'trust_revoke',
    'update_checks',
    'update_review',
    'updated_package'
  ] as const)('decodes the closed %s operation result variant', type => {
    expect(decodeMarketplaceOperation(operation(type))).not.toBeNull()
  })

  it('rejects every operation-kind/result mismatch and unknown operation kinds', () => {
    const entries = Object.entries(RESULT_KIND)

    for (const [resultType, expectedKind] of entries) {
      const knownResultType = resultType as keyof typeof RESULT_KIND
      expect(decodeMarketplaceOperation(operation(knownResultType))).not.toBeNull()

      for (const [, wrongKind] of entries) {
        if (wrongKind !== expectedKind) {
          expect(
            decodeMarketplaceOperation({
              ...operation(knownResultType),
              kind: wrongKind,
              source_name: wrongKind === 'refresh' ? 'company' : null
            })
          ).toBeNull()
        }
      }
    }

    expect(decodeMarketplaceOperation({ ...operation(), kind: 'future_marketplace_action' })).toBeNull()
    expect(decodeMarketplaceOperation({ ...operation('source_refresh'), source_name: null })).toBeNull()
    expect(decodeMarketplaceOperation({ ...operation('source_refresh'), source_name: 'Company' })).toBeNull()
    expect(decodeMarketplaceOperation({ ...operation('installed_package'), source_name: 'company' })).toBeNull()
  })

  it('rejects a succeeded refresh whose result names a different source', () => {
    const refresh = operation('source_refresh')

    if (refresh.result?.type !== 'source_refresh') {
      throw new Error('test fixture must be a source refresh result')
    }

    expect(
      decodeMarketplaceOperation({
        ...refresh,
        result: {
          ...refresh.result,
          value: { ...(refresh.result.value as Record<string, unknown>), source_name: 'other' }
        }
      })
    ).toBeNull()
  })

  it('enforces exact operation state/result/error pairings', () => {
    const pending = {
      ...operation(),
      error: null,
      finished_at: null,
      phase: 'queued',
      progress: 0,
      result: null,
      started_at: null,
      state: 'pending'
    }

    const running = {
      ...pending,
      kind: 'install_prepare',
      phase: 'fetching',
      progress: 10,
      started_at: NOW,
      state: 'running'
    }

    const failed = {
      ...operation(),
      error: { code: 'source_authentication_failed', message: 'Workflow marketplace operation failed.' },
      phase: 'failed',
      progress: 25,
      result: null,
      state: 'failed'
    }

    const cancelled = { ...failed, error: null, phase: 'cancelled', state: 'cancelled' }

    for (const kind of Object.values(RESULT_KIND)) {
      const source_name = kind === 'refresh' ? 'company' : null
      expect(decodeMarketplaceOperation({ ...pending, kind, source_name })).not.toBeNull()
      expect(decodeMarketplaceOperation({ ...running, kind, phase: 'running', source_name })).not.toBeNull()
      expect(decodeMarketplaceOperation({ ...failed, kind, source_name })).not.toBeNull()
      expect(decodeMarketplaceOperation({ ...cancelled, kind, source_name })).not.toBeNull()
    }

    expect(decodeMarketplaceOperation({ ...pending, result: operationResult('installed_package') })).toBeNull()
    expect(decodeMarketplaceOperation({ ...failed, result: operationResult('installed_package') })).toBeNull()
    expect(decodeMarketplaceOperation({ ...operation(), progress: 99 })).toBeNull()
    expect(decodeMarketplaceOperation({ ...cancelled, error: failed.error })).toBeNull()
    expect(decodeMarketplaceOperation({ ...pending, phase: 'running' })).toBeNull()
    expect(decodeMarketplaceOperation({ ...running, phase: 'completed' })).toBeNull()
    expect(decodeMarketplaceOperation({ ...operation(), phase: 'committing' })).toBeNull()
    expect(decodeMarketplaceOperation({ ...failed, phase: 'completed' })).toBeNull()
    expect(decodeMarketplaceOperation({ ...cancelled, phase: 'failed' })).toBeNull()
    expect(
      decodeMarketplaceOperation({ ...running, kind: 'refresh', phase: 'committing', source_name: 'company' })
    ).toBeNull()
    expect(decodeMarketplaceOperation({ ...running, kind: 'install_confirm', phase: 'committing' })).not.toBeNull()
    expect(decodeMarketplaceOperation({ ...running, progress: 99 })).not.toBeNull()
    expect(decodeMarketplaceOperation({ ...running, progress: 100 })).toBeNull()
    expect(decodeMarketplaceOperation({ ...failed, progress: 99 })).not.toBeNull()
    expect(decodeMarketplaceOperation({ ...failed, progress: 100 })).toBeNull()
    expect(decodeMarketplaceOperation({ ...cancelled, progress: 99 })).not.toBeNull()
    expect(decodeMarketplaceOperation({ ...cancelled, progress: 100 })).toBeNull()
    expect(decodeMarketplaceOperation({ ...running, progress: -1 })).toBeNull()
    expect(decodeMarketplaceOperation({ ...failed, progress: 101 })).toBeNull()
  })

  it('rejects unknown keys, wrong result pairings, and confirmation-token smuggling', () => {
    expect(decodeMarketplaceInstallReview({ ...installReview(), access_token: 'secret' })).toBeNull()
    expect(
      decodeMarketplaceOperation({
        ...operation(),
        result: { type: 'installed_package', value: installReview() }
      })
    ).toBeNull()
    expect(
      decodeMarketplaceOperation({
        ...operation(),
        result: {
          type: 'installed_package',
          value: { ...installedPackage(), confirmation_token: TOKEN }
        }
      })
    ).toBeNull()
    expect(decodeMarketplaceUpdateReview({ ...updateReview('unchanged'), confirmation_token: TOKEN })).toBeNull()
    expect(decodeMarketplaceUpdateReview({ ...updateReview(), confirmation_token: null })).toBeNull()
  })

  it('rejects invalid primitives, bounds, timestamps, digests, IDs, paths, and logical duplicates', () => {
    expect(decodeMarketplaceOperation({ ...operation(), progress: true })).toBeNull()
    expect(decodeMarketplaceOperation({ ...operation(), progress: Number.NaN })).toBeNull()
    expect(decodeMarketplaceOperation({ ...operation(), progress: Number.POSITIVE_INFINITY })).toBeNull()
    expect(decodeMarketplaceOperation({ ...operation(), progress: 1.5 })).toBeNull()
    expect(decodeMarketplaceOperation({ ...operation(), created_at: '2026-02-30T00:00:00Z' })).toBeNull()
    expect(decodeMarketplaceOperation({ ...operation(), id: 'operation-1' })).toBeNull()
    expect(decodeMarketplaceInstallReview({ ...installReview(), candidate_digest: 'ABC' })).toBeNull()
    expect(decodeMarketplaceInstallReview({ ...installReview(), package_path: '../escape' })).toBeNull()
    expect(
      decodeWorkflowMarketplaceInstalledPage({
        packages: [installedPackage(), installedPackage()],
        profile: 'support'
      })
    ).toBeNull()
    expect(
      decodeWorkflowMarketplaceOperationPage({
        limit: 101,
        offset: 0,
        operations: [],
        profile: 'support'
      })
    ).toBeNull()
    expect(
      decodeWorkflowMarketplaceSourceList({
        profile: 'support',
        sources: Array.from({ length: 129 }, (_, index) => ({
          attempted_at: null,
          diagnostic_code: null,
          enabled: true,
          message: null,
          name: `source-${index}`,
          ref: null,
          refresh_state: null,
          repository_url: 'https://example.test/team/workflows.git',
          resolved_commit: null,
          verified_at: null,
          verified_package_count: 0
        }))
      })
    ).toBeNull()
  })

  it('enforces exact source-list refresh and verified-cache relationships', () => {
    const base = {
      enabled: true,
      name: 'company',
      ref: null,
      repository_url: 'https://example.test/team/workflows.git'
    }

    const fresh = {
      ...base,
      attempted_at: NOW,
      diagnostic_code: null,
      message: null,
      refresh_state: 'fresh',
      resolved_commit: COMMIT,
      verified_at: NOW,
      verified_package_count: 1
    }

    const stale = {
      ...fresh,
      diagnostic_code: 'source_unavailable',
      message: 'workflow marketplace source is unavailable',
      refresh_state: 'stale'
    }

    const authenticationFailure = {
      ...base,
      attempted_at: NOW,
      diagnostic_code: 'source_authentication_failed',
      message: 'workflow marketplace source authentication failed',
      refresh_state: 'authentication-failed',
      resolved_commit: null,
      verified_at: null,
      verified_package_count: 0
    }

    for (const sourceRecord of [
      {
        ...base,
        attempted_at: null,
        diagnostic_code: null,
        message: null,
        refresh_state: null,
        resolved_commit: null,
        verified_at: null,
        verified_package_count: 0
      },
      fresh,
      stale,
      authenticationFailure,
      { ...fresh, enabled: false }
    ]) {
      expect(decodeWorkflowMarketplaceSourceList({ profile: 'support', sources: [sourceRecord] })).not.toBeNull()
    }

    for (const sourceRecord of [
      { ...fresh, unexpected: true },
      { ...fresh, attempted_at: 'yesterday' },
      { ...fresh, resolved_commit: null },
      { ...fresh, verified_at: null },
      { ...fresh, diagnostic_code: 'source_failed', message: 'failed' },
      { ...stale, diagnostic_code: null },
      { ...authenticationFailure, resolved_commit: COMMIT, verified_at: NOW, verified_package_count: 1 },
      { ...authenticationFailure, message: 'token=secret' },
      { ...authenticationFailure, message: 'token=secret /private/tmp/checkout' },
      { ...authenticationFailure, refresh_state: 'cancelled' }
    ]) {
      expect(decodeWorkflowMarketplaceSourceList({ profile: 'support', sources: [sourceRecord] })).toBeNull()
    }
  })

  it.each(diagnosticCorpus.unsafe)('rejects a noncanonical unsafe source diagnostic: %s', message => {
    expect(
      decodeWorkflowMarketplaceSourceList({
        profile: 'support',
        sources: [
          {
            attempted_at: NOW,
            diagnostic_code: 'source_unavailable',
            enabled: true,
            message,
            name: 'company',
            ref: null,
            refresh_state: 'unavailable',
            repository_url: 'https://example.test/team/workflows.git',
            resolved_commit: null,
            verified_at: null,
            verified_package_count: 0
          }
        ]
      })
    ).toBeNull()
  })

  it.each(diagnosticCorpus.safe)('accepts a canonical safe source diagnostic: %s', message => {
    expect(
      decodeWorkflowMarketplaceSourceList({
        profile: 'support',
        sources: [
          {
            attempted_at: NOW,
            diagnostic_code: 'source_unavailable',
            enabled: true,
            message,
            name: 'company',
            ref: null,
            refresh_state: 'unavailable',
            repository_url: 'https://example.test/team/workflows.git',
            resolved_commit: null,
            verified_at: null,
            verified_package_count: 0
          }
        ]
      })
    ).not.toBeNull()
  })

  it.each([1, 2, 8, 9])('rejects an absolute local path nested through %i percent-encoding layers', layers => {
    let message = '/root/workspace/secret.txt'

    for (let layer = 0; layer < layers; layer += 1) {
      message = encodeURIComponent(message)
    }

    expect(
      decodeWorkflowMarketplaceSourceList({
        profile: 'support',
        sources: [
          {
            attempted_at: NOW,
            diagnostic_code: 'source_unavailable',
            enabled: true,
            message,
            name: 'company',
            ref: null,
            refresh_state: 'unavailable',
            repository_url: 'https://example.test/team/workflows.git',
            resolved_commit: null,
            verified_at: null,
            verified_package_count: 0
          }
        ]
      })
    ).toBeNull()
  })

  it('enforces the exact source diagnostic size bound', () => {
    const response = (message: string) => ({
      profile: 'support',
      sources: [
        {
          attempted_at: NOW,
          diagnostic_code: 'source_unavailable',
          enabled: true,
          message,
          name: 'company',
          ref: null,
          refresh_state: 'unavailable',
          repository_url: 'https://example.test/team/workflows.git',
          resolved_commit: null,
          verified_at: null,
          verified_package_count: 0
        }
      ]
    })

    expect(decodeWorkflowMarketplaceSourceList(response('x'.repeat(4096)))).not.toBeNull()
    expect(decodeWorkflowMarketplaceSourceList(response('x'.repeat(4097)))).toBeNull()
    expect(decodeWorkflowMarketplaceSourceList(response('😀'.repeat(4096)))).not.toBeNull()
    expect(decodeWorkflowMarketplaceSourceList(response('😀'.repeat(4097)))).toBeNull()
  })

  it.each(['fatal:\nretry', 'fatal:\rretry', 'fatal:\tretry', 'fatal:\u007fretry'])(
    'rejects a source diagnostic with noncanonical controls: %s',
    message => {
      const response = {
        profile: 'support',
        sources: [
          {
            attempted_at: NOW,
            diagnostic_code: 'source_unavailable',
            enabled: true,
            message,
            name: 'company',
            ref: null,
            refresh_state: 'unavailable',
            repository_url: 'https://example.test/team/workflows.git',
            resolved_commit: null,
            verified_at: null,
            verified_package_count: 0
          }
        ]
      }

      expect(decodeWorkflowMarketplaceSourceList(response)).toBeNull()
    }
  )

  it.each([
    'https://example.test/team/workflows.git',
    'https://example.test/team/workflows.git#packages/support',
    'https://example.test/team/workflows.git?transport=smart',
    'https://example.test/team/redacted-tools.git',
    'ssh://git@example.test/team/workflows.git',
    'ssh://git@example.test/team/workflows.git#packages/support',
    'git@example.test:team/workflows.git',
    'file:/Users/operator/projects/workflows.git',
    'file:///REDACTED'
  ])('accepts the sanitized repository identity %s', repositoryUrl => {
    expect(decodeMarketplaceInstallReview({ ...installReview(), repository_url: repositoryUrl })).not.toBeNull()
  })

  it.each([
    'https://user:secret@example.test/team/workflows.git',
    'https://example.test/team/workflows.git?access_token=secret',
    'https://example.test/team/workflows.git?access%255Ftoken=secret',
    'https://example.test/team/workflows.git#clientSecret=secret',
    'https://example.test/team/workflows.git?next=https://alice:secret@private.example/repository.git',
    'https://example.test/team/%2e%2e/secrets.git',
    'https://example.test/team/%252e%252e/secrets.git',
    'https://example.test/team%2Fother/workflows.git',
    'https://user%3Asecret@example.test/team/workflows.git',
    'ssh://git%3Asecret@example.test/team/workflows.git',
    'ssh://git@example.test/team/workflows.git#clientSecret=secret',
    'git@example.test:team/workflows.git#access_token=secret',
    'javascript:alert(1)',
    'data:text/plain,secret',
    'https://example.test/team/%250Aworkflows.git',
    'ssh://git@example.test/team/%7Fworkflows.git',
    'file:/Users/operator/repository.git#secret',
    'https://example.test/team/[REDACTED_PATH].git',
    'file:///redacted',
    'file:/Users/oper\tator/repository.git',
    'file:/Users/oper\nator/repository.git',
    `file:/Users/oper${String.fromCodePoint(127)}ator/repository.git`,
    'file:/Users/operator/%FF/repository.git',
    'file:relative.git'
  ])('rejects the unsafe repository identity %s', repositoryUrl => {
    expect(decodeMarketplaceInstallReview({ ...installReview(), repository_url: repositoryUrl })).toBeNull()
  })

  it('separates sanitized response identities from source request identities', () => {
    expect(isWorkflowMarketplaceRepositoryUrl('file:///REDACTED')).toBe(true)
    expect(isWorkflowMarketplaceSourceRequestUrl('file:///REDACTED')).toBe(false)
    expect(isWorkflowMarketplaceSourceRequestUrl('https://example.test/team/[REDACTED_PATH].git')).toBe(false)
    expect(isWorkflowMarketplaceRepositoryUrl('file:/tmp/workflows.git')).toBe(false)
    expect(isWorkflowMarketplaceRepositoryUrl('file:/Users/operator/.cache/workflows.git')).toBe(false)

    for (const repositoryUrl of [
      'https://example.test/team/workflows.git',
      'https://example.test/team/workflows.git?transport=smart',
      'https://example.test/team/workflows.git#packages/support',
      'https://example.test/team/redacted-tools.git',
      'https://example.test/team/workflows.git?monkey=value&tokenizer=parser',
      'ssh://git@example.test/team/workflows.git',
      'ssh://git@example.test/team/workflows.git#packages/support',
      'git@example.test:team/workflows.git',
      'git@example.test:team/workflows.git#packages/support',
      'owner/repository/packages/support',
      'file:/Users/operator/projects/workflows.git',
      'file:/tmp/workflows.git',
      'file:/Users/operator/.cache/workflows.git',
      'file:/Users/operator/cache/workflows.git',
      'file:/tmp/workflows.git#packages/support'
    ]) {
      expect(isWorkflowMarketplaceSourceRequestUrl(repositoryUrl)).toBe(true)
    }

    for (const repositoryUrl of [
      'https://example.test/team/%2e%2e/secrets.git',
      'https://example.test/team/%252e%252e/secrets.git',
      'https://example.test/team/workflows.git#../secrets',
      'https://example.test/team/workflows.git#%252e%252e/secrets',
      'https://example.test/team/workflows.git?path=../secrets',
      'https://user%3Asecret@example.test/team/workflows.git',
      'ssh://git@example.test/team/workflows.git?token=secret',
      'git@example.test:team/workflows.git?transport=smart',
      'git@example.test:team/workflows.git#../secrets',
      'file:/tmp/workflows.git#../secrets',
      'owner/repository#../secrets',
      'owner/repository?path=../secrets',
      'file:relative.git',
      'https://example.test/team/[REDACTED_PATH].git'
    ]) {
      expect(isWorkflowMarketplaceSourceRequestUrl(repositoryUrl)).toBe(false)
    }
  })

  it('matches backend credential parameter normalization without substring false positives', () => {
    for (const identifier of [
      'https://example.test/team/workflows.git?monkey=value',
      'https://example.test/team/workflows.git?tokenizer=value',
      'https://example.test/team/workflows.git?keyboard=value',
      'https://example.test/team/workflows.git?label=redacted',
      'owner/repository?monkey=value',
      'owner/repository#packages/support'
    ]) {
      expect(isWorkflowMarketplaceSourceRequestUrl(identifier)).toBe(true)
      expect(isWorkflowMarketplaceInstallIdentifier(identifier)).toBe(true)
    }

    for (const key of [
      'access_token',
      'accessToken',
      'access-token',
      'apitoken',
      'clientSecret',
      'password',
      'authorization'
    ]) {
      expect(isWorkflowMarketplaceInstallIdentifier(`https://example.test/team/workflows.git?${key}=secret`)).toBe(
        false
      )
      expect(isWorkflowMarketplaceInstallIdentifier(`owner/repository?${key}=secret`)).toBe(false)
    }
  })

  it('scans credential parameters at every nested Git-source boundary', () => {
    for (const identifier of [
      'https://example.test/team/workflows.git?monkey=value&hockey=value&keyboard=value&keynote=value',
      'https://example.test/team/workflows.git?next=https://private.test/team/repo.git?monkey=value',
      'owner/repository#next=https://private.test/team/repo.git?keyboard=value'
    ]) {
      expect(isWorkflowMarketplaceSourceRequestUrl(identifier)).toBe(true)
      expect(isWorkflowMarketplaceInstallIdentifier(identifier)).toBe(true)
    }

    for (const identifier of [
      'https://example.test/team/workflows.git?key=secret',
      'https://example.test/team/workflows.git?%E2%84%AAey=secret',
      'https://example.test/team/workflows.git?%C5%BFecret=secret',
      'https://example.test/team/workflows.git?clie%6EtSecret=secret',
      'https://example.test/team/workflows.git?next=https://private.test/team/repo.git?access_token=secret',
      'https://example.test/team/workflows.git#next=https://private.test/team/repo.git?apiKey=secret',
      'https://example.test/team/workflows.git?next=https://private.test/team/repo.git&clientSecret=secret',
      'https://example.test/team/workflows.git?next=https://private.test/team/repo.git;password=secret',
      'https://example.test/team/workflows.git?next=https://private.test/team/repo.git#authorization=secret',
      'https://example.test/team/workflows.git?next=https://private.test/team/repo.git%3Faccess_token=secret',
      'https://example.test/team/workflows.git?next=https://private.test/team/repo.git%253Faccess_token=secret',
      'git@corp_alias:team/repo.git#next=https://private.test/team/repo.git?clientSecret=secret',
      'owner/repository?next=https://private.test/team/repo.git?auth=secret'
    ]) {
      expect(isWorkflowMarketplaceSourceRequestUrl(identifier)).toBe(false)
      expect(isWorkflowMarketplaceInstallIdentifier(identifier)).toBe(false)
    }
  })

  it.each([
    ['git@host:path', true],
    ['git@gitlab.example:team/repo.git#packages/support', true],
    ['git@corp_alias:team/repo@v2.git', true],
    ['git@Corp_Alias-2:team/dir@scope/repo.git', true],
    ['git@corp_alias:team/repo@@v2.git', true],
    ['git@:team/repo.git', false],
    ['git@corp_alias:', false],
    ['git@@corp_alias:team/repo.git', false],
    ['git@corp@alias:team/repo.git', false],
    ['git@alice:secret@corp_alias:team/repo.git', false],
    ['user:password@host:path', false],
    ['owner/repository/subdir?channel=stable', true],
    ['owner/repository?next=https://private.test/team/repo.git', true],
    ['owner/repository?next=ssh://private.test/team/repo.git', true],
    ['owner/repository?next=ssh://git@private.test/team/repo.git', false],
    ['owner/repository?scope=@team', false],
    ['owner/repository#scope=@team', false],
    ['owner/repository?next=https://private.test/repo?access_token=secret', false]
  ] as const)('matches backend Git-source request validation for %s', (identifier, expected) => {
    expect(isWorkflowMarketplaceSourceRequestUrl(identifier)).toBe(expected)
    expect(isWorkflowMarketplaceInstallIdentifier(identifier)).toBe(expected)
  })

  it.each([
    'git@gitlab.example:team/repo.git#packages/support',
    'git@corp_alias:team/repo.git',
    'git@Corp_Alias-2:team/repo.git',
    'git@repo_host.internal_2:team/repo.git#packages/support',
    'git@corp_alias:team/repo@v2.git',
    'git@Corp_Alias-2:team/dir@scope/repo.git',
    'git@corp_alias:team/repo@@v2.git'
  ])('accepts the backend-supported SCP host alias %s', repositoryUrl => {
    expect(isWorkflowMarketplaceRepositoryUrl(repositoryUrl)).toBe(true)
    expect(isWorkflowMarketplaceSourceRequestUrl(repositoryUrl)).toBe(true)
    expect(isWorkflowMarketplaceInstallIdentifier(repositoryUrl)).toBe(true)
  })

  it.each([
    'git@:team/repo.git',
    'git@corp_alias:',
    'git@corp/alias:team/repo.git',
    'git@corp alias:team/repo.git',
    'git@corp_alias:team repo.git',
    `git@corp_alias:team/${String.fromCodePoint(31)}repo.git`,
    'git@corp_alias:../repo.git',
    'git@corp_alias:team/repo.git?ref=main',
    'git@alice:secret@corp_alias:team/repo.git',
    'git@@corp_alias:team/repo.git',
    'git@corp@alias:team/repo.git',
    'git@corp_alias:team\\repo.git',
    'git@corp#alias:team/repo.git',
    `git@corp${String.fromCodePoint(127)}alias:team/repo.git`
  ])('rejects the invalid SCP identity %s', repositoryUrl => {
    expect(isWorkflowMarketplaceRepositoryUrl(repositoryUrl)).toBe(false)
    expect(isWorkflowMarketplaceSourceRequestUrl(repositoryUrl)).toBe(false)
    expect(isWorkflowMarketplaceInstallIdentifier(repositoryUrl)).toBe(false)
  })

  it('rejects shorthand authority markers before query and fragment truncation', () => {
    for (const identifier of [
      'owner/repository?scope=@team',
      'owner/repository#scope=@team',
      'owner/repository?scope=%40team',
      'owner/repository?scope=%2540team',
      'owner/repository#scope=%252540team',
      'owner/repository?next=https://private.test/repo?scope=@team',
      'owner/repository?next=https://private.test/repo?scope=%2540team',
      'owner/repository#next=https://private.test/repo#scope=@team',
      'owner/repository#next=https://private.test/repo#scope=%252540team',
      'owner/repository?next=ssh://git@private.test/team/repo.git',
      'owner/repository?next=ssh://git%40private.test/team/repo.git',
      'owner/repository?next=ssh://git%2540private.test/team/repo.git',
      'owner/repository?next=user:password@private.test:team/repo.git'
    ]) {
      expect(isWorkflowMarketplaceSourceRequestUrl(identifier)).toBe(false)
      expect(isWorkflowMarketplaceInstallIdentifier(identifier)).toBe(false)
    }

    for (const identifier of [
      'owner/repository/subdir?channel=stable',
      'owner/repository?next=https://private.test/team/repo.git',
      'owner/repository?next=ssh://private.test/team/repo.git',
      'owner/repository#packages/support?monkey=value',
      'https://example.test/team/repo@v2.git',
      'ssh://git@private.test/team/repo@v2.git',
      'file:/tmp/repo@v2.git'
    ]) {
      expect(isWorkflowMarketplaceSourceRequestUrl(identifier)).toBe(true)
      expect(isWorkflowMarketplaceInstallIdentifier(identifier)).toBe(true)
    }
  })

  it('rejects non-canonical UTC timestamps', () => {
    expect(decodeMarketplaceOperation({ ...operation(), created_at: '2026-09-04T00:00:00.1Z' })).toBeNull()
  })

  it('covers package resources, workflow references, MCP bindings, provenance, and installed state', () => {
    expect(decodeMarketplacePackageDetail(packageDetail())).not.toBeNull()
    expect(decodeMarketplacePackageDetail({ ...packageDetail(), identifier: 'partner/laptop-support' })).toBeNull()
    expect(decodeMarketplacePackageDetail({ ...packageDetail(), update_status: 'current' })).toBeNull()
    expect(
      decodeMarketplacePackageDetail({
        ...packageDetail(),
        resources: packageDetail().resources.filter(resource => resource.path !== 'mcp/resources.yaml')
      })
    ).toBeNull()
    expect(
      decodeMarketplacePackageDetail({
        ...packageDetail(),
        install_status: 'installed',
        installed: installedPackage(),
        update_status: 'current'
      })
    ).not.toBeNull()
  })

  it('rejects package detail resource, workflow identity, and digest incoherence', () => {
    const wrongDigest = packageDetail()
    wrongDigest.workflows[0].package_digest = '9'.repeat(64)
    expect(decodeMarketplacePackageDetail(wrongDigest)).toBeNull()

    const duplicateWorkflow = packageDetail()
    duplicateWorkflow.workflows = [{ ...workflowReview(), workflow_name: 'Laptop-Diagnostic' }, workflowReview()]
    expect(decodeMarketplacePackageDetail(duplicateWorkflow)).toBeNull()

    const duplicateResource = packageDetail()
    duplicateResource.resources = [
      { path: 'commands/Interpret.md', types: ['command'] },
      ...duplicateResource.resources
    ]
    expect(decodeMarketplacePackageDetail(duplicateResource)).toBeNull()

    const unicodeCasefoldResource = packageDetail()
    unicodeCasefoldResource.resources = [
      { path: 'commands/STRASSE.md', types: ['command'] },
      { path: 'commands/Straße.md', types: ['command'] },
      ...unicodeCasefoldResource.resources
    ]
    expect(decodeMarketplacePackageDetail(unicodeCasefoldResource)).toBeNull()

    const distinctDotlessIResource = packageDetail()
    distinctDotlessIResource.resources = [
      ...distinctDotlessIResource.resources,
      { path: 'scripts/i.py', types: ['script'] },
      { path: 'scripts/ı.py', types: ['script'] }
    ].sort((left, right) => (left.path < right.path ? -1 : left.path > right.path ? 1 : 0))
    expect(decodeMarketplacePackageDetail(distinctDotlessIResource)).not.toBeNull()
  })

  it('requires assessment workflow identities to exactly match reviewed workflows', () => {
    const install = installReview()
    install.assessment.workflow_names = ['extra']
    expect(decodeMarketplaceInstallReview(install)).toBeNull()

    const installDigest = installReview()
    installDigest.workflow_reviews[0].package_digest = '9'.repeat(64)
    expect(decodeMarketplaceInstallReview(installDigest)).toBeNull()

    const update = updateReview()
    update.assessment.workflow_names = ['extra', 'laptop-diagnostic']
    expect(decodeMarketplaceUpdateReview(update)).toBeNull()

    const updateDigest = updateReview()
    updateDigest.workflow_reviews[0].package_digest = '9'.repeat(64)
    expect(decodeMarketplaceUpdateReview(updateDigest)).toBeNull()

    const missing = installReview()
    missing.workflow_reviews.push({
      ...workflowReview(),
      definition_path: 'workflows/second.yaml',
      workflow_name: 'second'
    })
    missing.assessment.package_resources.push('workflows/second.yaml')
    expect(decodeMarketplaceInstallReview(missing)).toBeNull()

    const casefoldCollision = installReview()
    casefoldCollision.assessment.workflow_names = ['Laptop-Diagnostic', 'laptop-diagnostic']
    casefoldCollision.workflow_reviews = [{ ...workflowReview(), workflow_name: 'Laptop-Diagnostic' }, workflowReview()]
    expect(decodeMarketplaceInstallReview(casefoldCollision)).toBeNull()

    const missingResource = installReview()
    missingResource.assessment.package_resources = missingResource.assessment.package_resources.filter(
      path => path !== 'scripts/collect.py'
    )
    expect(decodeMarketplaceInstallReview(missingResource)).toBeNull()

    const trust = trustReview()
    trust.workflows = [{ ...workflowReview(), workflow_name: 'Laptop-Diagnostic' }, workflowReview()]
    expect(decodeMarketplaceTrustReview(trust)).toBeNull()

    const trustDigest = trustReview()
    trustDigest.workflows[0].package_digest = '9'.repeat(64)
    expect(decodeMarketplaceTrustReview(trustDigest)).toBeNull()

    const trustMissingResource = trustReview()
    trustMissingResource.package_resources = trustMissingResource.package_resources.filter(
      path => path !== 'commands/interpret.md'
    )
    expect(decodeMarketplaceTrustReview(trustMissingResource)).toBeNull()
  })

  it('validates maximum trust resource references without quadratic cross-object scans', () => {
    const resources = Array.from(
      { length: 512 },
      (_, index) => `commands/resource-${index.toString().padStart(3, '0')}.md`
    )

    const review = trustReview()
    review.package_resources = resources
    review.workflows = Array.from({ length: 512 }, (_, index) => ({
      ...workflowReview(),
      command_resources: resources,
      definition_path: resources[0],
      mcp_resource_files: [],
      mcp_resources: [],
      script_resources: [],
      workflow_name: `workflow-${index.toString().padStart(3, '0')}`
    }))

    const startedAt = performance.now()
    expect(decodeMarketplaceTrustReview(review)).not.toBeNull()
    expect(performance.now() - startedAt).toBeLessThan(5_000)
  })

  it('rejects update file-change endpoint overlap and case-fold collisions', () => {
    const duplicate = updateReview()
    duplicate.file_changes = [
      {
        candidate_digest: DIGEST,
        kind: 'added',
        old_digest: null,
        old_path: null,
        path: 'scripts/new.py'
      },
      {
        candidate_digest: DIGEST,
        kind: 'added',
        old_digest: null,
        old_path: null,
        path: 'scripts/new.py'
      }
    ]
    expect(decodeMarketplaceUpdateReview(duplicate)).toBeNull()

    const overlap = updateReview()
    overlap.file_changes = [
      {
        candidate_digest: DIGEST,
        kind: 'added',
        old_digest: null,
        old_path: null,
        path: 'scripts/New.py'
      },
      {
        candidate_digest: null,
        kind: 'removed',
        old_digest: DIGEST,
        old_path: null,
        path: 'scripts/new.py'
      }
    ]
    expect(decodeMarketplaceUpdateReview(overlap)).toBeNull()

    const renamedOverlap = updateReview()
    renamedOverlap.file_changes = [
      {
        candidate_digest: DIGEST,
        kind: 'renamed',
        old_digest: DIGEST,
        old_path: 'scripts/old.py',
        path: 'scripts/new.py'
      },
      {
        candidate_digest: DIGEST,
        kind: 'added',
        old_digest: null,
        old_path: null,
        path: 'scripts/old.py'
      }
    ]
    expect(decodeMarketplaceUpdateReview(renamedOverlap)).toBeNull()
  })

  it('requires an unchanged update to preserve the package digest and contain no file changes', () => {
    const changedDigest = updateReview('unchanged')
    changedDigest.old_digest = '9'.repeat(64)
    expect(decodeMarketplaceUpdateReview(changedDigest)).toBeNull()

    const changedFile = updateReview('unchanged')
    changedFile.file_changes = [
      {
        candidate_digest: DIGEST,
        kind: 'added',
        old_digest: null,
        old_path: null,
        path: 'scripts/new.py'
      }
    ]
    expect(decodeMarketplaceUpdateReview(changedFile)).toBeNull()
  })

  it('accepts the exact maximum file-change collection when endpoints are unique', () => {
    const review = updateReview()
    review.file_changes = Array.from({ length: 1024 }, (_, index) => ({
      candidate_digest: DIGEST,
      kind: 'added' as const,
      old_digest: null,
      old_path: null,
      path: `fixtures/change-${String(index).padStart(4, '0')}.txt`
    }))

    expect(decodeMarketplaceUpdateReview(review)).not.toBeNull()
  })

  it('decodes install, update, remove, and trust reviews directly', () => {
    expect(decodeMarketplaceInstallReview(installReview())).not.toBeNull()
    expect(decodeMarketplaceUpdateReview(updateReview())).not.toBeNull()
    expect(decodeMarketplaceUpdateReview(updateReview('unchanged'))).not.toBeNull()
    expect(decodeMarketplaceRemoveReview(removeReview())).not.toBeNull()
    expect(decodeMarketplaceTrustReview(trustReview())).not.toBeNull()
  })

  it('returns fresh deeply detached objects', () => {
    const input = packageDetail()
    const decoded = decodeMarketplacePackageDetail(input)
    expect(decoded).not.toBeNull()

    if (decoded === null) {
      return
    }

    input.tags[0] = 'mutated-input'
    input.workflows[0].requested_tools[0] = 'mutated-input'
    expect(decoded.tags).toEqual(['support'])
    expect(decoded.workflows[0].requested_tools).toEqual(['terminal'])

    decoded.tags[0] = 'mutated-output'
    decoded.workflows[0].requested_tools[0] = 'mutated-output'
    expect(input.tags[0]).toBe('mutated-input')
    expect(input.workflows[0].requested_tools[0]).toBe('mutated-input')
  })

  it('decodes only the stable error envelope fields', () => {
    expect(
      decodeMarketplaceErrorEnvelope({
        detail: { code: 'package_not_found', message: 'Workflow marketplace request failed.' }
      })
    ).toEqual({ code: 'package_not_found', message: 'Workflow marketplace request failed.' })
    expect(
      decodeMarketplaceErrorEnvelope({
        detail: { code: 'package_not_found', debug: 'secret', message: 'failed' }
      })
    ).toBeNull()
    expect(decodeMarketplaceErrorEnvelope({ detail: ['package_not_found'] })).toBeNull()
  })

  it.each([
    'access_token=top-secret',
    'clientSecret=top-secret',
    'https://alice:secret@example.test/private.git',
    '/private/tmp/marketplace-secret',
    'secret\ncontrol',
    'token%3Dencoded-secret'
  ])('replaces untrusted error message text locally: %s', message => {
    const decoded = decodeMarketplaceErrorEnvelope({ detail: { code: 'source_authentication_failed', message } })

    expect(decoded).toEqual({
      code: 'source_authentication_failed',
      message: 'Workflow marketplace request failed.'
    })
    expect(JSON.stringify(decoded)).not.toContain(message)
  })
})
