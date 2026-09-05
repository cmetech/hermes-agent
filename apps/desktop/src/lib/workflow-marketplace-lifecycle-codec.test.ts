import { describe, expect, it } from 'vitest'

import corpus from '../../../../tests/fixtures/workflow-marketplace-lifecycle-v2.json'

const codec = await import('./workflow-marketplace-lifecycle-codec').catch(() => null)

describe('Python lifecycle acceptance parity', () => {
  // Break caught: dropped Python union, field, bound or relationship validation.
  it.each(corpus.operationCases)('$name follows Python acceptance', testCase => {
    expect(codec?.decodeLifecycleOperation).toBeTypeOf('function')
    expect(codec!.decodeLifecycleOperation(testCase.value) !== null).toBe(testCase.accepted)
  })

  it.each(corpus.packageStateCases)('$name follows Python package-state acceptance', testCase => {
    expect(codec?.decodeLifecyclePackageState).toBeTypeOf('function')
    expect(codec!.decodeLifecyclePackageState(testCase.value) !== null).toBe(testCase.accepted)
  })

  it('returns independent objects and rejects accessors, cycles and nonfinite JSON', () => {
    expect(codec?.decodeLifecycleOperation).toBeTypeOf('function')
    const value = structuredClone(corpus.validOperationA)
    const decoded = codec!.decodeLifecycleOperation(value)
    expect(decoded).not.toBe(value)
    expect(decoded?.subject).not.toBe(value.subject)
    Object.defineProperty(value, 'progress', {
      get: () => {
        throw new Error('untrusted getter')
      }
    })
    expect(codec!.decodeLifecycleOperation(value)).toBeNull()
    expect(codec!.decodeLifecycleOperation({ ...corpus.validOperationA, progress: Infinity })).toBeNull()
  })

  it('rejects duplicate operation pages and incoherent continuation metadata', () => {
    expect(codec?.decodeLifecycleOperationPage).toBeTypeOf('function')
    expect(
      codec!.decodeLifecycleOperationPage({
        items: [corpus.validOperationA, corpus.validOperationA],
        complete: true,
        next_cursor: null
      })
    ).toBeNull()
    expect(
      codec!.decodeLifecycleOperationPage({
        items: [corpus.validOperationA],
        complete: true,
        next_cursor: 'a'.repeat(32)
      })
    ).toBeNull()
    expect(
      codec!.decodeLifecycleOperationPage({
        items: [corpus.validOperationA],
        complete: false,
        next_cursor: 'a'.repeat(32)
      })
    ).not.toBeNull()
  })
})
