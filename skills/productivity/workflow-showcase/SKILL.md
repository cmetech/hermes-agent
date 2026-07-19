---
name: workflow-showcase
description: Use when explaining, running, resuming, reporting, resetting, or cleaning a bundled workflow showcase or resilience demonstration.
---

<objective>
Route showcase requests through digest-verified showcase discovery while using
the reusable workflow operator contract for every general run action.
</objective>

<quick_start>
Resolve `PRODUCT_CLI` once from the active `brand.json` slug, falling back to
`hermes` only for a neutral install. Run `PRODUCT_CLI workflow showcase
preflight ID --json`, then read `result.command_contract`. Use that exact
contract; never speculate about flags or execute `PRODUCT_CLI` literally.
</quick_start>

<essential_principles>
- Showcase catalog commands use a showcase ID. Once a run starts, status,
  events, approve, reject, input, resume, reconcile, and cancel use its run ID.
- Retain one stable intent key across retries. Execute one mutation at a time;
  never parallel-probe variants, mask failure, or pipe approval.
- Stop at human gates. Interpret `coordinator_unavailable`, stalled/no-progress,
  conflict, waiting retry, and terminal outcomes from authoritative JSON.
- Never promise a pass before reading the evidence report. Expected resilience
  failures may validate a claim while the workflow remains truthfully failed.
</essential_principles>

<routing>
- Explanation/comparison: [workflows/explain-showcase.md](workflows/explain-showcase.md)
- Run/tour/resilience/AI/scheduling: [workflows/run-showcase.md](workflows/run-showcase.md)
- Status/gates/resume/report: [workflows/resume-and-report.md](workflows/resume-and-report.md)
- Reset/cleanup: [workflows/reset-and-cleanup.md](workflows/reset-and-cleanup.md)
</routing>

<reference_guides>
Read [references/showcase-contract.md](references/showcase-contract.md) and
[references/safety-and-interpretation.md](references/safety-and-interpretation.md)
before interpreting a run or expected failure.
</reference_guides>

<success_criteria>
- Preflight supplied exact syntax and the selected scenario's safety facts.
- General actions used the run ID and current interaction/version fields.
- No duplicate run, speculative mutation, hidden failure, or invented consent occurred.
- The report cites durable evidence and preserves the true terminal outcome.
</success_criteria>
