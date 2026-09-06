# LOOP24 Tool and Capability Portability Assessment

- Status: source assessment and capability roadmap, not an implementation plan
- Assessment date: 2026-08-09
- Legacy source: `loop_24` at
  `8ca26f882bc461d9aaa80a252685568c8749394a`
- Target source: `hermes-agent` `base` at
  `9f2df504f72f469c172a5c32e314f16e60350792`

## Executive conclusion

Co-worker already has a stronger general automation foundation than legacy
LOOP24: first-class browser control, web search/extraction, cross-platform
computer use, provider-portable AI execution, skills, isolated plugin agents,
and a durable workflow runtime. The migration gap is concentrated in reusable
enterprise domain adapters and deterministic document pipelines.

The current state is:

- **Strong now:** browser automation, web research, conversational computer
  use, generic DOCX/XLSX/PDF/PowerPoint work, image generation, and basic file
  operations.
- **Ported with caveats:** Outlook email/calendar has near source parity with
  the legacy COM MCP; Teams has the same basic list/read/send/reply operations
  with a better device-code sign-in flow.
- **Partial:** Jira provides assigned-ticket listing, issue detail, and comment
  creation, but not a comprehensive Jira service.
- **Natural-language only:** Confluence has a capable read-only research skill,
  but no stable structured tool contract for Archon workflows.
- **Missing:** GitLab and general SharePoint support.
- **Partial at the outcome level:** generic document skills can create and edit
  files, but the legacy telecom template, conversion, assembly, verification,
  spreadsheet-enrichment, and Ansible-generation pipeline is not ported.

The highest-value work is therefore: comprehensive GitLab, SharePoint plus
document ingestion, a deterministic document-generation pipeline, expanded
Jira, and a structured Confluence adapter. These must be implemented once and
consumed by both skills/natural-language chat and Archon workflows. Recreating
Langflow-only components would repeat the architecture that this migration is
meant to replace.

## Scope and method

The legacy inventory covers 61 production Python files under
`custom_components/` (including package initializers and the nested BOS Blues
component, excluding its test file) and the Outlook MCP server under
`mcp/outlook_mcp/`. The assessment reviewed every component family
and traced the target tool registry, plugin registration, skills, capability
manifest, workflow tool-policy resolution, and relevant user documentation.

“Ported” in this document means more than source code being copied. A completed
capability must have:

- a stable operation and result contract;
- installation and doctor/configuration behavior;
- bounded reads, pagination, timeouts, and result sizes;
- explicit mutation approval and retry/reconciliation behavior;
- secret and evidence boundaries;
- natural-language discoverability through a skill or registered tool;
- workflow admission and execution through the same implementation; and
- installed-distribution tests on every supported operating system.

## Reusable capability architecture

### One implementation, two consumers

The intended structure for each enterprise capability is:

```text
configuration + credential provider
                 |
          typed domain client
                 |
       bounded operation/service layer
          /                     \
plugin tool schemas          CLI commands
    |                            |
workflow allowed_tools      natural-language skill
    \____________________________/
             same behavior
```

The workflow isolated worker builds its visible tools from Hermes' registered
tool schemas, applies the sealed allow/deny policy, and rejects unknown names
before billing. Therefore a well-formed plugin tool can serve ordinary chat and
workflow AI nodes without a second Langflow-specific implementation.

Deterministic transformations should remain callable as libraries/CLI commands
or package-contained `script` nodes. A plugin tool is justified when the agent
needs structured parameters and results, provider authentication, mutation
approval, or a long-lived external-service client. None of these capabilities
requires a new core model tool.

### Required cross-capability contracts

Every new or expanded adapter should implement the following conventions:

1. **Configuration:** behavioral settings, server URLs, defaults, and limits go
   in `config.yaml`; credentials go in the credential store or secret
   environment. Preserve compatibility bridges for existing installations but
   do not add new user-facing non-secret `HERMES_*` variables.
2. **Tool shape:** use small task-oriented tools, stable JSON envelopes, typed
   identifiers, bounded strings, explicit pagination cursors, and declared
   maximum page/file/result sizes.
3. **Read/write separation:** reads and mutations use separate tool names.
   Mutations disclose the intended action, require the product's approval
   path, and return enough identity to reconcile an uncertain outcome.
4. **Idempotency:** retries must not duplicate comments, messages, branches,
   commits, merge requests, uploads, folders, or calendar events. Where an
   upstream API lacks idempotency keys, reconcile by a caller-supplied operation
   ID and stable resource query.
5. **Evidence:** retain bounded operation metadata and resource IDs, not
   credentials, prompts, full provider payloads, whole private documents,
   temporary absolute paths, or arbitrary response bodies.
6. **Skills:** the skill explains when and how to use the registered tools or
   CLI, is fully loaded into the current user turn, and does not mutate the
   system prompt. It must cover both read-only research and explicitly approved
   changes.
7. **Workflow:** expose the same canonical tool names to `allowed_tools`; let
   backend admission/doctor decide availability. Outward actions use workflow
   approval and backend-authored actions, not client-side interpretation.
8. **Tests:** unit tests for request/result normalization; contract tests using
   a real local HTTP stub; opt-in live tests; no-secret/error redaction tests;
   workflow admission/execution tests; installed-wheel/plugin tests; and Windows
   tests where COM, Edge, PowerShell, or desktop automation is involved.

### Microsoft 365 should share an auth/client seam

Teams and SharePoint both use Microsoft Graph, and a future non-COM Outlook
adapter would as well. The current Teams authentication is plugin-private while
the legacy SharePoint utility has a separate credential chain. Before adding
SharePoint, define one generic Microsoft Graph credential/session interface
with token caching, requested-scope reporting, tenant/account identity, refresh,
and doctor output. Keep domain tools separate, but do not build a third auth
stack.

Classic Outlook COM remains a distinct provider behind the Outlook operation
contract; Graph Outlook can later be another provider rather than a second set
of user-facing tool names.

### Natural-language skill coverage

Registered tool descriptions make simple operations callable from ordinary
chat, but multi-step business tasks still need skills. Skills should compose
the same canonical tools used by workflows:

| Capability | Skill coverage |
| --- | --- |
| GitLab | Add focused research, change/MR, CI-audit, and Jira-to-GitLab skills. |
| Jira | Add a Jira workbench skill for ticket discovery, investigation, triage guidance, comments, and approved transitions. |
| Confluence | Keep and extend the existing `confluence-research` skill over the structured read adapter. |
| Teams | Add a collaboration skill covering discovery, channel research, drafting, approval, send, reply, and result reconciliation. |
| Outlook | Add an email/calendar skill covering search/read, draft-first composition, explicit send, attachments, scheduling, and calendar reconciliation. |
| SharePoint | Add separate research/read and approved file-management skills. |
| Documents | Keep the existing format skills and add an Ericsson specification-generation skill over the deterministic document service. |
| Browser/computer use | Keep the existing computer-use skill; add site/app-specific skills only where an API adapter cannot provide the operation. |
| Web research | Use the existing research and grounded-citation skills over `web_search` and `web_extract`. |

Skills must not carry credentials or hard-code environment-specific IDs. They
should discover capabilities, use canonical resource identities, and defer all
mutation approval/idempotency behavior to the underlying operation layer.

## Portfolio inventory

| Capability family | Legacy behavior | Current Co-worker state | Natural language | Archon workflow | Verdict |
| --- | --- | --- | --- | --- | --- |
| GitLab | Resolve project; read tree/files; branch; atomic commit; create MR; review diff; collect CI/CD | No GitLab plugin/tool | No dedicated skill/tool | Tool names unavailable | **Missing, P0** |
| Jira | Assigned tickets, select/context, triage, comment/update | `jira_my_tickets`, `jira_get_issue`, `jira_add_comment` | Available | Available when plugin/config is installed | **Partial, P1** |
| Teams | List teams/channels, read, send, reply | Same six operations including `teams_auth` | Available | Plugin tools are reusable; mutation hardening needed | **Near parity, P1 hardening** |
| Outlook | Search/read/send/reply/delete mail, attachments, calendar CRUD/accept | Legacy MCP ported with only noninteractive-stdin hardening | Available through MCP | Existing legacy package works; Archon packaging/tool availability needs proof | **Ported, P1 hardening** |
| Confluence | Script-backed page fetch | Rich read-only browser-session mirror/sync/fetch skill | Strong | No structured workflow-facing adapter | **Partial, P1** |
| SharePoint | File list/stat/download/upload/mkdir/delete/move/copy; recursive fetch/parse | No general SharePoint tool | Browser/computer workaround only | No admitted domain tool | **Missing, P0** |
| Document conversion | Docling conversion across office/PDF/image formats | Generic file extraction and OCR/PDF skills | Good for ad hoc use | No stable conversion/artifact service | **Partial, P0/P1** |
| Document generation | Template extraction, generation, assembly, edit, diff, verify, Ansible, spec builder | DOCX/XLSX/PDF/PPTX skills | Strong generic authoring | No deterministic enterprise pipeline | **Partial, P0** |
| Spreadsheet processing | Extract/map/filter, per-row enrichment, CSV/XLSX output | `xlsx` skill and file/terminal abilities | Strong ad hoc | Missing stable batch contracts | **Partial, P1** |
| Browser automation | Limited Playwright use inside flows | 10 core browser tools plus gated CDP/dialog tools | Strong | Technically reusable when installed/admitted; prefer domain APIs | **Enhanced** |
| Web research | Flow-specific fetch/search | `web_search`, `web_extract`, provider registry | Strong | Reusable subject to provider capability/config | **Enhanced** |
| Computer use | Arbitrary PowerShell and some Playwright automation | Cross-platform `computer_use` with app/window/browser actions | Strong | Possible but interactive/mutating semantics need deliberate workflow design | **New/enhanced** |
| Image generation | Prompt/image generation and writer | `image_generate`, vision/browser/file tools | Strong | Reusable with provider/tool admission | **Enhanced; flow packaging remains** |
| Generic orchestration | ACP LLM, loop timer, run-flow trigger | Provider portability, workflow runtime, inline agents | Strong | Phase 6 needed for repeated subgraphs; runtime child flows intentionally absent | **Superseded with one planned gap** |
| Privacy vault | Reversible anonymize/de-anonymize mappings | Intentionally not ported | No | No | **Excluded by product decision** |

## Priority capability assessments

### GitLab — comprehensive support required

#### Legacy baseline

The legacy GitLab components already establish the minimum business outcomes:

- parse a GitLab URL from a Jira ticket and resolve project ID/default branch;
- walk repository trees and fetch bounded source files;
- read an arbitrary root/tree/blob URL, including refs with `/` characters;
- create or reuse a ticket-named branch;
- create one atomic multi-file commit from create/update/delete actions;
- create or reuse an open merge request;
- fetch MR diffs for review;
- collect pipelines, branches, `.gitlab-ci.yml` and its includes, and CI
  variable information; and
- support GitLab deployments requiring client certificates.

The LLM prompt construction and two-pass review embedded in legacy components
should not be part of the GitLab adapter. Those are workflow/skill policies.
The adapter should expose facts and bounded mutations; Archon structured output
and normal agent execution should perform analysis.

#### Target operation surface

Build a bundled edge plugin, provisionally `ericsson-gitlab`, backed by a pure
typed client. Preserve tool names once published. A comprehensive first version
should cover:

| Area | Required read operations | Required mutation operations |
| --- | --- | --- |
| Connection | status/doctor, authenticated user, server/version/capabilities | none |
| Projects | resolve URL/path/ID; get metadata/default branch; list/search authorized projects | none initially |
| Repository | list tree with cursor/depth; get raw/text file; compare refs; get commit/diff; bounded repository search where supported | none |
| Branches/tags | list/get branches and tags | create branch; delete branch only as a separately approved operation |
| Commits | get commit, diff, statuses | atomic multi-action commit with caller operation ID and expected base SHA |
| Merge requests | list/get; changes/diffs; discussions/notes; approvals; pipeline summary | create/update MR; add discussion/note; rebase/merge only as separately approved later operations |
| Pipelines/jobs | list/get pipelines and jobs; read bounded logs; list/download bounded artifacts | retry/cancel/play only as distinct approved tools |
| CI configuration | retrieve root and recursively resolved local/project includes with cycle/depth/byte bounds; call GitLab CI lint where available | none |
| Variables | names, scopes, protection/masking metadata only | variable values and variable mutation excluded from the initial agent surface |

Important contract details:

- Accept server-relative URLs and IDs but return canonical project/ref/resource
  identities. Never let the model hand-build API URLs.
- Paginate all list operations and cap recursive trees, diffs, logs, artifacts,
  and include traversal. Oversize content becomes a digest-bound artifact with
  a bounded preview, not an unbounded tool result.
- Support personal token and reviewed enterprise mTLS configuration. The
  GitLab base URL and behavioral limits belong in `config.yaml`; tokens and
  private keys remain secret inputs.
- For atomic commits require `branch`, `expected_head_sha`, `commit_message`,
  and bounded actions. Reject head drift rather than applying changes to an
  unexpected branch state.
- Branch creation reconciles an existing branch against the requested base.
  MR creation reconciles source/target and a caller operation ID. Note/comment
  creation must also reconcile retry outcomes.
- Do not return CI variable values, credentials, raw auth errors, or full
  unbounded GitLab responses.

#### Skills and workflows

Provide focused skills rather than one giant GitLab playbook:

- `gitlab-research`: resolve projects, inspect files/history/MRs/pipelines;
- `gitlab-change`: create a branch and atomic commit, then open/update an MR
  only after approval;
- `gitlab-ci-audit`: inspect resolved CI config, pipelines, jobs, logs, and
  artifacts;
- `jira-gitlab-change`: coordinate Jira context with GitLab without merging the
  two clients.

Archon workflows should name the same plugin tools in `allowed_tools`. The
Jira-to-GitLab workflow owns triage, structured change proposals, review, and
approval. The GitLab plugin owns API correctness, bounds, identity,
idempotency, and reconciliation.

#### GitLab completion definition

GitLab is complete for legacy migration when it can reproduce project resolve,
bounded source collection, branch creation, atomic commit, MR creation, diff
review input, CI configuration/include collection, and pipeline/job evidence
through both chat skills and an installed Archon workflow—without embedded LLM
calls or leaking variable values.

### Confluence

The current `confluence-research` skill is substantially better than the legacy
single fetcher. It can probe authentication, sign in with an enrolled Edge
profile, enumerate spaces, sync/fetch pages, maintain a stable Markdown mirror,
incrementally update a manifest, and optionally retrieve attachments. It works
through same-origin browser REST so enterprise SSO/mTLS remains in the enrolled
session.

What remains:

- extract the read/sync/fetch behavior behind a stable JSON CLI/service
  contract rather than requiring workflows to interpret skill scripts;
- expose bounded search, page fetch, space/page enumeration, attachment
  metadata/download, and mirror status as read-only operations;
- return stable page IDs, versions, canonical URLs, titles, ancestor paths,
  update timestamps, and artifact references;
- provide doctor output for enrolled browser/session availability and make
  unattended session-expiry behavior explicit; and
- add a small `confluence-research` tool/CLI bridge usable from workflows while
  keeping the existing skill as the natural-language guide.

Create/update/comment operations are not needed to port the reviewed legacy
flows and should be a later, separately approved capability.

### Jira

Current Co-worker functionality is intentionally small:

- `jira_my_tickets(max_results)` uses a fixed assigned/unresolved JQL;
- `jira_get_issue(key)` returns summary, status, priority, description, five
  recent comments, and extracted GitLab URLs; and
- `jira_add_comment(key, body)` creates a comment.

This supports the current ticket-summary workflow, but not the legacy triage
and Jira-to-GitLab portfolio. Required expansion:

- arbitrary bounded JQL search with field selection, pagination, and stable
  issue summaries;
- full issue detail with paginated comments, links, attachments metadata,
  labels/components/fix versions/assignee/reporter, and relevant custom fields;
- transitions and transition discovery;
- bounded create/update field operations where there is a concrete use case;
- comment listing and idempotent comment creation with reconciliation;
- attachment download through an artifact boundary; and
- server/auth doctor support for Jira Server/Data Center and Jira Cloud.

The legacy implementation supports bearer and basic-email auth and sometimes
uses `curl` because a target Jira deployment rejects Python TLS fingerprints.
The target should model transport as a provider choice and test the actual
enterprise path. Do not silently fall back from a failed authenticated request.

`JIRA_BASE_URL` is existing non-secret environment configuration. New design
should make the base URL and behavior settings first-class `config.yaml` values
while preserving a compatibility bridge; the PAT/API token stays secret.

Triage categorization, summary generation, ticket selection, and Jira-to-GitLab
policy belong in skills/workflows, not the Jira client.

### Browser automation

Co-worker already provides a comprehensive browser surface:

- navigate, accessibility snapshot, click, type, scroll, back, key press;
- image discovery and screenshot/vision analysis;
- browser-console evaluation;
- enrolled browser profiles and CDP attachment; and
- gated native-dialog handling through CDP, plus typed download/file-input
  actions through the `computer_use` browser route.

This is sufficient for natural-language tasks that require clicking, filling,
or collecting information from dynamic pages. The Confluence skill is direct
evidence that an enrolled enterprise browser session can be reused for a
domain-specific capability.

Remaining work is operational, not a new browser engine:

- ensure the target Desktop/Windows release installs and doctors the browser
  dependency and enrolled-profile configuration;
- add domain skills that prefer stable APIs/structured tools and use the
  browser only when the site requires it;
- define artifact handling for screenshots/downloads without exposing
  temporary absolute paths; and
- explicitly decide which browser mutations, if any, are acceptable in
  unattended workflows. Generic browser control should not bypass a domain
  adapter's approvals and idempotency.

### Web research

`web_search` and `web_extract` already provide the core research primitives.
The search provider registry supports multiple backends; extraction returns
bounded Markdown/text, handles PDF URLs, and saves oversize content for paged
reading. Research and citation skills add the natural-language process layer.

This is functionally sufficient for legacy open-web research. Complete the
migration by:

- documenting/doctoring which provider is selected and which keys or local
  services it needs;
- verifying the exact provider/tool disposition under Archon v5 for each
  packaged workflow;
- using grounded-citation skills for sourced reports; and
- adding enterprise-source adapters rather than treating web search as a
  substitute for Confluence, SharePoint, Glean, Jira, or GitLab.

### Computer-use automation

The `computer_use` capability can capture screens through vision/accessibility
or set-of-marks, click/double-click/right-click, drag, scroll, type, press keys,
set values, wait, list/focus apps and windows, and perform typed browser
actions. It is cross-platform, background-first, and has an existing
natural-language skill. This exceeds the legacy generic PowerShell component
for interactive application control.

For ordinary Co-worker chat this capability is already comprehensive enough to
operate desktop applications when the required driver and OS permissions are
available. For workflows, use a stricter policy:

- prefer Jira/GitLab/Graph/Outlook APIs for repeatable business operations;
- use `computer_use` only for a concrete UI-only application and require
  explicit approval for mutations;
- bound action count, wall time, screenshots, downloads, workdir, and
  cancellation under the workflow attempt;
- record semantic action/result evidence, not raw screen history or temporary
  paths; and
- test foreground/background behavior on Windows Desktop.

Do not port `powershell_script_runner.py` as an unrestricted elevated tool.
Fixed, reviewed diagnostics belong in package-contained scripts; ad hoc local
administration can already use terminal/computer-use under the normal agent
approval policy.

### Outlook

The current `plugins/outlook-mcp` source is functionally the legacy MCP server.
The only code difference found is setting subprocess stdin to `DEVNULL` so the
server cannot accidentally block on inherited input. It exposes:

- mailbox list;
- message list/search filters, read, send/draft, reply/draft, delete, and
  attachment download; and
- calendar list, create, update, delete, and meeting acceptance.

It remains Windows-only and requires Classic Outlook running, signed in, and
online because PowerShell drives Outlook COM.

Required hardening/completion:

- publish stable message/event identifiers that remain valid across list calls
  instead of relying on ephemeral list positions where applicable;
- add bounded pagination/result and attachment size contracts;
- separate draft creation from send and make send/delete/calendar mutations
  explicit approved operations;
- add caller operation IDs and reconciliation for uncertain send/calendar
  outcomes;
- make Classic Outlook/COM/session readiness visible in doctor;
- produce redacted structured errors rather than raw PowerShell/COM output;
- prove teardown and event-loop behavior on Windows; and
- make the same canonical tools available to Archon workflows without relying
  on an unsealed global MCP definition. A bundled plugin wrapper over an
  isolated COM subprocess is likely cleaner than copying the MCP server into
  every workflow package.

Keep the existing tool names for compatibility. A future Microsoft Graph
provider should implement the same Outlook operation contract rather than
introducing a competing vocabulary.

### Teams

The current Teams plugin preserves the main legacy outcomes and improves
sign-in: `teams_auth`, `teams_list`, `teams_channels`, `teams_read`,
`teams_send`, and `teams_reply` use a device-code flow with a local token cache.
The legacy read component required a manually copied Graph Explorer token in
some environments, so the installed tenant/scope behavior must be validated.

Gaps for a comprehensive tool:

- follow Graph pagination for joined teams, channels, and messages;
- support search/date filters and bounded thread/reply reading;
- return descriptions, membership type, subjects, normalized body plus content
  type, web URLs, and attachment/hosted-content metadata;
- add chats/direct messages only if required by an identified workflow;
- add file/attachment handling through SharePoint drive identities rather than
  embedding downloads in message responses;
- support safe rich text where required;
- add idempotency/reconciliation for send and reply; and
- expose scope/account/tenant/token-expiry diagnostics through the shared
  Microsoft Graph auth seam.

The existing tool names and natural-language availability should be preserved.

### SharePoint

No general SharePoint adapter exists in Co-worker. The legacy utility is more
capable than the Langflow fetcher alone: it resolves normal and
`/:w:/r/`-style UI URLs, discovers site/library/drive identities, paginates
Graph listings, retries throttling, and supports list, stat, download,
batch-download, upload (including chunked upload), mkdir, recycle-bin delete,
move/rename, and asynchronous copy. The Langflow component adds recursive
filtering, anchor-file/template discovery, per-file bounds, metadata, and
Docling/basic parsing.

Build a SharePoint plugin on the shared Microsoft Graph auth/client seam with:

- `sharepoint_resolve` for canonical site/drive/item identity from a browser
  URL;
- bounded `sharepoint_list` and `sharepoint_stat`;
- `sharepoint_download` and bounded batch download into managed artifacts;
- recursive discovery with explicit depth/item/byte ceilings, glob filters,
  and anchor-file discovery;
- separate approved upload, create-folder, recycle, move/rename, and copy
  operations;
- upload conflict policy and expected-version/eTag preconditions;
- async copy operation identity and polling/reconciliation; and
- optional handoff to the document-conversion service, not embedded duplicate
  parsers.

Natural-language skills should cover “find/read documents” and “manage files”
separately. Workflow packages should use the same read/mutation tools and store
downloaded outputs as bounded artifacts. Do not expose Graph bearer tokens,
preauthenticated download URLs, or local temporary paths.

### Document generation and conversion

Co-worker's bundled `docx`, `xlsx`, `pdf`, `powerpoint`, and
`ocr-and-documents` skills are a strong generic natural-language authoring
surface. `read_file` also extracts text from DOCX and XLSX. These are adequate
for ad hoc user requests such as creating a report, editing a template, or
building a spreadsheet.

They do not yet replace the legacy workflow pipeline, which includes:

- Docling conversion of PDF, DOCX, PPTX, XLSX, HTML, images, and audio with
  Markdown/HTML/DocTags/lossless JSON outputs, OCR and table options;
- template structure extraction;
- section generation, assembly, editing, split/merge/rename/remove/replace,
  cross-reference handling, headers/tables, and document diff;
- verification against ground truth, placeholders, cross references, and
  timelines, with optional LLM review;
- JSON-to-spreadsheet generation, append/multi-sheet/flatten behavior, and
  spreadsheet column extraction/mapping;
- Ansible generation; and
- a large telecom specification builder that can acquire SharePoint templates.

Recommended target:

1. Extend the existing document libraries/scripts behind a stable
   `hermes document`-style CLI and Python service rather than adding a core
   model tool.
2. Define deterministic operations such as `inspect`, `convert`,
   `apply-template`, `assemble`, `edit`, `diff`, `validate`, `render`, and
   `artifact-manifest`, each with bounded JSON input/output.
3. Keep domain-specific telecom section rules and Ansible templates in a
   separate Ericsson package/skill layered on the generic service.
4. Let natural-language skills invoke the CLI/service. Expose only the small
   structured plugin wrappers that workflows genuinely need, or call the
   deterministic CLI through package-contained scripts.
5. Make every produced artifact include media type, size, digest, relative
   artifact path, validation result, and bounded warnings. Never treat a
   successful LLM response as proof that a document rendered correctly.

Completion requires round-trip and render validation for DOCX/XLSX/PDF/PPTX,
template fidelity tests, installed dependency checks, and Windows tests for
the actual LOOP24 document corpus.

## Remaining legacy building blocks

| Legacy component family | Migration disposition |
| --- | --- |
| `ericsson_email` search/read/send | Superseded by Outlook MCP operations; consolidate around canonical Outlook tool names. |
| `ericsson_imggen` generation/writer | Native image generation plus file tools cover the primitive. Port only flow-specific HTML/layout/artifact behavior. |
| `ericsson_orchestrate/acp_llm.py` | Superseded by normalizer v5 provider/model authority. Do not port a second provider client. |
| `context7_library_check.py` | Use configured Context7 MCP or normal web/repository research; no dedicated core integration required. |
| `loop_timer.py` | Replace repeated subgraphs with Phase 6 `loop_group` or independent scheduled runs. |
| `run_flow_trigger.py` | Intentionally do not port unrestricted runtime child-flow execution. Use static workflow DAGs/variants. |
| `llm_response_to_email_fields.py` | Use Archon structured output with a bounded email-draft schema. |
| `record_enricher.py` | Use `loop_group` for per-item AI work or a bounded batch service when semantics permit. |
| `rule_based_record_filter.py` | Deterministic package script/library; no model tool needed. |
| `spreadsheet_data_extractor.py` / `json_to_spreadsheet.py` | Extend the XLSX/document service with stable batch schemas. |
| BOS Blues event consolidator | Deterministic transform in a package script/library. |
| `powershell_script_runner.py` | Do not port as an unrestricted elevated tool. Use terminal/computer-use interactively or fixed reviewed workflow scripts. |
| reversible privacy components | Remain excluded pending an explicit privacy-vault product decision. |

## Recommended delivery roadmap

### Tranche 0 — contracts and an Archon proof

- Define the shared tool envelope, bounds, mutation, idempotency, artifact, and
  doctor conventions.
- Convert Jira ticket summary and Outlook inbox digest into small explicit
  Archon v5 proofs.
- Verify plugin/MCP discovery in the isolated installed worker and Windows
  Desktop release, not only a source checkout.

### Tranche 1 — migration-critical reads

- Comprehensive GitLab read/project/repository/MR/CI operations.
- SharePoint resolve/list/stat/download/batch and shared Graph auth.
- Document convert/inspect/validate/artifact service.
- Structured read-only Confluence operations.
- Jira JQL search and complete issue/comment/attachment reads.

This tranche unlocks most audit, research, summary, and document-input flows
without granting broad mutation authority.

### Tranche 2 — controlled mutations

- GitLab branch/atomic commit/MR/note operations.
- Jira transitions, bounded field updates, and idempotent comments.
- SharePoint upload/folder/recycle/move/copy.
- Outlook and Teams idempotency/reconciliation hardening.
- Explicit workflow approval/evidence tests for every outward action.

### Tranche 3 — document outcomes and Phase 6 composition

- Template assembly/edit/diff/render/verify and telecom/Ansible layers.
- Spreadsheet batch transform/enrichment contracts.
- Phase 6 `loop_group` and migration of the eight iterative flows.
- Static NW Hardening workflow variants and final end-to-end migration tests.

## Proposed ownership boundaries

| Workstream | Owns | Does not own |
| --- | --- | --- |
| GitLab adapter | GitLab auth/client, schemas, bounds, identity, mutations, reconciliation | Jira policy, LLM review prompts, workflow graph |
| Microsoft Graph foundation | account/tenant/scopes/token cache/request/retry/doctor | Teams/SharePoint domain decisions |
| Teams/SharePoint adapters | domain operations and result normalization | generic document parsing or workflow scheduling |
| Jira adapter | Jira transport and issue/comment/transition contracts | triage policy or GitLab operations |
| Document service | parse/convert/template/edit/render/validate/artifacts | SharePoint authentication or domain research |
| Skills | natural-language task guidance and tool/CLI composition | duplicate API clients |
| Workflow packages | orchestration, structured AI output, approvals, retries, evidence | duplicate domain implementations |

## Acceptance criteria

The enterprise tool migration is complete when:

- every retained legacy business operation maps to an existing canonical tool,
  CLI operation, deterministic package script, or an explicitly documented
  no-port decision;
- GitLab, Jira, Confluence, Teams, Outlook, and SharePoint operations are usable
  through natural language without the user writing YAML;
- the same canonical operations can be admitted and invoked by Archon workflows
  without a second implementation;
- all reads are bounded and paginated, all mutations are separately approved
  and reconcilable, and retries cannot duplicate outward effects;
- provider responses, prompts, commands, credentials, secret values,
  preauthenticated URLs, and unnecessary local paths are absent from public
  evidence;
- configuration and doctor explain missing providers, credentials, scopes,
  platform dependencies, and installed extras before execution;
- skills are fully read into the current user turn and never alter the system
  prompt or cached prefix;
- installed-distribution E2E tests cover the real plugin/tool discovery path;
- Windows tests cover Outlook COM, enrolled browser/computer use, document
  rendering, and release installation; and
- upstream-owned generic seams are invariant-tested and recorded in the
  upstream customization ledger.

## Provenance and key claims

| Claim | Source |
| --- | --- |
| Full legacy component inventory | Legacy `custom_components/` and `mcp/outlook_mcp/` at `8ca26f8` |
| GitLab legacy operations | Legacy `custom_components/ericsson_gitlab/` and `custom_components/ericsson_jira/README.md` |
| SharePoint CRUD and recursive parse behavior | Legacy `utils/sp_files.py` and `custom_components/ericsson_parsers/sharepoint_files_fetcher.py` |
| Document pipeline breadth | Legacy `custom_components/ericsson_docgen/` and `custom_components/ericsson_parsers/` |
| Outlook parity | Diff of legacy `mcp/outlook_mcp/` against target `plugins/outlook-mcp/`; only README attribution and `stdin=DEVNULL` differ |
| Current Jira and Teams tools | Target `plugins/ericsson-jira/jira_tools.py` and `plugins/ericsson-teams/teams_tools.py` |
| Current browser/web/computer tools | Target `tools/browser_tool.py`, `tools/web_tools.py`, `tools/computer_use/`, and `website/docs/reference/tools-reference.md` |
| Current natural-language skills | Target `skills/ericsson/confluence-research/`, `skills/autonomous-ai-agents/computer-use/`, and `skills/productivity/{docx,xlsx,pdf,powerpoint,ocr-and-documents}/` |
| Shared workflow tool enforcement | Target `plugins/workflow/compat.py`, `plugins/workflow/executors/ai.py`, and `agent/plugin_agent_worker.py` |
| Ericsson installed capability set | Target `capabilities/ericsson.json`, `capabilities/mcp-servers.yaml`, and `capabilities/workflow-packages/ericsson/` |
