import { describe, expect, it } from 'vitest'

import { filterDesktopCommandsCatalog, isDesktopSlashCommand, isDesktopSlashSuggestion } from './desktop-slash-commands'

describe('workflow skill command', () => {
  it('remains visible and executable as an extension command', () => {
    expect(isDesktopSlashSuggestion('/workflow')).toBe(true)
    expect(isDesktopSlashCommand('/workflow')).toBe(true)
    expect(filterDesktopCommandsCatalog({ pairs: [['/workflow', 'Operate workflows']], skill_count: 1 }).pairs)
      .toEqual([['/workflow', 'Operate workflows']])
  })
})
