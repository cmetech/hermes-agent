# Capability Readiness Checklist

- **Capability:** [display name and stable ID]
- **Product maturity:** [available, partially-ported, planned-not-implemented, or
  not-supported-no-port-planned]
- **Readiness state:** unknown-needs-check
- **Supporting facts:**
  - Packaged and discoverable: [true/false/unchecked]
  - Enabled: [true/false/unchecked]
  - Platform supported: [true/false/unchecked]
  - Required protected settings configured (values never shown): [true/false/unchecked]
  - Required permission adequate: [true/false/unchecked]
  - Dependency or server available: [true/false/unchecked]
  - Authentication validated: [true/false/unchecked]
  - Read-only probe succeeded: [true/false/unchecked]
  - Draft/preview available: [true/false/unchecked]
  - Write path: [not requested/previewed/explicitly authorized to execute]
- **Missing user actions:** [sanitized action or none]
- **Next safe check:** [one check or none]

Configuration presence alone never changes the default readiness state.
Profile-scoped persistence must keep these facts distinct as Boolean/null fields.
The stable machine names for the two facts above are
`requiredSettingsConfigured` and `permissionAdequate`; the persistence schema and
implementation must use those names exactly.
