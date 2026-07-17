# Portable Workflow Orchestration

Hermes loads portable Archon-shaped YAML through the optional `workflow` plugin. The runtime remains at the capability edge: operators use `hermes workflow ...`, agents use the bundled workflow skills, and no permanent model-facing core tool is added.

## First safe run

The installed showcase suite is the quickest provider-free check:

```powershell
hermes workflow showcase list --json
hermes workflow showcase preflight laptop-diagnostic --json
hermes workflow showcase run laptop-diagnostic --symptom "fictional slow startup" --json
```

Laptop Diagnostic uses only bundled sanitized fictional evidence. It does not inventory the host, connect to a network, load credentials, or initialize a model. It pauses before finalizing a proposed remediation plan; use the ordinary workflow approval or rejection command with `--continue`, then request its evidence report.

The controlled resilience showcase accepts `retry`, `timeout`, or `cancel` as its symptom/mode. A timeout remains truthfully failed while its report can prove typed timeout and process-tree cleanup. AI/extensions and scheduling are optional and require the exact digest-bound confirmation token returned by preflight. Scheduling reuses Hermes cron with `repeat=1`.

## Operating runs

Use `workflow list/show/doctor` before trusting non-distribution packages. Trust is profile- and exact-digest-bound. `run`, `runs`, `status`, and `events` expose immutable definition/input/trigger identity, capacity, health, retry or interaction state, sanitized errors, and verified artifact metadata. Workflow RunStore and Kanban remain independent lifecycle authorities in the Desktop presentation layer.

Shutdown, cancellation, retries, duplicate delivery, reconciliation, resource limits, and cleanup are durable RunStore transitions. A report derives claims from events, attempts, interactions, cleanup observations, and verified artifact bytes; catalog declarations alone cannot make a claim pass. `showcase cleanup` defaults to dry-run, and `showcase reset` never silently removes audit evidence or cron records.

## Release and installed-distribution checks

Wheel and sdist builds must retain the exact showcase catalog, digest manifest, YAML, sidecars, fixtures, scripts, command templates, and local MCP resources. The release gate runs the offline showcase from installation-shaped assets with credentials and network unavailable, plus generic workflow, Desktop, customization-ledger, and isolated upstream-merge gates. Native Windows CI is required before all-platform claims; human UAT can then install the normal alpha release using the repository's existing `install.ps1` path.
