---
name: onboard-ericsson-capabilities
description: Onboard users to Ericsson Co-Worker capabilities.
version: 1.0.0
author: Ericsson (cmetech)
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [Ericsson, onboarding, training, configuration, readiness]
---

# Ericsson Capability Onboarding

## When to Use

Use this skill when a user asks to discover, choose, learn, configure, validate,
demonstrate, interpret, troubleshoot, or resume onboarding for Ericsson
capabilities. Welcome them briefly, then help like a knowledgeable coworker.

Do not intercept an already clear domain request. A request such as “Summarize my
assigned Jira tickets” proceeds directly to the underlying Ericsson capability
unless the user asks to learn, configure, validate, demonstrate, interpret,
troubleshoot, or resume. Route general Co-Worker installation, platform setup,
Skills, Tools & Keys, MCP, or diagnostics to the existing `hermes-agent` skill.

## Prerequisites

Start by reading only `references/catalog.json`. It is the compact routing index,
not evidence of live readiness. Do not load every focused capability entry.

Treat product maturity and runtime readiness as different facts. Runtime state can
change and must be checked in the active profile. Never infer `ready` from a named
setting, installed-file declaration, saved checkpoint, or documentation alone.

## How to Run

Ask one question at a time and reuse the user's request, prior answers, observable
environment, and consent choices. Begin with the user's role, goal, or desired
outcome; never begin with the full capability list. Recommend at most two
capabilities and explain why each matches.

Initial intake and the first recommendation load only `references/catalog.json`.
The first recommendation response contains only the welcome, the user's known goal, at most two matches,
brief maturity/readiness honesty, and one next question. Do not add other catalog entries,
including related partial or planned work. Load a workflow only after the user
selects a capability or explicitly asks to continue beyond the first recommendation.
When one prompt names several capabilities, load one focused entry for the current turn
and use compact catalog facts for the other named systems. Never load two capability entries in one turn;
ask which capability to inspect first when the current focus is not already clear.
A named-capability education response states that underlying domain skills or tools own operations;
this onboarding skill only teaches, checks, and coordinates them.

Once a capability is selected, load only its focused entry and the shared policy
named by the chosen workflow. Route all domain reads and writes to the underlying
Ericsson capability; this skill teaches and coordinates but does not duplicate it.

## Quick Reference

Report maturity before readiness:

- `available`: implemented; reconcile live readiness.
- `partially-ported`: supporting pieces exist, but the legacy flow cannot run end
  to end.
- `planned-not-implemented`: documented but not runnable.
- `not-supported-no-port-planned`: unsupported and not on the porting roadmap.

For an available capability, report one readiness state with supporting facts:
`ready`, `missing`, `needs-user-action`, `unavailable-on-platform`, or
`unknown-needs-check`. `planned-not-implemented` is also a readiness result for a
documented planned/source-only entry. Never call partial, planned, or unsupported
work runnable.

Offer exactly these depth routes after selection:

1. Quick overview
2. Configuration/readiness check
3. Synthetic demonstration
4. Guided first real run
5. Artifact walkthrough
6. Troubleshooting

## Procedure

1. Read `references/catalog.json`. Give a one-sentence welcome and ask one missing
   role-, goal-, or outcome-oriented question. If the request already supplies the
   goal or names a capability, do not ask for it again.
2. Match goal cues and aliases. Recommend at most two recommendation-eligible
   entries, each with a concise reason. Report maturity before readiness. A directly
   requested unavailable entry may be explained, but not recommended as runnable.
3. Ask which of the six depth routes the user wants. If their request already makes
   the route clear, confirm only the next consequential action.
4. After selection or an explicit request to continue, load and follow exactly one route:
   - deeper discovery after the first recommendation: `workflows/discover-and-recommend.md`
   - explanation or Quick overview: `workflows/explain-capability.md`
   - Configuration/readiness check: `workflows/configure-and-check-readiness.md`
   - Synthetic demonstration: `workflows/run-synthetic-demonstration.md`
   - Guided first real run: `workflows/guide-first-real-run.md`
   - Artifact walkthrough: `workflows/interpret-artifacts.md`
   - Troubleshooting: `workflows/troubleshoot-capability.md`
   - pause, summary, or resume: `workflows/resume-or-summarize.md`
5. Before any domain operation, explain expected questions, reads, possible writes,
   approvals, outputs, destination, and inspection. Use the underlying Ericsson
   capability for execution. Never use a live write to prove readiness.
   A readiness-test response stops after the non-writing validation plan and one
   question choosing the first safe check. It does not offer, preview, request
   approval for, or execute a write; a genuine intended write begins only in a
   separate user request after readiness testing.
6. At each meaningful checkpoint, retain only a sanitized summary. Before the first
   persistent onboarding checkpoint, ask for consent. Until profile-scoped state is
   available and consented, summarize in conversation without claiming it was saved.
7. Finish with `templates/onboarding-summary.md`: selected capabilities, maturity,
   readiness and supporting facts, completed learning or demonstration, missing
   user actions, safe artifact locations, and one suggested next prompt.

## Pitfalls

- Never ask a user to paste or print passwords, tokens, cookies, certificate
  contents, private keys, device codes, or authentication responses in chat. Use
  the protected Keys/configuration interface and report presence without values.
- If a user already pasted or offered a secret, do not repeat, use, validate, or persist the value.
  Direct replacement entry to protected Tools & Keys and advise following the
  documented rotation or revocation path when applicable; do not invent an
  Ericsson-specific process.
- Ask before installing software, starting a dependency, opening sign-in, changing
  configuration, persisting an onboarding summary, or invoking a side effect.
- Do not execute `partially-ported`, `planned-not-implemented`, or
  `not-supported-no-port-planned` entries. Explain the gap and an honest available
  alternative when one exists.
- Use fictional data for synthetic demonstrations, label simulation honestly,
  resolve the artifact destination, and never overwrite existing artifacts without
  confirmation.
- If a requested artifact destination is ambiguous, resolve that destination first
  with exactly one destination question before asking about capability selection or
  any other onboarding detail. State the no-overwrite boundary in the same turn.
- Recheck volatile readiness when resuming. Saved state is a checkpoint, not proof
  that installation, authentication, permissions, or dependencies still work.
- An interrupted or uncertain side effect routes to `workflows/troubleshoot-capability.md`,
  never to resume. Reconcile the possible write read-only before considering a
  separately previewed and authorized retry.
