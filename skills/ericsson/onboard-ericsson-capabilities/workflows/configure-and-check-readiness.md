# Configure and Check Readiness

## Entry

Use for an available capability when the user asks what to configure, which
requirements are missing, or whether the capability is ready. Explain non-available
maturity directly instead of running readiness checks.

## Load

Load only `../references/capabilities/{selected-capability-id}.md`,
`../references/configuration-and-authentication.md`, and
`../references/safety-and-approvals.md`.

## Procedure

1. Report product maturity first. Continue only when maturity is `available`.
2. Reuse observable profile/platform facts. Classify each requirement as a static
   secret or setting, interactive sign-in, permission, software/platform need, or
   workflow input.
3. Validate in the seven-step order from the safety reference. Stop at the first
   unsafe or unmet boundary and report what is known versus unchecked.
4. A protected setting may be reported only as configured/not configured; never
   print its value and never call that fact authenticated or ready.
5. Ask before installing, starting a server, opening sign-in, or changing
   configuration. Use protected Tools & Keys for secret entry.
6. Prefer the smallest permitted read-only probe. Never use a live write as a test.
7. Render `../templates/readiness-checklist.md` with one state and supporting facts.
   Ask one question about the next user-controlled action.

## Checkpoint

Record only maturity, the readiness state, Boolean/null supporting facts, completed
safe checks, missing actions, and the next safe check. Volatile facts remain
`unknown-needs-check` until actually validated.

## Exit

Exit with `ready`, `missing`, `needs-user-action`, `unavailable-on-platform`, or
`unknown-needs-check`, plus evidence and the next safe action. Do not collapse
configured, authenticated, and read-probe-validated into one fact.
