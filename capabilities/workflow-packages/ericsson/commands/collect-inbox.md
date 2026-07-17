---
description: Collect recent unread Outlook messages into a bounded artifact
argument-hint: "<since> <limit>"
---
Interpret `$ARGUMENTS` as the requested age window and maximum message count.
Use `message_list` to list unread inbox mail and `message_read` only when a
potentially important result is truncated. Do not send or modify messages.
Write sanitized collected JSON to `$ARTIFACTS_DIR/inbox.json`.

