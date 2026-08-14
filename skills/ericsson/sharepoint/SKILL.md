---
name: sharepoint
description: Discover and route Ericsson SharePoint requests to qualified connector skills.
metadata:
  hermes:
    tags: [ericsson, sharepoint, router]
---

# Ericsson SharePoint router

The `ericsson-sharepoint` plugin is disabled by default. This thin router stays
discoverable, but it contains no Graph or browser implementation and does not
claim the connector is ready.

If the plugin is disabled, explain how to enable and configure it through the
normal plugin surface. Enabling it or changing tool configuration changes the
available tool schema, so continue in a fresh conversation. Do not attempt to
load a plugin-owned skill until the plugin is enabled.

Once enabled, load the one qualified skill that matches the request and read it
completely:

- `ericsson-sharepoint:sharepoint-navigation` for URL resolution, bounded
  listing, item metadata, and owned-site discovery;
- `ericsson-sharepoint:sharepoint-file-operations` for bounded downloads and
  explicitly approved upload, folder, move, copy, or recycle operations; or
- `ericsson-sharepoint:sharepoint-permission-audit` for browser-authorized,
  bounded permission evidence.

Graph readiness and browser-audit readiness are separate. Never ask for tokens,
cookies, device codes, or client secrets in chat.
