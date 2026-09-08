# Adversarial code and product-quality review prompt — Workflow Package Marketplace

Paste this complete prompt into two separate fresh coding-agent sessions: one
Claude session and one Codex session. Both reviewers must use the same immutable
candidate and must not read one another's reports. This is independent review,
not implementation or remediation.

The reviewer may read the repository, run bounded local commands, and create
disposable synthetic probes in temporary directories. Do not modify production
code, tests, generated files, configuration, dependencies, Git history,
branches, refs, or worktrees. Do not merge, rebase, reset, push, publish,
package, deploy, install dependencies, use credentials, contact live services,
or invoke another model or subagent. Do not use the network.

The only authorized repository write is the reviewer's own final report:

- Claude writes docs/reviews/2026-09-07-workflow-package-marketplace-adversarial-code-review-claude.md
- Codex writes docs/reviews/2026-09-07-workflow-package-marketplace-adversarial-code-review-codex.md

If you cannot identify whether you are Claude or Codex, stop with
REVIEW_LANE_UNCLEAR rather than overwriting either report.

## Independence rule

Reach and freeze your complete independent findings and verdict before reading
any prior review or progress material. Do not read:

- .superpowers/sdd/2026-09-03-workflow-package-marketplace/
- the other model's report;
- any marketplace adversarial-review reconciliation or remediation report
  created after this prompt; or
- prior reviewer conclusions quoted in chat, commit messages, or handoff notes.

The approved specifications and implementation plan listed below are binding
inputs, not prior review conclusions, and must be read. Commit subjects and test
names are leads only. Treat every claim of safety, correctness, completion,
review cleanliness, and test coverage as unproved.

## Role and posture

You are a hostile principal-level reviewer with expertise in:

- Python transactional systems, SQLite durability, local Git, filesystem
  containment, symlink and replacement races, trust stores, redaction, and
  idempotent admission;
- React 19, TypeScript, TanStack Query, Nanostores, Electron main/renderer
  boundaries, IPC, cancellation, Strict Mode, and application-lifetime
  supervision;
- accessible package-management user interfaces, information architecture,
  responsive layouts, internationalization, RTL, keyboard operation, focus
  management, reduced motion, and honest destructive-action design; and
- test integrity, generated cross-language fixtures, temporary repository
  integration, process-tree ownership, offline builds, and release gates.

Try to falsify the Workflow Package Marketplace as shipped. Do not merely
confirm that named tests exist. Read final production files and relevant
unchanged callers, construct realistic interleavings, and inspect rendered
behavior where tooling permits.

A code finding needs a realistic trigger, a complete production path, and a
concrete wrong result. A UI or styling finding needs an exact state, viewport,
interaction or assistive-technology condition, and an observable user impact.
Do not report personal taste, speculative hardening, generic refactoring, or a
missing test without a demonstrated defect.

This is not destructive penetration testing. Use benign synthetic credentials,
canaries, repositories, packages, profiles, and operation records only.

## Immutable review scope

Repository:
/Users/coreyellis/Developer/personal/github.com/cmetech/hermes-agent

Preferred review checkout:
/Users/coreyellis/Developer/personal/github.com/cmetech/hermes-agent/.worktrees/workflow-package-marketplace

Feature-start commit:
c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d

Candidate production commit:
38c702203abbe213049eea4731750133508c0960

Candidate tree:
642e9cb29e1e1a7cdc717df3e849be621f4cdf5a

Review range:
c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..38c702203abbe213049eea4731750133508c0960

Expected range:

- 131 commits;
- 219 changed paths;
- 184,919 insertions;
- 774 deletions.

Development branch is base. Literal main is synchronization-only. At prompt
creation, base was a960d5e7c8158f2ab2c315ab0d406eb81c96e3f6, but base is
mutable and is context only. Do not merge or simulate a merge. Attribute
candidate defects against the immutable feature-start tree. Report possible
current-base integration conflicts separately as UNVERIFIED, not as candidate
defects, unless the candidate already violates its own binding contract.

Before reviewing, verify:

~~~bash
git status --short --branch
git status --porcelain --untracked-files=no
git branch --show-current
git rev-parse HEAD HEAD^{tree}
git merge-base c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d HEAD
git rev-list --count c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD
git diff --shortstat c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD
git diff --name-only c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD
git diff --check c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD
~~~

The tracked checkout must be clean and HEAD/tree/range must match exactly.
The untracked prompt and the two named review-report paths are permitted and
must not be treated as candidate changes. Any other tracked difference,
candidate mismatch, or unexpected branch mutation is SCOPE ERROR. Do not
repair the checkout yourself.

The large insertion count is dominated by generated contract fixtures. It is
neither proof of a defect nor permission to skip them. Use diffs as inventory,
then inspect complete final files and unchanged consumers.

## Binding sources — read completely and in order

1. AGENTS.md
2. apps/desktop/AGENTS.md
3. apps/desktop/DESIGN.md
4. docs/superpowers/specs/2026-09-03-workflow-package-marketplace-design.md
5. docs/superpowers/specs/2026-09-04-workflow-marketplace-lifecycle-recovery-amendment.md
6. docs/superpowers/specs/2026-09-05-workflow-marketplace-http-version-compatibility.md
7. docs/superpowers/specs/2026-09-05-workflow-marketplace-strict-wire-parity-amendment.md
8. docs/superpowers/specs/2026-09-05-workflow-marketplace-supervisor-identity-binding-amendment.md
9. docs/superpowers/plans/2026-09-04-workflow-marketplace-lifecycle-recovery.md

Verify these SHA-256 values before relying on the inputs:

| Artifact | SHA-256 |
| --- | --- |
| AGENTS.md | a19cb8c30fb0f9a73089b98c9fc20ce59759817b5564f0bb9e8110822799cfb4 |
| apps/desktop/AGENTS.md | 4300a2e1e636a71bcec9d4211010f749ab78719f8b9ee8544c74f1f000be8ced |
| apps/desktop/DESIGN.md | 390abf9aa4cf4418f542f91f782f48a1283621638c90bad5946c63382d10845c |
| Marketplace design | dd721b9f7b9370d8a5187d1b55c78658804c6706c5c9d3f2c2534ada411b7b6a |
| Lifecycle recovery amendment | 6d5f812ca87f0f65b86dc9a75171e3786c6b7575a5fca830181a58104466a203 |
| HTTP compatibility amendment | b14c9b28f1c092c63b61449e67b89f1445bccea739c920a28760ef7ef2f75a25 |
| Strict wire amendment | 07830657fe35928a9135d7aca932b06ff6d6c6f13232ab960ba38afddf851fb5 |
| Supervisor identity amendment | d85aae7d6e11710ef8e65a338a7f34afea8ba97ad190003115acdc6f1ca4857c |
| Lifecycle recovery plan | cfbf33e209721716330abee0af5c9053753b4f37dfe864af92f54873d57df712 |

Where the documents conflict, identify the exact conflict. Later approved
amendments override the original design only in their stated scope. Do not
invent authority from comments or tests.

## Production surfaces to inspect

Use the full changed-path inventory, not only this list.

Backend package and trust:

- plugins/workflow/marketplace/contract.py
- plugins/workflow/marketplace/package.py
- plugins/workflow/marketplace/discovery.py
- plugins/workflow/marketplace/catalog.py
- plugins/workflow/marketplace/git.py
- plugins/workflow/marketplace/source_store.py
- plugins/workflow/marketplace/provenance.py
- plugins/workflow/marketplace/trust_binding.py
- plugins/workflow/marketplace/transactions.py
- plugins/workflow/marketplace/service.py
- plugins/workflow/marketplace/cli.py

Backend lifecycle and HTTP:

- plugins/workflow/marketplace/lifecycle_models.py
- plugins/workflow/marketplace/lifecycle_state.py
- plugins/workflow/marketplace/operations.py
- plugins/workflow/marketplace/admissions.py
- plugins/workflow/marketplace/lifecycle_api.py
- plugins/workflow/marketplace/api.py
- plugins/workflow/dashboard/plugin_api.py

Desktop contract, transport, and authority:

- apps/desktop/src/api/workflow-marketplace.ts
- apps/desktop/src/api/workflow-marketplace-lifecycle.ts
- apps/desktop/src/lib/workflow-marketplace-codec.ts
- apps/desktop/src/lib/workflow-marketplace-lifecycle-codec.ts
- apps/desktop/src/lib/workflow-marketplace-connection-binding.ts
- apps/desktop/src/lib/workflow-marketplace-supervision.ts
- apps/desktop/src/lib/workflow-marketplace-reconciliation.ts
- apps/desktop/src/store/workflow-marketplace-supervisor.ts
- apps/desktop/electron/lifecycle-api-transport.ts
- apps/desktop/electron/connection-generation.ts
- apps/desktop/electron/connection-generation-routing.ts
- apps/desktop/electron/native-session-generation.ts
- apps/desktop/electron/connection-registry-publication.ts
- apps/desktop/electron/connection-generation-event.ts
- apps/desktop/electron/main.ts
- apps/desktop/electron/preload.ts

Desktop user experience:

- apps/desktop/src/app/workflows/index.tsx
- apps/desktop/src/app/workflows/catalog.tsx
- apps/desktop/src/app/workflows/marketplace/index.tsx
- apps/desktop/src/app/workflows/marketplace/package-list.tsx
- apps/desktop/src/app/workflows/marketplace/package-detail.tsx
- apps/desktop/src/app/workflows/marketplace/installed-packages.tsx
- apps/desktop/src/app/workflows/marketplace/source-dialog.tsx
- apps/desktop/src/app/workflows/marketplace/install-review-dialog.tsx
- apps/desktop/src/app/workflows/marketplace/remove-review-dialog.tsx
- apps/desktop/src/app/workflows/marketplace/trust-review-dialog.tsx
- apps/desktop/src/app/workflows/marketplace/review-sections.tsx
- apps/desktop/src/app/workflows/marketplace/use-marketplace-operation.ts
- apps/desktop/src/app/workflows/marketplace/use-package-lifecycle.tsx
- apps/desktop/src/app/workflows/marketplace/use-package-trust-review.tsx
- apps/desktop/src/app/workflows/marketplace/package-lifecycle-presentation.ts
- apps/desktop/src/app/workflows/marketplace/controlled-copy.ts
- apps/desktop/src/app/workflows/marketplace/supervisor-provider.tsx
- apps/desktop/src/i18n/types.ts and all five locale catalogs

Release and process boundaries:

- scripts/generate_workflow_marketplace_lifecycle_fixtures.py
- scripts/generate_workflow_marketplace_lifecycle_ts.py
- scripts/test_workflow_merge_gate.sh
- scripts/workflow_gate_build.py
- scripts/workflow_gate_clis.py
- tools/managed_process.py
- tools/process_registry.py
- apps/desktop/e2e/workflow-marketplace-layout.spec.ts
- apps/desktop/e2e/workflow-marketplace-lifecycle.spec.ts

Inspect unchanged callers of every changed generic helper. In particular,
enumerate all consumers of Git utilities, process ownership, connection
generation, API transport, query keys, shared dialogs, Workflow catalog
actions, and locale types.

## Delivered behavior to falsify

The candidate claims an offline-capable package marketplace that discovers
credential-free catalogs from configured Git or local sources; validates,
hashes, installs, updates, removes, and separately trusts package content;
publishes strict public projections; and exposes lifecycle work through
profile- and actor-bound idempotent background operations.

The Desktop claims strict Python-to-TypeScript contract parity, native
connection-generation and principal binding, navigation-surviving operation
supervision, exact request/operation/package correlation, outcome-aware copy,
mutation barriers over retained caches, full-package trust reconciliation,
explicit legacy read compatibility, responsive accessible dialogs, and
source-clean offline release gates.

Try to prove any of those claims false.

## Locked invariants

Return PASS, FAIL, or UNPROVEN for every invariant. A matching test name is not
proof.

1. Package manifests and catalogs are strict, bounded, versioned, canonical,
   and reject duplicate keys, type confusion, invalid Unicode, unsafe names,
   unsupported fields, and noncanonical representations before side effects.
2. Package identity is exact source plus package ID. Case folding, Unicode,
   source aliases, profiles, and connections cannot collide or cross-read.
3. Git credentials, URL userinfo, tokens, local paths, repository secrets,
   confirmation tokens, private targets, and unrestricted diagnostics never
   enter public projections, logs, errors, operation subjects, query keys,
   fixtures, or persisted UI state.
4. Source refresh and discovery are bounded, atomic, navigation-safe, scoped,
   and preserve a last-known-good cache without making stale data falsely
   authoritative.
5. Validation binds reviewed bytes, digests, repository identity, commit,
   package identity, declared files, executable content, and trust state.
   Replacement, symlink, hard-link, path traversal, submodule, partial clone,
   case, and time-of-check/time-of-use attacks fail closed.
6. Install, update, and removal are transactional. Every filesystem and SQLite
   cut yields committed truth, known unchanged truth, or explicit uncertain
   recovery; no partial candidate is mislabeled as rolled back.
7. Trust is separate from installation. A grant is package-byte-bound, cannot
   authorize changed content, and single-workflow grant accepts the complete
   known package trust map while requiring the selected workflow to be present
   and trusted.
8. Public lifecycle subjects are credential-free and discriminated by kind.
   Pending and running operations expose sufficient safe identity for exact
   recovery without leaking the generic private registry target.
9. Client admission IDs are bounded, actor/profile scoped, and content-bound.
   Exact replay locates the original operation; conflicting reuse fails; a lost
   POST response never causes blind duplicate mutation.
10. Public operation list/get/cancel and terminal results correlate exact
    operation ID, request ID, profile, kind, phase, subject, and result shape.
    Wrong-operation or wrong-package responses cannot replace a watch.
11. The backend owns admission, phase, result, installed version, trust state,
    rollback facts, and recovery truth. Desktop never derives those facts from
    timestamps, latest-operation guesses, card snapshots, or operation kind.
12. Desktop preparation tokens remain ephemeral: never URL, log, storage,
    operation record, query key, public event, or cross-profile memory.
13. The application-level supervisor outlives Marketplace views and dialogs,
    resumes exact operations after navigation, stops renderer work on
    disconnect, preserves backend work, and reconciles only the originating
    connection/profile.
14. Capability, registry epoch, principal binding, and native connection
    generation are captured and revalidated across every asynchronous seam.
    Stale or mismatched work cannot publish, authorize, cancel, or expose a
    newer connection's result.
15. Generation exhaustion fails closed for the whole main-process lifetime.
    No rollover, renderer reload, or queued IPC alias can reuse authority.
16. Every recoverable or terminal path releases admission/action guards,
    schedulers, abort listeners, timers, leases, and view locks without losing
    unresolved mutation barriers.
17. Outcome states distinguish committed, known unchanged, cancelled before
    commit, recovery required, and outcome unknown. Rollback failed, recovery
    ambiguous, lost response after possible admission, invalid terminal data,
    and eviction never produce definitive rollback or installed-version copy.
18. Terminal success is a mutation barrier over stale cache. Install, update,
    remove, and trust cannot leave a pre-mutation action or version actionable
    after failed background refetch.
19. Ambiguous outcomes disable contradictory package actions, reconcile all
    relevant exact-scope data, and tell the user state could not be confirmed.
    They never claim which version is installed without backend confirmation.
20. Legacy read compatibility is permitted only after explicit lifecycle
    unsupported truth, no prior V2 binding in this application lifetime, and a
    successful post-transition exact-scope catalog fetch. It never enables
    package mutations or retained stale data.
21. One Escape performs one action. Child handlers can prevent parent
    navigation; confirmation admission cannot be bypassed; focus returns
    predictably after success, cancellation, failure, navigation, and package
    disappearance.
22. All package and trust actions are capability-gated, keyboard operable,
    screen-reader truthful, localized in all five catalogs, and never announce
    success before terminal backend truth.
23. Narrow layouts, 200% zoom, RTL, reduced motion, long translations, loading,
    empty, stale, offline, unsupported, busy, recovery, and unknown states
    remain readable and actionable without clipping or page-level overflow.
24. Release tooling is offline and source-clean. It executes only proven local
    Node/TypeScript/Vitest/tsx/Playwright identities, owns and reaps all build
    descendants, suppresses receipts on failure, and never falls back to npx,
    package installation, global caches, or network.
25. Temporary Git/package transaction workspaces have exact ownership,
    containment, cleanup, and recovery provenance. Cleanup cannot delete a
    foreign/replaced path or run while a mutator remains alive.
26. Existing V1 catalog/run compatibility remains functional where approved,
    while V1 mutation starts stay retired and V2 lifecycle authority cannot be
    bypassed.
27. Installed-distribution behavior uses packaged files only, not source-tree
    imports, implicit developer dependencies, mutable current directories, or
    repository-local secrets.
28. The feature does not regress ordinary Workflow browsing/running, other
    profiles/connections, non-Marketplace API transport, Git consumers,
    process-registry callers, or application shutdown.

## Attack campaign A — package, source, Git, trust, and transaction safety

- Trace remote and local source creation through canonicalization, persistence,
  refresh, retained cache, discovery, selection, review, confirmation,
  transaction, rollback/recovery, trust, installed state, and public output.
- Attack URL userinfo, percent encoding, fragments, scp-like syntax, IPv6,
  control characters, Unicode confusables, case aliases, dot segments, absolute
  paths, symlinks, hard links, repository replacement, malicious refs, moving
  branches, submodules, oversized trees, duplicate manifests, digest aliases,
  missing files, extra files, and executable-content changes.
- Use two profiles, two sources, and colliding package/workflow IDs. Prove no
  cache, trust, installed state, review, token, or operation crosses scope.
- Force failure at every mutation cut. Reopen real temporary state and inspect
  filesystem plus SQLite truth. Verify all rollback/recovery copy matches what
  actually remains installed.

## Attack campaign B — lifecycle admission, public schema, and recovery

- Trace prepare, review, confirmation, admission-ID reservation, operation
  publication, list/get/cancel, polling, terminal fold, eviction, replay,
  recovery inspection, and cache reconciliation.
- Attack duplicate and conflicting request IDs; lost POST responses; response
  after cancellation; wrong operation ID; wrong kind/phase/result; wrong
  subject; wrong profile; stale review; single-workflow trust response with
  other package workflows; duplicated/unknown/missing trust entries; invalid
  versions; malformed public JSON; and result mutation after publication.
- Enumerate every durable cut before and after the external mutation. Prove the
  next request can locate truth without a latest/timestamp guess or duplicate
  side effect.
- Compare every backend schema/fixture to the strict Desktop decoder. Generate
  values from Python where practical; do not rely only on hand-shaped mocks.

## Attack campaign C — Electron transport and application supervisor

- Trace connection descriptor resolution, native generation allocation,
  principal binding, registry epoch, IPC routing, raw header cardinality,
  URL normalization, redirects, leases, capabilities, supervisor binding,
  operation records, polling, cancellation, visibility, disconnect, profile
  switch, reconnect, exhaustion, disposal, and cache/query events.
- Construct deterministic barriers at every await. Attack A/B/A generation and
  route transitions, shared-primary to dedicated profile changes, deferred old
  responses, resolver rejection, caller cancellation, late list/get/cancel,
  status loss, operation eviction, stale query success, and Strict Mode
  mount/unmount.
- Prove no token or private binding leaks into renderer persistence or public
  events. Prove a stale body/error is masked and cannot adopt a replacement
  lease.
- Verify explicit unsupported legacy-read mode is revoked by fetch start,
  failed refetch, manual cache insertion, reprobe, disconnect, route/profile
  change, prior V2 authority, and stale in-flight success.

## Attack campaign D — user-visible lifecycle truth

Exercise install, update, remove, trust-all, trust-one, cancellation,
known-unchanged failures, rollback completed, rollback failed, recovery
ambiguous, lost admission response, invalid terminal response, terminal
eviction, disconnect, package disappearance, and failed cache refetch.

For every state, answer:

- Which fact is backend authoritative?
- Which action is enabled, disabled, or replaced by recovery?
- What exact version/trust statement is displayed?
- Can the user close, navigate, change tabs, or switch profile safely?
- What regains actionability?
- Does returning later reconcile the exact operation?
- Does any copy claim more certainty than the backend proved?

Use authoritative update/removal review versions, not an older card snapshot.
Trace every focus-return target and every Escape layer.

## Attack campaign E — visual design, styling, accessibility, and adversarial UX

This campaign is mandatory and equal in importance to code correctness.

Adopt this operator persona while exercising the UI:

> A busy operations engineer using a small laptop at 200% zoom, often
> keyboard-only, responsible for several profiles, cautious about installing
> untrusted automation, and unwilling to interpret backend jargon or guess
> whether a destructive action completed.

Test the actual rendered interface when local browser tooling permits. Use the
existing isolated build and synthetic backend fixtures; never live services.
Save evidence screenshots only in a temporary directory. If browser or native
platform tooling is unavailable, mark the corresponding runtime claim
UNPROVEN and perform source-level analysis without pretending it rendered.

Required presentation matrix:

- widths: 320 CSS px, 768 px, and 1440 px;
- zoom: 100% and 200%;
- direction: English LTR and Arabic RTL;
- motion: normal and prefers-reduced-motion;
- appearance: every supported light/dark/system theme reachable in the app;
- input: pointer, keyboard-only, and screen-reader semantics from the
  accessibility tree where available;
- content: empty, one item, maximum bounded list, long package/source/workflow
  names, long translated labels, multiline diagnostics, and missing package;
- state: initial loading, background refresh, retained stale data, total error,
  offline/disconnected, explicit unsupported, busy, confirmation admission,
  running, cancellation, committed success, known unchanged, recovery
  required, and outcome unknown.

Inspect:

1. Information hierarchy: page title, source controls, list/detail navigation,
   installed state, version, provenance, trust, warnings, and primary action
   are distinguishable at a glance.
2. Action hierarchy: install/update/trust/remove/recovery/cancel buttons use
   appropriate prominence and destructive styling. Disabled controls remain
   legible and explain why they are unavailable.
3. Dialog composition: headings, review sections, diffs, file lists, trust
   state, warnings, progress, error/recovery copy, button order, scroll regions,
   sticky areas, and close affordances do not compete or disappear.
4. Responsive behavior: no clipped text, hidden action, overlapping footer,
   off-screen dialog, accidental page-level horizontal scroll, unreachable
   content, or nested-scroll trap.
5. RTL and localization: logical spacing/order, icon direction, alignment,
   wrapping, interpolation, punctuation, and focus order work in Arabic and
   long CJK content. No English fallback or raw enum/error code appears.
6. Accessibility: correct landmarks/headings/dialog names/status or alert
   announcements, focus visibility, target size, contrast, disabled semantics,
   accessible names, tab order, Escape ownership, focus restoration, and no
   duplicate or premature live-region announcement.
7. Motion and feedback: progress never implies completion, reduced motion is
   respected, refresh does not cause disruptive layout shift, and navigation
   does not steal focus.
8. Cognitive load and terminology: source, package, workflow, installation,
   trust, review, recovery, and unknown outcome are explained in user language.
   The user never has to interpret transaction codes or infer whether it is
   safe to retry.
9. State continuity: close/reopen, list/detail navigation, tab switch, profile
   switch, disconnect/reconnect, and package disappearance do not strand the
   user or leave contradictory controls.
10. Visual consistency: use existing Desktop tokens and primitives; no raw
    colors, arbitrary spacing, inconsistent radii, misleading iconography,
    weak focus rings, or one-off control styles that visibly diverge from the
    surrounding Workflows application.

For each UI complaint, step out of the hostile persona and apply a pragmatism
filter:

- REAL DEFECT: a competent busy user or assistive-technology user would be
  harmed; eligible as a finding.
- VALID LOW-PRIORITY ISSUE: reproducible but localized; eligible as Minor.
- FEATURE REQUEST: potentially useful but not required by the approved design;
  report separately, never as a defect.
- PERSONA NOISE OR TASTE: omit.

Every visual finding must include viewport, zoom, locale/direction, theme,
state, interaction, screenshot path when available, applicable design rule,
and smallest correction. A CSS class preference without rendered consequence
is not a finding.

## Attack campaign F — release, process, and offline boundaries

- Inspect the merge gate from dependency-root selection through local CLI
  identity validation, generators, Python suites, TypeScript, Vitest, tsx,
  isolated build, Playwright, process cleanup, and exact-SHA receipt.
- Replace local package manifests, CLIs, intermediate directories, Node path,
  Playwright identity, lock files, and dependency roots with missing, malformed,
  linked, non-regular, wrong-version, wrong-name, and escaping fixtures. Prove
  failure occurs before work and receipt. Never execute a network fallback.
- Race immediate wrapper exit with a long-lived descendant, signal delivery,
  scope availability versus systemd-run resolution, post-spawn setup failure,
  cleanup failure, and late child mutation. Prove exact ownership and
  quiescence before temporary checkout removal.
- Audit all changed generic process and Git callers for regression. Distinguish
  candidate changes from byte-identical baseline debt.

## Attack campaign G — compatibility, tests, generated parity, and documentation

- Compare V1 and V2 routes, HTTP envelopes, backend dataclasses/models,
  generated fixtures, TypeScript unions/codecs, Electron response collection,
  and renderer consumers. Reject structural subtype acceptance where exact
  shape is required.
- Verify all five locales contain meaningful controlled lifecycle labels, not
  copied English placeholders or translated protocol values.
- Build or inspect an installed distribution from a clean temporary home. Prove
  it does not import the source checkout or depend on undeclared tools.
- Audit load-bearing mocks. Identify where hand-shaped responses could hide a
  backend/Desktop mismatch, but report a test issue only with a demonstrated
  production defect.
- Compare documentation to real commands, capability behavior, trust model,
  offline limits, unknown outcomes, recovery guidance, and platform evidence.
- Attribute full-repository checker, formatter, lint, native-platform, and base
  integration limitations precisely. Pre-existing debt is not a candidate
  finding unless this feature worsens it or makes a false release claim.

## Required verification

Python tests must run only through scripts/run_tests.sh with automatic
per-file retries disabled. Never invoke pytest directly. Do not run or collect
these two explicitly excluded suites:

- tests/plugins/workflow/test_phase3_bash_lexer_security.py
- tests/hermes_cli/test_persistent_session_recovery.py

Do not use npx, npm install, pnpm install, yarn install, package download,
global/cached fallback, live credentials, or network. Use only already proven
repository-local tools.

At minimum run:

~~~bash
env -u WORKFLOW_MERGE_GATE_FAST HERMES_TEST_FILE_RETRIES=0 \
  scripts/test_workflow_merge_gate.sh --phase base
~~~

This gate is expected to verify generators, the authorized Python inventory,
installed-distribution integration, canonical Workflow UI, marketplace
contract/UI/Electron/structured-channel tests, type checking, a source-clean
isolated Desktop build, and three real browser scenarios. Record the exact
TESTED_BASE_SHA receipt. A green gate is evidence, not proof that no adversarial
defect exists.

Run additional focused deterministic tests and disposable probes necessary to
prove or refute candidate findings. Record exact commands, exit codes, skips,
warnings, retries, durations where material, and unavailable platforms.

Do not mutate tracked files to create tests. Temporary probes may import the
candidate from an isolated temp directory. Clean up every process and temp
resource you create, and verify no descendant remains.

## Finding severity

- CRITICAL: cross-profile/connection/actor authority breach; credential or
  confirmation-token disclosure; unauthorized trust or mutation; wrong package
  bytes installed; duplicate mutation after replay/lost response; durable
  corruption/data loss; unsafe deletion; false rollback that can cause a
  destructive follow-up; or realistic systemic unbounded resource exhaustion.
- IMPORTANT: violated locked invariant with realistic production impact;
  false lifecycle outcome/version/trust; stale actionable cache; stuck
  operation or guard; broken navigation recovery; unsafe transport race;
  installed/offline gate failure; unusable or inaccessible core UI flow;
  keyboard trap; required action hidden/clipped at a supported layout; or a
  design/accessibility defect likely to cause an unsafe operator decision.
- MINOR: reproducible localized correctness, accessibility, copy, visual
  hierarchy, responsive, localization, or maintainability defect with bounded
  impact. Minor is allowed for objective UI/design defects. Do not use it for
  personal taste, speculative defense, generic refactoring, or test style.

Verdict:

- BLOCK if any CRITICAL or IMPORTANT finding exists;
- PASS WITH MINOR FINDINGS if only MINOR findings exist;
- PASS only if no qualifying finding exists.

Do not inflate severity to force a block, and do not downgrade a demonstrated
authority, truthfulness, destructive-action, or accessibility failure because
tests are green.

## Finding proof standard

Every finding must include all ten elements:

1. stable ID and severity;
2. concise title;
3. exact immutable production file and line plus relevant unchanged caller;
4. violated specification section or locked invariant;
5. realistic trigger and step-by-step production path;
6. concrete wrong observable result and user/operator consequence;
7. code evidence plus bounded reproduction, rigorous interleaving, or rendered
   evidence;
8. exact feature-start comparison showing candidate causality;
9. why existing tests and gates miss it; and
10. smallest safe root-cause correction plus required regression test.

For a visual finding, element 3 may cite the responsible JSX/CSS/component,
and element 7 must include the exact presentation matrix coordinates and
screenshot path when browser evidence exists.

If any element is missing, place the concern under Unresolved questions or
Unverified observations, not in the findings table. Do not stop after the
first defect. Be specific or be silent.

## Required report

Write only your lane-specific report. It must be self-contained and contain:

1. reviewer model/version, lane, date, host OS/architecture, and tooling;
2. immutable scope verification, input hashes, branch/status, and tracked
   cleanliness;
3. verdict: BLOCK, PASS WITH MINOR FINDINGS, or PASS;
4. severity-sorted findings table;
5. complete ten-element proof for every finding;
6. all 28 locked invariants rated PASS, FAIL, or UNPROVEN with concise direct
   evidence;
7. backend package/source/Git/trust/transaction matrix;
8. lifecycle admission/outcome/recovery matrix;
9. Electron identity/supervisor/cache/navigation matrix;
10. UI state-to-copy/action/focus matrix;
11. visual design, styling, accessibility, responsive, localization, and
    adversarial-UX assessment, including screenshots or explicit runtime
    UNPROVEN markers;
12. release/offline/process ownership assessment;
13. test-integrity and unchanged-caller assessment;
14. top adversarial probes and their observable results;
15. exact verification command ledger;
16. native Windows, native Linux, current-base integration, and other
    unverified evidence;
17. candidate versus feature-start attribution for every failure or warning;
18. non-defect feature requests and unresolved questions, kept separate from
    findings; and
19. final tracked worktree status proving only the authorized report was
    written.

If no finding qualifies, say so explicitly and still provide every matrix,
verification ledger, limitation, and clean-status proof. Do not implement
corrections. Stop after writing the report.

End with exactly one line:

- BLOCK — CRITICAL OR IMPORTANT FINDINGS REQUIRE REMEDIATION.
- PASS WITH MINOR FINDINGS — NO CRITICAL OR IMPORTANT FINDINGS.
- PASS — NO QUALIFYING ADVERSARIAL FINDINGS.
