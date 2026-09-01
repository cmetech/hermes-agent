# Engineering Proposals

Engineering proposals describe candidate or accepted architecture and
implementation directions that may not yet be shipped. Treat each document's
status and decision section as authoritative for its current stage.

## Active proposals

- [Shared Agent Handoff Facade for Workflows and Bot Mode — Consolidated Design](2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md)
  — accepted implementation baseline for local and remote Hermes handoffs,
  GitLab+ICM town-hall communication, Workflow and Bot Mode integration,
  durable reconciliation, audit evidence, and Needs Attention.

## Supporting analyses

- [Workflow Agent-to-Agent Integration (Codex)](2026-09-01-workflow-agent-to-agent-integration-codex.md)
  — route durable workflow steps to named Hermes profiles and peer gateways,
  including restart recovery, cancellation, approvals, and Needs Attention;
  later amended with the shared-facade direction.

- [Workflow Agent-to-Agent Integration (Claude)](2026-09-01-workflow-agent-to-agent-integration-claude.md)
  — independent assessment against upstream v2026.8.31: depend on `/v1/runs`
  directly, declare assignments in the Hermes companion, park remote work in a
  `waiting_remote` state, and reuse `reconcile` for ambiguous outcomes.

- [Shared Agent Handoff Facade for Workflows and Bot Mode (Claude)](2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-claude.md)
  — independent facade assessment covering shared ownership, local and peer
  mechanisms, Bot Mode, GitLab+ICM, lifecycle, and audit evidence.
