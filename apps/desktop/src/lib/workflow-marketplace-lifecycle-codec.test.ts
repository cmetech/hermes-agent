import { describe, expect, it } from 'vitest'

import corpus from '../../../../tests/fixtures/workflow-marketplace-lifecycle-v2.json'

const codec = await import('./workflow-marketplace-lifecycle-codec').catch(() => null)

describe('Python lifecycle acceptance parity', () => {
  it('reports actual ambiguous publication as unconfirmed recovery, never rollback or success', () => {
    const operation = corpus.operationCases.find(item => item.name === 'service recovery ambiguous')
    const state = corpus.packageStateCases.find(item => item.name === 'service ambiguous state')
    expect(operation).toBeDefined()
    expect(codec!.decodeLifecycleOperation(operation!.value)).toMatchObject({
      state: 'failed',
      result: null,
      outcome: { type: 'recovery_required', reason: 'recovery_ambiguous' }
    })
    expect(codec!.decodeLifecyclePackageState(state!.value)).toMatchObject({
      state: 'unconfirmed',
      installed: null,
      trust: null
    })
  })
  it('preserves a Python-valid U+FEFF repository identity', () => {
    const value = structuredClone(corpus.operationCases.find(item => item.name === 'service install confirm')!.value)

    const visit = (input: unknown): void => {
      if (!input || typeof input !== 'object') {
        return
      }

      for (const [key, child] of Object.entries(input)) {
        if (key === 'repository_url') {
          ;(input as Record<string, unknown>)[key] = '\ufeffhttps://fixtures.example/workflows.git'
        } else {
          visit(child)
        }
      }
    }

    visit(value)
    expect(codec!.decodeLifecycleOperation(value)).toEqual(value)
  })

  it('has generated strict outer-envelope acceptance cases', () => {
    expect((corpus as unknown as Record<string, unknown>).outerEnvelopeCases).toBeInstanceOf(Array)
  })
  it.each(corpus.outerEnvelopeCases)('$name follows Python outer-envelope acceptance', testCase => {
    const decoders: Record<string, (value: unknown) => unknown> = {
      LifecycleCapabilities: codec!.decodeLifecycleCapabilities,
      AdmissionFound: codec!.decodeLifecycleAdmission,
      AdmissionEvicted: codec!.decodeLifecycleEvicted,
      LifecycleOperationPage: codec!.decodeLifecycleOperationPage
    }

    expect(decoders[testCase.model](testCase.value) !== null).toBe(testCase.accepted)
  })
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
