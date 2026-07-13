// scripts/brand/brand-json.mjs
//
// The build-time half of the discoverable brand.json. MUST stay in sync with the Python
// builder in hermes_cli/brand_config.py (brand_json_payload) — same keys, same order.
// Both are asserted against the same key list in their tests.
export const BRAND_JSON_SCHEMA_VERSION = 1

// `descriptor` is a normalized descriptor from descriptor.mjs loadDescriptor (defaults applied).
export function brandJsonPayload(descriptor) {
  return {
    schemaVersion: BRAND_JSON_SCHEMA_VERSION,
    slug: descriptor.slug,
    displayName: descriptor.displayName,
    appId: descriptor.appId,
    scheme: descriptor.scheme,
    schemes: [descriptor.scheme, 'hermes'],
    homeDir: descriptor.homeDir,
    releasesRepo: descriptor.releasesRepo,
    updateCommand: descriptor.updateCommand,
    gateway: descriptor.gateway,
  }
}
