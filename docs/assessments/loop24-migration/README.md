# LOOP24 Migration Assessments

- Status: current as of 2026-08-09
- Legacy baseline: `loop_24`
  `8ca26f882bc461d9aaa80a252685568c8749394a`
- Target baseline: `hermes-agent` `base`
  `9f2df504f72f469c172a5c32e314f16e60350792`

## Purpose

This assessment set answers two implementation-planning questions:

1. Which legacy LOOP24 Langflow workflows can be expressed and operated by the
   Co-Worker Archon workflow runtime?
2. Which reusable tools and integrations must be ported or enhanced so those
   workflows and natural-language skills can perform the same work?

## Documents

- [Legacy workflow portability assessment](legacy-workflow-portability.md)
- [Tool and capability portability assessment](tool-capability-portability.md)

## Headline assessment

- 21 of 30 active legacy graph topologies are expressible with the Phase 5
  Archon language, although most still depend on missing domain adapters.
- 8 flows need Phase 6 `loop_group` or an explicit bounded-batch redesign.
- `NW Hardening - Main` needs redesign because runtime child workflows and
  dynamic includes are intentionally outside Archon.
- Browser automation, web research, computer use, Outlook, basic Teams, and
  generic document authoring are already useful from natural language.
- GitLab and general SharePoint are absent; Jira is partial; Confluence and the
  enterprise document pipeline need structured workflow-facing contracts.
- The recommended architecture implements each business operation once, then
  exposes it to both skills/chat and workflows through the same plugin or CLI
  service.

## How to use these assessments

Use the workflow assessment to decide whether a flow can be migrated with the
current workflow language, needs Phase 6 `loop_group`, or requires redesign.
Use the tool assessment to plan reusable plugins, MCP servers, CLI commands,
skills, and deterministic package resources before translating individual
flows.

These documents are assessments, not authorization to implement or release the
roadmap. Before beginning a capability, convert its section into a reviewed
design and strict-TDD implementation plan against the then-current `base`.
