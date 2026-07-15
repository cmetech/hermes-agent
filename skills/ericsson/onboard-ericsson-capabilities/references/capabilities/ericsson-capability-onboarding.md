---
id: ericsson-capability-onboarding
display_name: Ericsson Capability Onboarding
aliases: [Co-Worker onboarding, Ericsson capability guide, capability training]
goals:
  - Onboard me to the Co-Worker capabilities.
  - Help me find Ericsson capabilities for my job.
  - Resume my Ericsson capability onboarding.
maturity: available
recommendation_eligible: false
source_flows: []
implementation:
  skills: [skills/ericsson/onboard-ericsson-capabilities]
  plugins: []
  mcp_servers: []
  workflows: []
  tools: []
platforms: [macos, linux, windows]
configuration: []
reads: [bundled capability catalog, current profile readiness facts, consented sanitized onboarding state]
writes: [optional sanitized onboarding summary after consent]
artifacts: [readiness summary, learning handoff]
demonstrations: [synthetic-offline]
troubleshooting: [unknown goal, stale saved readiness, unavailable selected capability]
---

# Ericsson Capability Onboarding

## What it solves

Finds relevant Ericsson capabilities and teaches safe use without replacing the
underlying domain capability.

## Try saying

- “Please onboard me to the Co-Worker capabilities.”
- “Which Ericsson capabilities fit my role?”
- “Resume my onboarding and explain what is left.”

Follow up with a narrower filter, request a preview, choose an output format or
artifact destination, ask about exclusions or warnings, or ask how to rerun safely.

## Questions

It starts with one question about the goal or role and reuses known answers.

## Reads and writes

It reads bundled education and observable readiness. It writes only a sanitized
summary after consent; domain data and operations stay with the selected capability.

## Readiness

The skill is bundled for every profile. It separately checks the selected
capability's maturity, platform, configuration, authentication, and safe probes.

## Demonstration

A fictional conversation can demonstrate routing without credentials or live data.

## Artifacts

The output is a concise readiness or learning handoff with safe artifact pointers.

## Troubleshooting

If the goal is unclear, ask one clarifying question. Recheck volatile facts when
resuming; never treat saved readiness as current proof.
