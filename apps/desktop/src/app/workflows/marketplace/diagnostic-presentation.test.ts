import { describe, expect, it } from 'vitest'

import { en } from '@/i18n/en'

import { marketplaceDiagnosticMeaning } from './diagnostic-presentation'
import { lifecycleFixture } from './lifecycle-test-harness'

describe('marketplaceDiagnosticMeaning', () => {
  it('presents backend-generated known diagnostics without exposing protocol codes as copy', () => {
    const install = lifecycleFixture('service install prepare')
    const update = lifecycleFixture('service update prepare')
    if (install.result?.type !== 'install_review' || update.result?.type !== 'update_review') {
      throw new Error('Expected backend-generated review fixtures')
    }

    const expected = new Map([
      ['legacy_language_profile', 'The workflow uses permissive legacy Hermes language rules.'],
      ['missing_provider', 'A required provider is unavailable.'],
      ['missing_runtime', 'A required runtime is unavailable.'],
      ['missing_secret', 'A required secret is unavailable.'],
      ['missing_service', 'A required service is unavailable.'],
      ['missing_tool', 'A required tool is unavailable.'],
      ['legacy_idle_timeout_seconds', 'The legacy idle timeout is interpreted in seconds.']
    ])
    const diagnostics = [...install.result.value.assessment.advisories, ...update.result.value.assessment.advisories]

    for (const diagnostic of diagnostics) {
      const meaning = marketplaceDiagnosticMeaning(en.operations, diagnostic.code)
      expect(meaning).toBe(expected.get(diagnostic.code))
      expect(meaning).not.toContain(diagnostic.code)
      expect(meaning).not.toBe(diagnostic.message)
    }
  })

  it('uses honest localized fallback copy for a generated safe unknown diagnostic', () => {
    const operation = lifecycleFixture('result UTF-8 budget boundary plus 0')
    if (operation.result?.type !== 'package_detail') {
      throw new Error('Expected backend-generated package detail fixture')
    }
    const diagnostic = operation.result.value.advisories[0]

    expect(marketplaceDiagnosticMeaning(en.operations, diagnostic.code)).toBe(
      'Hermes reported a compatibility issue that has no translated description in this version.'
    )
  })
})
