import { Switch } from '@/components/ui/switch'

// OTTO: a switch for `browser.default_profile`, which holds a profile NAME.
//
// Declaring the key `type: 'boolean'` would route it to the generic Switch
// branch in config-field.tsx, which writes `true` — and the backend then looks
// up a profile called "True" (default_profile_name coerces with str()), finds
// none, and trusts nothing. It fails closed, but for the wrong reason and
// without telling anyone. So the mapping is explicit in both directions.
//
// Off writes an empty string rather than deleting or writing `false`, because
// empty is precisely what the backend reads as "no profile":
// `default_profile_name()` returns `name or None`
// (tools/browser_session_registry.py).
//
// Turning this ON is what routes the agent's unbound browsing sessions through
// the corporate browser. Trust stays origin-scoped either way — the profile's
// trusted_origins still decide which hosts are reachable, and cloud-metadata
// endpoints are blocked regardless.
//
// Design: docs/plans/2026-07-26-enrolled-browser-profile-seeding-design.md

export const ENROLLED_PROFILE_NAME = 'enrolled'

interface EnrolledProfileFieldProps {
  onChange: (value: string) => void
  value: unknown
}

export function EnrolledProfileField({ onChange, value }: EnrolledProfileFieldProps) {
  return (
    <div className="flex items-center justify-end">
      <Switch
        checked={value === ENROLLED_PROFILE_NAME}
        onCheckedChange={checked => onChange(checked ? ENROLLED_PROFILE_NAME : '')}
      />
    </div>
  )
}
