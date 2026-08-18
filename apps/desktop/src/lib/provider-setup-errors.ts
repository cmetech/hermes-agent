// Keep the legacy backend token lowercase so the case-sensitive display-brand
// transform cannot rewrite this functional matcher. The `i` flag still accepts
// the title-case "Hermes" message emitted by older/current backends.
const PROVIDER_SETUP_ERROR_RE =
  /No (?:inference|hermes) provider(?: is)? configured|no_provider_configured|set an API key/i

const SESSION_INFO_CREDENTIAL_WARNING_RE = /^No API key configured for provider '[^']*'\. First message will fail\.$/

export function isProviderSetupErrorMessage(message: null | string | undefined): boolean {
  const text = message?.trim()

  if (!text) {
    return false
  }

  return PROVIDER_SETUP_ERROR_RE.test(text) || SESSION_INFO_CREDENTIAL_WARNING_RE.test(text)
}
