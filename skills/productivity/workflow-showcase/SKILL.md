---
name: workflow-showcase
description: Use when the user asks for a workflow showcase, demo, tour, Laptop Diagnostic, resilience scenario, optional AI/extensions, scheduling, status, report, resume, reset, or cleanup.
---

# Workflow showcase

Before any command, resolve `PRODUCT_CLI` once from the active product:

- Read `$HERMES_HOME/brand.json` when available and use its `slug` as the executable name. For example, LOOP24 uses `loop24` and OTTO uses `otto`.
- If there is no brand descriptor, use `hermes` only for a neutral Hermes Agent installation.
- Every command template below uses `PRODUCT_CLI`. Replace that token with the resolved executable; do not execute it literally and do not substitute a different product's bare `hermes` command on a branded installation.

Always call `PRODUCT_CLI workflow showcase ... --json` and interpret the returned evidence. Never promise a pass before reading the report, never approve an interaction for the user, and never invent consent.

- For an explanation or scenario comparison, read [workflows/explain-showcase.md](workflows/explain-showcase.md).
- To run Laptop Diagnostic, the resilience lab (retry, timeout, or cancel), optional AI/extensions, or scheduling, read [workflows/run-showcase.md](workflows/run-showcase.md).
- For status, report, resume, approval/rejection handoff, or “what is waiting?”, read [workflows/resume-and-report.md](workflows/resume-and-report.md).
- For reset or cleanup, read [workflows/reset-and-cleanup.md](workflows/reset-and-cleanup.md).

The Laptop Diagnostic is synthetic and offline: ask for one short symptom/focus, and explain that fictional evidence is used instead of inventorying the real laptop. The default showcase uses no model, network, or external integration. AI and scheduling require the exact confirmation token from preflight. Ask for only one missing input at a time.

Read [references/showcase-contract.md](references/showcase-contract.md) for command/result contracts and [references/safety-and-interpretation.md](references/safety-and-interpretation.md) before interpreting expected failures.
