# Guide a First Real Run

## Entry

Use after the user understands the capability and has a current readiness result.
The selected capability's product maturity must be available; if readiness has not
been checked, route to the readiness workflow before continuing.

## Load

Load only `../references/capabilities/{selected-capability-id}.md` and
`../references/safety-and-approvals.md`.

## Procedure

1. Continue only when maturity is `available`. Explicitly refuse execution when it
   is `partially-ported`, `planned-not-implemented`, or
   `not-supported-no-port-planned`; explain the missing end-to-end behavior.
2. Verify the current readiness facts. Recheck volatile authentication, dependency,
   permission, and platform facts instead of trusting a saved result.
3. Ask one missing input question at a time. Prefer the smallest useful scope and a
   read, preview, or draft before a write.
4. Render `../templates/first-run-checklist.md` as the target/effect preview,
   containing the exact destination or target, intended effect, data read, possible
   writes, output format/destination, and how the result will be inspected.
5. For any side effect, wait for explicit authorization to execute the previewed action.
   Acknowledging the preview, approving its content, or approving a draft alone is not write authorization.
   A prior request to “test,” general onboarding consent, or configuration consent
   is not authorization to execute.
6. Route the action to the underlying Ericsson capability. Do not recreate domain
   operations in this workflow.
7. After execution, report confirmed effects, artifacts, exclusions, warnings, and
   uncertainty. If interrupted or uncertain, inspect state before considering a
   rerun so duplicate side effects are avoided.

## Checkpoint

Record sanitized target identity, intended effect, authorization status, result category,
safe artifact pointers, warnings, and pending inspection. Do not record message,
ticket, email, or credential content.

## Exit

Exit after a confirmed read/preview, an explicitly authorized real action, a refusal,
or a safe stop. Provide one suggested next prompt for inspection or a scoped rerun.
