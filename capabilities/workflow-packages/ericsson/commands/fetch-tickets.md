---
description: Fetch open Jira tickets into an immutable run artifact
argument-hint: "[delivery=chat|email] [recipient]"
---
Use `jira_my_tickets` to fetch at most 25 open tickets for the authenticated
user. Write the returned JSON to `$ARTIFACTS_DIR/tickets.json`. Treat
`$ARGUMENTS` only as delivery preferences for later nodes; never include a
credential value in an artifact.

