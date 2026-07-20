import { existsSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { RICH_FENCE_LANGUAGES } from './registry'

const TEST_DIRECTORY = dirname(fileURLToPath(import.meta.url))

describe('workflow topology rendering contract', () => {
  it('routes Mermaid fences through the existing secure rich renderer', () => {
    expect(RICH_FENCE_LANGUAGES.has('mermaid')).toBe(true)
    const source = readFileSync(resolve(TEST_DIRECTORY, 'mermaid-embed.tsx'), 'utf8')
    expect(source).toContain("securityLevel: 'strict'")
    expect(source).toContain('SourcePreview')
  })

  it('routes the workflow View modal through that same boundary and renderer', () => {
    const indexSource = readFileSync(resolve(TEST_DIRECTORY, '../../../app/workflows/index.tsx'), 'utf8')
    const modalPath = resolve(TEST_DIRECTORY, '../../../app/workflows/view-workflow-dialog.tsx')
    const modalSource = existsSync(modalPath) ? readFileSync(modalPath, 'utf8') : ''

    expect(indexSource).toContain('ViewWorkflowDialog')
    expect(modalSource).toContain("import('@/components/assistant-ui/embeds/mermaid-embed')")
    expect(modalSource).toContain('RichBoundary')
    expect(modalSource).not.toContain('mermaid.initialize')
    expect(modalSource).not.toContain('securityLevel:')
  })
})
