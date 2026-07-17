import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import { RICH_FENCE_LANGUAGES } from './registry'

describe('workflow topology rendering contract', () => {
  it('routes Mermaid fences through the existing secure rich renderer', () => {
    expect(RICH_FENCE_LANGUAGES.has('mermaid')).toBe(true)
    const source = readFileSync(new URL('./mermaid-embed.tsx', import.meta.url), 'utf8')
    expect(source).toContain("securityLevel: 'strict'")
    expect(source).toContain('SourcePreview')
  })
})
