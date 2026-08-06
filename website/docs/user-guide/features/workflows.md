---
sidebar_position: 13
title: "Workflows"
description: "Discover, inspect, trust, run, and operate durable workflow packages"
---

# Workflows

Hermes workflows are durable, resumable packages that coordinate commands, prompts, scripts, approvals, and other supported nodes. The same profile-scoped catalog and run state back the CLI and the Desktop app.

## Language profiles

Existing unversioned packages retain `hermes-legacy` behavior. New packages can
opt into the fail-closed `archon-2026-07` contract by declaring it in the
Hermes companion file. Archon-profile packages require a profile-aware
backend; keep workflow directories shared with older runtimes unversioned until
every consumer recognizes the declaration.

See the [Workflow YAML reference](./workflow-yaml-reference) for the generated
schema commands, complete field inventory, current Phase 4 status, sealed
compatibility guidance, and migration steps.

The portable graph has seven node kinds: `command`, `prompt`, `bash`, `script`,
`loop`, `approval`, and `cancel`. MCP and skills are existing per-node options
on `command` and `prompt`, not extra node kinds. Script nodes already support
the documented `uv` and `bun` runtimes; structured data does not add another
script or extension node type.

### Phase 4 operation

New and default `archon-2026-07` admissions use normalizer v4. Current
`hermes-legacy` admissions use v2. Explicit and already sealed v1, v2, and v3
packages remain supported compatibility inputs; their pinned semantics do not
move when the default changes.

<!-- workflow-language-version-selection -->
```json
{
  "current_normalizer_by_profile": {
    "hermes-legacy": 2,
    "archon-2026-07": 4
  },
  "supported_normalizer_versions": [1, 2, 3, 4]
}
```

The current v4 contract adds compile-only package includes and confirmed
ordinary-loop signals. The root companion remains the only policy authority;
authenticated child companions are ignored. Included named resources are
sealed from their logical child package, and the admitted composite digest
covers the root, every selected dependency, and their resources. An explicit
version selection must validate and diagnose that composite package, review and
trust that exact digest, and admit the resulting immutable compilation—not
reload a child independently.

After admission, the ordinary operator surface applies. Keep the run ID and
inspect `status` and `events`. A loop signal awaiting confirmation appears as
the existing approval/input interaction: before the final iteration the
backend may advertise `approve`, `provide-input`, and `cancel`; on the final
iteration it advertises only `approve` and `cancel`. Approval completes from
the sealed result without a provider replay. Feedback resumes another bounded
iteration. Always send the advertised interaction ID and current expected
state version.

Resume verifies the pinned normalizer, composite dependency manifest, and
sealed resource origins before execution. The original root and child source
trees may be unavailable after admission without changing the run; any missing
or changed snapshot byte fails closed. Diagnose include failures by their
stable generated codes and bounded logical paths. The v4 contract does not
provide live child workflows, include parameters, or loop groups.

## Browse the catalog

Open **Workflows** in the Desktop sidebar to see the catalog for the selected profile. Each row shows the package name, version, description, trust state, supported inputs, and whether it came from the profile, current project, or the bundled showcase collection. A user workflow and a showcase with the same name remain separate rows; **Project**, **Profile**, and **Bundled showcase** identify which package View and Run target.

Bundled rows are loaded only after the installed showcase catalog and package trees pass digest and path-safety verification. A bundle integrity failure omits the entire bundled collection rather than treating tampered content as trusted. A safe package that is incompatible with the current environment remains visible with an **Incompatible** badge so other verified showcases are not suppressed. **Verified bundle** is distribution trust, not a user trust-store grant, so no trust action is offered for these rows.

Select **View** to inspect a workflow without changing it. The Diagram view uses the normalized workflow topology. If the diagram exceeds a safety bound, Desktop shows the bounded text outline and explains why the diagram was omitted. The Definition view shows stable, read-only JSON derived from the normalized redacted definition—not raw YAML—and provides a copy action.

Select **Run** to open **Review & Run**. Desktop fetches a fresh preflight and requires that exact package to be trusted, compatible, supported by the flat-input form, and backed by a healthy coordinator. Review the trust verdict, risk summary, and inputs before selecting **Start workflow**. Parameterless workflows and flat `string`, `number`, `boolean`, and `enum` inputs are supported in this version. Other input shapes remain available through the CLI.

### Bundled showcase coverage

All five bundled showcases are visible and View remains available even when Run is not. Two are admitted through Desktop's standard background-only path:

| Showcase | Desktop Run | Why |
| --- | --- | --- |
| `approval-gate` | Available | Parameterless, offline approval workflow. |
| `resilience` | Available | Flat inputs and background-safe package behavior. |
| `laptop-diagnostic` | CLI only | Its file/text inputs are outside Desktop's flat-input form; use `hermes workflow showcase run laptop-diagnostic`. |
| `ai-extensions` | CLI only | AI authorization still requires the showcase consent flow; use `hermes workflow showcase run ai-extensions`. |
| `scheduling` | CLI only | Schedule creation and exact-ID/nonce cleanup live in the showcase CLI wrapper rather than its workflow package; use `hermes workflow showcase run scheduling`. |

For the marquee walkthrough, select `approval-gate`, inspect Diagram and the redacted Definition, then choose **Run** and **Start workflow**; when `approval-gate` reaches **Attention**, open it and select **Approve** to let the background coordinator complete the run.

Enums must publish a bounded non-empty list of string choices; incomplete or
non-string enum metadata is treated as unsupported instead of rendering an
empty or ambiguous form. Untouched optional inputs are omitted from admission
so package defaults retain their meaning.
Optional booleans use an explicit **Not set / On / Off** control.
Desktop input names must also be portable filename segments: Windows device
names, path separators, control characters, and characters rejected by Windows
filenames are classified unsupported rather than failing during admission. The
generated text-input component (the name plus `.txt`) must fit both the 255-byte
UTF-8 and 255-code-unit UTF-16 filename limits. This includes Windows'
superscript device aliases (`COM¹`–`COM³`, `LPT¹`–`LPT³`), and names must remain
distinct under case-insensitive filename matching. File and text inputs must
also produce distinct targets—for example, file `report.txt` conflicts with
text input `report` because text values receive a `.txt` suffix.

After admission, Desktop opens the run on the **Active board**. A workflow waiting for approval or input appears in the **Attention** inbox. Opening that item shows the authoritative run state and available action. Starting a workflow only persists and queues it; execution happens in the background coordinator, outside the HTTP request.

A partial-catalog warning means Hermes reached a safety or capacity limit; visible rows remain valid, but the list is incomplete. A corrupt package appears as a typed per-entry error so valid neighboring workflows stay usable.

The CLI exposes the same discovery path:

```bash
hermes workflow list
hermes workflow show NAME
hermes workflow doctor NAME
```

Add `--json` for automation-safe output.

### Select a package through the API

For API detail and run requests, a name without `catalog_source` resolves the user-precedence package. To target the verified bundled showcase when it has the same name as a user package, send `catalog_source=showcase`.

### Desktop state and recovery guide

| State                                     | What Desktop shows                                                        | Operator action                                                             |
| ----------------------------------------- | ------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Catalog unavailable                       | “Could not load workflows” with **Retry**                                 | Check the Desktop backend, then retry.                                      |
| Empty catalog                             | “No workflows installed” and a documentation link                         | Install a package under the project or profile workflows directory.         |
| Partial catalog                           | Warning above the valid rows                                              | Use the visible rows or reduce catalog size before relying on completeness. |
| Invalid or over-capacity entry            | Error in that workflow's row; neighboring rows remain available           | Validate or reduce that package with the CLI.                               |
| Detail missing or unavailable             | Typed View error with **Retry**                                           | Confirm the package still exists; retry transient failures.                 |
| Diagram omitted                           | Explanation plus bounded text outline                                     | Review the outline or inspect the workflow with the CLI.                    |
| Untrusted workflow                        | Run stays disabled with an associated explanation                         | Review and trust the current digest through the CLI.                        |
| Unsupported inputs                        | Run stays disabled; the dialog points to `hermes workflow run NAME`       | Run it through the CLI; Desktop v1 does not build rich or file-input forms. |
| Incompatible workflow                     | Blocking findings; no admission request is sent                           | Resolve the reported runtime or package incompatibility.                    |
| Coordinator unavailable / HTTP 503        | Warning and retry path; no run is created                                 | Start or repair the coordinator host, then retry from the same review.      |
| Validation failure / HTTP 422             | Field-level message when possible, otherwise a general validation error   | Correct the rejected values and submit again.                               |
| Idempotency conflict / HTTP 409           | Conflict message instructing a fresh review                               | Close the modal, review current inputs, and start a new intent.             |
| Network failure                           | Connection error with **Retry**                                           | Retry in the same modal; Desktop reuses that modal's idempotency key.       |
| Created admission                         | “Started,” then the new run opens on the Active board                     | Monitor the run and respond to Attention items.                             |
| Existing admission                        | “Already running—showing you that run”                                    | Continue with the previously admitted run; no duplicate is created.         |
| Run admitted but profile activation fails | The run is retained and a retry offers to locate it without posting again | Retry locating the admitted run.                                            |

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

### Inspect typed output

Under `archon-2026-07`, a successful output-producing node with an
`output_type` can publish one typed artifact for its winning attempt. The label
is open and case-sensitive. It never becomes a path or filename, and a stale or
losing attempt cannot publish. `hermes-legacy` remains unchanged:
`output_format` is post-execution validation and `output_type` does not create a
typed publication.

Publication is atomic and journal-authoritative. Content and metadata become
visible together; recovery removes incomplete or unjournaled bundles and
reconstructs missing content only from the corroborated winning attempt with
the recorded digest. A mismatch becomes an explicit integrity or
reconciliation state instead of silently selecting another attempt.

Run evidence contains bounded metadata—output type, media type, producer,
winning attempt, size, SHA-256, optional schema fingerprint, production time,
and optional session identity—not the body. The backend issues an opaque
publication ID. Authenticated preview and download operations accept that ID,
never a filesystem path. Preview is limited to 64 KiB: complete canonical JSON
can be formatted, text can be truncated, and unknown media remains download
only. Empty successful text is a valid zero-byte artifact.

In Desktop, open a run and choose its **Artifacts** evidence tab. A row appears
only when the backend confirms a valid publication ID. Select **Preview**
explicitly or **Download** to choose a native destination. An older backend's
generic artifact evidence remains visible in the generic evidence view instead
of being guessed into a typed artifact.

## Background operation

Background and cron-triggered workflows require a running coordinator host, such as the gateway or Desktop/headless server. On a CLI-only installation with no coordinator running, background admission is refused; durable notification facts remain available to query when an operator surface reconnects.

Hermes never auto-approves an outward action. A paused workflow releases worker capacity and can be resumed after the required approval or input is recorded.
