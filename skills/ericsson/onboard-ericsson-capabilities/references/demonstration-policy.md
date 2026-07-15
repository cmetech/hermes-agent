# Demonstration Policy

## Machine-to-user mode mapping

- `synthetic-offline` maps to `synthetic/offline`.
- `simulated` maps to `simulated`.
- `read-only-live` maps to `read-only live`.
- `approved-live` maps to `approved live`.

This mapping is one-to-one. Catalog entries store the machine slug; every user-facing
explanation, preview, checkpoint, and summary uses only the mapped label.

## Approved user-facing labels

- **synthetic/offline**: deterministic fictional fixture; no live integration.
- **simulated**: behavior or response is modeled and explicitly not live.
- **read-only live**: current authorized source data is read without a write.
- **approved live**: a real operation occurs only after readiness, target/effect
  preview, and explicit authorization to execute that previewed action; it is not a
  configuration test.

Use only a mode advertised by the selected capability entry.
Never present a simulation as successful live integration, and never substitute an
undocumented live action because no fixture exists.

## Fixture rules

Synthetic fixtures must be fictional, clearly labeled, deterministic, reusable, and
separate from generated output. They must not contain real Ericsson customer,
employee, ticket, email, project, opportunity, or confidential data. Reject or
replace real data offered for a showcase. Reuse an existing approved showcase
fixture before creating another.

## Before running

State the mode, fixture, actions, expected result, exclusions/warnings to expect,
artifact destination, and inspection plan. Resolve ambiguity and ask before writing.
Prefer offline, then read-only live. A useful synthetic demonstration must not
require live credentials.

## After running

Perform an expected-versus-actual comparison. Report matching evidence,
discrepancies, warnings, exclusions, interruption, and any uncertain side effect.
Show where output was written and how to inspect it. Do not fake a success when a
server, credential, or live system was unavailable.

## Output isolation

Keep reusable fixtures read-only and place generated demonstration output in a new,
clearly named destination. Never overwrite existing artifacts without confirmation.
Do not treat fixture content as an artifact pointer or save generated content in the
onboarding checkpoint.
