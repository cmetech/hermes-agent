---
description: Render a concise Jira ticket summary for review
---
Read the upstream `tickets.json` artifact and write
`$ARTIFACTS_DIR/ticket-summary.md`. Group by priority, include key, summary,
status, and any GitLab links on one line per ticket, then add at most three
suggested-focus bullets. Do not post, comment, or send anything.

