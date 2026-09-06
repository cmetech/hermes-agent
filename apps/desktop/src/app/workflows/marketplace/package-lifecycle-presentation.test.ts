import { describe, expect, it } from 'vitest'

import type { SupervisedRecord } from '@/store/workflow-marketplace-supervisor'

import { lifecycleFixture } from './lifecycle-test-harness'
import { packageLifecyclePresentation } from './package-lifecycle-presentation'

function record(name: string): SupervisedRecord {
  const operation = lifecycleFixture(name)

  return {
    key: 'test-record',
    binding: {
      connectionId: 'remote-a',
      connectionGeneration: 1,
      profile: operation.profile,
      principalBinding: 'a'.repeat(64),
      registryEpoch: operation.registry_epoch
    },
    requestId: operation.request_id,
    operationId: operation.id,
    kind: operation.kind,
    subject: operation.subject,
    selection: operation.selection,
    status: 'terminal',
    callPending: false,
    barrier: true,
    operation,
    errorCode: null,
    admissionWindowClosed: false
  }
}

describe('authoritative package lifecycle presentation', () => {
  it('offers a new check, not preparation or status retry, after an authoritative check error', () => {
    const actual = packageLifecyclePresentation(record('service failed update check'))
    expect(actual.retryAction).toBe('check')
    expect(actual.canPrepareAgain).toBe(false)
  })

  it.each([
    ['service install confirm', 'Installed version 1.0.0'],
    ['service update confirm', 'Updated to version 2.0.0'],
    ['service remove confirm', 'Package removal completed'],
    ['service verified rollback', 'Currently installed: 2.0.0'],
    ['service rollback failed', 'State could not be confirmed'],
    ['service recovery ambiguous', 'State could not be confirmed'],
    ['service install prepare cancelled', 'Cancelled before changes were committed'],
    ['service unchanged update', 'Version 1.0.0 is current'],
    ['service update check', 'Version 1.0.0 is current'],
    ['service available update check', 'An update is available'],
    ['service orphaned update check', 'source is unavailable'],
    ['service failed update check', 'Could not check for updates']
  ])('%s presents only its backend evidence', (name, expected) => {
    const actual = packageLifecyclePresentation(record(name), lifecycleFixture('service update prepare'))
    expect(actual.message).toContain(expected)

    if (name.includes('rollback failed') || name.includes('ambiguous')) {
      expect(actual.message).not.toMatch(/remains installed|Nothing new was installed|rolled back|version/i)
      expect(actual.canPrepareAgain).toBe(false)
    }
  })

  it.each(['operationId', 'requestId'] as const)('rejects mismatched terminal %s without success copy', field => {
    const value = record('service install confirm')
    const actual = packageLifecyclePresentation({ ...value, [field]: 'wrong' })
    expect(actual.message).toContain('State could not be confirmed')
    expect(actual.message).not.toMatch(/Installed version|remains installed/)
  })

  it('does not use a removal review as evidence of the previous update version', () => {
    const actual = packageLifecyclePresentation(
      record('service verified rollback'),
      lifecycleFixture('service remove prepare')
    )

    expect(actual.message).toContain('Currently installed: 2.0.0')
    expect(actual.message).not.toContain('remains installed')
  })

  it('accepts a direct preparation only as review evidence, never as an installation', () => {
    const actual = packageLifecyclePresentation(record('service direct prepare'))
    expect(actual.kind).toBe('review')
    expect(actual.message).not.toMatch(/installed|success|trusted/i)
  })

  it.each(['admission_unknown', 'status_unknown', 'evicted'] as const)('keeps %s history unknown', status => {
    const actual = packageLifecyclePresentation({ ...record('service update confirm'), status })
    expect(actual.message).toContain('State could not be confirmed')
    expect(actual.message).not.toMatch(/version|unchanged|rolled back/i)
  })
})
