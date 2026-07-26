# Seed the enrolled browser profile, and make activating it one click

Date: 2026-07-26
Status: approved, not yet implemented
Affected: `capabilities/ericsson.json`, `hermes_cli/capability_staging.py`,
`apps/desktop/src/app/settings/{enrolled-profile-field.tsx,config-field.tsx,constants.ts}`

## Problem

Reaching an internal Ericsson site with the agent currently requires a manual
YAML edit. The enrolled-browser checkpoint runbook's Step 2 is:

```bash
otto config edit
```

followed by hand-writing a `browser.profiles.enrolled` block, correctly indented
under `browser:`, with `trusted_origins` as a genuine YAML **list**. `config set`
cannot create a list, so there is no CLI shortcut. Getting it wrong is silent:
`browser_profiles.py` fails closed on a non-list and logs a warning nobody reads,
so the profile trusts nothing and every subsequent step fails with a
private-address block that looks like an mTLS or hostname problem.

That is unacceptable friction for a corporate rollout where the goal is install
and go.

## Decisions

Each of these was chosen explicitly rather than defaulted into.

**Defined but not activated.** A fresh install gets the profile *and* its
`trusted_origins`, but **not** `browser.default_profile`. The capability stays
inert until a user turns it on. Shipping it active would mean every unbound agent
browsing session resolves to the corporate profile, so arbitrary external pages
would share a browser identity with live SSO cookies. Keeping the disposable
browser as the default preserves that isolation.

**Origins: `https://*.ericsson.com` and `https://*.ericsson.net`.** Two entries,
not three: the matcher (`browser_profiles.py:216-222`) treats `*.ericsson.com` as
any strict subdomain of `.ericsson.com`, which already covers
`eteamspace.internal.ericsson.com`, so a separate `*.internal.ericsson.com` entry
would be redundant. Wildcards match strict subdomains only — never the apex,
never a lookalike such as `evilericsson.com`.

**Activation is a real toggle**, not a text field. See below.

**The values live in the capability manifest**, not the brand descriptors. These
origins are Ericsson capability content, not brand identity; both brands are the
same Ericsson product, so one copy serves both and re-vendoring updates it.
Accepted cost: a future non-Ericsson brand built from `base` would inherit the
vendored manifest and be seeded these origins. If that ever happens, the gate is
the descriptor's existing `capabilitySets` field.

## Design

### 1. The values — `capabilities/ericsson.json`

A new `configDefaults` key carrying the block to seed:

```json
"configDefaults": {
  "browser": {
    "profiles": {
      "enrolled": {
        "kind": "enrolled",
        "executable": "auto",
        "user_data_dir": "${HERMES_HOME}/browser-profiles/enrolled",
        "cdp_port": 9222,
        "headed": true,
        "trusted_origins": ["https://*.ericsson.com", "https://*.ericsson.net"]
      }
    }
  }
}
```

Manifests are located exactly as `_inject_capability_env_vars` does
(`config.py:9448-9452`): `<repo root>/capabilities/*.json`, fail-safe, skipping
anything unparseable.

`browser.default_profile` is seeded as an **empty string** — present, but not
activated.

Both halves matter, and v4.2.0 shipped only one of them. Not `"enrolled"`,
because that would route every unbound agent browsing session through the
corporate profile. But not *omitted* either: `sectionFieldEntries` renders a key
only when the served schema declares it or the config already holds a value, and
this key is in neither (it is absent from `DEFAULT_CONFIG`, hence from
`CONFIG_SCHEMA`). Omitting it meant the activation switch never rendered, leaving
the feature enable-able only by the hand-editing it existed to remove.

Empty keeps it inert: `default_profile_name()` returns `name or None`.

### 2. The seeding — `seed_brand_defaults`

Extended, not replaced. It already has precisely the right semantics
(`capability_staging.py:838-885`): read a marker, write through
`load_config`/`save_config` under a `set_hermes_home_override` token so config IO
targets the home being seeded, record what was seeded, never reconsider it.

- The marker gains a third key beside `skills` and `toolsets`, recording which
  `configDefaults` paths have been seeded. `BRAND_DEFAULTS_SCHEMA_VERSION` stays
  1; the new key is additive and its absence reads as "nothing seeded yet".
- Seeding is **seed-once per leaf path** and never overwrites an existing value.
  A user who deletes an origin does not get it back on next launch; a user who
  configured the profile by hand keeps their values untouched.
- Existing installs pick this up on their next launch, which is what makes it
  work for the fleet already deployed rather than only for new installs.
- Fail-safe, like every other step in `run_brand_startup`.

Seed-once is the load-bearing property. Capability staging deliberately
re-applies managed content on a version bump; that is right for MCP definitions
and wrong for a security list a user may have deliberately trimmed.

### 3. The toggle

`browser.default_profile` is a profile **name**, so it cannot simply be marked
`type: 'boolean'` — the `Switch` would write `true`, and the backend would look
up a profile called `"True"`, find none, and silently trust nothing. It fails
closed, but for the wrong reason.

Instead, follow the precedent already in the file. `config-field.tsx:82`
dispatches one specific key to a dedicated component:

```tsx
if (schemaKey === 'fallback_providers') {
  return row(<FallbackModelsField onChange={onChange} value={value} />, true)
}
```

A new `enrolled-profile-field.tsx` renders a `Switch` reading `value ===
'enrolled'` and writing `'enrolled'` on / `''` off, with one matching branch in
`config-field.tsx`. Empty string is the correct "off": `default_profile_name`
(`browser_session_registry.py:80`) returns `name or None`.

`FIELD_LABELS` / `FIELD_DESCRIPTIONS` entries for the key already exist in
`constants.ts`; the copy changes to read as a switch rather than a text box.

## Preservation across upstream merges

`capabilities/ericsson.json` and `enrolled-profile-field.tsx` are absent upstream
and cannot conflict. `capability_staging.py` is an OTTO-new module.

The only shared upstream files touched are `config-field.tsx` (one import, one
branch — the same shape as an edit already living there) and `constants.ts`
(copy only, already an OTTO-owned surface per the browser-profiles ledger).

Both edits are additive and greppable, and both are covered by tests that fail if
they are dropped. A ledger entry is added to the existing
`docs/upstream-customizations/browser-profiles.yaml` rather than creating a new
manifest, since this extends that same capability.

## Testing

Python, extending `tests/hermes_cli/test_brand_defaults_seed.py`:

- A fresh home gets the profile and both origins seeded, with
  `trusted_origins` a genuine **list** — the exact failure the runbook warns
  about.
- `browser.default_profile` IS written, as an empty string, so the Settings
  switch renders in the off position. Asserted as present-and-empty, never as
  absent — absence is the v4.2.0 defect.
- A user value already present is never overwritten.
- A value the user deleted after seeding is not restored (the marker is honoured).
- A malformed or missing manifest seeds nothing and does not raise.

Desktop, in a new `enrolled-profile-field.test.tsx`:

- Renders a switch, on when the value is `enrolled`, off otherwise.
- Toggling on writes `enrolled`; toggling off writes a value that
  `default_profile_name` resolves to "no profile".

## Known blocker

This delivers no user-visible capability until the absent browser tool is
resolved: the agent currently reports it cannot browse at all, so the profile
this seeds cannot yet be exercised. Seeding is still correct work — it removes a
manual step that would otherwise be required the moment the tool returns — but it
should not be described as making the enrolled browser work.

## Out of scope

- Why the browser tool is absent in chat.
- The runbook's remaining steps.
- Any change to `browser.allow_private_urls`, the blunt global switch this
  feature exists to replace.
