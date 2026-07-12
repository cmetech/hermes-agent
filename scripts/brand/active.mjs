// scripts/brand/active.mjs
//
// Resolves which brand is "active" for the current build/run.
//
// Precedence:
//   1. env OTTO_BRAND (if set and non-empty)
//   2. the committed brand/active marker file (if present and non-empty)
//   3. default: 'otto'
import fs from 'node:fs'
import path from 'node:path'

export function resolveActiveBrand({ root }) {
  const envBrand = process.env.OTTO_BRAND
  if (envBrand && envBrand.trim()) return envBrand.trim()

  const markerPath = path.join(root, 'brand', 'active')
  if (fs.existsSync(markerPath)) {
    const contents = fs.readFileSync(markerPath, 'utf8').trim()
    if (contents) return contents
  }

  return 'otto'
}
