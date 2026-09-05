import { expect, expectTypeOf, it } from 'vitest'

import { decodeLifecycleOperation } from '@/lib/workflow-marketplace-lifecycle-codec'

import corpus from '../../../../tests/fixtures/workflow-marketplace-lifecycle-v2.json'

import type { LifecycleOperation, TrustGrantResult } from './workflow-marketplace-lifecycle'

it('narrows decoded success by kind and excludes results from nonterminal states', () => {
  // Break caught by typecheck: allowing an unrelated result after discriminant checks.
  const value = decodeLifecycleOperation(
    corpus.operationCases.find(testCase => testCase.name === 'service grant one A')!.value
  )

  expect(value?.kind).toBe('trust_confirm')

  if (value?.state === 'succeeded' && value.kind === 'trust_confirm') {
    expectTypeOf(value.result).toEqualTypeOf<TrustGrantResult>()
    expect(value.result.value.workflows.map(item => item.state)).toEqual(['trusted', 'untrusted'])
  }

  expectTypeOf<Extract<LifecycleOperation, { state: 'pending' }>['result']>().toEqualTypeOf<null>()
})
