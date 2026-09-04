# Legacy LOOP24 Workflow Portability Assessment

- Status: source assessment, not an implementation plan
- Assessment date: 2026-08-09
- Legacy source: `loop_24` at
  `8ca26f882bc461d9aaa80a252685568c8749394a`
- Target source: `hermes-agent` `base` at
  `9f2df504f72f469c172a5c32e314f16e60350792`

## Executive conclusion

The Archon workflow runtime delivered through Phase 5 is a viable replacement
for the legacy Langflow graph engine, but it is not yet a complete replacement
for the LOOP24 product. The graph language and the domain tools are separate
parts of the migration:

- 21 of the 30 active legacy flow graphs can be represented with the current
  Phase 5 language, subject to the required tools being present.
- 8 flows contain group-level or hidden record iteration that needs Phase 6
  `loop_group`, or a deliberate batch-tool redesign.
- 1 flow, `NW Hardening - Main`, dynamically invokes child flows and permits
  interactive reruns. That is intentionally outside the Archon contract and
  must be redesigned as a static DAG or a set of explicit top-level workflows.
- Only `Issue_ Jira Assigned Tickets Summary` currently has both an expressible
  graph and enough production domain tooling to call it close to migration
  ready. Outlook inbox digest is also a useful first proof, but its current
  package is still legacy-profile and its MCP packaging must be made Archon
  compliant.
- None of the two currently bundled Ericsson workflow definitions proves a
  fully admitted Archon v5 package. Both currently preserve legacy semantics.

The practical conclusion is therefore **language-ready for most graphs, but
tooling-ready for only a small first tranche**. Phase 6 will remove the largest
remaining language gap. GitLab, SharePoint, structured Confluence access,
document conversion/generation, and richer Jira operations are the largest
capability gaps.

## Scope and method

This assessment treats `flows/` in the legacy repository as the active
portfolio. `flows/archieve/` is historical material and was not counted as a
separate migration obligation. `flows/attributes.json` and
`flows/manifest.json` are metadata, leaving 30 active JSON flow definitions.

The assessment separated three questions that are easy to conflate:

1. **Graph expressibility:** can the control flow be represented by current
   Archon nodes, dependencies, conditions, ordinary loops, approvals, retries,
   and immutable includes?
2. **Runtime admissibility:** will normalizer v5, provider authority, sealed
   resources, and the selected installed environment admit and execute it?
3. **Business capability:** do Co-worker tools provide the Jira, GitLab,
   Microsoft 365, document, browser, or research operation the flow performs?

The 30 active definitions contain 215 nodes and 219 edges. Six definitions use
an explicit Langflow `LoopComponent`; two more implement repeated graph or
per-record execution through custom components. These eight are classified as
Phase 6 dependent even when the surrounding graph is otherwise portable.

## Archon capability baseline

Normalizer v5 is the current default for newly admitted
`archon-2026-07` packages. It preserves sealed normalizer v1-v4 snapshots and
legacy normalizer v2 behavior. The current language supplies:

- a static dependency DAG with `command`, `prompt`, `bash`, `script`, `loop`,
  `approval`, and `cancel` nodes;
- immutable compile-time includes;
- typed structured output, direct-dependency output references, conditions,
  retries, timeouts, cancellation, and shared/fresh AI contexts;
- sealed model/provider resolution, hooks, package-local MCP, full skill
  snapshots, bounded inline agents, and exact tool allow/deny semantics;
- one backend-authored capability result used by validation, doctor,
  admission, execution, evidence, catalog/detail, and Desktop.

The language deliberately does not provide runtime child workflows, dynamic
includes, `include.with`, deep child-output navigation, arbitrary input
mapping, or `loop_group`. A workflow cannot manufacture those meanings with a
prompt or an unsealed script.

Archon v5 also does not make every installed tool automatically suitable for a
workflow. A named tool must exist in the isolated worker, pass availability and
provider checks, remain within the node's sealed tool policy, and have
appropriate approval/evidence behavior. Unknown worker tools fail before a
model call.

## Portfolio findings

### Group iteration is the main language gap

The following six flows contain an explicit Langflow `LoopComponent`:

- `LCM Version Tracker`
- `Issue_ JIRA Defect Triage`
- `BOSBlues - 5G FWA Home Location Intelligence & Mon`
- `Contribution Agent - Follow-up Drafting`
- `CI File Auditor`
- `Search and Read E-Mails`

Two more require equivalent semantics:

- `3PP_ Support and LCM Tracker` uses `RecordEnricher` to make roughly one LLM
  operation per record. Treating that as one opaque prompt would lose bounded
  per-item progress, cancellation, and evidence.
- `5G Core Vitals - NF Health Check Automation` uses `LoopTimerComponent` to
  repeat a multi-node diagnostic graph. An ordinary single-node `loop` cannot
  faithfully repeat that group.

These should use Phase 6 `loop_group` unless the operation is intentionally
collapsed into a deterministic batch tool with an explicit bounded contract.

### Dynamic child-flow execution needs redesign

`NW Hardening - Main` launches selected Langflow blocks at runtime through
`uvx lf-nw_hardening_blocks` and allows interactive tool reruns. Archon excludes
runtime child workflows and dynamic includes by design. The correct migration
is one of:

- compile a fixed selection into a static top-level DAG;
- publish several explicit top-level workflow variants; or
- move a genuinely atomic operation behind one bounded domain tool.

It should not be ported as an unrestricted workflow-dispatch tool.

### Existing Ericsson packages are still compatibility examples

`capabilities/workflow-packages/ericsson/workflows/inbox-digest.yaml` has no
companion file, and `my-tickets-summary.hermes.yaml` does not declare
`language_compatibility`. Both therefore retain `hermes-legacy` behavior. They
demonstrate useful end-to-end surfaces, but they are not evidence that an
Ericsson package has passed the full Archon v5 admission boundary.

The first migration tranche should produce small, genuinely Archon v5 packages
and run validate, doctor, trust, installed-distribution, Desktop, and Windows
tests against their exact digests.

## Flow-by-flow assessment

Legend:

- **Phase 5 graph** — the control flow is expressible now; domain tools may
  still be missing.
- **Phase 6** — exact behavior requires `loop_group` or a conscious batch-tool
  redesign.
- **Redesign** — the legacy orchestration model conflicts with an intentional
  Archon boundary.
- **Exclude** — do not migrate without a new product decision.

| Legacy flow | Graph verdict | Capability assessment and required action |
| --- | --- | --- |
| `3PP_ Support and LCM Tracker` | Phase 6 | Port spreadsheet ingestion, bounded research, record filtering, and spreadsheet output. Replace hidden `RecordEnricher` iteration with `loop_group` or a deterministic bounded batch enrichment tool. |
| `LCM Version Tracker` | Phase 6 | Requires group iteration plus spreadsheet extraction/output and web or internal-source research. |
| `Glean MCP Search` | Phase 5 graph | Glean is configured as a remote MCP today. Archon v5 canonicalizes remote HTTP/SSE MCP but blocks execution; provide an accepted package-local bridge or another reviewed domain adapter. |
| `Issue_ Jira Assigned Tickets Summary` | Near-ready | Current Jira read tools cover the core outcome. Re-author as Archon v5, seal the package, and validate installed/Windows behavior. |
| `Issue_ Jira -_ Gitlab` | Phase 5 graph | The Jira-to-code-change topology is expressible. Comprehensive GitLab tools are absent, so the business operation is not portable yet. |
| `Issue_ JIRA Defect Triage` | Phase 6 | Needs item iteration, expanded Jira search/update operations, and GitLab read/change/MR tools. Keep LLM triage as workflow logic rather than embedding it in the Jira plugin. |
| `5G Core Vitals - NF Health Check Automation` | Phase 6 or redesign | Repeating a diagnostic subgraph needs `loop_group`; scheduled independent runs may be a better representation. Port its parsers, notification/email outputs, and any fixed diagnostic commands. |
| `AI Radio Sense — Fault Triage` | Phase 5 graph | Needs the concrete webhook/API, PowerShell or fixed diagnostic operations, and any retrieval source. Do not expose an unrestricted PowerShell runner merely to preserve the Langflow component. |
| `BOSBlues - 5G FWA Home Location Intelligence & Mon` | Phase 6 | Needs group iteration, spreadsheet processing, and a durable human-review/approval step. |
| `Contribution Agent - Follow-up Drafting` | Phase 6 | Needs group iteration, spreadsheet inputs, Outlook drafts/send operations, and explicit approval of outward messages. |
| `SecureMoP - MoP to SecureCRT Script Converter` | Phase 5 graph | Needs high-fidelity document parsing plus a bounded SecureCRT/MoP validator and document output contract. |
| `Windows Laptop Diagnostic` | Phase 5 graph | Package reviewed, fixed diagnostic scripts and bounded outputs. Do not port the general arbitrary elevated PowerShell component. |
| `nw_hardening_anon` | Exclude | Reversible pseudonymization was intentionally not ported. Reconsider only as a separate privacy product decision with a maintained identity vault. |
| `nw_hardening_ansible` | Phase 5 graph | Port deterministic Ansible generation and validation as package resources or a document-generation capability. |
| `nw_hardening_confluence_data_f` | Phase 5 graph | Natural-language Confluence research exists; workflows still need a stable structured read/sync contract. |
| `nw_hardening_confluence_distro` | Phase 5 graph | Same Confluence adapter gap, plus explicit distribution/output semantics. |
| `nw_hardening_docgen` | Phase 5 graph | Requires the telecom document template, assembly, verification, rendering, and artifact contract. Generic DOCX skills alone are not a workflow API. |
| `nw_hardening_gitlab_f` | Phase 5 graph | Blocked on comprehensive GitLab repository/file/branch/commit/MR support. |
| `nw_hardening_glean` | Phase 5 graph | Blocked by the Archon remote-MCP execution boundary unless replaced with an accepted adapter. |
| `nw_hardening_intention` | Phase 5 graph | The intent/classification portion can use structured AI output now. Keep downstream tool selection static and admitted. |
| `nw_hardening_re_id` | Exclude for now | Re-identification depends on the intentionally unported reversible privacy vault. |
| `nw_hardening_rn` | Phase 5 graph | Port its specific research/network input and structured result contract; avoid a generic raw-command shortcut. |
| `nw_hardening_sharepoint_data_f` | Phase 5 graph | SharePoint list/download/parse support is absent and is a priority tool gap. |
| `NW Hardening - Main` | Redesign | Runtime child-flow selection and reruns are outside Archon. Compile static variants or expose atomic domain operations, not workflow recursion. |
| `Privacy Vault - Anonymize` | Exclude | Same explicit privacy-vault product decision as `nw_hardening_anon`. |
| `Privacy Vault - Re-Identification` | Exclude | Same explicit privacy-vault product decision; never approximate reversible identity mapping in workflow evidence. |
| `CI File Auditor` | Phase 6 | Requires group iteration and comprehensive GitLab CI configuration, include, pipeline, job, and artifact reading. |
| `TOL Generation` | Phase 5 graph | Needs Docling-class parsing, spreadsheet/table processing, template-based document generation, and validation. |
| `Image Generation` | Phase 5 graph | Co-worker has image generation and browser tooling. Repackage the exact prompt, rendering, and artifact behavior or deliberately simplify it to the native image tool. |
| `Search and Read E-Mails` | Phase 6 for exact graph | Outlook MCP already covers search/read. The existing inbox-digest workflow provides a useful equivalent outcome now; exact per-message processing needs group iteration. |

## Migration sequence

1. **Prove the Archon package boundary.** Convert Jira Assigned Tickets Summary
   and a bounded Outlook inbox digest to explicit `archon-2026-07` companions.
   These are intentionally small enough to expose packaging, tool admission,
   Windows, evidence, and Desktop defects before more complex migrations.
2. **Build shared domain adapters.** Prioritize GitLab, SharePoint/document
   ingestion, document generation, expanded Jira, and structured Confluence.
   The same implementations must serve natural-language skills and workflows.
3. **Deliver Phase 6 `loop_group`.** Then port the eight iterative flows with
   bounded item counts, shared cancellation/deadline/budget authority, and
   per-item progress/evidence.
4. **Migrate the static NW Hardening blocks.** Test each block independently as
   an Archon package before composing fixed top-level variants.
5. **Redesign NW Hardening Main.** Choose static variants or a compile-time
   selection model; do not add runtime workflow delegation.
6. **Resolve excluded product decisions.** Privacy-vault identity mapping and
   any other excluded behavior remain outside the migration until explicitly
   approved.

## Acceptance gates for each migrated flow

A translated YAML file is not a completed migration. Each flow must prove:

- explicit `archon-2026-07` admission under normalizer v5;
- no blocking validation or doctor finding for its exact installed profile;
- package-contained and digest-bound commands, scripts, skills, and MCP
  resources;
- all tools available in the isolated workflow worker, including exact
  preservation of `allowed_tools: []`;
- deterministic bounded outputs, pagination, artifact sizes, and redaction;
- explicit approval and reconciliation for outward or mutating operations;
- retry, timeout, cancellation, recovery, and prompt-cache invariants;
- execution from an installed distribution on the supported OS, including
  Windows for Outlook, desktop automation, and LOOP24 releases;
- CLI, Gateway/REST, evidence, catalog/detail, doctor, notifications, and
  Desktop consuming backend-authored status/actions rather than reinterpreting
  the workflow;
- old-client vocabulary, existing REST mutation URLs, legacy snapshots, and
  normalizer v1-v4 compatibility unchanged.

## Risks and product decisions

- **Phase 6 semantics:** decide whether the eight iterative flows require true
  group iteration or whether selected cases should become deterministic batch
  tools. This is a per-flow decision, not a blanket optimization.
- **NW Hardening composition:** select static variants versus a user-facing
  compile-time configurator.
- **Privacy vault:** confirm the current no-port decision or fund a separately
  owned identity-vault capability. Workflow evidence is not such a vault.
- **Remote enterprise MCP:** Glean and similar remote servers are blocked by
  the current Archon execution contract. Any change requires a separate design;
  it must not silently weaken package containment.
- **GUI automation in workflows:** conversational computer use exists, but raw
  GUI automation should not become the default workflow substitute for stable
  Jira, GitLab, SharePoint, Outlook, or Teams APIs.

## Provenance and key claims

| Claim | Source |
| --- | --- |
| Active flow inventory and graph structures | Legacy `flows/**/*.json`, excluding `flows/archieve/**`, `flows/attributes.json`, and `flows/manifest.json`, at `8ca26f8` |
| Custom iteration behavior | Legacy `custom_components/ericsson_parsers/record_enricher.py` and `custom_components/ericsson_orchestrate/loop_timer.py` |
| Dynamic child-flow behavior | Legacy `flows/nw_hardening_main/NW Hardening - Main.json` and `custom_components/ericsson_orchestrate/run_flow_trigger.py` |
| Current language and exclusions | Target `website/docs/user-guide/features/workflow-yaml-reference.md` and `plugins/workflow/language_schema.py` at `9f2df50` |
| Current Ericsson packages | Target `capabilities/workflow-packages/ericsson/` and `capabilities/ericsson.json` |
| Current Jira, Teams, Outlook, and Confluence surfaces | Target `plugins/ericsson-jira/`, `plugins/ericsson-teams/`, `plugins/outlook-mcp/`, and `skills/ericsson/confluence-research/` |

This assessment intentionally trusts the completed Phase 1-5 compatibility and
snapshot ledgers. It does not reopen or redispatch those phases.
