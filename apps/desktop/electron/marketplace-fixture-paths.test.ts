import { describe, expect, it } from 'vitest'

import { marketplaceFixturePython } from '../e2e/marketplace-fixture-paths'

describe('marketplace fixture interpreter path', () => {
  it('selects the standard Windows virtualenv executable without a POSIX bin path', () => {
    expect(marketplaceFixturePython('C:\\fixture', 'win32')).toBe('C:\\fixture\\.venv\\Scripts\\python.exe')
  })
  it.each(['darwin', 'linux'] as const)('selects the local POSIX virtualenv on %s', platform => {
    expect(marketplaceFixturePython('/fixture', platform)).toBe('/fixture/.venv/bin/python')
  })
})
