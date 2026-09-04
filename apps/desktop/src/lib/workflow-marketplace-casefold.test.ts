import { describe, expect, it } from 'vitest'

import { workflowMarketplaceCanonicalIdentity } from './workflow-marketplace-casefold'
import {
  WORKFLOW_MARKETPLACE_CANONICAL_PARITY_CASES,
  WORKFLOW_MARKETPLACE_CASEFOLD_ENTRIES,
  WORKFLOW_MARKETPLACE_UNICODE_VERSION
} from './workflow-marketplace-casefold.generated'

describe('workflow marketplace canonical identity', () => {
  it('pins the backend Python Unicode version and matches every generated parity case', () => {
    expect(WORKFLOW_MARKETPLACE_UNICODE_VERSION).toBe('14.0.0')

    for (const [source, expected] of WORKFLOW_MARKETPLACE_CANONICAL_PARITY_CASES) {
      expect(workflowMarketplaceCanonicalIdentity(source), JSON.stringify(source)).toBe(expected)
    }
  })

  it('covers every full casefold override, including multi-codepoint mappings', () => {
    for (const [source, expected] of WORKFLOW_MARKETPLACE_CASEFOLD_ENTRIES) {
      if (source.normalize('NFC') === source) {
        expect(workflowMarketplaceCanonicalIdentity(source), JSON.stringify(source)).toBe(expected)
      }
    }
  })

  it('matches Python identity semantics for boundary scripts and normalization', () => {
    expect(workflowMarketplaceCanonicalIdentity('ı')).toBe('ı')
    expect(workflowMarketplaceCanonicalIdentity('i')).toBe('i')
    expect(workflowMarketplaceCanonicalIdentity('ı')).not.toBe(workflowMarketplaceCanonicalIdentity('i'))
    expect(workflowMarketplaceCanonicalIdentity('ß')).toBe(workflowMarketplaceCanonicalIdentity('ss'))
    expect(workflowMarketplaceCanonicalIdentity('Σ')).toBe('σ')
    expect(workflowMarketplaceCanonicalIdentity('σ')).toBe('σ')
    expect(workflowMarketplaceCanonicalIdentity('ς')).toBe('σ')
    expect(workflowMarketplaceCanonicalIdentity('e\u0301')).toBe(workflowMarketplaceCanonicalIdentity('é'))
    expect(workflowMarketplaceCanonicalIdentity('Ꭰ')).toBe('Ꭰ')
    expect(workflowMarketplaceCanonicalIdentity('ꭰ')).toBe('Ꭰ')
    expect(workflowMarketplaceCanonicalIdentity('𐐀')).toBe('𐐨')
    expect(workflowMarketplaceCanonicalIdentity('\u0315\u0300A')).toBe('\u0300\u0315a')
    expect(workflowMarketplaceCanonicalIdentity('A\u0315\u0300')).toBe('à\u0315')
    expect(workflowMarketplaceCanonicalIdentity('\u1100\u1161\u11a8')).toBe('각')
    expect(workflowMarketplaceCanonicalIdentity('\u1e0a\u0323')).toBe('\u1e0d\u0307')
  })
})
