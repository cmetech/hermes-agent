---
sidebar_position: 13
title: "Workflows"
description: "Discover, inspect, trust, run, and operate durable workflow packages"
---

# Workflows

Hermes workflows are durable, resumable packages that coordinate commands, prompts, scripts, approvals, and other supported nodes. The same profile-scoped catalog and run state back the CLI and the Desktop app.

## Browse the catalog

Open **Workflows** in the Desktop sidebar to see the catalog for the selected profile. Each row shows the package name, version, description, trust state, supported inputs, and whether it came from the profile or the current project.

The Desktop catalog currently provides discovery only. **Review & Run arrives with the Task 6 trigger flow.** Until then, use the CLI to inspect and start workflows. The catalog still shows its safety gates: an untrusted package or unsupported input shape has a disabled Run affordance with a focusable explanation. A partial-catalog warning means Hermes reached a safety or capacity limit; visible rows are still valid, but the list is incomplete.

The CLI exposes the same discovery path:

```bash
hermes workflow list
hermes workflow show NAME
hermes workflow doctor NAME
```

Add `--json` for automation-safe output.

## Validate and trust a package

Inspect a workflow before trusting or running it:

```bash
hermes workflow validate NAME
hermes workflow doctor NAME
hermes workflow show NAME
```

Trust is bound to the current package digest. Review the reported requirements, approvals, outward actions, and topology before trusting that exact content:

```bash
hermes workflow trust NAME --digest REVIEWED_DIGEST
hermes workflow untrust NAME
```

Changing the package changes its digest, so Hermes will not silently treat modified content as trusted.

## Start and inspect runs

Start a run from the CLI:

```bash
hermes workflow run NAME --foreground --arguments 'operator-provided input'
hermes workflow runs
hermes workflow status RUN_ID
hermes workflow events RUN_ID
```

Workflow actions are profile-scoped. Keep the returned run ID. Approval and rejection decisions plus provided input require an expected state version. Retry and reconciliation accept one when the operator has a current version.

```bash
hermes workflow approve RUN_ID --interaction-id INTERACTION_ID --expected-version VERSION
hermes workflow reject RUN_ID --interaction-id INTERACTION_ID --expected-version VERSION --reason "Needs revision"
hermes workflow provide-input RUN_ID INTERACTION_ID answer --expected-version VERSION
hermes workflow retry RUN_ID NODE_ID --expected-version VERSION
hermes workflow resume RUN_ID
hermes workflow cancel RUN_ID
```

Use `hermes workflow --help` or `hermes workflow ACTION --help` for the exact options supported by your installed version.

## Background operation

Background and cron-triggered workflows require a running coordinator host, such as the gateway or Desktop/headless server. On a CLI-only installation with no coordinator running, background admission is refused; durable notification facts remain available to query when an operator surface reconnects.

Hermes never auto-approves an outward action. A paused workflow releases worker capacity and can be resumed after the required approval or input is recorded.
