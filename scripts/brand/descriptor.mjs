// scripts/brand/descriptor.mjs
import fs from 'node:fs'
import path from 'node:path'

const SLUG_RE = /^[a-z][a-z0-9-]*$/

export function withDefaults(raw) {
  const slug = raw.slug
  const displayName = raw.displayName || slug.toUpperCase()
  const curation = raw.curation || {}
  const skills = curation.skills || {}
  const tools = curation.tools || {}
  const channels = curation.channels || {}
  return {
    slug,
    displayName,
    wordmark: raw.wordmark || `${displayName} AGENT`,
    tagline: raw.tagline || `${displayName} orchestrates your thoughts and tasks into effective outcomes.`,
    appId: raw.appId || `io.cmetech.${slug}`,
    scheme: raw.scheme || slug,
    homeDir: raw.homeDir || `.${slug}`,
    releasesRepo: raw.releasesRepo || `cmetech/${slug}`,
    updateCommand: raw.updateCommand || `${slug} update`,
    theme: raw.theme || 'otto',
    gateway: raw.gateway || 'otto',
    curation: {
      skills: { exclude: skills.exclude || [], disabledByDefault: skills.disabledByDefault || [] },
      tools: { excludeToolsets: tools.excludeToolsets || [], disabledByDefault: tools.disabledByDefault || [] },
      channels: { allow: channels.allow || [] }
    },
    capabilitySets: raw.capabilitySets || [],
    capabilitySources: raw.capabilitySources || {},
    capabilityRequiresEnv: raw.capabilityRequiresEnv || {},
    personaSets: raw.personaSets || [],
    cli: raw.cli || { bannerLogo: '', bannerHero: '' }
  }
}

export function loadDescriptor(slug, { root }) {
  if (!SLUG_RE.test(slug)) {
    throw new Error(`invalid brand slug: ${JSON.stringify(slug)} (must match ${SLUG_RE})`)
  }
  const file = path.join(root, 'brands', `${slug}.json`)
  const raw = JSON.parse(fs.readFileSync(file, 'utf8'))
  if (raw.slug !== slug) {
    throw new Error(`descriptor slug mismatch: file ${slug}.json has slug ${JSON.stringify(raw.slug)}`)
  }
  return withDefaults(raw)
}
