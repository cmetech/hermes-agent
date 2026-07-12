// scripts/brand/emitters/skin.mjs
import fs from 'node:fs'
import path from 'node:path'

const FILE = 'hermes_cli/skin_engine.py'

export function hasBrandSkin(source, slug) {
  return new RegExp(`\\n    "${slug}": \\{`).test(source)
}
export function hasActiveSkin(source, slug) {
  return source.includes(`_active_skin_name: str = "${slug}"`)
}

export const skinEmitter = {
  id: 'skin',
  check(d, { root }) {
    const src = fs.readFileSync(path.join(root, FILE), 'utf8')
    const ok = hasBrandSkin(src, d.slug) && hasActiveSkin(src, d.slug)
    return ok ? { ok: true } : { ok: false, detail: `skin/active for ${d.slug} missing` }
  },
  write() {
    throw new Error('skin emitter write is deferred to Plan 2 (skin-dict + theme templating)')
  }
}
