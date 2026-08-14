---
name: sharepoint-permission-audit
description: Collect bounded Ericsson SharePoint permission evidence through a Hermes-enrolled browser.
metadata:
  hermes:
    tags: [ericsson, sharepoint, permissions]
---

# SharePoint permission audit

Permission auditing has two independent readiness facets. Graph readiness—via
delegated MSAL, app-only, or Azure CLI—supports site discovery and file tools.
`sharepoint_audit_permissions` additionally requires browser enrollment for the
configured Hermes-owned profile and trusted tenant origin. If browser readiness
is absent, report `browser_enrollment_required`; do not imply that Graph file
tools are unavailable.

Ask for exact sites and categories. Set bounded site, page, row, byte, and
deadline limits before calling the audit. Preserve partial, truncated,
unreachable, and per-category failure states. An empty category after an error
is not a successful complete audit.

The core owns browser acquisition and release. Do not start a browser, claim a
debugging endpoint, select an executable, or manage profile processes. Keep
cookies, headers, browser scripts, profile paths, raw response bodies, and
credentials out of results and artifacts. Optional structured evidence must
stay beneath the configured artifact root.

This is read-only collection. It does not change permissions, parse documents,
or perform file mutations, so no write approval substitutes for missing
browser authority.
