# Adversarial code-review prompt — Ericsson SharePoint connector

Paste everything below the line into a fresh, capable model or coding agent
with read and shell access to the repositories named in this prompt.

The reviewer must assess the complete Ericsson SharePoint connector and generic
Microsoft Graph candidate for **CRITICAL and HIGH production defects only**
before SharePoint Task 12 promotes and publishes branded v5.7.0 candidates.
This is a review task, not an implementation task. Do not modify production
code, tests, generated files, Git history, branches, worktrees, or refs. Do not
push, publish, open a pull request, dispatch a workflow, create a release, or
begin installed UAT. The only authorized persistent repository write is the
final review report named under Required output.

This is an adversarial correctness review. Try to falsify the SharePoint
candidate's behavioral, authorization, boundary, and integration claims. Do
not merely summarize the implementation, repeat test names, or bless green
suites.

Security-sensitive paths are in functional correctness scope. A concrete
defect such as token or cookie disclosure, cross-origin authority, approval
bypass, local file escape, unsafe replay, browser-session theft, or incomplete
process/file cleanup may be reported when established through ordinary code
tracing and deterministic synthetic tests. However, do **not** run a standalone
threat model, security audit, security-review workflow, penetration test,
vulnerability scanner, or exploit-development exercise. Do not use real
credentials, malicious payloads, or live Microsoft/Ericsson services. All
reproductions must use benign synthetic data, fake transports/browser
authorities, isolated temporary state, and no network access.

---

## Role

You are a skeptical principal-level reviewer experienced with Python plugin
systems, Microsoft Graph, OAuth/MSAL and Azure identity, bounded streaming and
pagination, resumable uploads, asynchronous operations, browser-session
ownership, local filesystem boundaries, durable approval, Windows behavior,
TypeScript/Electron integration, source-first vendoring, and installed package
behavior.

Your job is to find release-blocking defects, not to produce a general code
quality report. Assume every completion claim is unproven until you trace the
actual final production path and establish the invariant from code plus
ordinary behavioral evidence.

Commit messages, completion reports, prior reviews, comments, test names, mock
call counts, and green aggregate counts are leads, not proof. Read every
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
legacy implementation, and Git history before calling it a defect. Permanent
deletion, document parsing/OCR/conversion/generation, hidden connector-local
LLM calls, a private Edge/CDP launcher, and unbounded tenant-wide collection
are intentionally excluded.

## Repository and immutable review scope

### Ericsson source repository

Root:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities`

| Meaning | Commit |
|---|---|
| Original pinned pre-program source baseline | `dae405ede7049b621e502d9259f97481c940a65b` |
| Exact merged Jira source predecessor | `6b178d170b6f0c81f71fd19fa00f18370e985b5c` |
| Final merged SharePoint source candidate | `fdb83a7859456776556d99274284c01acc05de10` |
| Final SharePoint source tree | `73464cf97b51c6086d04201db0af49f0b5f3adbf` |

Primary source review range:

```text
6b178d170b6f0c81f71fd19fa00f18370e985b5c..fdb83a7859456776556d99274284c01acc05de10
```

At prompt creation this range contains 14 commits and 48 changed files, with
6,185 insertions and 27 deletions. Verify the ancestry, counts, changed paths,
and final tree yourself. This range includes the pre-Jira SharePoint commits,
the exact Jira/SharePoint synchronization merge, disabled-by-default
correction, and authenticated workflow-package correction. Review merge
resolution in shared manifests, catalogs, docs, and workflow-package files;
do not silently exclude it.

### Hermes repository and installed candidate

Root:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

| Meaning | Commit |
|---|---|
| Original pinned Hermes baseline | `da59906aaad8f9cb023fb66426c6f60ff5afa04a` |
| Exact merged Jira neutral-base predecessor | `911b7e77e8c6a536d5f95ecc945b1a9396c547bb` |
| Final merged SharePoint Hermes candidate | `dea2900d19665ccd3119963fe8b60a0f529a9ba8` |
| Final SharePoint Hermes tree | `fbb3637536c433fb32192597453b5fcbaa7ebaba` |

Primary Hermes review range:

```text
911b7e77e8c6a536d5f95ecc945b1a9396c547bb..dea2900d19665ccd3119963fe8b60a0f529a9ba8
```

At prompt creation this range contains eight commits and 37 changed files,
with 5,930 insertions and 101 deletions. Verify those numbers. Review the final
tree at `dea2900d19665ccd3119963fe8b60a0f529a9ba8` as the sole installed-candidate
verdict target.

At prompt creation, local Ericsson `main` points at the exact source candidate
and is 14 commits ahead of `origin/main`. Hermes neutral `base` has since
advanced one unrelated documentation-only commit to
`aac5eb45420b4241d525d4deea21c2e41ff0f5da`; the exact installed SharePoint
candidate `dea2900d19665ccd3119963fe8b60a0f529a9ba8` is its parent and remains the
immutable verdict target. Hermes `base` is therefore nine commits ahead of
`origin/base`, while the SharePoint review range itself remains eight commits.
The temporary SharePoint feature worktrees and local feature branches were
removed after successful fast-forward integration. Remote refs have not been
advanced. Root tips are observational state only and may move again; never
substitute them for the immutable candidate SHAs.

Hermes literal `main` is synchronization-only. It is not a SharePoint
development target. Do not merge it, reconcile it, or use its mutable tip as
the candidate under review.

### Legacy behavior repository

Read-only root:

`/Users/coreyellis/code/gitlab.rosetta.ericssondevops.com/loop_24`

Pinned behavioral snapshot:

`fc3bf26d64e05cc3703ee39e323bbf3c1eaa4cd6`

Accepted connector snapshot:

`8ca26f882bc461d9aaa80a252685568c8749394a`

Verify that the three inspected SharePoint Python files are byte-identical
between these snapshots before using the later snapshot as behavior evidence.

### Preservation rules

Begin by recording branch, exact SHA, status, and worktree lists for both
active repositories. Preserve every unrelated tracked or untracked change and
every existing worktree. Do not clean, reset, stash, switch a shared checkout,
delete refs, or remove a worktree you did not create.

The Hermes root intentionally contains unrelated untracked `.otto/` and
`docs/` material, including this prompt. Preserve it. Use read-only commands
against immutable Git objects. Do not run candidate gates from a mutable root
tip. Create detached private review worktrees at the exact candidate SHAs as
specified below.

Create the detached review worktrees under a private `mktemp -d` directory,
change only those disposable worktrees, make no commit, and remove only what
you created. Use the same disposable worktrees for mutation testing. Temporary
fixtures and analysis scripts must also live under that private directory.

## Immutable design and behavior inputs

Read these files completely before judging the implementation:

1. `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/AGENTS.md`
2. `/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/AGENTS.md`
3. `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/apps/desktop/AGENTS.md`
4. `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/specs/2026-08-09-ericsson-connector-plugins-design.md`
5. `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-09-ericsson-sharepoint-connector.md`
6. `docs/connector-porting/sharepoint-behavior-map.md` from the exact final Ericsson source tree
7. `docs/configuration.md`, `docs/flows/sharepoint-document-intake.md`, and `docs/onboarding/windows-sharepoint-release-validation.md` from that tree
8. `sets/ericsson.json` and
   `skills/ericsson/onboard-ericsson-capabilities/references/catalog.json` from
   that tree
9. `capabilities/ericsson.json`, `capabilities/ericsson-vendored-paths.json`,
   `capabilities/workflow-packages/ericsson/digests.json`, and both
   `capabilities/workflow-packages/ericsson/workflows/sharepoint-document-intake.yaml`
   and
   `capabilities/workflow-packages/ericsson/workflows/sharepoint-document-intake.hermes.yaml`
   from the exact Hermes tree
10. `docs/upstream-customizations/microsoft-graph-connectors.yaml` and `plugin-configuration.yaml` from the exact Hermes tree
11. `docs/reviews/2026-08-13-ericsson-jira-connector-adversarial-code-review-prompt.md`
12. `docs/reviews/2026-08-13-ericsson-jira-connector-adversarial-code-review-fable-5.md`
13. `docs/reviews/2026-08-09-ericsson-connector-plugins-adversarial-plan-final-rereview-fable-5.md`

The Superpowers design and plan are intentionally ignored by Git. Read them
from the absolute root-checkout paths above, not from a temporary worktree.

Verify these content hashes before review:

| Artifact | SHA-256 |
|---|---|
| Connector design | `93e2f4d2b52e6f2be48b551364033522079958cebbc34d4725c0e8422f261cc6` |
| SharePoint implementation plan | `82b62c92843e062a0586cd08c086187c330623b43e6c7c58c56d2bd2c1abe2ea` |
| Final SharePoint behavior map | `b67f7628fbc29d09738f344e39a6e021ee4e254b65b99fa0c1ce526cf5d15c2c` |

If one differs, record `REVIEW_INPUT_CHANGED`, the observed hash, and whether
the immutable code candidate remains meaningfully reviewable. Do not silently
substitute a changed contract. Treat the design, SharePoint plan, and behavior
map as the delivery contract. Treat prior reviews and completion summaries as
claims to verify, not authorities that override final code.

## Legacy sources to reconstruct independently

At `fc3bf26d64e05cc3703ee39e323bbf3c1eaa4cd6`, read fully:

- `utils/sp_files.py`
- `utils/sp_audit.py`
- `custom_components/ericsson_parsers/sharepoint_files_fetcher.py`
- `flows/nw_hardening_blocks/nw_hardening_sharepoint_data_f.json`
- the PowerShell wrappers only to identify process/environment behavior relied
  on by real callers, not as target architecture

Independently distinguish deterministic SharePoint behavior from Langflow UI
behavior, embedded LLM reasoning, document parsing, dead code, unsafe behavior
deliberately replaced, and unbounded behavior deliberately constrained. Do not
require preservation of arbitrary-host URL acceptance, unrestricted local
paths, implicit overwrite, permanent deletion, raw CDP ownership, absolute
path disclosure, unbounded traversal, or whole-file buffering.

## What the candidate claims to deliver

The final candidate claims all of the following:

- a bundled standalone `ericsson-sharepoint` plugin disabled for every fresh
  profile until explicitly enabled;
- profile-scoped static configuration and write-only secrets with setup
  actions for Graph authentication/testing and browser enrollment/release;
- generic connector-neutral Graph identity supporting deterministic `auto`,
  delegated MSAL, app-only, and explicitly enabled Azure CLI modes while
  preserving existing app-only and Teams behavior;
- a bounded private delegated token cache with account selection, corruption
  handling, silent refresh, explicit interactive setup, and secret-free
  readiness;
- generic Graph request, pagination, redirected download, resumable upload,
  and asynchronous-operation primitives with authority, deadline,
  cancellation, redaction, and ambiguous-write controls;
- exact public tools `sharepoint_resolve_url`, `sharepoint_get_item`,
  `sharepoint_list_items`, `sharepoint_download`,
  `sharepoint_list_owned_sites`, `sharepoint_audit_permissions`,
  `sharepoint_upload`, `sharepoint_create_folder`, `sharepoint_move_item`,
  `sharepoint_copy_item`, and `sharepoint_recycle_item`;
- strict configured-tenant URL, site, drive, folder, and DriveItem identity;
- bounded listing, filtering, recursion, owned-site discovery, downloads, and
  normalized partial/truncation evidence;
- download and upload access confined to configured local roots, with safe
  names, symlink/device/traversal rejection, partial cleanup, relative public
  evidence, digests, and exact one-operation interactive expansion;
- small and resumable uploads, folder creation, move, copy, and recycle writes
  with exact approval/admitted authority, conflict policy, idempotent recovery,
  async completion, and no blind ambiguous retry;
- permission auditing through a named core-owned enrolled-browser session with
  trusted origins, ownership-aware release, bounded categories, and truthful
  complete/partial/truncated/unreachable status;
- independent Graph and browser-audit readiness so missing browser enrollment
  hides only the audit tool;
- source-owned navigation, file-operation, and permission-audit skills plus a
  thin always-discoverable router;
- a packaged authenticated document-intake workflow that stops after bounded
  artifact acquisition and does not claim parsing or generation;
- exact source-first vendoring, generated provenance, Desktop configuration,
  fresh-session lifecycle, workflow admission, and installed-distribution
  behavior; and
- no permanent delete, connector-local LLM, document parser/OCR/generator,
  private Graph client, private browser launcher/CDP port, new core model tool,
  or unintended Jira/GitLab/Teams behavior change.

## Task map to cover

Use the plan for exact acceptance criteria, but cover every completed task:

| Task | Production concern |
|---:|---|
| 1 | Frozen legacy SharePoint and current Graph/Teams behavior |
| 2 | Generic Graph identity, cache, readiness, and app-only compatibility |
| 3 | Bounded Graph download/upload/async-operation primitives |
| 4 | Standalone descriptor, configuration, setup actions, and independent readiness |
| 5 | Tenant URL, site, drive, path, and DriveItem resolution |
| 6 | Bounded listing, filtering, recursion, downloads, and local artifacts |
| 7 | Owned-site discovery and browser-backed permission auditing |
| 8 | Approval-aware upload/folder/move/copy/recycle writes |
| 9 | Skills, router, authenticated workflow, onboarding, and UAT contract |
| 10 | Source closure, Teams invariant, and ordinary review corrections |
| 11 | Jira synchronization, shared regeneration, exact vendoring, installed surfaces, and merge |

Task 11 has fast-forwarded the verified candidates locally into Ericsson
`main` and Hermes neutral `base`. Task 12 has not started. Your verdict controls
whether these immutable SHAs should proceed to Task 12 brand promotion,
release construction, and installed Windows/live-tenant UAT. Do not perform
those actions.

## Non-negotiable invariants

A demonstrated violation of any invariant is HIGH or CRITICAL depending on
impact.

1. **Source ownership and byte identity are exact.** Hermes `vendoredFrom`
   equals `fdb83a7859456776556d99274284c01acc05de10`; every manifest-managed
   SharePoint byte and workflow sidecar/package byte matches the clean source
   candidate; installed execution needs no source checkout.
2. **Disabled means absent from model requests.** Fresh or explicitly disabled
   profiles do not import SharePoint executable code or expose plugin tools or
   plugin-owned skills. Static configuration and the thin router may remain
   discoverable. Changes affect only fresh conversations and never rewrite a
   cached prompt/tool prefix.
3. **Configuration has one profile-scoped authority.** Settings, write-only
   secrets, cache state, browser profile, local roots, bounds, and enablement
   resolve from the executing profile across CLI/TUI, Desktop, gateway/API,
   Kanban, cron, and workflows; credentials never imply enablement.
4. **Auth selection is deterministic and noninteractive by default.** `auto`
   selects only a fully configured supported mode, partial state is a readiness
   error, Azure CLI requires its explicit gate, and unattended work never opens
   a browser/device flow.
5. **Identity material remains private and isolated.** Tokens, client secrets,
   MSAL cache bytes, account identifiers where sensitive, browser cookies,
   CDP URLs, profile paths, raw Graph/browser bodies, and authorization headers
   never enter reprs, errors, logs, tool results, evidence, Desktop state, or
   another profile.
6. **Generic Graph request authority never drifts.** Initial URLs, OData next
   links, redirected downloads, upload sessions, and async-operation locations
   stay within their separately approved authority rules. A Graph bearer is
   never forwarded to a CDN or SharePoint/browser origin.
7. **Bounds apply before uncontrolled work.** Pages, items, recursion depth,
   rows, bytes, response bodies, chunks, retries, delays, sites, audit
   categories, local files, and polling attempts are bounded in production,
   not only checked after unbounded buffering/allocation.
8. **Cancellation and deadlines reach real work.** Cancellation/deadline
   controls reach HTTP streaming, retry delays, pagination, upload chunks,
   async polling, browser evaluation/navigation, local staging, and cleanup.
   They cannot leave `.part` files, staged copies, sessions, or remote replay
   loops behind.
9. **SharePoint identity is exact.** URL parsing, UI-prefix removal, percent
   decoding, tenant allowlisting, root/site/team/library selection, default
   drive rules, drive/item ids, and safe path fallback cannot broaden or
   redirect authority, confuse a file with a folder, or silently select an
   ambiguous drive.
10. **Reads and listings are truthful.** Ordering, filtering, pagination,
    recursion, cycle handling, truncation, malformed rows, partial failures,
    and safe web URLs cannot invent facts, omit work while claiming
    completeness, or expose raw remote payloads.
11. **Local file boundaries are exact.** Downloads write only beneath the
    configured authorized root or an explicitly approved one-operation root;
    uploads read only beneath their authorized root. Traversal, symlink races,
    special/device files, unsafe names, absolute-path evidence, and unattended
    boundary expansion are rejected.
12. **Redirected downloads preserve confidentiality and atomicity.** Redirect
    acceptance is narrow and host-constrained, Graph authorization is stripped
    before CDN access, total bytes are streamed-bounded, final publication is
    atomic, and every failure removes the partial.
13. **Resumable uploads never guess progress.** Chunk alignment/ranges,
    returned offsets, expiration, local file stability, aggregate/session
    limits, final identity, and cancellation are validated. An unknown outcome
    never restarts or duplicates the upload blindly.
14. **Write authority binds the exact mutation.** Interactive approval or
    sealed workflow admission covers the exact tool and current arguments for
    upload, folder creation, move, copy, or recycle. Caller-authored approval,
    stale approval, argument mutation, or a sibling invocation cannot widen it.
15. **Write recovery is truthful and non-replaying.** Conflicts, existing
    folders, ETags, upload acceptance, async-copy acceptance, timeout, and
    disconnect are reconciled read-only where safe. No ambiguous mutation is
    automatically retried, and no unobserved object is reported created,
    moved, copied, uploaded, or recycled.
16. **Browser authority remains core-owned.** The connector uses only the
    configured enrolled profile through the core registry/manager, validates
    trusted origins, never claims a raw port/profile/process, preserves reused
    and parallel sessions, and releases only the session it owns.
17. **Audit readiness and results are independent and truthful.** Missing
    browser enrollment blocks only `sharepoint_audit_permissions`; Graph tools
    and owned-site discovery keep Graph readiness. Category/site failures and
    bounds produce explicit partial/truncated/unreachable status rather than
    false empty success.
18. **Skills reason; tools integrate deterministically.** Skills contain no
    Graph/browser client, credentials, hidden provider call, local boundary
    bypass, or write authorization. The plugin contains no connector-local LLM.
19. **Workflow contracts are executable and honest.** Flat
    `requires: [ericsson-sharepoint]`, exact `allowed_tools`, readiness,
    approval, output references, terminal status, package authentication, and
    installed lookup use real current semantics. Document intake stops after
    acquisition and never claims parsing/generation.
20. **Every installed surface uses one registered plugin.** CLI/TUI, Desktop,
    gateway/API, Kanban, cron, and Archon use the same profile-scoped
    implementation and fresh-session behavior. Desktop is a backend projection,
    not an independent Graph/browser resolver.
21. **Generic and neighboring behavior is preserved.** Generic Graph code has
    no Ericsson/Jira/SharePoint id, existing app-only and Teams behavior remains
    compatible, Jira/GitLab installed bytes and public behavior are unchanged
    except required shared generated references, no permanent delete or new
    core model tool exists, and upstream customization/merge gates cover every
    generic symbol changed.

## Specific implementation decisions to attack

Reach an explicit `supported`, `contradicted`, or `not established` verdict on
each decision. Do not accept a premise merely because a named test passes.

1. `auto`, delegated MSAL, app-only, and Azure CLI selection reject partial or
   ambiguous configuration and use correct scopes/authority without silently
   switching identity.
2. The delegated token cache enforces size, private mode, no-follow/symlink
   rules, atomic replacement, corruption handling, account selection, refresh
   serialization, and profile isolation on POSIX and Windows-relevant paths.
3. Explicit interactive authentication updates only the intended profile cache;
   readiness and ordinary operations never trigger interaction.
4. Azure CLI is used only when `azure_cli_enabled` permits it and reuses the
   existing adapter without copying token-store state or leaking CLI details.
5. Existing `GraphCredentials.from_env()` and `MicrosoftGraphTokenProvider`
   app-only callers retain their exact Teams-compatible behavior.
6. 401 refresh, transport/429/5xx retries, `Retry-After`, cancellation, and one
   absolute deadline cannot multiply across pagination, redirects, upload
   sessions, or polling.
7. OData next links are treated as opaque only after Graph-origin validation;
   first-page params are not incorrectly reapplied and loops are detected.
8. Redirected download logic validates the expected status/location, strips
   Graph bearer authority, constrains allowed CDN semantics, streams within the
   aggregate limit, and removes partial files on every exception class.
9. Upload-session creation and chunk upload validate 320-KiB alignment,
   `Content-Range`, monotonic returned offsets, expiration, terminal response,
   local size/stability, and ambiguous outcomes without blind restart.
10. Async copy start and polling validate `Location`, origin, `Retry-After`,
    status transitions, deadline/cancellation, terminal errors, and unknown
    acceptance without turning a timeout into success.
11. SharePoint URL parsing handles legacy UI prefixes, root sites,
    `sites/`/`teams/`, encoded spaces, malformed percent escapes, userinfo,
    ports, Unicode/IDNA, query/fragment, and host lookalikes without authority
    escape or identity drift.
12. Default-drive aliases and underscore-prefixed internal paths work as
    documented; named-drive matching is bounded and rejects ambiguity instead
    of selecting the first match.
13. Drive/item ids and path fallback encode segments independently and cannot
    be confused by slashes, commas, apostrophes, viewer URLs, or root/folder
    identities.
14. Recursive listing enforces depth/item/page/byte/deadline/cycle limits while
    preserving stable relative paths, filters, ordering, and explicit
    truncation/partial warnings.
15. Remote names and local paths cannot escape through traversal, Unicode or
    separator variants, symlink components/races, pre-existing special files,
    parent creation, rename, or absolute-path result projection.
16. One-operation interactive file authorization is exact, cannot be forged in
    arguments or reused, and is categorically unavailable to unattended
    workers, cron, and workflows.
17. Owned-site discovery paginates groups and site resolution within bounds,
    retains successful sites after per-group failures, and reports partial
    warnings rather than silently omitting inaccessible sites.
18. Browser enrollment, acquisition, navigation, evaluation, and release use
    the configured core profile/trusted origin and do not steal, close, or
    corrupt reused or parallel sessions.
19. Browser-evaluated REST follows only same-origin bounded next links; scripts,
    cookies, raw responses, CDP details, profile paths, and provider errors do
    not escape into public results or artifacts.
20. Permission-audit category normalization and aggregate status distinguish
    complete, partial, truncated, and unreachable across users, admins, roles,
    groups/members, lists, subsites, and site metadata.
21. Approval and admitted authority are bound to the exact current write tool
    and arguments and cannot be widened from a tool name alone or bypassed by
    direct `invoke()`/operation calls.
22. Upload source staging rechecks the authorized regular file, detects
    mutation where required, bounds copy/read work, removes staging on every
    path, and never discloses the absolute source path.
23. Folder `exist_ok`, upload conflict policy, move rename/parent identity,
    cross-drive/cross-tenant constraints, ETags, recycle-root rejection, and
    copy destination identity match the documented user intent.
24. All ambiguous upload/folder/move/copy/recycle paths remain non-retryable at
    both plugin and generic Graph layers; outer generic retry code cannot replay
    a mutation the connector classified as unknown.
25. The source-shaped and authenticated packaged document-intake workflows are
    byte-consistent, discoverable after installation, admit only when ready,
    use exact tools, and stop after returning bounded artifact evidence.
26. Vendoring from `fdb83a7859456776556d99274284c01acc05de10`
    preserves all managed bytes and policy sidecars, removes only stale managed
    files, records exact provenance, and does not alter Jira/GitLab production
    bytes.
27. Tests for identity, authority, bounds, cleanup, approval, browser ownership,
    workflows, installed behavior, and Teams preservation fail when the actual
    production guard is removed; they do not merely assert fixtures, duplicate
    implementation logic, or prove imports.

## Required review method

### 1. Establish immutable scope and traceability

- Verify commits, trees, parentage, ancestry, counts, changed paths, input
  hashes, root states, remotes, and absence of the removed SharePoint feature
  worktrees/branches.
- Build a Task 1–11 matrix with `proven`, `contradicted`, or `not established`.
- Classify changed files as SharePoint production, generic Graph production,
  integration/vendoring, skills/workflows/docs, shared generated artifact, or
  test/evidence.
- Trace every acceptance criterion to final production code and real callers.
  Review final trees, not only task commits; look for later corrections or
  merge resolution that weakened an earlier invariant.

### 2. Reconstruct legacy and current public behavior

- Build a parity matrix from the legacy files/flow, current Graph/Teams
  baseline, behavior map, Jira predecessor, and final tools.
- Record inputs/defaults, identity source, authority, pagination/filter/order,
  outputs, deadlines/cancellation, local effects, remote mutations, Windows
  assumptions, and downstream consumers.
- Mark behavior preserved, safely adapted, intentionally excluded, deferred,
  or contradicted. Do not demand preservation of legacy unsafe or unbounded
  mechanics.

### 3. Attack lifecycle, configuration, identity, and profile propagation

- Trace source manifest through vendoring, catalog projection, descriptor
  validation, staging, settings/secrets/cache, enablement, plugin import,
  tools/skills, fresh agent construction, workflow readiness, and installed
  lookup.
- Exercise fresh, disabled, configured-disabled, each complete auth mode, each
  partial/ambiguous mode, interactive-required, browser-unenrolled,
  Graph-ready/browser-unready, explicitly enabled, restarted, restaged, and
  separate-profile states.
- Trace CLI/TUI, Desktop, gateway/API, Kanban, cron, and workflows. Find any
  default-profile, process-global env, or interactive fallback path. Confirm
  existing conversations remain byte-stable.

### 4. Attack generic Graph authority, bounds, and lifecycle

- Trace credentials/provider/cache into every request, refresh, retry, page,
  redirected download, upload session, and async poll.
- Exercise conspicuous benign token/secret sentinels through reprs, exceptions,
  logs, results, config projections, and cleanup.
- Test initial and returned URLs, encoded/Unicode hosts, userinfo, ports,
  fragments, redirects, next-link loops, upload-session locations, async
  locations, Retry-After extremes, cancellation, and shared deadlines.
- Verify streamed limits occur during collection and mutation retry classifiers
  cannot be bypassed by an outer layer.

### 5. Attack SharePoint identity, reads, and local artifacts

- Trace every public read schema through URL parsing, tenant allowlisting, site
  and drive resolution, item addressing, pagination, recursion, filtering,
  normalization, download publication, and result projection.
- Exercise roots, files, folders, default/named/ambiguous drives, legacy UI
  prefixes, malformed escaping, hostile authority spellings, duplicate/cyclic
  pages, filtered pages, partial rows, lying/missing totals, and hard bounds.
- Exercise traversal, symlink components/races, special files, unsafe remote
  names, pre-existing destinations, partial failures, cancellation, one-time
  authorization, and unattended calls. Confirm only relative safe artifact
  evidence is public.

### 6. Attack owned-site discovery and browser-backed permission audit

- Trace Graph group/site discovery separately from browser readiness.
- Trace setup actions and the audit path through browser profile lookup,
  registry, manager acquisition, trusted origins, navigation, evaluation,
  pagination, normalization, artifact export, and release.
- Exercise missing enrollment, wrong origin, reused/parallel/operation-owned
  sessions, navigation timeout, cancellation, category failure, inaccessible
  group, truncation at every limit, malformed results, and export boundaries.
- Prove a failed category cannot become empty-complete and audit unavailability
  cannot hide Graph file or owned-site tools.

### 7. Attack writes, approval, upload, copy, and reconciliation

- Trace validation, local source authorization/staging, exact approval or
  admitted authority, request construction, conflict policy/ETag, upload
  chunks, async copy polling, recycle semantics, reconciliation, result
  projection, and cleanup.
- Exercise rejection, stale/reused/caller-authored approval, argument mutation,
  sibling invocation paths, duplicate clicks, concurrent operations, existing
  folders, overwrite/rename/fail, local file mutation, resume disagreement,
  timeout/disconnect after possible acceptance, cross-drive/tenant move/copy,
  root recycle, permission failure, and cancellation.
- Confirm no ambiguous mutation retries and reconciliation never mutates or
  falsely claims an outcome.

### 8. Attack skills and workflow execution

- Read all SharePoint skills and both workflow representations fully. Confirm
  exact tool names, discoverability, configuration/readiness guidance, local
  boundaries, approval rules, warning propagation, and absence of a second
  client/browser/LLM.
- Compile and trace the workflow through real admission/runtime and installed
  package lookup, not only YAML shape checks.
- Exercise disabled/unready blocking, bounded listing, selection, download
  partials, approval where applicable, artifact references, terminal status,
  and resume. Confirm it stops before parsing/OCR/conversion/generation.

### 9. Attack vendoring, installed surfaces, and neighboring regressions

- Compare every manifest-managed source/vendor pair using Git blobs at the two
  immutable SHAs. Verify provenance, inventories, policy sidecars, authenticated
  workflow package/digest, stale-file handling, and no source-tree borrowing.
- Trace generic Graph and plugin-configuration changes through Teams, Jira,
  GitLab, workflow, old descriptors, Desktop, gateway, workers, and schedules.
- Confirm generic Graph production contains no connector id, Desktop consumes
  backend descriptors/actions/readiness, and no Jira/GitLab production bytes
  changed in the SharePoint source range.
- Inspect upstream-customization and merge-rehearsal coverage without advancing
  real refs or brand branches.

### 10. Audit test quality adversarially

- For each invariant, identify whether coverage uses real imports, temp profile
  state, protected caches, descriptor parsing, staging, files/symlinks,
  streaming fake transports, browser authority, workflow compilation/admission,
  installed lookup, and UI projection or only mocks/copied logic.
- Mutation-check the highest-risk guards in a private detached worktree:
  origin/next-link/redirect validation, token stripping, partial cleanup,
  upload offset validation, ambiguous mutation no-retry, approval binding,
  local-root/symlink enforcement, browser ownership/trusted origin, audit
  partial status, disabled tool/skill absence, workflow package lookup, vendor
  parity, and Teams app-only compatibility.
- Record exact mutations and outcomes. Do not commit tests or fixes.

## Required commands and evidence discipline

Use the existing repository environments; do not mutate or replace them:

```text
SOURCE_ROOT=/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities
HERMES_ROOT=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
SOURCE_PY=/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.venv/bin/python
HERMES_PY=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
```

Start with read-only identity evidence:

```bash
git -C "$SOURCE_ROOT" status --short --branch
git -C "$SOURCE_ROOT" worktree list --porcelain
git -C "$SOURCE_ROOT" rev-parse main origin/main
git -C "$SOURCE_ROOT" branch --list feat/ericsson-sharepoint-connector
test ! -e "$SOURCE_ROOT/.worktrees/ericsson-sharepoint-connector"
git -C "$SOURCE_ROOT" cat-file -e fdb83a7859456776556d99274284c01acc05de10^{commit}
git -C "$SOURCE_ROOT" rev-parse fdb83a7859456776556d99274284c01acc05de10^{tree}
git -C "$SOURCE_ROOT" merge-base --is-ancestor 6b178d170b6f0c81f71fd19fa00f18370e985b5c fdb83a7859456776556d99274284c01acc05de10
git -C "$SOURCE_ROOT" diff --stat 6b178d170b6f0c81f71fd19fa00f18370e985b5c..fdb83a7859456776556d99274284c01acc05de10
git -C "$SOURCE_ROOT" log --reverse --oneline 6b178d170b6f0c81f71fd19fa00f18370e985b5c..fdb83a7859456776556d99274284c01acc05de10

git -C "$HERMES_ROOT" status --short --branch
git -C "$HERMES_ROOT" worktree list --porcelain
git -C "$HERMES_ROOT" rev-parse base origin/base
git -C "$HERMES_ROOT" branch --list feat/ericsson-sharepoint-connector
test ! -e "$HERMES_ROOT/.worktrees/ericsson-sharepoint-connector"
git -C "$HERMES_ROOT" cat-file -e dea2900d19665ccd3119963fe8b60a0f529a9ba8^{commit}
git -C "$HERMES_ROOT" rev-parse dea2900d19665ccd3119963fe8b60a0f529a9ba8^{tree}
git -C "$HERMES_ROOT" merge-base --is-ancestor 911b7e77e8c6a536d5f95ecc945b1a9396c547bb dea2900d19665ccd3119963fe8b60a0f529a9ba8
git -C "$HERMES_ROOT" diff --stat 911b7e77e8c6a536d5f95ecc945b1a9396c547bb..dea2900d19665ccd3119963fe8b60a0f529a9ba8
git -C "$HERMES_ROOT" log --reverse --oneline 911b7e77e8c6a536d5f95ecc945b1a9396c547bb..dea2900d19665ccd3119963fe8b60a0f529a9ba8

shasum -a 256 \
  "$HERMES_ROOT/docs/superpowers/specs/2026-08-09-ericsson-connector-plugins-design.md" \
  "$HERMES_ROOT/docs/superpowers/plans/2026-08-09-ericsson-sharepoint-connector.md"
git -C "$SOURCE_ROOT" show \
  fdb83a7859456776556d99274284c01acc05de10:docs/connector-porting/sharepoint-behavior-map.md \
  | shasum -a 256
```

After recording root state, create detached review worktrees at the immutable
candidates. Put the private directory beneath Hermes `.worktrees` so the
existing Node installation remains discoverable. First verify `.worktrees` is
ignored in both repositories. Record the generated paths and remove only these
worktrees after the report is written.

```bash
git -C "$SOURCE_ROOT" check-ignore -q .worktrees
git -C "$HERMES_ROOT" check-ignore -q .worktrees
REVIEW_TMP="$(mktemp -d "$HERMES_ROOT/.worktrees/sharepoint-adversarial.XXXXXX")"
SOURCE_REVIEW_WT="$REVIEW_TMP/source"
HERMES_REVIEW_WT="$REVIEW_TMP/hermes"
git -C "$SOURCE_ROOT" worktree add --detach "$SOURCE_REVIEW_WT" fdb83a7859456776556d99274284c01acc05de10
git -C "$HERMES_ROOT" worktree add --detach "$HERMES_REVIEW_WT" dea2900d19665ccd3119963fe8b60a0f529a9ba8
test "$(git -C "$SOURCE_REVIEW_WT" rev-parse HEAD)" = fdb83a7859456776556d99274284c01acc05de10
test "$(git -C "$HERMES_REVIEW_WT" rev-parse HEAD)" = dea2900d19665ccd3119963fe8b60a0f529a9ba8
```

At minimum run and report these deterministic gates from those exact detached
candidate worktrees:

```bash
cd "$SOURCE_REVIEW_WT"
HERMES_AGENT_DIR="$HERMES_REVIEW_WT" "$SOURCE_PY" -m pytest tests/test_sharepoint_*.py -q
HERMES_AGENT_DIR="$HERMES_REVIEW_WT" "$SOURCE_PY" -m pytest tests/test_teams_plugin.py -q
"$SOURCE_PY" skills/ericsson/onboard-ericsson-capabilities/scripts/build_catalog.py --check
"$SOURCE_PY" skills/ericsson/onboard-ericsson-capabilities/scripts/validate_catalog.py
git diff --check 6b178d170b6f0c81f71fd19fa00f18370e985b5c..fdb83a7859456776556d99274284c01acc05de10
git diff --quiet 6b178d170b6f0c81f71fd19fa00f18370e985b5c..fdb83a7859456776556d99274284c01acc05de10 -- \
  plugins/ericsson-jira plugins/ericsson-gitlab \
  skills/ericsson/jira skills/ericsson/jira-to-gitlab \
  skills/ericsson/gitlab workflows/jira-single-ticket-showcase.yml \
  workflows/jira-single-ticket-showcase.hermes.yaml \
  workflows/jira-to-gitlab.yml workflows/jira-to-gitlab.hermes.yaml

cd "$HERMES_REVIEW_WT"
HERMES_PYTHON="$HERMES_PY" scripts/run_tests.sh \
  tests/tools/test_microsoft_graph_auth.py \
  tests/tools/test_microsoft_graph_identity.py \
  tests/tools/test_microsoft_graph_client.py \
  tests/tools/test_microsoft_graph_large_transfer.py \
  tests/hermes_cli/test_plugin_configuration.py \
  tests/hermes_cli/test_plugin_configuration_api.py \
  tests/hermes_cli/test_capability_staging.py \
  tests/hermes_cli/test_ericsson_connector_distribution.py \
  tests/hermes_cli/test_ericsson_connector_surfaces.py \
  tests/plugins/workflow/test_ericsson_connector_toolsets.py \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  tests/gateway/test_teams.py \
  tests/gateway/test_teams_dotenv_isolation.py \
  tests/gateway/test_teams_pipeline_runtime_wiring.py \
  tests/plugins/test_teams_pipeline_plugin.py -q
"$HERMES_PY" scripts/check_upstream_customizations.py
node --test scripts/__tests__/vendor-ericsson.test.mjs
npm --workspace apps/desktop run typecheck
npm --workspace apps/desktop run lint
npm --workspace apps/desktop run test:ui -- \
  src/app/settings/plugin-toolset-config-panel.test.tsx
git diff --check 911b7e77e8c6a536d5f95ecc945b1a9396c547bb..dea2900d19665ccd3119963fe8b60a0f529a9ba8
```

After writing the report, validate the recorded paths and remove only the two
detached worktrees and their now-empty private parent:

```bash
test -n "$REVIEW_TMP"
case "$SOURCE_REVIEW_WT" in "$REVIEW_TMP"/*) ;; *) exit 2 ;; esac
case "$HERMES_REVIEW_WT" in "$REVIEW_TMP"/*) ;; *) exit 2 ;; esac
git -C "$SOURCE_ROOT" worktree remove --force "$SOURCE_REVIEW_WT"
git -C "$HERMES_ROOT" worktree remove --force "$HERMES_REVIEW_WT"
rmdir "$REVIEW_TMP"
```

Use `scripts/run_tests.sh` for Hermes Python tests, never direct `pytest`.
Because a detached worktree has no local virtual environment, pass the
existing root interpreter through `HERMES_PYTHON` as shown. Ericsson source
tests use `SOURCE_PY`. Set `HERMES_AGENT_DIR` when source tests need the paired
real workflow compiler; Jira-era fallback discovery depends on checkout depth
and is not reliable from every root/worktree layout.

The implementation team reports that the final merged Ericsson source suite
passed at 100%, and the final merged Hermes suite passed 33,349 tests across
2,816 files with zero failures at eight workers. It also reports 47 vendor
tests, Desktop typecheck/lint/UI, catalog, customization-ledger, workflow merge,
and Teams gates passed. Treat these as prior evidence, not proof of a specific
invariant. Rerun full suites only if needed for a candidate finding or broad
regression; spend most effort on production tracing, boundary cases, and
meaningful mutation checks.

Record every command actually run, working directory, result, duration where
meaningful, and evidence type: inspection, execution, mutation, legacy tracing,
or documentation comparison. Never report an unrun command as passed. Reproduce
a failed baseline or environment-dependent check at the appropriate predecessor
before attributing it to SharePoint.

Do not use another agent's summary as proof. Reduce every lead to direct code,
contract, legacy, and command evidence.

## Finding severity and proof standard

Only these severities are eligible:

- **CRITICAL** — credentials, browser authority, authenticated Graph/SharePoint
  authority, or arbitrary local file authority are disclosed or redirected; an
  unapproved remote mutation can execute; source/vendor identity is materially
  false; SharePoint corrupts a major shared Graph/plugin/workflow path; or the
  release premise is fundamentally false.
- **HIGH** — a non-negotiable invariant is violated with a realistic trigger;
  a supported identity/deployment mode is materially unusable; bounds,
  cancellation, or cleanup can hang/exhaust a real process or corrupt local
  artifacts; URL/file/browser authority escapes; a deterministic race/retry
  can duplicate, lose, or misreport a mutation; profile/readiness/lifecycle is
  wrong; installed bytes differ from reviewed source; legacy public behavior
  is materially broken; or a generic Graph change creates a major
  Teams/Jira/GitLab regression.

Do not inflate severity. Do not downgrade a deterministic race because its
interleaving is difficult. Do not report a concern whose realistic production
consequence is only MEDIUM or LOW.

Every finding must contain all of these elements:

1. stable ID and severity;
2. concise title;
3. affected source and/or Hermes immutable SHA and plan task;
4. exact final production file and current line or symbol;
5. violated invariant and contract source;
6. realistic trigger, input, state, or interleaving;
7. concrete wrong result and user/operator/release consequence;
8. direct code trace plus deterministic reproduction or compelling executable evidence;
9. why existing validation, reconciliation, another layer, or Task 12 UAT does not already prevent the defect;
10. smallest safe fix that closes the whole defect class without widening SharePoint scope; and
11. exact missing regression test or mutation that would prove the fix.

If one element is missing, do not present the concern as a finding. Put it in
the unverified-evidence section without remediation, or omit it. Installed
Windows/live-tenant checks that cannot run on the review platform are residual
validation risks, not automatically code findings.

## Required output

Write the review to:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/reviews/2026-08-13-ericsson-sharepoint-connector-adversarial-code-review-<model_name>.md`

Replace `<model_name>` with the reviewing model's short name.

The report must contain:

1. model, platform, date, repository states, immutable SHAs/trees, input
   hashes, exact ranges, and changed-path counts actually reviewed;
2. overall verdict: `READY FOR TASK 12`, `CONDITIONAL`, or `DO NOT ENTER TASK 12`;
3. findings table sorted CRITICAL before HIGH, followed by the complete
   eleven-element proof for every finding;
4. Task 1–11 traceability matrix with `proven`, `contradicted`, or
   `not established`;
5. exact verdict on all 21 non-negotiable invariants;
6. exact verdict on all 27 specific implementation decisions;
7. legacy/current/final parity matrix for identity, URL/item resolution,
   listing, downloads, uploads, folder/move/copy/recycle, owned sites, audit,
   skills, and workflows;
8. lifecycle/profile/surface matrix covering disabled, configured, each auth
   mode, browser readiness, fresh-session, CLI/TUI, Desktop, gateway/API,
   Kanban, cron, workflow, and installed behavior;
9. source-to-vendor provenance and byte-parity assessment, including policy
   sidecars, authenticated workflow package/digests, Jira preservation, and
   connector-neutral Graph code;
10. tests and mutation-quality assessment for every load-bearing area;
11. concise verification ledger with every command/result and evidence type;
12. what was verified safe, including adversarial cases attempted, without
   praise or generic summaries;
13. residual installed-UAT-only risks, especially Windows cache/file semantics,
   real delegated/app-only/Azure CLI identities, real tenant URL variants,
   CDN redirects, large upload resume, async copy, browser enrollment/audit,
   Desktop rendering, restart/upgrade, and live Jira/GitLab/Teams regression;
14. explicit confirmation that no standalone security/threat-review workflow,
   live service, real credential, release, push, brand mutation, or Task 12
   action was attempted.

If there are no qualifying findings, write exactly:

```text
NO CRITICAL OR HIGH FINDINGS
```

Then still provide the matrices, decisions, adversarial cases, evidence
ledger, and residual installed-UAT-only risks. Do not append MEDIUM/LOW
observations after that verdict.

End the report with exactly one of these statements:

- `SHAREPOINT CANDIDATE MUST NOT ENTER TASK 12 UNTIL ALL CRITICAL AND HIGH FINDINGS ARE RESOLVED.`
- `SHAREPOINT CANDIDATE MAY ENTER TASK 12 ONLY AFTER THE LISTED RELEASE-BLOCKING EVIDENCE IS OBTAINED.`
- `SHAREPOINT CANDIDATE IS READY FOR TASK 12 INSTALLED RELEASE VALIDATION.`

Do not implement fixes. Stop after writing the report.
