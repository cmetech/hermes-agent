// scripts/brand/emitters/provider.mjs
import fs from 'node:fs'
import path from 'node:path'

export function renderProvider(d) {
  const init = `"""${d.displayName} gateway provider profile.

Routes chat completions through the ${d.displayName} gateway (an OpenAI-compatible Go
service) which forwards to Kilo / \`\`kiro-cli\`\` via ACP. See the otto-gateway
repo for the server side.

Endpoint / auth:
  - OpenAI-compatible. Default base URL \`\`http://127.0.0.1:18080/v1\`\`
    (override with \`\`OTTO_BASE_URL\`\`).
  - Bearer auth via \`\`OTTO_API_KEY\`\`. The gateway may run WITHOUT \`\`AUTH_TOKEN\`\`
    (no auth). The provider declaration opts into the SDK's non-secret
    placeholder behavior; set a real \`\`OTTO_API_KEY\`\` only if the gateway was
    launched with \`\`AUTH_TOKEN\`\`.
  - Model \`\`auto\`\` (the safe default) lets the gateway use kiro's current model;
    \`\`fetch_models\`\` lists the live catalog from \`\`/v1/models\`\`.
  - Verified model metadata comes from the gateway's
    \`\`/v1/model-capabilities\`\` endpoint.

Adding this directory is the entire wiring: the loader enumerates
plugins/model-providers/, \`\`register_provider\`\` self-registers the profile, and
hermes_cli/auth.py auto-extends PROVIDER_REGISTRY from any api_key provider
(deriving the key var from \`\`OTTO_API_KEY\`\` and the base-URL override from
\`\`OTTO_BASE_URL\`\`). No edits to the shared provider machinery required.
"""

from providers import register_provider
from providers.base import ProviderProfile


${d.slug} = ProviderProfile(
    name="${d.slug}",
    aliases=("${d.slug}-gateway",),
    display_name="${d.displayName} Gateway",
    description="${d.displayName} gateway → Kilo (kiro-cli)",
    # OTTO_API_KEY → bearer token; OTTO_BASE_URL → base URL override.
    # auth.py's registry auto-merge splits *_URL/*_BASE_URL vars out of the key
    # list, so OTTO_BASE_URL becomes base_url_env_var automatically.
    env_vars=("OTTO_API_KEY", "OTTO_BASE_URL"),
    base_url="http://127.0.0.1:18080/v1",
    auth_type="api_key",
    supports_unauthenticated=True,
    model_capabilities_path="model-capabilities",
    otto_tool_contract_version="v1",
    # Safe default; the picker also shows live ids from GET /v1/models.
    fallback_models=("auto",),
    # The gateway reports honest-zero usage and does not cap output itself;
    # give a generous floor so responses aren't truncated when the user hasn't
    # set model.max_tokens (mirrors the custom/local provider).
    default_max_tokens=65536,
)

register_provider(${d.slug})
`
  const yaml = `name: ${d.slug}-provider
kind: model-provider
version: 1.0.0
description: ${d.displayName} gateway → Kilo (kiro-cli), OpenAI-compatible
author: cmetech
`
  return { '__init__.py': init, 'plugin.yaml': yaml }
}

export const providerEmitter = {
  id: 'provider',
  check(d, { root }) {
    const rendered = renderProvider(d)
    for (const [name, content] of Object.entries(rendered)) {
      const p = path.join(root, 'plugins/model-providers', d.slug, name)
      if (!fs.existsSync(p) || fs.readFileSync(p, 'utf8') !== content) {
        return { ok: false, detail: `${p} missing or differs` }
      }
    }
    return { ok: true }
  },
  write(d, { root }) {
    const dir = path.join(root, 'plugins/model-providers', d.slug)
    fs.mkdirSync(dir, { recursive: true })
    let changed = false
    for (const [name, content] of Object.entries(renderProvider(d))) {
      const p = path.join(dir, name)
      const prev = fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : null
      if (prev !== content) { fs.writeFileSync(p, content); changed = true }
    }
    return { changed, detail: dir }
  },
  // Inverse of write(): the neutral/upstream state has no
  // plugins/model-providers/<slug>/ directory at all (the provider is a
  // pure additive new-file — see the OTTO customization surface table).
  // Guarded no-op if already absent.
  neutralize(d, { root, dryRun = false } = {}) {
    const dir = path.join(root, 'plugins/model-providers', d.slug)
    if (!fs.existsSync(dir)) return { changed: false, detail: dir }
    if (!dryRun) fs.rmSync(dir, { recursive: true, force: true })
    return { changed: true, detail: dir }
  }
}
