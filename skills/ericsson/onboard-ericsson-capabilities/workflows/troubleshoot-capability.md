# Troubleshoot a Capability

## Entry

Use for a selected capability when readiness, execution, authentication, a
dependency, workflow state, source system, or artifact outcome fails or is unclear.

## Load

Load only `../references/capabilities/{selected-capability-id}.md` and
`../references/troubleshooting-taxonomy.md`.

## Procedure

1. Preserve product maturity as a separate fact. Do not troubleshoot a planned,
   partial, or unsupported flow as though its missing implementation were a runtime
   fault.
2. Classify the symptom using exactly one primary taxonomy category, adding a
   secondary category only when evidence requires it.
3. Gather redacted facts in increasing-risk order. Never ask for or echo a secret,
   authentication response, raw confidential payload, or full diagnostic dump.
4. Ask one question for the smallest missing discriminating fact. Distinguish “not
   checked” from “failed.”
5. Propose the least risky recovery. Ask before installs, server starts, sign-in,
   configuration changes, artifact writes, or side effects.
6. For partial or uncertain side effects, inspect source-system state before any
   rerun. Do not assume failure means nothing changed.
7. Re-run only the narrow failed check and report evidence, remaining uncertainty,
   and a safe escalation summary.

## Checkpoint

Record category, redacted symptom, checks performed, confirmed facts, uncertainty,
recovery attempted, and pending user action. Omit raw logs and source content.

## Exit

Exit when recovery is verified, a safe next user action is identified, or the issue
is accurately classified for escalation without sensitive material.
