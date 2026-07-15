# Interpret Artifacts

## Entry

Use when the user wants to locate, inspect, understand, or validate the result of a
selected capability.

## Load

Load only `../references/capabilities/{selected-capability-id}.md` and
`../references/artifact-interpretation.md`.

## Procedure

1. Identify the artifact type and whether it is generated output, a draft/preview,
   live-system state, or an optional user-requested summary.
2. Reuse a known safe pointer. If destination or artifact identity is ambiguous,
   ask one clarifying question before reading or writing anything.
3. Explain where the result is stored, which file/state is authoritative, and how
   to inspect it without exposing sensitive payloads.
4. Compare the result with expected success evidence. Explain exclusions, warnings,
   partial completion, and supporting manifests or metadata.
5. Describe a safe rerun that preserves existing output. Do not overwrite or mutate
   the artifact merely to prove it can be found.

## Checkpoint

Record only the capability ID, safe artifact pointer, artifact kind, inspection
status, exclusions/warnings, and next action. Do not save artifact contents.

## Exit

Exit when the user can locate and validate the result, or with one precise question
or troubleshooting route when the artifact cannot be resolved safely.
