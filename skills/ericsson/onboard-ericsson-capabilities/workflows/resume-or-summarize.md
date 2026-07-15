# Resume or Summarize Onboarding

## Entry

Use when the user asks to pause, summarize, resume, restart, complete, or forget an
Ericsson onboarding journey. State persistence is a separate implementation
contract; do not claim a journey was saved unless the profile-scoped state operation
actually succeeded after consent.

## Load

Load `../references/catalog.json` first. Only when resuming selected capability work,
load `../references/capabilities/{selected-capability-id}.md`, one entry at a time.
Load `../references/configuration-and-authentication.md` and
`../references/safety-and-approvals.md` only when rechecking volatile readiness.

## Procedure

1. For a summary, render `../templates/session-handoff.md` with the stable selected-
   capability, maturity, readiness-fact, completed-step, pending-action, safe-
   artifact-pointer, and next-prompt fields. Exclude credentials, authentication
   responses, source records, raw diagnostics, pasted files, and transcript text.
2. Before the first persistent checkpoint, ask one consent question. Without
   consent, keep the summary in conversation only. Never claim persistence based on
   intent alone.
3. For resume, compare the saved catalog version with the current catalog. Resolve
   renamed, removed, or maturity-changed IDs before continuing.
4. Load focused entries only for selected IDs, one at a time as needed. Re-report
   current product maturity before live readiness.
5. Recheck volatile discoverability, enablement, platform, dependency,
   authentication, permission, and safe-probe facts. Saved `ready` never survives as
   proof; use `unknown-needs-check` until revalidated.
6. Ask one question for the next pending choice. For restart, complete, or forget,
   confirm the requested state action and report its actual outcome.

## Checkpoint

The checkpoint contains only schema/catalog version, selected IDs, maturity,
readiness facts, completed steps, pending actions, safe pointers, next prompt, and
timestamps. This workflow defines the contract but does not itself implement state
persistence.

## Exit

Exit with a concise handoff or route to one selected workflow. State whether the
journey is unsaved, saved, completed, cleared, stale, or resumed only when that fact
is known.
