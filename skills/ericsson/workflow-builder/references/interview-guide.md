# Interview guide

Ask ONE question per message. Prefer multiple choice. Suggested order —
skip anything the user already answered:

1. Trigger: on-demand from chat, on a schedule, or both? (If scheduled: when?)
2. First step: where does the data come from? (Offer: Jira tickets, Outlook
   inbox/calendar, Teams channels, Glean search, a file the user provides.)
3. Processing: what should be produced from it (digest, report, spreadsheet,
   draft reply, …) and in what format/file?
4. Branches: does anything happen only sometimes? ("only if there are
   unread…", "only when deliver_to=email") → `when:` + an input.
5. Outward actions: send/post/comment anywhere? → mark `side_effects: true`
   and ask whether the user wants an approval gate before it (default YES).
6. Inputs: what should be tweakable per run (defaults!)?
7. Delivery: where does the final result go (chat, email, Teams channel)?
8. Name it: propose a short slug; confirm.

Then play back the flow in plain words and iterate before writing YAML.
