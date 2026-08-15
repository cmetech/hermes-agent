# Final adversarial plan re-review prompt — Ericsson connector plugins

Paste everything below the line into a fresh, capable model or coding agent
with read and shell access to the three repositories named in this prompt.

The reviewer must assess the complete design and executable implementation
plans for four independently deliverable Ericsson connector plugins:

1. GitLab
2. Jira
3. SharePoint
4. Confluence

The review must determine whether following the plans as written is likely to
preserve the proven LOOP24 behavior, integrate correctly with Hermes' plugin
architecture, and make the connectors usable through natural-language chat,
the interactive CLI/TUI, Electron Desktop, Kanban workers, cron, Archon
workflows, gateway/API surfaces, and installed branded distributions.

This is an adversarial **plan-quality and porting-fidelity review**, not an
implementation task and not a review of completed connector code. Try to
falsify the plans' assumptions before implementation begins. Identify missing
work, wrong premises, incompatible interfaces, incomplete behavioral coverage,
incorrect sequencing, non-executable tests, and acceptance gates that could
allow a broken or incomplete port to ship.

Do not modify production code, tests, generated files, existing documentation,
Git history, branches, worktrees, or refs. Do not create feature branches,
commit, rebase, merge, push, publish, open a pull request, build a release, or
contact live Ericsson/GitLab/Jira/Microsoft/Confluence services. The only
authorized repository write is the final review report named under
**Required output**.

## Explicit security exclusion

This review intentionally excludes security work because standalone security
and threat-review workflows trigger the execution platform's safety gate.

Do **not**:

- run a threat-model, security-audit, security-review, penetration-testing, or
  vulnerability-scanning skill or workflow;
- search for exploits, construct malicious payloads, probe credentials, or
  attempt to bypass authentication or authorization;
- use real credentials or contact live services;
- report security vulnerabilities or speculative hardening recommendations;
  or
- stop the functional review because a security-oriented path is unavailable.

Normal authentication and configuration behavior remains in functional scope:
the plans must preserve the supported LOOP24 login modes, expose configuration
correctly, recognize missing/expired/unready states, and allow the intended
operation to run. Review those paths as product functionality, not as security
analysis. Deterministic tests already specified by the design may be evaluated
for plan completeness, but do not run standalone security suites.

---

## Role

You are a skeptical principal-level reviewer experienced with Python,
TypeScript/React/Electron, plugin systems, REST clients, Microsoft Graph,
browser-mediated enterprise APIs, durable workflow engines, scheduled jobs,
multi-agent workers, packaging, Windows deployment, and legacy-system
migration.

Your job is to determine whether the design and plans are implementation-ready,
not to bless them, summarize them, or rewrite them wholesale.

Assume every claim is unproven until you trace it to:

1. the legacy LOOP24 behavior being ported;
2. current Ericsson capability source where functionality already exists;
3. current Hermes plugin/runtime/client architecture; and
4. an explicit plan task, file owner, RED test, GREEN change, verification
   command, regression gate, and acceptance criterion.

Do not stop after finding the first gap. Review all four plans and their shared
foundation. A locally sound connector plan can still fail because another plan
assumes a different manifest, configuration, Graph, skill, workflow, vendoring,
or release contract.

Commit messages, filenames, existing tests, assessment prose, and prior
approvals are leads, not proof. Inspect the actual current code and legacy
implementation needed to validate each premise. When a missing capability may
be deliberate, use Git history and current documentation to distinguish a real
gap from an intentional boundary.

Do not treat the absence of future connector files as an implementation defect.
They have not been implemented. Report whether the **plan** correctly accounts
for creating them and proving their behavior.

## Repository scope and preservation rules

### Hermes target repository

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

At this final re-review prompt creation:

- connector-development target: `base`;
- expected `base` and `origin/base`:
  `786f8dc0175410044000113233bec2bb610e7733`;
- the unrelated npm-install-gate work is merged into `base`; it touches only
  installer files/tests and has no connector-plan overlap;
- literal `main` is synchronization-only and must not be used for development;
- `docs/assessments/` contains preserved untracked user documents; and
- the specification and four plans under `docs/superpowers/` are ignored by a
  repository rule, so they may not appear in ordinary `git status` output.

### Ericsson source repository

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities`

At prompt creation:

- branch: `main`;
- expected `HEAD`: `dae405ede7049b621e502d9259f97481c940a65b`;
- preserved user modifications exist in:
  `mcp/outlook-mcp/src/outlook_cli/__init__.py` and
  `plugins/ericsson-teams/graph_auth.py`.

Do not alter, stash, reset, clean, or accidentally stage either file.

### Legacy LOOP24 repository

`/Users/coreyellis/code/gitlab.rosetta.ericssondevops.com/loop_24`

This repository is behavioral evidence. It is read-only for this review.

### Worktree rules

Begin by recording each repository's branch, exact SHA, status, and worktree
list. Preserve every existing worktree and every user change. Use read-only
commands. Do not switch the shared checkout. If a detached temporary worktree
is genuinely needed for historical inspection, create it outside existing
worktree paths and remove only the temporary worktree you created.

If current repository state differs from the expected state, record the
difference and determine whether the review inputs themselves still match the
hashes below. Do not mutate refs to make them match.

## Immutable review inputs

Read these five files completely. Verify their SHA-256 hashes before review:

| Artifact | Expected SHA-256 |
| --- | --- |
| `docs/superpowers/specs/2026-08-09-ericsson-connector-plugins-design.md` | `7f378086c722d35434fba5892349fe8438083779dcd3c8bc622ae278b8218b29` |
| `docs/superpowers/plans/2026-08-09-ericsson-gitlab-connector.md` | `b6fbd791514d36cad8448a148f6ba2d18953cc15a19fc46991e2e10944a105ea` |
| `docs/superpowers/plans/2026-08-09-ericsson-jira-connector.md` | `f1dce2669dddaac0e4d72080b827a8dddfdecdcfb22c6ef1bd0f7e5800926a79` |
| `docs/superpowers/plans/2026-08-09-ericsson-sharepoint-connector.md` | `524ff2f1fc174d2bc37ff9524dd999a86f9f70a441e48ab17a7315fea2d41e2b` |
| `docs/superpowers/plans/2026-08-09-ericsson-confluence-connector.md` | `ed3d495bf9f1086add01cad5ee8e58f28a21be2dfeef19d5cb01bad8b7eabd56` |

If any hash differs, stop and report `REVIEW_INPUT_CHANGED` with the observed
hash. Do not silently review a different plan.

## Sources of truth to read before judging the plans

### Project and plugin architecture

Read completely:

1. `hermes-agent/AGENTS.md`
2. `hermes-agent/apps/desktop/AGENTS.md`
3. `hermes-agent/website/docs/user-guide/features/plugins.md`
4. `hermes-agent/website/docs/developer-guide/plugins/index.md`
5. `ericsson-capabilities/AGENTS.md`
6. `ericsson-capabilities/docs/README.md`
7. `ericsson-capabilities/docs/configuration.md`
8. `ericsson-capabilities/sets/ericsson.json`

Also compare the local plugin documentation with the current official pages,
when network access is available:

- `https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins`
- `https://hermes-agent.nousresearch.com/docs/developer-guide/plugins`

Record the access date and any meaningful local/upstream documentation skew.
Do not use a newer web page to invent an interface absent from the checked-out
runtime. Current code remains the implementation authority.

### Migration assessments

Read completely:

1. `hermes-agent/docs/assessments/loop24-migration/README.md`
2. `hermes-agent/docs/assessments/loop24-migration/legacy-workflow-portability.md`
3. `hermes-agent/docs/assessments/loop24-migration/tool-capability-portability.md`

Treat these as discovery evidence and intended scope, not as proof that every
legacy behavior was inventoried correctly.

### Current Hermes implementation paths

Inspect the real current implementation, including relevant unchanged callers:

- plugin manifests, discovery, loading, enable/disable precedence, plugin
  kinds, `PluginContext`, CLI registration, tool and skill registration;
- tool registry and toolset construction;
- profile configuration and `.env` credential helpers;
- `hermes tools`, plugin commands, setup actions, and readiness paths;
- web routers/models and Electron Desktop Tools settings;
- gateway/JSON-RPC command and tool catalog projection;
- natural-language CLI/TUI/Desktop agent construction and prompt-cache rules;
- cron job execution and profile selection;
- Kanban dispatcher/worker agent construction and toolset propagation;
- workflow admission, supported flat `requires`, `allowed_tools`, backend
  readiness/tool snapshots, action authority,
  snapshotting, resumption, and installed-distribution behavior;
- capability staging and source-first vendoring;
- existing Microsoft Graph auth/client modules and Azure identity adapter;
- existing Ericsson Jira and Teams plugins; and
- upstream-customization ledger and merge-rehearsal checks.

Do not assume a proposed interface exists because the plan names it. Confirm
whether it exists, needs a generic extension, or conflicts with an established
pattern.

### Legacy LOOP24 behavior

Do not sample only the obvious flow JSON. Trace the implementation used by the
flows, including custom components, utility modules, scripts, configuration,
and generated artifacts.

At minimum inspect:

- Jira assigned-ticket summary, defect-triage, and Jira-to-GitLab flows;
- CI File Auditor and GitLab-related network-hardening blocks;
- SharePoint file utilities, audit utilities, custom components, and
  SharePoint-dependent flows;
- Confluence utilities, research/synchronization behavior, and
  Confluence-dependent flows;
- supporting config, authentication selection, retry behavior, file formats,
  output contracts, and Windows launch assumptions; and
- tests or documentation that clarify intended behavior.

For each behavior, distinguish:

- actually executed production behavior;
- dead or unreachable legacy code;
- behavior supplied by Langflow rather than the custom component;
- embedded LLM reasoning that should move to a Hermes skill;
- deterministic integration behavior that belongs in a plugin tool; and
- deliberate non-goals or deferred capability.

## Approved delivery intent

The design proposes four separate user-testable releases:

| Release | Deliverable | Proposed Desktop version |
| --- | --- | ---: |
| 1 | Shared standalone-plugin configuration foundation and GitLab | 5.5.0 |
| 2 | Jira enhancement, triage skill, and single-ticket showcase | 5.6.0 |
| 3 | Generic Graph enhancement, SharePoint files, and permission audit | 5.7.0 |
| 4 | Browser-mediated, read-only Confluence connector | 5.8.0 |

Each connector is sourced in `ericsson-capabilities`, vendored into Hermes,
bundled but disabled by default, configurable through CLI and Desktop, and
usable by the same agent/tool path across chat, workers, schedules, and
workflows. Connector skills provide guidance and reasoning; plugin tools own
deterministic remote operations. No connector introduces a hidden second LLM
or a new core model tool.

The reviewer must assess whether the plans actually achieve that intent.

## Non-negotiable plan invariants

A demonstrated plan violation is Critical or Important depending on how much
of a release it can invalidate.

1. **One source owner.** Ericsson connector code, descriptors, connector-owned
   skills, workflows, and documentation originate in `ericsson-capabilities`.
   Hermes receives exact, attributable vendored bytes. Plans must not create
   divergent hand-maintained copies.
2. **Plugin architecture is genuine.** Each connector uses supported manifest,
   lifecycle, registration, configuration, and toolset mechanisms. Connector
   names do not leak into generic Hermes production branches where validated
   metadata or plugin registration should drive behavior.
3. **Disabled means absent.** A bundled standalone connector is not enabled for
   a fresh profile. While disabled, its code is not imported and its tools and
   plugin-owned skills do not enter a model request. Static configuration
   metadata may still be displayed without importing executable plugin code.
4. **Enablement is exact.** Explicit enable and disable precedence,
   disabled-by-default behavior for every profile, restart/fresh-session
   behavior, and retained settings are deterministic and tested. There are no
   Jira users to migrate, but upgraded profiles contain historically
   auto-seeded Jira enablement: a manifest-driven transition clears it exactly
   once and preserves explicit enables made afterward. The existing workflow
   backend and unchanged Teams backend remain enabled.
5. **Configuration has one authority.** Non-secret settings live in profile
   `config.yaml`; credentials use the existing profile credential mechanism.
   CLI and Desktop consume the same backend validation, readiness, and setup
   actions. Desktop does not implement connector resolution independently.
6. **The core remains narrow.** Reuse and minimally widen generic plugin,
   Graph, configuration, and staging infrastructure. Do not add connector-
   specific core tools, prompt mutation, synthetic conversation messages, or
   global toolset swaps.
7. **Prompt caching remains stable.** Enabling, disabling, reconfiguring, or
   changing a plugin tool fingerprint affects new conversations; it does not
   mutate the system prompt or cached tool prefix of an existing conversation.
8. **Skills and tools stay separate.** Skills are fully read into the current
   user turn and provide reasoning/workflow guidance. Tools perform bounded
   deterministic integration work. No skill duplicates an HTTP/Graph/browser
   client or embeds a hidden provider call.
9. **Every execution surface uses the same plugin.** Interactive CLI/TUI,
   Electron Desktop chat, gateway/API sessions, Kanban workers, cron, and
   Archon workflows receive the same registered schemas and implementation,
   subject to the executing profile and unattended/approval limitations.
10. **Workflow declarations are exact.** Archon workflows use the supported
    flat `requires` service-id list, exact per-node tools, and preserve
    `allowed_tools: []`. Every production admission path receives backend-
    authored ready-service/tool facts and blocks before run creation when they
    are unavailable. The Jira release does not claim its Phase 6 `loop_group`
    multi-ticket loop, and no workflow uses unsupported language features.
11. **Legacy behavior is accounted for.** Every in-scope LOOP24 operation is
    explicitly preserved, intentionally adapted, or explicitly excluded with
    rationale and UAT impact. Refactoring or repackaging does not silently
    narrow behavior.
12. **Writes are deliberate.** Remote mutations are separate operations with
    preview or dry-run where supported, ordinary interactive approval or
    admitted workflow authority, conflict handling, and no accidental replay
    after an ambiguous result.
13. **Retries do not change semantics.** Reads may retry classified transient
    failures within bounds. Writes, uploads, comments, branches, commits,
    merge requests, copies, moves, and recycle operations require operation-
    appropriate reconciliation rather than blind duplication.
14. **Cancellation reaches the real work.** HTTP calls, curl compatibility,
    Graph transfers/polling, browser sessions, file operations, workers, cron,
    and workflows have deadlines and cancellation paths that terminate or
    safely reconcile the underlying operation.
15. **Performance is bounded.** Pagination, recursion, included files, result
    counts, response bytes, conversion size, attachments, download/upload
    bytes, upload chunks, polling, browser processes, sync scope, evidence, and
    diagnostics have explicit tested bounds before uncontrolled work occurs.
16. **Windows is a first-class runtime.** Executable discovery, path handling,
    subprocess behavior, browser enrollment, profile storage, temporary files,
    Graph identity, curl compatibility, and installed Desktop operation are
    tested or covered by mandatory installed Windows UAT.
17. **Packaging is real.** Installed wheels and branded Desktop builds contain
    manifests, descriptors, modules, skills, workflows, and docs without
    borrowing from either source checkout.
18. **Upstream preservation is explicit.** Every generic Hermes change is
    minimal, invariant-tested, recorded in the upstream-customization ledger,
    and exercised by upstream merge rehearsal. Ericsson-specific source does
    not become an upstream-owned core patch.
19. **Releases are independent.** Each release can be enabled, tested,
    disabled, rolled back, and patched without depending on unshipped later
    connectors. Later phases begin only after the preceding installed UAT is
    accepted or explicitly waived.
20. **Plans are executable.** Every task names the correct repository and
    working directory, exact file ownership, RED command and expected failure,
    GREEN change, verification command, narrow staging scope, atomic commit,
    and stop/authorization boundary.

## Required review method

### 1. Establish scope and build traceability matrices

- Verify the five hashes and all repository states before substantive review.
- Build a matrix for every plan task with `complete`, `partial`, `missing`, or
  `contradicted` plan coverage.
- Map every approved design requirement to one or more concrete tasks and
  tests. Identify orphan design requirements and plan work that lacks design
  authority.
- Identify every proposed production file outside the connector's owned
  source or an established generic seam. Confirm the change is necessary and
  has a concrete consumer.
- Check ordering and prerequisites across the four plans. Find circular
  dependencies, hidden reliance on later releases, and generic foundation work
  introduced too late.

### 2. Reconstruct legacy behavior independently

For each connector, build a behavior inventory from actual LOOP24 code and
flows. Do not copy the design's claims without tracing them.

For every behavior record:

- legacy entry point and caller;
- accepted inputs and defaults;
- authentication/configuration source;
- remote endpoints and API versions;
- pagination, filters, ordering, parsing, and output shape;
- retry, timeout, partial-failure, and cancellation behavior;
- local artifacts and path expectations;
- writes and duplicate/conflict behavior;
- Windows-specific assumptions;
- downstream consumers; and
- planned disposition: preserved exactly, deliberately adapted, deferred, or
  removed.

Flag any legacy behavior with no planned disposition. Flag a claimed
preservation when the proposed tool schema, normalized result, workflow, or
skill cannot support the downstream consumer that currently uses it.

Do not require preservation of a legacy bug, dead code, Langflow-only UI
accident, or an embedded private LLM loop when the approved architecture moves
reasoning into the active Hermes agent. Require the plan to state and test the
replacement behavior clearly.

### 3. Validate the plugin architecture against current Hermes

Trace the proposed connector lifecycle through the current production code:

1. source manifest and vendoring;
2. installed plugin discovery;
3. disabled static catalog projection;
4. configuration descriptor parsing;
5. profile setting and credential persistence;
6. enable/disable precedence;
7. plugin import and `PluginContext` registration;
8. toolset/schema construction;
9. plugin-owned skill registration;
10. fresh agent/session construction;
11. readiness and setup actions;
12. disable/re-enable and upgrade behavior; and
13. installed package lookup with no source-tree dependency.

For each proposed generic interface, answer:

- Does an existing interface already solve it?
- Is the plan extending the correct module?
- Is the interface truly connector-neutral?
- Is there a concrete consumer in Release 1?
- Can disabled metadata be read without importing plugin code?
- Can old plugins omit the new metadata and remain unchanged?
- Does the proposed API create a second configuration or lifecycle authority?
- Will plugin enablement alter only new sessions?
- Are connector-specific branches or enumeration-count tests being introduced?
- Does the plan cover public documentation and developer examples for the
  generic interface?

Compare the plan with both local plugin documentation and actual runtime code.
When documentation and code differ, identify which must change and ensure the
plan owns that work.

### 4. Prove every client and execution surface is planned

Build a surface matrix for every connector and every tool:

| Surface | Questions the review must answer |
| --- | --- |
| Interactive CLI | Can a user discover, configure, enable/disable, inspect readiness, invoke through chat, and receive useful errors? |
| TUI/dashboard chat | Does the ordinary agent session receive plugin tools/skills without a parallel client-side implementation? |
| Electron Desktop | Can the user discover, configure, authenticate/enroll, enable/disable, inspect readiness, and invoke via natural language? |
| Gateway/API chat | Does the same profile-aware agent construction receive the connector? |
| Kanban | Does the worker inherit the executing profile, toolsets, limits, cancellation, and approval constraints? |
| Cron | Can unattended execution use ready credentials, avoid interactive setup, and return actionable terminal diagnostics? |
| Archon workflow | Are toolset admission, exact allowed tools, snapshot identity, write authority, retries, and resume covered? |
| Installed brand | Are the same bytes and behavior present in OTTO and LOOP24 Windows installations? |

For each cell mark `planned and evidenced`, `planned but weak`, `missing`, or
`not applicable with explicit rationale`.

Do not accept one generic test named “surfaces” as proof. Verify that its
fixtures and assertions traverse the real registration and agent-construction
path for each surface. Check that chat, Kanban, cron, and workflows do not
silently receive different tool subsets or credentials.

Test the lifecycle conceptually and in planned cases:

- present but disabled after installation;
- disabled and unconfigured;
- disabled but configured;
- enabled but incomplete;
- enabled and ready;
- enabled after a conversation is already open;
- configuration changed during an existing conversation;
- disabled during an existing conversation;
- fresh conversation after each change;
- restart and upgrade;
- profile switch;
- worker or schedule using another profile; and
- rollback to the preceding release.

### 5. Review GitLab port fidelity

Trace the plan against actual legacy GitLab and CI behavior. At minimum attack:

- project references by numeric ID, canonical URL, and nested namespace;
- ambiguous namespace/project text and configured-origin behavior;
- branches and tags containing slashes or URL-sensitive characters;
- tree pagination, ordering, recursion, truncation, and empty repositories;
- text, binary, base64, large, missing, and moved files;
- merge-request metadata needed by the review skill;
- pipeline selection by exact branch, recent scope, and all/live branches;
- `.gitlab-ci.yml`, local includes, project includes, group/project variable
  metadata, include depth, cycles, missing includes, and partial permission;
- consistency between CI inspection output and legacy flow consumers;
- branch slugging, existing branch reuse, source-ref changes, and collisions;
- atomic multi-file commit actions, create/update/delete semantics, optimistic
  concurrency, empty actions, and partial results;
- duplicate or already-open merge-request reconciliation;
- pagination, rate limiting, retries, deadlines, cancellation, and result
  bounds; and
- the explicit decision to use direct REST rather than `glab`, Git subprocesses,
  or local clones.

Confirm that legacy fix generation and review reasoning move to complete skills
without losing the deterministic data collection required by those skills.

### 6. Review Jira port fidelity

At minimum attack:

- compatibility of existing `jira_my_tickets`, `jira_get_issue`, and
  `jira_add_comment` schemas/results;
- the new `jira_search_issues` scope and bounds;
- bearer PAT and basic email/API-token configuration;
- Jira Cloud REST v3 and Server/Data Center v2 response differences;
- v3-to-v2 fallback classification without changing ordinary failure meaning;
- ADF paragraphs, lists, tables, links, mentions, code, unknown nodes, malformed
  nodes, and plain-text descriptions/comments;
- status/category/priority/age/threshold filters and triage ordering;
- GitLab URL extraction, punctuation cleanup, and deduplication;
- pagination, partial comments, missing fields, deleted users, and empty
  results;
- comment formatting, dry-run, duplicate reconciliation, ambiguous response,
  and downstream workflow expectations;
- native HTTP versus curl compatibility selection;
- the exact `auto` classifier: only a normal native HTTP response with
  Cloudflare metadata plus the bounded error-1010 marker may select curl;
  TLS/DNS/timeout/auth/permission/generic server failures may not;
- explicit curl mode, Windows curl discovery, timeout, cancellation, output
  capture, and cleanup as functional behavior; and
- the approved zero-user baseline plus real historical config state: no
  user/credential inference is introduced, but the auto-seeded Jira
  `plugins.enabled` entry is cleared exactly once on lifecycle transition,
  workflow/Teams remain unchanged, and a later explicit enable survives.

Confirm that defect triage and fix-comment generation are owned by skills and
workflows while ticket retrieval and comment posting remain deterministic
plugin operations. Confirm Release 2's showcase is truthfully single-ticket
and no artifact claims the Phase 6 `loop_group` multi-ticket defect loop.

### 7. Review SharePoint and shared Graph fidelity

At minimum attack:

- whether generic Graph auth/client changes are necessary and minimal;
- existing Teams app-only behavior before and after the proposed extension;
- app-only, delegated MSAL, silent refresh, Azure CLI-assisted identity,
  interactive setup, and deterministic `auto` selection;
- user-facing configuration required for each mode and readiness transitions;
- standard site/library URLs, encoded paths, sharing URLs, Office-style
  `/:w:/r/` and `/:x:/r/` links, query strings, root folders, and malformed or
  ambiguous identities;
- site, drive, item, and path resolution fallback;
- OData pagination, opaque next links, throttling, Retry-After, token refresh,
  partial results, and cancellation;
- folder listing order, recursion, page and aggregate bounds;
- streaming download, partial cleanup, filenames, digests, and artifact paths;
- small upload and upload-session boundaries, chunk alignment, resume offsets,
  expired sessions, ambiguous completion, and excessive chunk counts;
- folder creation, move, async copy/polling, name conflicts, and recycle rather
  than permanent delete;
- owned-site discovery and bounded site metadata, users, admins, role
  assignments, SharePoint groups/members, lists, and subsites from
  `sp_audit.py`;
- independent readiness for Graph file operations versus browser-mediated
  SharePoint REST permission auditing;
- reuse of a named core browser profile without a connector-owned CDP port,
  launcher, profile directory, or teardown of another session;
- local input/output boundaries for interactive and unattended execution; and
- the boundary between SharePoint transport and later document processing or
  generation skills.

Require explicit Teams regression tests through real imports and current Graph
call paths, not only mocked SharePoint fixtures.

### 8. Review Confluence port fidelity

At minimum attack:

- Cloud and Server/Data Center REST-root detection;
- page resolution by URL, ID, space/title, and configured defaults;
- CQL construction, escaping, pagination, subtree/label/recent/custom scopes,
  missing pages, and partial failures;
- `body.storage` retrieval and deterministic conversion of macros, tasks,
  code, tables, links, attachments, and unknown markup to Markdown;
- attachment metadata, explicit download, size/aggregate limits, filenames,
  and partial cleanup;
- stable page paths, manifest, index, version-based incremental sync, forced
  refresh, and a second unchanged sync;
- parity between the accepted current Confluence research skill and the
  extracted library;
- exactly one operational implementation after the old top-level skill becomes
  a compatibility router;
- Playwright and agent-browser engine behavior;
- named core browser profile/CDP lifecycle, process ownership, collision
  refusal, locking, cancellation, stale session recovery, and Windows Edge
  discovery, with no connector-owned parallel authority;
- interactive sign-in, probe, clear-session, expired-session, and readiness
  behavior;
- configured Confluence origin routing and redirect behavior as functional
  correctness;
- attached interactive use versus the explicitly unproven unattended reuse of
  an enrolled Conditional Access session; and
- honest cron/Kanban/workflow behavior when no usable enrolled session exists.

Do not let the plan claim scheduled Confluence reliability merely because
interactive Desktop use works. Require the Windows UAT result to control what
the product advertises.

### 9. Review skills, natural-language use, and workflow composition

For every proposed skill:

- verify its trigger/use case is distinct and discoverable;
- require full-read behavior in the current user turn;
- require exact registered tool names and useful decision guidance;
- confirm it contains no duplicate client implementation or hidden LLM call;
- confirm it tells the agent when to ask for user approval;
- confirm it handles missing configuration and partial tool results;
- check whether plugin-owned versus top-level compatibility ownership is
  unambiguous; and
- confirm it can be used from CLI/TUI, Desktop chat, workers, schedules, and
  workflows without assuming a client-specific prompt.

For every planned workflow:

- validate `archon-2026-07` syntax against the current schema;
- verify the supported flat `requires` service-id list and exact per-node
  `allowed_tools`;
- verify every production admission entry point receives the backend-authored
  ready-service and available-tool snapshot and blocks before run creation;
- preserve the exact meaning of `allowed_tools: []`;
- verify required write authority and unattended limitations;
- ensure plugin skills are selected/read through the supported mechanism;
- ensure no credentials or client-specific paths appear in YAML;
- verify retry, cancellation, output, and partial-failure handling;
- confirm no `loop_group`, dynamic includes, runtime child workflow, input
  mapping, or other deferred feature is assumed; and
- prove it composes with the normalized plugin output rather than a legacy
  Langflow object shape.

Review at least these cross-connector journeys:

1. Jira ticket discovery → ticket detail → GitLab repository research → branch
   and commit → merge request → Jira comment.
2. GitLab CI inspection initiated from natural-language chat and from an
   Archon workflow.
3. SharePoint document discovery/download → controlled local artifact → later
   document-processing skill boundary.
4. Confluence research/sync interactively, then the same request from cron or
   Kanban without a usable enrolled session.
5. A connector disabled after a workflow package is authored but before
   admission.
6. An admitted workflow resumed after configuration changes or upgrade.

### 10. Review edge cases, concurrency, recovery, and performance

For every connector, require planned coverage of:

- empty, missing, malformed, partial, duplicate, and oversized responses;
- first-page, middle-page, final-page, and cyclic/invalid pagination;
- rate limits, Retry-After, transient errors, permanent errors, and timeouts;
- cancellation before transport, during transport, during conversion, during
  file publication, and after a remote write may have succeeded;
- worker/gateway shutdown and process restart;
- duplicate user clicks, duplicate job delivery, workflow retry, and
  coordinator takeover;
- concurrent operations sharing an auth cache, browser profile, output path,
  or remote object;
- bounded memory when responses/files approach limits;
- bounded disk use and cleanup of partial files;
- bounded recursion, include traversal, sync enumeration, upload chunks, and
  async polling;
- deterministic ordering and stable normalized output;
- realistic performance thresholds rather than tests that merely complete;
- no full-body buffering where the plan promises streaming; and
- no retry multiplication across plugin, worker, cron, and workflow layers.

Identify where the plans rely on a bound but never name its configuration,
default, maximum, enforcement layer, diagnostic, or boundary test.

### 11. Review configuration and operator experience

Verify the plans make these tasks possible from both CLI and Desktop without
manual file editing:

- discover the installed but disabled connector;
- understand what it does and where its documentation lives;
- enable or disable it;
- enter and validate non-secret settings;
- set, replace, and clear credentials without reading them back;
- start supported interactive authentication/enrollment;
- cancel or recover a setup action;
- test the connection;
- understand incomplete, disabled, expired, interactive-required, and ready
  states;
- learn that a new conversation is required after tool-affecting changes; and
- retain configuration across disable/re-enable, restart, upgrade, and rollback.

Check old-client behavior, unknown descriptor fields, backend version skew,
backend disconnects, action timeouts, and partial setup failures. Desktop must
remain a backend-driven projection and fail non-destructively.

### 12. Review source-first vendoring, packaging, and release independence

- Verify each plan modifies connector source first, commits a clean exact
  source revision, then vendors it through the established script.
- Check manifest and inventory schema migration from current Jira/Teams source.
- Confirm generated inventories bind the exact source SHA and all connector
  modules, descriptors, skills, workflows, and documentation.
- Confirm vendoring removes stale managed files but cannot remove unmanaged
  user files.
- Verify installed-wheel tests operate outside the source checkout.
- Confirm both OTTO and LOOP24 branded builds receive identical shared bytes
  except intended branding.
- Check that Desktop release version and Python package version remain
  independently managed.
- Confirm the plans never use literal `main` in Hermes development.
- Verify release commands target exact branded SHAs and restore the root
  checkout to clean `base`.
- Confirm each release has rollback instructions and cannot accidentally rely
  on a connector scheduled for the next version.
- Verify patch releases are available for UAT defects without consuming the
  next feature version.

### 13. Audit the strict-TDD plans for executability

For every task in every plan:

- confirm every listed existing file really exists at the current baseline;
- confirm every new file has a clear owner and package location;
- confirm the command runs from the named repository and environment;
- confirm Hermes Python tests use `scripts/run_tests.sh`;
- confirm Ericsson-source commands use the source repository's supported
  environment and test harness;
- confirm the RED test precedes production implementation and names the
  expected reason for failure;
- confirm the GREEN step is small enough to satisfy that RED contract;
- confirm the verification command includes relevant neighboring regressions;
- confirm staging lists only task-owned files and cannot capture preserved or
  parallel edits;
- confirm commit boundaries separate generic Hermes changes from vendored
  Ericsson source where necessary;
- confirm generated files are regenerated and checked, not hand-edited;
- confirm full-suite, Desktop, installed-distribution, merge-rehearsal, and
  brand gates appear at the correct release boundary;
- confirm no command depends on an unimplemented helper from a later task;
- confirm placeholders such as “relevant file,” “selected workflow,” or
  “discover at execution” have been eliminated or bounded by an explicit stop
  and review rule; and
- confirm release/push/merge actions require separate authorization.

Do not demand tests that merely freeze file counts, tool counts, manifest
versions, or other change-detector values. Require behavioral and relational
assertions.

### 14. Review regression and compatibility coverage

The plans must preserve:

- existing Hermes plugin behavior for manifests without configuration
  descriptors;
- current Jira public tool names and useful normalized results;
- existing Ericsson Teams behavior while Graph is generalized;
- current workflow Phase 1–5 semantics and v1–v4 snapshots;
- prompt-cache stability and new-session toolset activation;
- exact workflow `allowed_tools: []` behavior;
- old-client action vocabulary and existing REST mutation URLs;
- profile isolation across interactive sessions, workers, schedules, and
  workflows;
- installed Desktop backend resolution and bundled capability staging;
- source-first Ericsson vendoring and user capability overrides; and
- upstream mergeability of generic Hermes changes.

Check that regression suites exercise the relevant production paths rather
than only mocks. Find sibling constructors, loaders, staging paths, and
installed entry points omitted by the planned tests.

## Specific plan premises to challenge

Reach an explicit `supported`, `unsupported`, or `insufficiently established`
verdict on each premise:

1. A disabled standalone plugin can expose static configuration metadata
   without importing executable plugin code.
2. One generic descriptor can serve CLI and Desktop without becoming a second
   plugin framework.
3. Existing profile configuration and credential helpers can meet all four
   connectors' setup needs without connector-specific core branches.
4. `PluginContext` can support the proposed setup actions and skill/tool
   registration with a minimal generic extension.
5. The same plugin registrations reach interactive chat, Desktop, gateway,
   Kanban, cron, and workflows.
6. Fresh-session behavior is sufficient to preserve prompt caching after
   enable/disable/configuration changes.
7. Ericsson source manifests can express disabled standalone capabilities and
   a bounded one-time `auto_seeded_backend` lifecycle transition without
   breaking workflow/Teams deployment or clearing later explicit enables.
8. Current vendoring can carry descriptors and plugin-owned skills and remove
   stale managed files deterministically.
9. Direct GitLab REST covers all legacy in-scope behavior without `glab`, Git,
   or repository clones.
10. The Jira curl compatibility design preserves the proven Cloudflare case
    on Windows and does not create a second result/error model.
11. Jira v3-first/v2-fallback behavior preserves both Cloud and Server/Data
    Center deployments.
12. Generic Graph identity and transfer extensions are sufficient for
    SharePoint without regressing Teams.
13. The SharePoint tool boundary is sufficient for later document-generation
    skills without embedding document logic in the connector.
14. The existing Confluence skill can be decomposed into one reusable library,
    structured tools, and thin compatibility guidance without behavioral loss.
15. SharePoint audit and Confluence can reference the existing named core
    enrolled-browser authority without claiming a raw CDP port, colliding with
    port 9333, or tearing down a browser owned by another session, while
    unattended failure remains explicit and non-hanging.
16. Four sequential releases with mandatory Windows UAT are sufficient to
    isolate failures and prevent later connector work from masking an earlier
    defect.
17. The proposed test commands actually cover installed branded behavior, not
    only source-tree development behavior.
18. Upstream customization records and merge rehearsal cover every generic
    Hermes change proposed by these plans.

## Finding severity

- **CRITICAL** — the plan is built on a false architectural or legacy premise
  that would make a connector fundamentally unusable, corrupt the shared
  plugin foundation, break an existing major Hermes/Teams/Jira/workflow path,
  or require redesign across multiple releases.
- **IMPORTANT** — a realistic missing or incorrect plan step could ship
  materially incomplete behavior, lose a proven LOOP24 capability, omit a
  required client surface, introduce duplicate operations, cause hanging or
  unbounded work, break Windows/installed use, invalidate a UAT gate, or make
  the task sequence non-executable.
- **MINOR** — a bounded plan-quality, diagnostic, maintainability,
  documentation, or test-clarity issue with a concrete implementation
  consequence that does not by itself block implementation.

Do not inflate severity. Do not report style preferences, naming taste,
speculative future features, or an unproven concern. A missing test is not by
itself Important unless a release-critical behavior lacks another credible
verification path.

The verdict is:

- `BLOCK` if any Critical or Important finding remains;
- `CONDITIONAL` if only Minor findings remain but one requires an explicit
  product decision before implementation; or
- `READY FOR IMPLEMENTATION` only when no Critical/Important finding remains
  and every required matrix is complete.

## Finding proof standard

Every finding must include all ten elements:

1. stable ID and severity;
2. concise title;
3. affected connector release, design section, and plan task;
4. exact plan text or omission and the current source/legacy evidence that
   contradicts it;
5. violated invariant or approved product decision;
6. realistic implementation or runtime scenario;
7. concrete wrong result and user/operator consequence;
8. why another task, test, UAT step, or current framework does not already
   cover it;
9. the smallest plan correction that fixes the whole gap without unnecessary
   scope; and
10. exact RED test, verification command, or acceptance criterion that must be
    added or changed.

If one element is missing, do not present the concern as a finding. Put it in
the unresolved-question or verification-ledger section with the evidence
needed to decide it. Do not disguise speculation as residual risk.

## Evidence and command discipline

Start with read-only evidence:

```bash
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent status --short --branch
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent branch --show-current
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent rev-parse HEAD
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent rev-parse origin/base
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent worktree list --porcelain

git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities status --short --branch
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities branch --show-current
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities rev-parse HEAD
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities worktree list --porcelain

git -C /Users/coreyellis/code/gitlab.rosetta.ericssondevops.com/loop_24 status --short --branch
git -C /Users/coreyellis/code/gitlab.rosetta.ericssondevops.com/loop_24 branch --show-current
git -C /Users/coreyellis/code/gitlab.rosetta.ericssondevops.com/loop_24 rev-parse HEAD

shasum -a 256 \
  /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/specs/2026-08-09-ericsson-connector-plugins-design.md \
  /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-09-ericsson-gitlab-connector.md \
  /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-09-ericsson-jira-connector.md \
  /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-09-ericsson-sharepoint-connector.md \
  /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-09-ericsson-confluence-connector.md
```

Use `rg`, `git log`, `git show`, `git diff`, static imports, existing test
collection, and harmless help/catalog commands as needed. Do not edit tracked
files or create tests inside a repository. Any temporary analysis script or
fixture must live under a private temporary directory and must be removed when
the review ends.

This is a plan review, so do not run multi-hour full implementation suites to
prove code that has not been written. Validate that planned commands, paths,
test selectors, workspaces, and harnesses are real. Run only small existing
tests when necessary to verify an architectural premise. Record every command
actually run and do not report an unrun command as passed.

When a plan refers to an existing function, test, command, workspace, or API,
inspect it. When it refers to a new file, verify that its proposed package and
caller are coherent. When a plan relies on behavior in another future task,
trace the dependency and ordering explicitly.

Do not use a subagent report as proof. Leads from other agents must be reduced
to the full finding standard using direct code, plan, legacy, and command
evidence.

## Required output

Write the review to:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/reviews/2026-08-09-ericsson-connector-plugins-adversarial-plan-final-rereview-<model_name>.md`

Replace `<model_name>` with the reviewing model's short name.

The report must contain:

1. exact repository states, plan hashes, model, platform, date, and evidence
   sources reviewed;
2. overall verdict: `BLOCK`, `CONDITIONAL`, or `READY FOR IMPLEMENTATION`;
3. findings table sorted by severity, followed by the complete ten-element
   proof for every finding;
4. design-requirement-to-plan traceability matrix;
5. all-plan task coverage matrix;
6. legacy behavior parity matrix for GitLab, Jira, SharePoint, and Confluence;
7. plugin-architecture compliance matrix;
8. per-connector/per-tool client-surface matrix covering CLI/TUI, Desktop,
   gateway/API, Kanban, cron, Archon workflow, and installed brands;
9. exact verdict on all twenty non-negotiable invariants;
10. exact verdict on all eighteen specific plan premises;
11. connector-specific edge-case and performance coverage assessment;
12. strict-TDD command/file/ordering audit;
13. packaging, vendoring, upstream-preservation, release, rollback, and Windows
    UAT assessment;
14. what was verified complete and why, including production paths inspected;
15. required plan corrections ordered by severity and dependency;
16. unresolved product decisions, unverified premises, and evidence needed to
    resolve them;
17. command/evidence ledger with exact command, working directory, result,
    duration where meaningful, and whether evidence came from inspection,
    execution, legacy tracing, or documentation comparison; and
18. explicit confirmation that standalone security/threat-review workflows
    were excluded and not attempted.

If there are no findings, say so explicitly and still provide every required
matrix and verdict. Do not use plan length, prior approval, task count, test
names, or confident prose as a substitute for evidence.

End the report with one of these exact statements:

- `IMPLEMENTATION MUST NOT BEGIN UNTIL CRITICAL AND IMPORTANT PLAN FINDINGS ARE RESOLVED.`
- `IMPLEMENTATION MAY BEGIN ONLY AFTER THE LISTED PRODUCT DECISIONS ARE APPROVED.`
- `THE REVIEWED DESIGN AND FOUR PLANS ARE READY FOR IMPLEMENTATION.`

Do not implement the corrections. Stop after writing the report.
