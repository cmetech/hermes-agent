# Adversarial code-review prompt — Ericsson Jira connector

Paste everything below the line into a fresh, capable model or coding agent
with read and shell access to the repositories named in this prompt.

The reviewer must assess the complete Ericsson Jira connector candidate for
**CRITICAL and HIGH production defects only** before Jira Task 10 creates and
publishes branded v5.6.0 candidates. This is a review task, not an
implementation task. Do not modify production code, tests, generated files,
Git history, branches, worktrees, or refs. Do not push, publish, open a pull
request, dispatch a workflow, create a release, or begin installed UAT. The
only authorized persistent repository write is the final review report named
under Required output.

This is an adversarial correctness review. Try to falsify the Jira candidate's
behavioral and integration claims. Do not merely summarize the implementation,
repeat test names, or bless green suites.

Security-sensitive paths are in functional correctness scope. A concrete
defect such as secret disclosure, cross-origin request authority, approval
bypass, unsafe replay, or incomplete process containment may be reported when
it is established through ordinary code tracing and deterministic synthetic
tests. However, do **not** run a standalone threat model, security audit,
security-review workflow, penetration test, vulnerability scanner, or exploit
development exercise. Do not use real credentials, malicious payloads, or
live Jira/GitLab/Ericsson services. All reproductions must use benign synthetic
data, fake transports/executables, isolated temporary state, and no network
access.

---

## Role

You are a skeptical principal-level reviewer experienced with Python plugin
systems, REST clients, subprocess lifecycle, Windows behavior, authentication,
bounded parsing, retries and cancellation, durable workflow approval,
TypeScript/Electron integration, source-first vendoring, and installed package
behavior.

Your job is to find release-blocking defects, not to produce a general code
quality report. Assume every completion claim is unproven until you trace the
actual final production path and establish the invariant from code plus
ordinary behavioral evidence.

Commit messages, task reports, prior review verdicts, comments, test names,
mock call counts, and green aggregate counts are leads, not proof. Read every
changed production file and the relevant unchanged callers on which its
behavior depends. Review the final immutable trees because later correction
commits may have changed an earlier task's contract.

Do not report MEDIUM, LOW, stylistic, documentation-polish, speculative,
test-only, or optional-hardening findings. Do not inflate severity to make a
concern eligible. A finding requires a realistic trigger, a violated
load-bearing invariant, and a concrete production consequence. A missing test
is not itself HIGH unless production behavior is demonstrably absent, wrong,
or unprotected against a realistic regression.

If a suspected omission may be intentional, inspect the design, behavior map,
legacy implementation, and Git history before calling it a defect. The Jira
release intentionally excludes several operations and defers exact
multi-ticket defect-loop parity.

## Repository and immutable review scope

### Ericsson source repository

Root:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities`

Existing clean Jira worktree:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/ericsson-jira-connector`

| Meaning | Commit |
|---|---|
| Original pinned pre-program source baseline | `dae405ede7049b621e502d9259f97481c940a65b` |
| Accepted GitLab-complete source predecessor for Jira | `634ca3bc9d4c543a1dc02e1ec01e2e1c604ee2e8` |
| Final Jira source candidate | `f52a131cc63643f995e9d125bfa3fc7fa865700f` |
| Final Jira source tree | `48f06bd4828a96c4e03ec5ff1e634508e4dfe23c` |

Primary source review range:

```text
634ca3bc9d4c543a1dc02e1ec01e2e1c604ee2e8..f52a131cc63643f995e9d125bfa3fc7fa865700f
```

At prompt creation this range contains 12 commits and 47 changed files, with
4,597 insertions and 299 deletions. Verify the ancestry, counts, and changed
paths yourself. Two final source commits also correct GitLab group URL and
descendant-ordering behavior needed by the Jira-to-GitLab path; review their
actual necessity and regression impact rather than silently excluding them.

### Hermes repository and installed candidate

Root:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

Existing clean Jira worktree:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/ericsson-jira-connector`

| Meaning | Commit |
|---|---|
| Original pinned Hermes baseline | `da59906aaad8f9cb023fb66426c6f60ff5afa04a` |
| Reconciled neutral base immediately before Jira | `d48f783b254ac2faa93b9f9db7a7ed6098e2172b` |
| Final vendored Jira Hermes candidate | `7d35a7ec27707483dda7991f60f9d26aeda43389` |
| Final vendored Jira Hermes tree | `7fa983c149c2887593addc219b43ff99774dfaf3` |

Primary Hermes review range:

```text
d48f783b254ac2faa93b9f9db7a7ed6098e2172b..7d35a7ec27707483dda7991f60f9d26aeda43389
```

At prompt creation this range contains five commits and 31 changed files, with
2,567 insertions and 248 deletions. Verify those numbers. Review the final tree
at `7d35a7ec27707483dda7991f60f9d26aeda43389` as the sole installed-candidate
verdict target.

The local neutral `base` and the Jira feature worktree both point at this exact
Hermes candidate. The local Ericsson `main` and source feature worktree both
point at the exact source candidate. Remote refs have not been advanced.

Hermes literal `main` is synchronization-only and may differ from
`origin/main`. It is not a Jira development target. Do not merge it, reconcile
it, or use its mutable tip as the candidate under review.

### Legacy behavior repository

Read-only root:

`/Users/coreyellis/code/gitlab.rosetta.ericssondevops.com/loop_24`

Pinned behavioral snapshot:

`fc3bf26d64e05cc3703ee39e323bbf3c1eaa4cd6`

The accepted earlier connector snapshot is
`8ca26f882bc461d9aaa80a252685568c8749394a`. Verify that the only change from
that snapshot to `fc3bf26d64e05cc3703ee39e323bbf3c1eaa4cd6` is unrelated documentation under
`custom_components/ericsson_docgen/**` before treating the later snapshot as
Jira behavior evidence.

### Preservation rules

Begin by recording branch, exact SHA, status, and worktree lists for both
active repositories. Preserve every unrelated tracked or untracked change and
every existing worktree. Do not clean, reset, stash, switch the shared
checkout, delete refs, or remove a worktree you did not create.

The Hermes root checkout intentionally contains unrelated untracked `.otto/`
and `docs/` material, including this prompt. Those paths are not part of the
Jira candidate. Preserve them and use the clean Jira feature worktrees for
candidate execution.

Use read-only commands and the existing clean Jira worktrees. If mutation
testing is needed, create a private detached temporary worktree outside the
existing `.worktrees` paths, change only that disposable worktree, make no
commit, and remove only what you created. Temporary fixtures and analysis
scripts must live under a private `mktemp -d` directory.

## Immutable design and behavior inputs

Read these files completely before judging the implementation:

1. `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/AGENTS.md`
2. `/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/AGENTS.md`
3. `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/apps/desktop/AGENTS.md`
4. `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/specs/2026-08-09-ericsson-connector-plugins-design.md`
5. `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-09-ericsson-jira-connector.md`
6. `docs/connector-porting/jira-behavior-map.md` from the exact final Ericsson source tree
7. `docs/configuration.md` and all Jira flow/onboarding pages changed in the source range
8. `sets/ericsson.json` from the exact final source tree
9. `capabilities/ericsson.json` and `capabilities/ericsson-vendored-paths.json` from the exact final Hermes tree
10. `docs/reviews/2026-08-09-ericsson-connector-plugins-adversarial-plan-review-prompt.md`
11. `docs/reviews/2026-08-09-ericsson-connector-plugins-adversarial-plan-final-rereview-fable-5.md`

The Superpowers design and plan are intentionally ignored by Git. Read them
from the absolute root-checkout paths above, not from a temporary worktree.

Verify these current content hashes before review:

| Artifact | SHA-256 |
|---|---|
| Connector design | `93e2f4d2b52e6f2be48b551364033522079958cebbc34d4725c0e8422f261cc6` |
| Jira implementation plan | `205eb8149fc7e0a6668bbae4513d06b53fedb8d7c6f3f7642e76308a3ea99a63` |
| Final Jira behavior map | `ce518bf1d9eda8a0bbd67d63dc1e45e0f475b2a3095e23b9707ff00048aae21b` |

If one differs, record `REVIEW_INPUT_CHANGED`, the observed hash, and whether
the immutable code candidate is still meaningfully reviewable. Do not silently
substitute a changed contract.

Treat the design, Jira plan, and behavior map as the delivery contract. Treat
prior plan-review reports and implementation completion summaries as claims to
verify, not authorities that override final code.

## Legacy sources to reconstruct independently

At `fc3bf26d64e05cc3703ee39e323bbf3c1eaa4cd6`, read the complete Jira package:

- `custom_components/ericsson_jira/README.md`
- `custom_components/ericsson_jira/__init__.py`
- `custom_components/ericsson_jira/jira_assigned_tickets_fetcher.py`
- `custom_components/ericsson_jira/jira_ticket_context_builder.py`
- `custom_components/ericsson_jira/jira_ticket_selector.py`
- `custom_components/ericsson_jira/jira_ticket_triage.py`
- `custom_components/ericsson_jira/jira_ticket_updater.py`
- `custom_components/ericsson_jira/fix_summary_composer.py`
- the Jira Assigned Tickets Summary, Jira-to-GitLab, Jira Defect Loop, and
  development Jira triage flow JSON files

Independently distinguish actual deterministic Jira behavior from Langflow UI
behavior, embedded LLM reasoning, dead code, unsafe behavior deliberately
replaced, and multi-ticket behavior explicitly deferred to workflow Phase 6.
Do not require preservation of a legacy defect or the legacy practice of
putting credentials and request bodies in process arguments.

## What the candidate claims to deliver

The final candidate claims all of the following:

- a bundled standalone `ericsson-jira` plugin that is disabled for every fresh
  profile until explicitly enabled;
- one generic, manifest-driven lifecycle transition that clears only the
  historic auto-seeded Jira enablement once, leaves workflow and Teams
  unchanged, preserves settings/secrets, and never infers enablement from a
  user or credential;
- bearer PAT and basic email/API-token configuration through the existing
  opaque per-profile configuration authority;
- one normalized origin and one redacted error/result model;
- native HTTP as primary transport, with bounded retries for classified reads;
- REST v3 preference with v2 fallback only for one classified unsupported
  endpoint response;
- a private bounded curl compatibility path, selected automatically only for
  an exact normal Cloudflare error-1010 response or selected explicitly by the
  operator;
- exact public tools `jira_my_tickets`, `jira_search_issues`,
  `jira_get_issue`, and `jira_add_comment`;
- bounded normalized ticket/search/detail/comment results across Jira Cloud
  ADF and Server/Data Center plain-text shapes;
- comment writes only, with dry-run, host approval or admitted workflow
  authority, duplicate reconciliation, and no blind ambiguous retry;
- source-owned ticket-research and defect-triage skills plus a thin always
  discoverable Jira router;
- a bounded assigned-ticket summary workflow and an exactly one-ticket
  research/triage/approval/comment showcase;
- exact source-first vendoring, generated provenance, Desktop configuration,
  fresh-session tool/skill lifecycle, workflow admission, and installed
  distribution behavior; and
- no SharePoint implementation, new core model tool, hidden connector LLM,
  issue creation/transition/assignment/edit/attachment mutation, or claim of
  exact multi-ticket defect-loop parity.

## Task map to cover

Use the plan for exact acceptance criteria, but cover every completed task:

| Task | Production concern |
|---:|---|
| 1 | Frozen legacy behavior map and exact source identity |
| 2 | Standalone descriptor, configuration, and lifecycle metadata |
| 3 | Typed auth, normalized endpoint, native client, retry, and REST compatibility |
| 4 | Private bounded curl compatibility transport |
| 5 | Ticket reads, search, pagination, filtering, ADF/plain normalization, and GitLab links |
| 6 | Comment validation, approval, versioned bodies, reconciliation, and no ambiguous replay |
| 7 | Skills, router, workflows, onboarding, and truthful Phase 6 deferral |
| 8 | Source closure, neighboring regressions, and exact clean source SHA |
| 9 | Vendoring, lifecycle migration, cross-surface behavior, Desktop, packaging, and upstream preservation |

Task 9 Step 5 has already fast-forwarded the verified candidates locally into
Ericsson `main` and Hermes neutral `base`. Task 10 has not started. Your verdict
controls whether the immutable Jira SHA should proceed to Task 10 brand
candidate construction and installed Windows UAT; do not perform those steps.

## Non-negotiable invariants

A demonstrated violation of any invariant is HIGH or CRITICAL depending on
impact.

1. **Source ownership and byte identity are exact.** Connector production
   bytes originate in the clean Ericsson source candidate. Hermes
   `vendoredFrom` equals `f52a131cc63643f995e9d125bfa3fc7fa865700f`, every manifest-managed Jira byte matches,
   workflow policy sidecars are preserved, and no source checkout is required
   at installed runtime.
2. **Disabled means absent from model requests.** A fresh or explicitly
   disabled profile does not import Jira executable code or expose Jira tools
   or plugin-owned skills. Static configuration metadata and the thin router
   may remain discoverable. Tool-affecting changes apply only to fresh
   conversations and do not mutate an existing cached prompt/tool prefix.
3. **Lifecycle migration is exact and one-time.** Only historical auto-seeded
   Jira enablement is cleared; workflow and Teams stay unchanged; settings and
   secrets are retained; credentials never enable Jira; and a later explicit
   enable survives restart and restaging.
4. **Configuration has one profile-scoped authority.** Non-secret settings use
   `config.yaml`, secrets use the existing write-only credential mechanism,
   inactive auth-mode fields do not make the active mode unusable, and CLI,
   Desktop, chat, workers, schedules, and workflows resolve the executing
   profile rather than process-global Jira environment variables.
5. **Origin and request authority never drift.** All native and curl requests
   remain on the normalized configured HTTP(S) Jira origin and approved REST
   path. User input, JQL, response headers, redirects, proxies, environment,
   URL spelling, or retry/fallback cannot select another origin or authority.
6. **Authentication and private data remain private.** PATs, API tokens, Basic
   encodings, comment bodies, raw Jira/Cloudflare bodies, private temp paths,
   and unbounded remote text do not appear in argv, representations, errors,
   logs, results, evidence, generated config projections, or Desktop state.
7. **REST and transport fallback are narrow.** REST v3 falls back to v2 only
   for the approved bounded structured unsupported-endpoint response. Native
   `auto` falls back to curl only for a normal 403 response carrying the exact
   bounded Cloudflare metadata and error-1010 evidence. TLS, DNS, connection,
   timeout, malformed, authentication, permission, generic 4xx/5xx, and
   cancellation failures never silently select another path.
8. **Bounds exist before uncontrolled work.** Request, response, header,
   stderr/stdout, URL, JSON, ADF depth/text, field, result, page, comment,
   retry, delay, temporary-file, and subprocess work is bounded in the real
   production path, not only checked after unbounded buffering or allocation.
9. **Cancellation and deadlines reach the real work.** Cancellation before or
   during native I/O, retry delay, curl execution, output collection, and
   cleanup terminates or abandons work deterministically within the declared
   bound and cannot leave a child, private file, or partial write loop behind.
10. **Reads have stable cross-version meaning.** Cloud v3 ADF and Server/Data
    Center v2/plain shapes normalize to the documented bounded projection.
    Pagination, filtering, ordering, missing users/fields/comments, malformed
    nodes, empty results, partial results, and truncation cannot silently turn
    a failure into an empty workload or invent ticket facts.
11. **Comment authority binds the exact write.** Caller arguments cannot forge
    approval. Interactive approval or admitted workflow authority covers the
    exact current tool invocation, issue key, and body. Rejection blocks the
    write. Dry-run performs no remote read/write. A conflict or unknown outcome
    is reconciled read-only and never blindly reposted.
12. **Write reconciliation is truthful.** The plugin reports `created`,
    `duplicate`, `ambiguous`, permission, conflict, and failure outcomes
    according to observed evidence. It never reports an unobserved comment as
    created or creates a duplicate because the reconciliation search was
    accidentally incomplete.
13. **Skills do reasoning; tools do deterministic integration.** No Jira skill
    contains curl commands, credentials, a second REST client, hidden provider
    call, or write authorization. The plugin contains no connector-local LLM.
14. **Workflow contracts are executable and honest.** Flat
    `requires: [ericsson-jira]`, exact per-node tools, `allowed_tools: []`,
    readiness gating, approval/rejection, output references, terminal status,
    and resume behavior use actual current workflow semantics. The showcase
    cannot act on surrounding prose, a different key/body, rejection, missing
    configuration, or ambiguous output as though it succeeded.
15. **Every execution surface uses one registered plugin.** CLI/TUI, Desktop
    chat, gateway/API, Kanban, cron, and Archon workflows receive the same
    profile-scoped tool implementation and fresh-session enablement behavior.
    Desktop remains a backend projection rather than a second Jira resolver.
16. **Compatibility and exclusions are truthful.** Existing public tool names
    and documented useful result fields remain usable. Unsupported Jira issue
    mutations are absent. No artifact claims the deferred Phase 6 multi-ticket
    loop, silently substitutes legacy Langflow behavior, or imports SharePoint.
17. **Generic Hermes changes remain generic.** Vendoring/lifecycle changes do
    not hardcode Jira, alter unrelated plugins, drop policy sidecars, change
    GitLab/Teams behavior unexpectedly, widen the core model-tool surface, or
    mutate the prompt of an existing conversation.
18. **Installed and future-merge paths are preserved.** Generated inventories,
    capability staging, Desktop assets, workflow packages, and upstream
    customization gates operate from installed bytes. Temporary merge/brand
    rehearsals do not mutate real refs and contain no SharePoint candidate.

## Specific implementation decisions to attack

Reach an explicit `supported`, `contradicted`, or `not established` verdict on
each decision. Do not accept the premise merely because a named test passes.

1. The schema's `required`, `visible_when`, readiness, and opaque
   configuration behavior correctly allow bearer mode despite inactive basic
   fields and basic mode despite inactive PAT state, while rejecting truly
   ambiguous active secrets.
2. `_origin()` and every downstream URL builder represent exactly one origin
   across omitted schemes, trailing slashes, ports, IPv6, Unicode/IDNA, encoded
   path characters, userinfo, fragments, and redirects without allowing an
   operation to escape it.
3. Holding the complete authorization value in `JiraAuth` is still safely
   contained across dataclass helpers, repr/str, exceptions, transport
   construction, test diagnostics, and plugin result serialization.
4. Native retries share one absolute deadline, honor Retry-After without
   exceeding it, remain cancellation-responsive during delay, and never
   multiply with REST-version or native-to-curl fallback.
5. The exact v3-unsupported classifier recognizes the intended Jira
   Server/Data Center behavior without converting ordinary 404/auth/permission
   or malformed responses into version fallback.
6. The Cloudflare-1010 classifier has one meaning in client and transport code;
   response metadata/header normalization cannot make the two copies disagree
   or trigger curl for an unrelated failure.
7. Explicit curl mode and automatic curl fallback use the same normalized
   auth, request body, result, deadline, status, and error semantics as native
   transport rather than becoming a parallel client with behavioral drift.
8. Curl executable validation remains true at process launch, works on the
   documented Windows paths, does not follow an unintended executable, and
   does not expose its private config path or content through diagnostics.
9. Curl stdout, stderr, headers, and body are bounded **during collection**.
   A subprocess cannot force unbounded parent memory before the later size
   checks execute.
10. Timeout and cancellation terminate the actual curl work, reap it, close
    pipes, and remove all private files on every success/failure boundary,
    including spawn failure and output/header parsing failure.
11. Curl header/status parsing handles the real bounded sequences that curl
    can emit—such as interim responses—without selecting an attacker- or
    proxy-controlled block, following a redirect, or misclassifying success.
12. Search pagination, `total` handling, post-fetch filters, page size,
    ordering, truncation warnings, and malformed/partial pages cannot omit
    eligible issues silently, loop, over-fetch beyond bounds, or report a
    filtered partial set as complete.
13. ADF normalization is bounded by aggregate work/output as well as recursion
    depth and handles links, mentions, tables, unknown nodes, and malformed
    structures without raw object leakage, quadratic growth, or invented text.
14. GitLab URL recognition accepts the intended URLs and stable punctuation
    cleanup without treating arbitrary strings containing `gitlab` as trusted
    repository identity or dropping legacy URLs needed downstream.
15. Comment duplicate/ambiguous reconciliation searches enough authoritative
    history, across pagination and v2/v3 shapes, to justify `duplicate`; an
    older exact comment or concurrent write cannot cause an unjustified repost
    or false created result.
16. Interactive approval and workflow admission are bound to the exact current
    comment key/body and cannot be reused, caller-authored, widened from tool
    name alone, or bypassed through a sibling invocation path.
17. The single-ticket workflow enforces its one-key contract and rejection
    path through actual compiler/runtime behavior, rather than relying only on
    an LLM instruction that can be ignored while the tool remains callable.
18. Fresh-session plugin enable/disable, qualified skill visibility, router
    visibility, profile switching, workers, cron, and workflow admission all
    traverse the same production configuration/toolset authority.
19. The one-time `auto_seeded_backend` transition distinguishes historical
    automatic state from later explicit state without credentials/users,
    survives restaging, and does not clear another plugin or profile.
20. Vendoring from `f52a131cc63643f995e9d125bfa3fc7fa865700f` preserves all managed bytes and policy sidecars,
    removes only stale managed files, records exact provenance, and leaves
    SharePoint completely absent from the Jira tree and delta.
21. The two GitLab source corrections in the Jira range are required by the
    accepted cross-connector contract, preserve prior GitLab behavior, and are
    actually represented byte-for-byte in the Hermes candidate where required.
22. Tests exercising lifecycle, surfaces, workflows, transport, and installed
    behavior would fail if their production guard were removed; they do not
    merely assert fixtures, duplicate implementation logic, or prove import
    success.

## Required review method

### 1. Establish immutable scope and traceability

- Verify all named commits, trees, parentage, ancestry, counts, changed paths,
  design/plan/map hashes, and clean feature-worktree states.
- Build a Task 1–9 matrix with `proven`, `contradicted`, or `not established`.
- Classify changed files as Jira production, generic integration/vendoring,
  cross-connector prerequisite, skills/workflows/docs, generated artifact, or
  test/evidence.
- Trace each plan acceptance criterion to final production code and actual
  runtime callers. A task may be `not established` without becoming a finding;
  create a finding only when the CRITICAL/HIGH standard is met.
- Review the final trees, not only individual task commits. Look for a later
  correction that weakens an earlier invariant.

### 2. Reconstruct legacy and current public behavior

- Build a parity matrix from the actual legacy package, flow callers, accepted
  pre-Jira source, behavior map, and final tools.
- For each behavior record inputs/defaults, auth source, API/version,
  pagination/filter/order, output fields, retry/deadline/cancellation, write
  effects, Windows assumptions, and downstream consumers.
- Mark each as preserved, safely adapted, intentionally excluded, deferred, or
  contradicted.
- Confirm `jira_my_tickets`, `jira_get_issue`, and `jira_add_comment` retain the
  useful compatibility contract; assess `jira_search_issues` as the new bounded
  surface.
- Do not demand exact preservation of unsafe argv secrets, raw response
  snippets, hidden connector LLM calls, or the deferred multi-ticket loop.

### 3. Attack lifecycle, configuration, and profile propagation

- Trace source manifest through vendoring, static catalog projection,
  descriptor validation, staging, one-time migration, settings/secrets,
  enable/disable precedence, plugin import, toolset/skill construction, fresh
  agent construction, workflow readiness/admission, and installed lookup.
- Exercise fresh, upgraded-auto-seeded, explicitly disabled, configured but
  disabled, enabled incomplete, enabled ready, explicitly re-enabled,
  restaged, restarted, and separate-profile states.
- Trace CLI/TUI, Desktop, gateway/API, Kanban, cron, and workflow constructors.
  Find any sibling path that uses default-profile or process-global state.
- Confirm existing conversations remain byte-stable while new conversations
  reflect tool/skill changes.

### 4. Attack authentication, origin, native transport, and REST fallback

- Trace every setting/secret lookup into `JiraAuth`, headers, native client,
  curl files, retry/fallback, errors, results, and cleanup.
- Exercise both auth modes, inactive fields, empty/oversized values, lookup
  exceptions, representations, and exception paths with conspicuous benign
  synthetic sentinels.
- Trace origin normalization and path validation through encoded and boundary
  cases. Confirm redirects/proxies/environment cannot replace authority.
- Evaluate every status and transport exception across GET and comment POST,
  retry count, delay, cancellation, absolute deadline, REST fallback, and curl
  fallback.
- Verify v3 and v2 request bodies and response behavior for reads and comments.

### 5. Attack the private curl transport

- Use only a benign fake executable in isolated temporary state. Inspect argv,
  environment, config/body/header/output files, permissions, handles, process
  lifecycle, and all cleanup paths.
- Test bounds before and during output collection, not merely after a fake
  process returns a bounded value.
- Review Windows executable spelling/path semantics and POSIX symlink/file
  semantics without touching a system executable.
- Exercise interim headers, malformed status/header blocks, duplicate safe
  headers, oversized values, nonzero exit, missing outputs, timeout,
  cancellation, spawn failure, and parser failure.
- Prove auto fallback requires the exact response contract and explicit curl
  bypasses native probing without changing operation meaning.

### 6. Attack reads, normalization, pagination, and results

- Trace `jira_my_tickets`, search, and detail from tool schema through
  validation, JQL/params, pagination, field selection, filters, normalization,
  warning generation, and public projection.
- Exercise first/middle/final pages, missing or dishonest totals, empty pages,
  duplicate issues, malformed issues, filters that remove an entire page,
  boundary max results, and partial comments.
- Compare v3 ADF and v2/plain shapes. Test bounded nesting, wide nodes, repeated
  structures, unknown content, malformed timestamps/users, links, code,
  tables, mentions, and large-but-allowed text.
- Check GitLab URL cleanup/deduplication and every downstream skill/workflow
  field expectation.
- Ensure failures and truncation remain explicit and no raw body or arbitrary
  fields escape.

### 7. Attack comment writes and approval/reconciliation

- Trace validation, dry-run, interactive approval hook, workflow admission,
  exact v3/v2 body creation, POST, status classification, read-only
  reconciliation, result projection, and workflow reporting.
- Exercise approval rejection, stale/reused approval, caller-injected approval
  fields, key/body mutation, duplicate clicks, concurrent identical comments,
  conflict, timeout, disconnect after possible success, permission, and an
  older exact duplicate outside the first response page.
- Confirm no ambiguous path retries the write and reconciliation itself cannot
  mutate or falsely claim creation.
- Check all direct and registered invocation paths; testing only the main
  handler is insufficient if another public path can call `invoke()`.

### 8. Attack skills and workflow execution

- Read every Jira skill and workflow fully. Confirm exact tool names,
  discoverability, configuration guidance, full-read behavior, approval rules,
  warning propagation, and absence of a second client/LLM.
- Compile and trace both workflows through real admission/runtime semantics,
  not only YAML shape checks.
- Exercise disabled/unready blocking before run creation, one-key parsing,
  missing/not-found/permission results, approval rejection, approved comment,
  ambiguous comment, output-reference propagation, and resume.
- Confirm `allowed_tools: []` remains empty and the report nodes cannot turn a
  skipped/failed/ambiguous comment into success.
- Search all artifacts for false multi-ticket parity or unsupported issue
  mutations.

### 9. Attack vendoring, installed surfaces, and neighboring regressions

- Independently compare every manifest-managed source/vendor pair at the two
  immutable SHAs using Git blobs, not working-copy timestamps.
- Verify `vendoredFrom`, generated inventories, policy sidecars, stale-file
  handling, installed package lookup, and no source-tree borrowing.
- Trace generic vendor-script and lifecycle changes through non-Jira plugins,
  especially GitLab, Teams, workflow, and old descriptors that omit new
  metadata.
- Verify Desktop consumes backend descriptors/readiness/actions and does not
  parse Jira auth or lifecycle independently.
- Confirm no SharePoint-named implementation path exists in the Jira candidate
  tree or Jira delta.
- Inspect upstream customization and merge-rehearsal coverage for every generic
  Hermes symbol changed. Do not advance real refs or brand branches.

### 10. Audit test quality adversarially

- For each load-bearing invariant, identify whether coverage uses real imports,
  temp profile state, descriptor parsing, lifecycle staging, subprocesses,
  files, workflow compilation/admission, installed lookup, and renderer
  behavior or only mocks and copied logic.
- Mutation-check the highest-risk guards in a private detached worktree: REST
  fallback, Cloudflare fallback, argv/body secrecy, output bound, ambiguous
  write no-retry, approval binding, migration marker, disabled tool/skill
  absence, workflow readiness, and vendor provenance/parity.
- A useful mutation removes or weakens the production guard and demonstrates
  that an existing or small temporary test fails for the intended reason.
- Look for broad exception swallowing, assertions against the fixture rather
  than the result, tests that pass with the guard reverted, unobserved
  background failure, platform skips that erase the contract, and change-
  detector tests that freeze counts instead of behavior.
- Do not commit mutation tests or fixes. Record the exact mutation and outcome
  in the report.

## Required commands and evidence discipline

Use the existing repository environments. Do not mutate or replace root
virtual environments:

```text
SOURCE_PY=/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.venv/bin/python
HERMES_PY=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
```

Start with read-only identity evidence:

```bash
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities status --short --branch
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities worktree list --porcelain
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities cat-file -e f52a131cc63643f995e9d125bfa3fc7fa865700f^{commit}
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities diff --stat 634ca3bc9d4c543a1dc02e1ec01e2e1c604ee2e8..f52a131cc63643f995e9d125bfa3fc7fa865700f
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities log --reverse --oneline 634ca3bc9d4c543a1dc02e1ec01e2e1c604ee2e8..f52a131cc63643f995e9d125bfa3fc7fa865700f

git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent status --short --branch
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent worktree list --porcelain
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent cat-file -e 7d35a7ec27707483dda7991f60f9d26aeda43389^{commit}
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent diff --stat d48f783b254ac2faa93b9f9db7a7ed6098e2172b..7d35a7ec27707483dda7991f60f9d26aeda43389
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent log --reverse --oneline d48f783b254ac2faa93b9f9db7a7ed6098e2172b..7d35a7ec27707483dda7991f60f9d26aeda43389

shasum -a 256 \
  /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/specs/2026-08-09-ericsson-connector-plugins-design.md \
  /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-09-ericsson-jira-connector.md \
  /Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/ericsson-jira-connector/docs/connector-porting/jira-behavior-map.md
```

At minimum run and report these ordinary deterministic gates from the exact
candidate worktrees:

```bash
cd /Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/ericsson-jira-connector
/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.venv/bin/python -m pytest tests/test_jira_*.py -q
/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.venv/bin/python skills/ericsson/onboard-ericsson-capabilities/scripts/build_catalog.py --check
/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.venv/bin/python skills/ericsson/onboard-ericsson-capabilities/scripts/validate_catalog.py
git diff --check 634ca3bc9d4c543a1dc02e1ec01e2e1c604ee2e8..f52a131cc63643f995e9d125bfa3fc7fa865700f

cd /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/ericsson-jira-connector
node --test scripts/__tests__/vendor-ericsson.test.mjs
scripts/run_tests.sh tests/hermes_cli/test_plugin_configuration.py \
  tests/hermes_cli/test_plugin_configuration_api.py \
  tests/hermes_cli/test_capability_staging.py \
  tests/hermes_cli/test_ericsson_connector_distribution.py \
  tests/hermes_cli/test_ericsson_connector_surfaces.py \
  tests/plugins/workflow/test_ericsson_connector_toolsets.py \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  tests/hermes_cli/test_plugins.py \
  tests/hermes_cli/test_tools_config.py \
  tests/hermes_cli/test_capability_env_vars.py -q
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/check_upstream_customizations.py
npm --workspace apps/desktop run typecheck
npm --workspace apps/desktop run lint
npm --workspace apps/desktop run test:ui -- \
  src/app/settings/plugin-toolset-config-panel.test.tsx
git diff --check d48f783b254ac2faa93b9f9db7a7ed6098e2172b..7d35a7ec27707483dda7991f60f9d26aeda43389
```

Use `scripts/run_tests.sh` for Hermes Python tests, never direct `pytest`.
Ericsson source tests use the source repository interpreter shown above.

The implementation team reports that the full Ericsson source suite and full
33,298-test Hermes suite passed. Treat those as prior evidence, not proof of a
particular invariant. Rerun full suites only if needed to validate a candidate
finding or a broad regression; spend most review effort on production tracing,
boundary cases, and meaningful mutation checks.

The exact Jira Hermes candidate has a successful detached upstream/brand merge
simulation report at:

`/tmp/jira-final-origin-merge-evidence.QSrPBZ/merge-evidence.json`

If that temporary file still exists, inspect it as a claim. It records
`pre_base_commit` and `post_base_commit` as exact
`7d35a7ec27707483dda7991f60f9d26aeda43389`, tests `origin/main` at
`36cb5ae5530a75def7df3195e49b7a4aa2add482`, and reports base, ledger, OTTO, and LOOP24
invariant gates passed. Do not treat the existence of the report as code proof.
If you rerun the simulation, use explicit immutable refs and a new temporary
report directory; confirm all actual branch refs are unchanged afterward.

Record every command actually run, working directory, result, duration where
meaningful, and whether the evidence came from inspection, execution,
mutation, legacy tracing, or documentation comparison. Never report an unrun
command as passed. A failed baseline or environment-dependent check must be
reproduced at the appropriate predecessor before attribution.

Do not use another agent's summary as proof. Reduce every lead to direct code,
contract, legacy, and command evidence.

## Finding severity and proof standard

Only these severities are eligible:

- **CRITICAL** — credentials or authenticated Jira authority are disclosed or
  redirected; an unapproved remote mutation can execute; source/vendor
  identity is materially false; Jira integration corrupts a major shared
  Hermes/plugin/workflow path; or the release premise is fundamentally false.
- **HIGH** — a non-negotiable invariant is violated with a realistic trigger;
  a supported auth/deployment mode is materially unusable; a deterministic
  race or retry can duplicate/lose a comment; cancellation or bounds fail in a
  way that can hang or exhaust a real process; lifecycle/profile state is
  wrong; workflow approval/readiness can be bypassed; installed bytes differ
  from the reviewed source; legacy public compatibility is materially broken;
  or a generic change creates a major regression.

Do not inflate severity. Do not downgrade a deterministic race because its
interleaving is difficult. Do not report a concern if its realistic production
consequence is only MEDIUM or LOW.

Every finding must contain all of these elements:

1. stable ID and severity;
2. concise title;
3. affected source and/or Hermes immutable SHA and plan task;
4. exact final production file and current line or symbol;
5. violated invariant and contract source;
6. realistic trigger, input, state, or interleaving;
7. concrete wrong result and user/operator/release consequence;
8. direct code trace plus deterministic reproduction or compelling executable
   evidence;
9. why existing validation, reconciliation, another layer, or Task 10 UAT does
   not already prevent the defect;
10. smallest safe fix that closes the whole defect class without widening Jira
    scope; and
11. exact missing regression test or mutation that would prove the fix.

If one element is missing, do not present the concern as a finding. Put it in
the unverified-evidence section without remediation, or omit it. Installed
Windows checks that cannot run on the review platform are residual validation
risks, not automatically code findings.

## Required output

Write the review to:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/reviews/2026-08-13-ericsson-jira-connector-adversarial-code-review-<model_name>.md`

Replace `<model_name>` with the reviewing model's short name.

The report must contain:

1. model, platform, date, repository states, immutable SHAs/trees, input
   hashes, exact ranges, and changed-path counts actually reviewed;
2. overall verdict: `READY FOR TASK 10`, `CONDITIONAL`, or `DO NOT ENTER TASK 10`;
3. findings table sorted CRITICAL before HIGH, followed by the complete
   eleven-element proof for every finding;
4. Task 1–9 traceability matrix with `proven`, `contradicted`, or
   `not established`;
5. exact verdict on all eighteen non-negotiable invariants;
6. exact verdict on all twenty-two specific implementation decisions;
7. legacy/current/final behavior parity matrix for reads, search, auth,
   transports, normalization, comments, skills, and workflows;
8. lifecycle/profile/surface matrix covering disabled, configured, ready,
   migrated, explicitly re-enabled, fresh-session, CLI/TUI, Desktop,
   gateway/API, Kanban, cron, workflow, and installed behavior;
9. source-to-vendor provenance and byte-parity assessment, including policy
   sidecars, GitLab prerequisite corrections, and SharePoint exclusion;
10. tests and mutation-quality assessment for each load-bearing area;
11. concise verification ledger with every command/result and evidence type;
12. what was verified safe, including the adversarial cases attempted, without
   praise or generic summaries;
13. residual installed-UAT-only risks, especially native Windows curl path and
   permissions, real bearer/basic Jira deployments, real Cloudflare 1010,
   process/log inspection, Desktop configuration, and restart/upgrade state;
14. explicit confirmation that no standalone security/threat-review workflow,
   live service, real credential, release, push, brand mutation, or Task 10
   action was attempted.

If there are no qualifying findings, write exactly:

```text
NO CRITICAL OR HIGH FINDINGS
```

Then still provide the matrices, decisions, adversarial cases, evidence
ledger, and residual installed-UAT-only risks. Do not append MEDIUM/LOW
observations after that verdict.

End the report with exactly one of these statements:

- `JIRA CANDIDATE MUST NOT ENTER TASK 10 UNTIL ALL CRITICAL AND HIGH FINDINGS ARE RESOLVED.`
- `JIRA CANDIDATE MAY ENTER TASK 10 ONLY AFTER THE LISTED RELEASE-BLOCKING EVIDENCE IS OBTAINED.`
- `JIRA CANDIDATE IS READY FOR TASK 10 INSTALLED RELEASE VALIDATION.`

Do not implement fixes. Stop after writing the report.
