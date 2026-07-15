# Run a Synthetic Demonstration

## Entry

Use when the selected entry advertises an appropriate demonstration and the user
wants a safe showcase. If no synthetic mode exists, offer only a documented
read-only live mode; never invent a fixture or claim live success.

## Load

Load only `../references/capabilities/{selected-capability-id}.md`,
`../references/demonstration-policy.md`, and
`../references/artifact-interpretation.md`.

## Procedure

1. Confirm the entry is `available` and the requested demonstration mode is listed.
   Refuse execution for partial, planned, or unsupported maturity.
2. Translate the entry's machine mode slug through the one-to-one mapping and show only its user-facing label.
   State that label, fictional fixture, actions, expected
   result, destination, and inspection plan before running anything.
3. Reject real Ericsson customer, employee, ticket, email, opportunity, or project
   data for a showcase. Ask one question only if a safe fixture, mode, or destination
   decision remains.
4. Confirm before creating output. Never overwrite an existing artifact; choose a
   new destination or ask for explicit overwrite direction outside showcase mode.
5. Route execution to the underlying capability or deterministic fixture helper.
6. Compare actual results with expected results, identify differences, and explain
   the artifact location and inspection evidence. Never describe simulation as a
   successful live integration.

## Checkpoint

Record the capability ID, mode label, fictional fixture ID, safe destination,
expected-versus-actual outcome, warnings, and inspection status. Exclude fixture
payloads and generated content.

## Exit

Exit with an honest pass, discrepancy, interruption, or unsupported-mode result;
then offer one next prompt for artifact walkthrough, readiness, or a guided run.
