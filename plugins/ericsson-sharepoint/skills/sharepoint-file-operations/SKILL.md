---
name: sharepoint-file-operations
description: Download and perform explicitly approved bounded Ericsson SharePoint file operations.
metadata:
  hermes:
    tags: [ericsson, sharepoint, files]
---

# SharePoint file operations

Confirm Graph readiness using delegated MSAL, app-only, or Azure CLI before
selecting a tool. Downloads must stay beneath the configured download root;
uploads may read only beneath the configured upload root. Report artifact
paths relative to the authorized root with size and digest evidence. Never
follow traversal, symlink, hard-link overwrite, special-file, or unapproved
one-operation boundary expansion.

Use `sharepoint_download` for bounded acquisition. Downloaded files can be
handed to a separate document processing capability, but this connector does
not parse or generate their contents.

Every mutation requires the backend's exact approval immediately before the
call. Show the source, destination, tenant, drive, conflict policy, and proposed
effect. Use `sharepoint_upload`, `sharepoint_create_folder`,
`sharepoint_move_item`, or `sharepoint_copy_item` only after approval. Preserve
operation bounds and cancellation. Do not blindly retry an uncertain write;
inspect or reconcile its remote outcome first.

`sharepoint_recycle_item` moves an item to the SharePoint recycle bin after
approval. Permanent delete is deliberately unavailable. Explain recovery via
the site's recycle bin without claiming recovery is guaranteed indefinitely.
