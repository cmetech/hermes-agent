# Artifact Interpretation

## Identify the result

Classify the output as a generated local artifact, draft/preview, conversation
result, downloaded file, or live-system state. Use the selected capability entry as
the contract; do not invent a default path or claim a file exists without checking.

If an artifact destination or identity is ambiguous, ask one question and resolve it
before writing, downloading, opening, or changing anything.

## Explain location and authority

State the confirmed destination or source-system location, output format, and which
file, object, manifest, metadata, or system state is authoritative. Distinguish a
preview from a committed/live result and generated output from reusable fixtures.

## Inspect safely

Explain the smallest safe inspection:

- verify existence, type, timestamp, and expected non-sensitive metadata;
- inspect manifests, counts, hashes, exclusions, warnings, or audit evidence where
  the capability produces them;
- use the source application for drafts and live state;
- avoid printing message bodies, ticket content, credentials, or confidential file
  contents in diagnostics or summaries.

## Interpret outcomes

Describe expected success evidence, exclusions, warnings, partial completion, and
uncertainty. Compare expected with actual rather than declaring success from file
presence alone. When an operation was interrupted, inspect for partial side effects.

## Rerun safely

Preserve the existing artifact. Select a new destination or obtain confirmation
before overwrite, and revalidate changed inputs, filters, format, and target. A safe
checkpoint stores only the artifact pointer and inspection status, never contents.
