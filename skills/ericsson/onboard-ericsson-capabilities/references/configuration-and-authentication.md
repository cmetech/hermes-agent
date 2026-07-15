# Configuration and Authentication

Use the selected capability entry to identify requirements, then keep these five
classes distinct. A configured name is not proof that its value is valid, current,
authorized, or accepted.

## Static secrets and settings

- A static secret is a credential-like value stored only through protected Tools & Keys
  or another approved secret-entry mechanism.
- A static setting is a non-secret endpoint, tenant, public-client override, or
  similar value. It may still be sensitive operational information; follow the
  entry's protected-interface guidance.
- Detect configured/not configured without returning the value. Never ask the user
  to paste, repeat, or print a password, token, cookie, certificate, private key, or
  protected value in ordinary chat.
- Explain the general credential source stated in current documentation. Do not
  guess an organization-specific owner, ticket queue, approval path, or URL.

## Interactive sign-in

- Authentication completed in a browser, device-code page, desktop application, or
  approved native flow is not a static secret.
- Ask before opening or starting sign-in. Tell the user what application/server will
  request access and what success evidence will be checked.
- Do not ask the user to paste device codes or authentication responses into chat.
  A local cache's presence is not proof that a session remains valid.

## Permissions

- Separate successful authentication from authorization to the requested mailbox,
  Jira project, Teams/channel, knowledge source, file, or other resource.
- State only documented permission needs. When access is denied, do not invent an
  Ericsson process; direct the user to the appropriate local owner or support path.

## Software and platform

- Check operating-system support, required local application, package, executable,
  server, network/TLS path, and source-system availability separately.
- Ask before installing a package, launching an application, starting a server, or
  changing local configuration. Offer a supported fallback when the capability
  entry documents one.

## Workflow inputs

- Ordinary inputs include scope, filters, issue IDs, date ranges, limits, source
  files, output formats, and destinations. They are supplied only when the domain
  capability needs them.
- Do not store an input as configuration or treat it as authentication. Resolve an
  ambiguous destination before creating artifacts.

## Readiness evidence

Report each fact independently: discoverable, enabled, platform-supported,
dependency/server available, authentication validated, permission adequate, safe
probe succeeded, preview available, and write path unchecked or explicitly
authorized to execute. Persist required protected-setting presence as
`requiredSettingsConfigured` and permission adequacy as `permissionAdequate`;
both are non-sensitive Boolean/null facts and never contain values. Use
`unknown-needs-check` whenever current evidence is absent.
