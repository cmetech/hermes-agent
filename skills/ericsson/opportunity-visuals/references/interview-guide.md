# Interview Guide

Inspect the user's request and available file metadata first. Ask only for
decisions that cannot be safely inferred, one question at a time. Skip every
question whose answer is already known. Do not force the user through a fixed
wizard.

## Conditional question order

Ask for missing decisions in this exact order:

1. **Source:** Obtain a local CSV, JSON, or XLSX path. For pasted tabular data,
   get approval to write it to a temporary local UTF-8 CSV, write only that
   approved temporary file, and inspect the CSV as the source.
2. **View:** Choose wins, losses, all-stage progression, or positive
   progression.
3. **Range:** Confirm the chronological month columns to include.
4. **Mapping:** Confirm Area, Sub-area, Opportunity Name, TCV, Probability, and
   month columns only when aliases are ambiguous.
5. **Semantics:** Confirm terminal aliases, ordered stage paths, and special
   positive transitions needed by this dataset.
6. **Filters:** Accept optional Area, Sub-area, TCV, probability, or opportunity
   filters.
7. **Analysis:** Run the read-only `analyze` command with the proposed choices.
   First resolve each output-affecting unknown terminal status one question at
   a time and rerun. Then resolve each remaining inclusion-affecting direction
   one question at a time and rerun before preparing artifacts.
8. **Output:** Default to 1920×1080 SVG, HTML, and PNG pages. Ask only when the
   user needs a different size, format, or destination.
9. **Confidentiality:** If the data appears sensitive, state that file helpers
   remain local, while minimal stage labels and diagnostics used for the
   interview may enter the model-backed chat. Confirm the output destination
   before writing. Never request pasted confidential rows unless the configured
   model and privacy policy permit them.

Inspection or analysis can reveal another missing decision. Ask the next
unresolved item in this order, then rerun the applicable read-only command.
When multiple XLSX sheets, ambiguous columns or months, unknown terminal
statuses, or unknown directions affect output, resolve only one ambiguity per
question.

## Question patterns

- Source: “Which local CSV, JSON, or XLSX file should I inspect?”
- View: “Should this show wins, losses, all-stage progression, or positive
  progression?”
- Range: “I found monthly stages from March through May. Use all three months?”
- Mapping: “Should `Deal Value` map to TCV?”
- Semantics: “Is Deferred a positive terminal, negative terminal, or
  non-terminal stage?”
- Direction: “For progression, is Discovery → Deferred forward, backward, or neutral?”
- Filters: “Should I limit this to any Area, Sub-area, TCV, probability, or
  opportunity?”
- Output: “Use 1920×1080 SVG, HTML, and PNG pages in the default timestamped
  output directory?”
- Confidentiality: “The file helpers stay local; these minimal stage labels may
  enter this chat. May I use them for the interview and write artifacts to this
  destination?”

When an unknown stage has more than one unresolved property, separate them.
First confirm whether it is positive terminal, negative terminal, or
non-terminal. If it is non-terminal, add it to `non_terminal_stages` and rerun
`analyze`. Only after that rerun may the next turn ask whether the exact
transition is forward, backward, or neutral. Update the confirmed stage path
or edge, rerun `analyze` again, and only then play back the execution summary.

## Playback and confirmation

Before creating artifacts, play back a concise execution summary containing
the source and selected rows, view, range, stage rules, filters, output formats,
dimensions, and destination. Ask the user to confirm. Do not normalize into a
run directory or render until confirmation. If the user corrects an item,
return to that decision, update the playback, and ask for confirmation again.
The analyze step must finish before preparing artifacts. A mixed transition is
reported for review, but every output-impact unknown terminal status and every
inclusion-affecting unknown direction must be resolved and analysis rerun
before playback and confirmation.

## Positive triggers

- “Create an Ericsson opportunity progression infographic from this spreadsheet.”
- “Show me only the opportunities we won this quarter.”
- “Make a losses view from this pipeline data.”
- “Visualize positive opportunity movement from March through May.”
- “Turn this CSV into a 16:9 Ericsson stage-progression visual.”
- “Create a slide-ready view of our TCV, probability, and monthly stages.”
- “Which deals progressed positively, and can you visualize them?”
- “Make the Loop24 opportunity image from this data.”

## Negative examples

Do not activate this skill for these requests. Route generic image requests to
the ordinary image capability, and leave unrelated data requests with their
appropriate tool or skill.

- “Generate an image of a cellular tower at sunset.”
- “Make this headshot look more professional.”
- “What opportunities are assigned to me in Jira?”
- “Create a pie chart from these survey results.”
