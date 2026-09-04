import { describe, expect, it } from 'vitest'

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
  decodeWorkflowMarketplaceSourceResponse
} from './workflow-marketplace-codec'

const DIGEST = '1'.repeat(64)
const RISK_DIGEST = '2'.repeat(64)
const REVIEW_DIGEST = '3'.repeat(64)
const COMMIT = '4'.repeat(40)
const TOKEN = 'confirmation-token-value-1234567890'
const NOW = '2026-09-04T00:00:00Z'

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
  return {
    assessment: assessment(),
    candidate_commit: COMMIT,
    candidate_digest: DIGEST,
    candidate_version: '2.0.0',
    compatibility_changes: { added: [], removed: [] },
    confirmation_token: result === 'unchanged' ? null : TOKEN,
    configured_ref: 'main',
    file_changes: [],
    identity: identity(),
    old_commit: '5'.repeat(40),
    old_digest: '6'.repeat(64),
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

function operationResult(type: string) {
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

function operation(type = 'installed_package') {
  return {
    created_at: NOW,
    error: null,
    finished_at: NOW,
    id: `wmop_${'a'.repeat(12)}_${'b'.repeat(32)}`,
    kind: 'install_confirm',
    phase: 'completed',
    profile: 'support',
    progress: 100,
    result: operationResult(type),
    schema_version: 1,
    started_at: NOW,
    state: 'succeeded',
    updated_at: NOW
  }
}

describe('workflow marketplace codec', () => {
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
    expect(decodeWorkflowMarketplaceSourceList({ profile: 'support', sources: [source] })).not.toBeNull()
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
  ])('decodes the closed %s operation result variant', type => {
    expect(decodeMarketplaceOperation(operation(type))).not.toBeNull()
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

    const running = { ...pending, phase: 'fetching', progress: 10, started_at: NOW, state: 'running' }

    const failed = {
      ...operation(),
      error: { code: 'source_authentication_failed', message: 'Workflow marketplace operation failed.' },
      progress: 25,
      result: null,
      state: 'failed'
    }

    const cancelled = { ...failed, error: null, phase: 'cancelled', state: 'cancelled' }

    expect(decodeMarketplaceOperation(pending)).not.toBeNull()
    expect(decodeMarketplaceOperation(running)).not.toBeNull()
    expect(decodeMarketplaceOperation(failed)).not.toBeNull()
    expect(decodeMarketplaceOperation(cancelled)).not.toBeNull()
    expect(decodeMarketplaceOperation({ ...pending, result: operationResult('installed_package') })).toBeNull()
    expect(decodeMarketplaceOperation({ ...failed, result: operationResult('installed_package') })).toBeNull()
    expect(decodeMarketplaceOperation({ ...operation(), progress: 99 })).toBeNull()
    expect(decodeMarketplaceOperation({ ...cancelled, error: failed.error })).toBeNull()
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
          enabled: true,
          name: `source-${index}`,
          ref: null,
          repository_url: 'https://example.test/team/workflows.git'
        }))
      })
    ).toBeNull()
  })

  it.each([
    'https://example.test/team/workflows.git',
    'ssh://git@example.test/team/workflows.git',
    'git@example.test:team/workflows.git',
    'file:/Users/operator/projects/workflows.git',
    'file:///REDACTED'
  ])('accepts the sanitized repository identity %s', repositoryUrl => {
    expect(decodeMarketplaceInstallReview({ ...installReview(), repository_url: repositoryUrl })).not.toBeNull()
  })

  it.each([
    'https://user:secret@example.test/team/workflows.git',
    'https://example.test/team/workflows.git?access_token=secret',
    'javascript:alert(1)',
    'data:text/plain,secret',
    'https://example.test/team/%250Aworkflows.git',
    'ssh://git@example.test/team/%7Fworkflows.git',
    'file:/Users/operator/repository.git#secret',
    'file:/Users/oper\nator/repository.git',
    'file:/Users/operator/%FF/repository.git'
  ])('rejects the unsafe repository identity %s', repositoryUrl => {
    expect(decodeMarketplaceInstallReview({ ...installReview(), repository_url: repositoryUrl })).toBeNull()
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
})
