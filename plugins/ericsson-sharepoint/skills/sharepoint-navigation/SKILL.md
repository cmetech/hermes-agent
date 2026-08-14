---
name: sharepoint-navigation
description: Resolve Ericsson SharePoint links and perform bounded folder, file, and owned-site discovery.
metadata:
  hermes:
    tags: [ericsson, sharepoint, navigation]
---

# SharePoint navigation

Use this skill only after the `ericsson-sharepoint` plugin is enabled in a
fresh conversation. The toolset needs Graph readiness through one configured
identity: delegated MSAL, app-only, or Azure CLI. An enrolled browser is not
needed for file navigation.

Start with `sharepoint_resolve_url`. Accept only the configured tenant's HTTPS
SharePoint URL. Use its normalized site, drive, and item identity for later
calls rather than reconstructing Graph paths. Use `sharepoint_get_item` for one
item and `sharepoint_list_items` for bounded discovery. For owned sites, use
`sharepoint_list_owned_sites` with conservative page and item limits.

Ask before broadening a direct file URL to its parent folder. For recursive
listing, state the requested depth, item, page, byte, and deadline bounds.
Basename globs match names; patterns containing `/` match relative paths.
Treat truncation and per-item warnings as partial results, never as complete
enumeration.

For document intake, select only the requested artifacts and hand them to the
file-operations skill. The connector does not parse, OCR, interpret, convert,
or generate documents; document processing is a separate capability.
