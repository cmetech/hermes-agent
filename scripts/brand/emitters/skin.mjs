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

// Template = the current "otto" skin dict block from hermes_cli/skin_engine.py,
// verbatim, with only the brand-varying tokens swapped for placeholders. The
// shared gold palette (`colors`), `spinner`, and `tool_prefix` stay LITERAL —
// they are reused byte-for-byte across brands (see skin-palette.mjs, which
// documents this same palette/spinner but is not imported here on purpose:
// re-serializing it risks drifting from the exact Python formatting below).
//
// Placeholders:
//   __SLUG__           -> descriptor.slug            (dict key + "name")
//   __DESCRIPTION__     -> "${displayName} — gold on black brand theme"
//   __AGENT_NAME__      -> descriptor.displayName
//   __WELCOME__         -> "Welcome to ${displayName}! Type your message or /help for commands."
//   __RESPONSE_LABEL__  -> " ⚕ ${displayName} "
//   __BANNER_LOGO__     -> descriptor.cli.bannerLogo
//   __BANNER_HERO__     -> descriptor.cli.bannerHero
// `goodbye`, `prompt_symbol`, and `help_header` carry no brand word, so they
// stay literal.
const TEMPLATE = `    "__SLUG__": {
        "name": "__SLUG__",
        "description": "__DESCRIPTION__",
        "colors": {
            "banner_border": "#8A6D1A",
            "banner_title": "#FAD22D",
            "banner_accent": "#FFDE5C",
            "banner_dim": "#5C4E14",
            "banner_text": "#FAFAFA",
            "ui_accent": "#FAD22D",
            "ui_label": "#FFDE5C",
            "ui_ok": "#0FC373",
            "ui_error": "#FF3232",
            "ui_warn": "#FF8C0A",
            "prompt": "#FAFAFA",
            "input_rule": "#8A6D1A",
            "response_border": "#C9A227",
            "status_bar_bg": "#1A1500",
            "status_bar_text": "#FAFAFA",
            "status_bar_strong": "#FAD22D",
            "status_bar_dim": "#6E6144",
            "status_bar_good": "#0FC373",
            "status_bar_warn": "#FAD22D",
            "status_bar_bad": "#FF3232",
            "status_bar_critical": "#FF3232",
            "session_label": "#FAD22D",
            "session_border": "#6E6144",
        },
        "spinner": {
            "waiting_faces": ["(◎)", "(◉)", "(⊙)", "(◈)", "(◇)"],
            "thinking_faces": ["(◉)", "(◎)", "(⊙)", "(◈)", "(◇)"],
            "thinking_verbs": [
                "routing", "spinning up", "warming the gateway", "reaching kilo",
                "orchestrating", "wiring it up", "queuing", "syncing",
            ],
            "wings": [
                ["⟪◈", "◈⟫"],
                ["⟪◉", "◉⟫"],
                ["⟪⊙", "⊙⟫"],
                ["⟪◇", "◇⟫"],
            ],
        },
        "branding": {
            "agent_name": "__AGENT_NAME__",
            "welcome": "__WELCOME__",
            "goodbye": "Goodbye! ⚕",
            "response_label": "__RESPONSE_LABEL__",
            "prompt_symbol": "❯",
            "help_header": "(◉‿◉) Available Commands",
        },
        "tool_prefix": "┊",
        "banner_logo": """__BANNER_LOGO__""",
        "banner_hero": """__BANNER_HERO__""",
    },
`

export function renderSkin(d) {
  const bannerLogo = (d.cli && d.cli.bannerLogo) || ''
  const bannerHero = (d.cli && d.cli.bannerHero) || ''
  // Use split/join for every substitution so a literal `$` in a replacement
  // value (a future brand's displayName or banner art) is inserted verbatim —
  // String.prototype.replace would treat `$&`/`$$`/`$'` as special patterns.
  return TEMPLATE
    .split('__SLUG__').join(d.slug)
    .split('__DESCRIPTION__').join(`${d.displayName} — gold on black brand theme`)
    .split('__AGENT_NAME__').join(d.displayName)
    .split('__WELCOME__').join(`Welcome to ${d.displayName}! Type your message or /help for commands.`)
    .split('__RESPONSE_LABEL__').join(` ⚕ ${d.displayName} `)
    .split('__BANNER_LOGO__').join(bannerLogo)
    .split('__BANNER_HERO__').join(bannerHero)
}

// Brace-matches the "<slug>": {...}, block out of a skin_engine.py source
// string, starting at the top-level (4-space-indented) `"<slug>": {` line.
// Includes the trailing comma when present, matching what renderSkin emits.
export function extractSkinBlock(source, slug) {
  const headerRe = new RegExp(`(^|\\n)(    "${slug}": \\{\\n)`)
  const m = headerRe.exec(source)
  if (!m) return null
  const blockStart = m.index + m[1].length
  const openBraceIdx = blockStart + m[2].indexOf('{')

  let depth = 0
  let i = openBraceIdx
  for (; i < source.length; i++) {
    const ch = source[i]
    if (ch === '{') depth++
    else if (ch === '}') {
      depth--
      if (depth === 0) break
    }
  }
  if (depth !== 0) return null // unbalanced; no matching close found

  let end = i + 1
  if (source[end] === ',') end += 1
  if (source[end] === '\n') end += 1
  return source.slice(blockStart, end)
}

export const skinEmitter = {
  id: 'skin',
  check(d, { root }) {
    const src = fs.readFileSync(path.join(root, FILE), 'utf8')
    if (!(hasBrandSkin(src, d.slug) && hasActiveSkin(src, d.slug))) {
      return { ok: false, detail: `skin/active for ${d.slug} missing` }
    }
    const expected = renderSkin(d)
    const actual = extractSkinBlock(src, d.slug)
    if (actual === null) {
      return { ok: false, detail: `could not extract "${d.slug}" skin block from ${FILE}` }
    }
    if (actual !== expected) {
      return { ok: false, detail: diffDetail(expected, actual) }
    }
    return { ok: true }
  },
  write(d, { root }) {
    const file = path.join(root, FILE)
    const src = fs.readFileSync(file, 'utf8')
    const rendered = renderSkin(d)
    const existing = extractSkinBlock(src, d.slug)
    if (existing !== null) {
      if (existing === rendered) return { changed: false, detail: file }
      const idx = src.indexOf(existing)
      const next = src.slice(0, idx) + rendered + src.slice(idx + existing.length)
      fs.writeFileSync(file, next)
      return { changed: true, detail: file }
    }
    // No existing block for this slug: splice a new one in immediately before
    // the "otto" block (or at the top of _BUILTIN_SKINS if "otto" is missing
    // too), so every brand skin lives alongside the others.
    const anchor = extractSkinBlock(src, 'otto')
    const anchorText = anchor !== null ? `    "otto": {` : null
    const insertAt = anchorText !== null ? src.indexOf(anchorText) : null
    if (insertAt === null || insertAt < 0) {
      throw new Error(`skin emitter: could not find an insertion point for "${d.slug}" in ${FILE}`)
    }
    const next = src.slice(0, insertAt) + rendered + src.slice(insertAt)
    fs.writeFileSync(file, next)
    return { changed: true, detail: file }
  }
}

function diffDetail(expected, actual) {
  const expLines = expected.split('\n')
  const actLines = actual.split('\n')
  const max = Math.max(expLines.length, actLines.length)
  for (let i = 0; i < max; i++) {
    if (expLines[i] !== actLines[i]) {
      return `first diff at line ${i + 1}:\n  expected: ${JSON.stringify(expLines[i])}\n  actual:   ${JSON.stringify(actLines[i])}`
    }
  }
  return 'blocks differ (length mismatch beyond compared lines)'
}
