# Adversarial code-review prompt — Portable Workflow Orchestration S01–S14

Paste everything below the line into a fresh, capable model or coding agent
with read and shell access to this repository:

/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent

The reviewer must assess the complete Portable Workflow Orchestration delivery,
S01 through S14, as shipped in OTTO and LOOP24 v2.0.0. This is a review task,
not an implementation task. Do not modify source, rewrite history, create
releases, or disturb unrelated work. The only authorized repository write is
the final review document named in Required output.

---

## Role

You are a hostile principal-level reviewer of Python, TypeScript/React,
subprocess lifecycles, durable workflow engines, security boundaries, and
release engineering. Your job is to break this implementation, not to bless it.

Assume every completion claim is unproven until you trace the production path
and either reproduce the behavior or establish the invariant from code and
tests. Test filenames, mocks, green CI, and plan checklists are not proof.
Identify happy-path theater, tests that assert their own fixtures, races hidden
by single-process tests, cleanup that only works on cooperative children, and
documentation that promises behavior the runtime does not implement.

Praise is not useful. If an area is safe, state exactly what code path,
interleaving, boundary, and test you checked. Do not stop at the first defect.
Read every changed file; do not sample the diff.

## Repository and immutable review scope

Repository root:

/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent

The source repository uses a neutral base plus generated brand overlays:

| Meaning | Commit |
|---|---|
| Approved design/plan baseline; implementation starts after this commit | 46fa66af60073dfc71ea2223668a4512d4ea1b32 |
| Released and tested neutral base | 365e1605ba4864c35f64a9be8e77d97b09845e5f |
| Released OTTO v2.0.0 source | 15165df332ebe60fea3e0d21b13765421f9a2735 |
| Released LOOP24 v2.0.0 source | bfc378da533e9558c28d221f8cb030adef6c0f37 |

Primary review range:

46fa66af60073dfc71ea2223668a4512d4ea1b32..365e1605ba4864c35f64a9be8e77d97b09845e5f

At prompt creation this range contains 222 changed files, approximately 27,002
insertions and 1,713 deletions. Verify those numbers yourself. Review the final
state, not merely each commit in isolation: follow-up fixes may have changed
the contract introduced by an earlier slice.

Also inspect the final brand overlays:

    git diff --name-status 365e1605ba4864c35f64a9be8e77d97b09845e5f..15165df332ebe60fea3e0d21b13765421f9a2735
    git diff --name-status 365e1605ba4864c35f64a9be8e77d97b09845e5f..bfc378da533e9558c28d221f8cb030adef6c0f37

Generic workflow, plugin-agent, managed-process, RunStore, Kanban, API, and
Desktop behavior must not diverge between base and either brand. Generated
identity and explicitly brand-owned art may differ.

Preserve unrelated local work. Start with git status. Use read-only commands or
detached temporary worktrees for test execution. Do not clean, reset, checkout
over, stash, or delete untracked files in the shared checkout.

## Sources of truth — read completely before reviewing code

Read these files in full and treat them as the approved contract:

1. AGENTS.md
2. docs/design/portable-workflow-orchestration.md
3. docs/plans/2026-07-15-portable-workflow-orchestration-plan.md
4. docs/workflow-orchestration.md
5. docs/upstream-customizations/README.md
6. docs/upstream-customizations/workflow-orchestration.yaml
7. docs/upstream-customizations/merge-evidence.schema.json
8. docs/otto-desktop-release-install.md
9. ../.claude/skills/otto-upstream-merge/SKILL.md

The design and implementation plan are the design of record. Do not redesign
the product because you prefer another architecture. Report deviations,
missing behavior, unsafe implementation evidence, or contradictions.

Do not use, import, compare against, or recommend the legacy Pi/OTTO workflow
runtime. Archon supplies only the portable YAML compatibility shape. The
runtime under review must be Hermes-native and neutrally located under the
workflow plugin and skills.

## Non-negotiable architecture and safety invariants

A violation of any item in this section is at least HIGH severity.

1. Capability lives at the edges: workflow plugin plus skills. There is no new
   permanent model-facing core tool.
2. Hermes prompt caching and message-role alternation remain intact. The
   implementation must not mutate historical context, rebuild the system
   prompt mid-conversation, swap global toolsets, or inject synthetic user
   messages inside the agent loop.
3. Every AI node executes in an isolated, bounded worker process. Concurrent
   nodes cannot leak registry scope, tools, hooks, environment, workdir,
   sessions, credentials, or provider state into each other or the parent.
4. Worker and descendant cleanup is identity guarded, cross-platform,
   deadline-bound, escalating, idempotent, and always observed with wait/reap.
   Cancellation, timeout, shutdown, coordinator EOF, PID reuse, and partial
   spawn must not leave owned children or zombies.
5. No workflow silently repeats an outward action with an unknown outcome,
   guesses success after a crash, or lets late completion overwrite a committed
   cancel/newer attempt.
6. Admission, claims, approvals, retries, cancellation, resume, reconciliation,
   and terminal transitions are durable and compare-and-set safe under
   duplicate delivery and multi-process races.
7. Package trust is profile- and digest-bound. Immutable inputs are snapshotted
   at admission. Executable-resource changes revoke trust. Untrusted local
   execution fails closed unless a configured backend advertises the complete
   isolation contract.
8. Prompts, reasoning, credentials, secret and sudo values, raw unrestricted
   tool arguments, and unsafe paths never enter durable operational state,
   artifacts, logs, API responses, Desktop projections, or plugin-visible IPC.
9. Workflow RunStore and Hermes Kanban remain independent lifecycle
   authorities. They share presentation primitives only. A workflow node is
   not a Kanban task and no card move advances a workflow.
10. Desktop endpoints are bounded authorization/validation/projection adapters
    over public stores. They do not become schedulers, provider clients, or a
    second persistence authority. Cursor scope, stale-write rejection, profile
    isolation, and reconnect behavior must hold.
11. Default showcase operation needs no workflow-node model, credentials,
    network, external integration, elevation, real-laptop inventory, or
    destructive fault injection. Every showcase claim comes from RunStore
    events, interactions, cleanup evidence, and verified artifact bytes.
12. Optional AI and scheduling are explicit opt-ins. Scheduling reuses existing
    repeat=1 cron plumbing and cannot collide with or delete user schedules.
13. Wheel and sdist contain every showcase catalog, digest, YAML, sidecar,
    command, script, fixture, local MCP resource, and the workflow-showcase
    skill at the expected relative location.
14. Upstream-owned changes stay within the approved touch budget, remain
    generic and separately replaceable, and are completely represented in the
    customization ledger. No ledger-owned merge may be resolved with blanket
    whole-file ours/theirs.
15. OTTO and LOOP24 are built from exact gated brand commits through their
    existing release-only repositories. The Hermes source repository is not a
    branded product-release repository.

## Actual S01–S14 commit map

Use this table to relate the implementation history to the plan. Confirm every
hash and subject with git. A repeated subject is a later hardening commit, not
a typo.

| Slice | Planned concern | Actual implementation commits |
|---|---|---|
| S01 | Public plugin agent runner, managed process tree, customization ledger | 2a8aa3b70 refactor(tools): extract managed process tree; 90dd43744 feat(plugins): expose scoped host agent runner |
| S02 | Portable package discovery, models, validation, topology, trust, CLI | 51bf7dc17 feat(workflow): discover and validate Archon packages |
| S03 | Durable admission, RunStore, Bash DAG tracer | 1dfd4a143 feat(workflow): add idempotent bounded run admission; 0ad7979f4 fix(tools): clean exited process descendants; 1de0aa9e3 feat(workflow): execute durable bash DAG runs |
| S04 | Command and prompt AI nodes, fresh/shared sessions | c1a9a09fa feat(workflow): run Archon command and prompt nodes; 217ba97a2, 6d04c52c0, e6b46d06c, and 6b94fe546 harden the S01 runner/process contracts |
| S05 | Bounded parallel scheduling, trigger rules, retries, resume, crash recovery | 7eb081ac4 feat(workflow): add bounded parallel resume and retries; 01469943b fix(workflow): tolerate atomic capacity scans; ed5857adc fix(tools): verify managed process cleanup |
| S06 | Script, loop, cancellation, deterministic cleanup races | c02a92eff feat(workflow): support script loop and cancel nodes; 0cf7f48df docs(upstream): record managed cleanup verification; 2ae1bf4d1 fix(workflow): use cross-platform process probes; 27b218c28 fix(tools): verify managed process cleanup |
| S07 | Durable approval, capture, rejection, exact one-shot grants | 005eeafd7 fix(plugins): consume exact approval grants once; 225acb304 feat(workflow): add durable approval and rejection gates |
| S08 | Per-node tools, skills, hooks, MCP, provider and resource policy | ae535091b feat(workflow): enforce per-node agent resources |
| S09 | CLI/chat/gateway/Desktop-chat/cron activation and surface contracts | 8036a8d11 feat(workflow): activate runs from chat and cron |
| S10 | Native Workflow and Kanban operations with separate authorities | 2262b30ce workflow API; cc7df69aa Kanban mutation preconditions; 4f8cd4622 Kanban API hardening; b4fd68b33 shared activity board; 1550171f6 operations pages; 30d7fc260 navigation; 7cbb1efed operator-scope fixture isolation; 1dd557e4c ledger completion |
| S11 | Workflow builder skill and compatibility doctor | 23b7b0aaa feat(workflow): author and diagnose portable packages |
| S12 | Ericsson conversion, capability staging, neutral brand delivery | 37c913aaa feat(ericsson): ship portable workflow packages |
| S13 | Offline production showcase harness and workflow-showcase skill | c3a832dc6 feat(workflow): add offline guided showcase suite |
| S14 | Production quality, fault/security/performance gates, merge rehearsal | d30450f3a test(workflow): enforce production and merge gates; follow-ups e2a31d3e7, 32e9eed89, 6364000c3, fcd912f9e, 3aaa3bcf9, 97296af0d, and 9efca7e2b |

Non-functional release-history commits inside the primary range:

- d654f6a74 prepared v2.0.0-alpha.1 metadata during development.
- 27c9b59a0 prepared stable v2.0.0 metadata.
- 644d1f368 and 365e1605b documented and pinned the corrected paired branded
  release process.

Do not omit those files from final-tree review, but do not attribute a runtime
feature to them. The mistaken neutral GitHub release/tag was deleted and PyPI
2.0.0 was never published; review repository state, not deleted remote history.

## Required review method

### 1. Establish the diff and plan traceability

- Verify all immutable commits and ancestry.
- Enumerate every changed file and group it by slice, production subsystem,
  test-only support, packaging, documentation, and brand overlay.
- For each task in the implementation plan, compare its Files, Interfaces,
  steps, required tests, acceptance criteria, and definition-of-done claims to
  the final implementation.
- Produce an S01–S14 coverage matrix with status: proven, partial, missing, or
  contradicted. Cite production files and tests. A test filename alone is not
  evidence.
- Check whether later follow-up commits fixed the entire bug class or only the
  observed test case.

### 2. Attack the worker/process boundary

- Trace PluginAgentRunner from immutable request validation through frame
  encoding, spawn, child revalidation, AIAgent construction, progress/output
  buffering, cancellation, shutdown, and parent cleanup.
- Attack oversized/malformed/truncated/duplicated/out-of-order IPC frames,
  blocked stdout/stderr, child exit mid-frame, parent EOF, stuck descendants,
  PID reuse, identity-probe failure, TERM/KILL refusal, Windows taskkill
  behavior, and cleanup called repeatedly or concurrently.
- Prove output, progress, audit, and error streams are bounded before
  accumulation. Look for an apparently bounded final result backed by an
  unbounded intermediate list or pipe.
- Verify deadlines are mandatory before spawn and no None or zero value becomes
  an accidental infinite wait.
- Verify dangerous-tool, clarification, sudo, and secret callbacks fail closed
  and cannot bypass Hermes hardline, approval, or cron policy.

### 3. Attack scoped AI execution

- Run parallel nodes with disjoint tools, skills, hooks, MCP servers, providers,
  workdirs, environment, and session modes. Look for process-global registry
  mutation, cache contamination, inherited secrets, stale plugin state, or
  parent state drift.
- Confirm denied tools remain absent through discovery, Tool Search, unwrap,
  lookup, dispatch, and any agent-owned schema not sourced from the registry.
- Test None versus empty allowlists, allow/deny overlap, unknown names, dynamic
  registration, MCP refresh, nested/incompatible scopes, exceptions, and
  cancellation during scope teardown.
- Verify fresh contexts cannot alter the parent conversation and shared context
  can resume only the intended workflow/node/session identity.

### 4. Attack durable state and scheduler races

- Trace admission identity, start digest, overlap policy, capacity accounting,
  claim/lease ownership, journal append, projection replace, retry scheduling,
  and recovery after every persistence boundary.
- Construct duplicate delivery, duplicate scheduler, queue/allow/forbid,
  completion-versus-cancel, retry-versus-cancel, approval-versus-cancel,
  reconciliation-versus-late-result, shutdown-versus-admission, cleanup-versus-
  reader, and stale-attempt-versus-new-lease interleavings.
- Verify one winner, monotonic sequence/state, no worker held while queued,
  backing off, or waiting for a user, and no capacity leakage after crash.
- Corrupt/truncate the projection, event journal, admission ledger, artifact
  metadata, and locks independently. Confirm recovery is deterministic,
  bounded, and never silently claims success.
- Exercise suspend/wake and wall-clock jumps. Confirm elapsed and deadline
  decisions use the intended monotonic/durable evidence.

### 5. Attack each node type and compatibility boundary

- Verify Bash, command, prompt, uv script, loop, approval, reject, cancel,
  condition, parallel join, structured output, hook, MCP, and persistent-session
  semantics against the compatibility table.
- Probe path traversal, symlinks, YAML alias/depth bombs, oversized documents,
  unsafe dependency tokens, shell injection, variable substitution ambiguity,
  loop nontermination, invalid conditions, retry classification, and malformed
  structured output.
- Unsupported Archon fields must be explicit diagnostics, never guessed,
  ignored, or accidentally accepted.
- Verify retries do not repeat outward actions without idempotency or explicit
  reconciliation.

### 6. Attack trust, secrets, artifacts, and quotas

- Change each executable resource independently and prove trust revocation.
  Attempt package/sidecar self-trust, digest replay across profile/source, path
  aliasing, symlink substitution, and bundled-showcase impersonation.
- Trace secret names and values through environment expansion, MCP/hook inputs,
  worker IPC, exceptions, logging, RunStore, APIs, reports, and artifacts.
- Attack artifact containment at write and read time, including symlink races,
  digest mismatch, oversized content, media type spoofing, and cleanup.
- Exceed every documented process, worker, output, artifact, event, run,
  profile-storage, rate, queue, paused, nonterminal, memory, CPU, descendant,
  and free-disk limit one at a time. Verify refusal happens before expensive or
  unsafe allocation and later work can proceed.

### 7. Attack approvals and interaction replay

- Race 20 or more approval/rejection/input decisions. Only one exact state
  version and action digest may win.
- Replay or tamper with grants, interaction IDs, comments, expected versions,
  and run/node identity. Confirm a grant is consumed exactly once and cannot
  authorize a different tool call or attempt.
- Restart before and after every decision persistence boundary. Confirm pending
  interaction state is durable, sanitized, and recoverable.

### 8. Attack activation and surface consistency

- Compare shell CLI, natural chat, slash command, gateway, TUI gateway, Desktop
  chat, cron, and native Desktop operations. They must resolve the same catalog
  and RunStore, use the same runtime, and expose consistent sanitized states.
- Every operational CLI command must honor JSON contracts without leaking
  prompt/command bodies, reasoning, credentials, or unrestricted arguments.
- Confirm plugin-disabled and unavailable-provider behavior fails clearly and
  non-destructively.
- Verify cron repeat=1 ownership, duplicate firing, restart recovery, delivery,
  and cleanup cannot touch unrelated schedules.

### 9. Attack Desktop Workflow and Kanban behavior

- Confirm REST authentication and profile/operator scope under local token,
  remote token, and remote OAuth paths. Unauthorized and not-found responses
  must not reveal existence.
- Attack cursors across profiles, runs, boards, sort orders, expiry, malformed
  encoding, and reconnect. Verify 409 stale-state and 410 cursor-reset behavior
  where contracted.
- Race late responses after profile/board changes. Ensure caches and nanostores
  cannot display or mutate the previous scope.
- Prove Kanban lifecycle preconditions run inside the same SQLite write
  transaction as mutation. Reclaim must capture an exact process identity,
  release the transaction before signaling, and reject late worker completion.
- Test keyboard navigation, focus retention, screen-reader labels, loading,
  empty, error, stale, laptop-width, hidden-window refresh suspension,
  virtualization, and large-board request/render bounds.
- Confirm Mermaid source is strict, bounded, injection-safe, rendered only on
  supported surfaces, and always has text/source fallback.

### 10. Attack showcase and installed packaging claims

- Build wheel and sdist, install each into isolated environments, and run
  showcase discovery/preflight/run/report/cleanup from installed assets rather
  than the source tree.
- Tamper with every catalog, digest, YAML, sidecar, command, script, MCP,
  fixture, and evidence input. Distribution trust and report verification must
  fail closed.
- Verify Laptop Diagnostic uses only sanitized fictional evidence and performs
  no host inventory. Confirm destructive, corruption, exhaustion, flood, and
  soak scenarios are unreachable from production catalog and skill.
- Verify resilience timeout remains a truthful failure with typed diagnostics
  and cleanup evidence. A caught timeout must not become a showcase success.
- Verify optional AI skips without weakening offline claims and scheduling
  requires explicit digest-bound opt-in.

### 11. Attack the production and upstream-merge gates

- Read the gate scripts as production code. Look for tests that pass because
  expected failures are swallowed, wrong worktrees are inspected, dependencies
  are borrowed incorrectly, refs are mutable, or generated changes are left
  uncommitted and therefore omitted from ancestry/diff checks.
- Verify the checker covers every upstream-owned feature file and owned symbol,
  but excludes unrelated release metadata for the correct reason.
- Exercise all overlap classes: none, same_file, owned_symbol, and
  possible_upstream_equivalent. Required preserve/adapt/remove decisions must
  be explicit and schema-validated.
- Confirm rehearsal is offline, uses temporary worktrees, never advances real
  refs, preserves evidence on meaningful failures, and validates exact tested
  base ancestry plus non-divergent generic runtime files for every discovered
  brand.
- Compare the external merge skill to repository gates. Find stale command,
  emitter-count, branch, path, or baseline assumptions.

### 12. Audit test quality itself

- For every high-risk contract, identify whether the test uses real imports,
  filesystem, SQLite, process trees, installed packages, API adapters, and
  concurrency, or only mocks the behavior it claims to prove.
- Inspect test sizes and assertions critically. A short test named fault,
  security, performance, soak, E2E, or production is not evidence that Task 14
  was implemented.
- Find sleeps, timing flakiness, platform skips, broad exception swallowing,
  weak substring assertions, tests that cannot fail, and assertions against
  implementation details rather than behavioral invariants.
- Name the highest-risk untested path for every slice.
- Distinguish a real defect from a test gap, but treat an unproven
  production-critical requirement as a release blocker where the plan requires
  proof.

## Required commands and evidence

Run commands from clean detached worktrees when checkout state matters. Follow
AGENTS.md: use scripts/run_tests.sh rather than invoking pytest directly,
except when an existing repository gate intentionally owns its own test
invocation.

Start with:

    git status --short --branch
    git cat-file -e 46fa66af60073dfc71ea2223668a4512d4ea1b32^{commit}
    git cat-file -e 365e1605ba4864c35f64a9be8e77d97b09845e5f^{commit}
    git cat-file -e 15165df332ebe60fea3e0d21b13765421f9a2735^{commit}
    git cat-file -e bfc378da533e9558c28d221f8cb030adef6c0f37^{commit}
    git log --reverse --oneline 46fa66af60073dfc71ea2223668a4512d4ea1b32..365e1605ba4864c35f64a9be8e77d97b09845e5f
    git diff --check 46fa66af60073dfc71ea2223668a4512d4ea1b32..365e1605ba4864c35f64a9be8e77d97b09845e5f
    git diff --stat 46fa66af60073dfc71ea2223668a4512d4ea1b32..365e1605ba4864c35f64a9be8e77d97b09845e5f
    git diff --name-status 46fa66af60073dfc71ea2223668a4512d4ea1b32..365e1605ba4864c35f64a9be8e77d97b09845e5f

At minimum, run and report the following from a detached worktree at the exact
neutral-base commit, not from whichever branch happens to be checked out in the
shared workspace:

    python3 scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml
    scripts/test_workflow_merge_gate.sh --phase base
    scripts/run_tests.sh
    cd apps/desktop && npm test
    cd apps/desktop && npm run typecheck

Run each brand gate from a separate detached worktree at its exact released
commit:

    scripts/test_workflow_merge_gate.sh \
      --phase brand \
      --brand otto \
      --tested-base-sha 365e1605ba4864c35f64a9be8e77d97b09845e5f

    scripts/test_workflow_merge_gate.sh \
      --phase brand \
      --brand loop24 \
      --tested-base-sha 365e1605ba4864c35f64a9be8e77d97b09845e5f

Run the full temporary-worktree rehearsal when the local refs and dependencies
are available:

    scripts/test_workflow_upstream_merge.sh \
      --upstream-ref main \
      --base-ref base \
      --brand-ref otto \
      --brand-ref loop24 \
      --report-dir /tmp/workflow-merge-review-evidence

Also build and inspect the wheel, sdist, OTTO Desktop, and LOOP24 Desktop using
the repository's existing documented build paths. Do not invent a new
installer or publish anything. If a platform, dependency, credential, or tool
is unavailable, record the exact unavailable gate and compensate with code
inspection; never report an unrun gate as passed.

For concurrency, process, cleanup, security, packaging, and API claims, add
small temporary reproduction tests outside tracked source when necessary.
Show exact commands and outputs for every finding. Remove only your own
temporary worktrees/files.

## Severity

- CRITICAL: credential disclosure, arbitrary unauthorized execution, durable
  state corruption/data loss, cross-profile authority breach, systemic
  unbounded resource exhaustion, or cleanup failure capable of orphaning a
  fleet of processes.
- HIGH: violation of a load-bearing invariant, deterministic race causing
  duplicate/incorrect execution, bypass of trust/approval/limits, shipped
  runtime or packaging failure, false success, or a required production claim
  with no credible implementation/proof.
- MEDIUM: bounded correctness, recovery, operability, accessibility,
  compatibility, or performance defect with a realistic production trigger.
- LOW: narrow maintainability, diagnostics, documentation, or test-quality
  problem that does not presently violate a production invariant.

Do not inflate severity without a concrete failure path. Conversely, do not
downgrade a race merely because reproducing its interleaving is difficult.

## Required output

Write the review to:

docs/reviews/2026-07-17-portable-workflow-orchestration-adversarial-review.md

The review must contain:

1. Scope and immutable refs actually reviewed.
2. A release verdict: SHIP, CONDITIONAL, or DO NOT SHIP.
3. Findings table sorted by severity with:
   - stable finding ID;
   - slice(s);
   - file and current line;
   - violated design/plan invariant;
   - concrete failure scenario or interleaving;
   - observed or reasoned evidence;
   - minimal safe fix;
   - missing regression test.
4. S01–S14 coverage matrix: proven, partial, missing, or contradicted, with
   production and test evidence.
5. Concrete reproductions for at least the top five findings. Include exact
   inputs, process/thread ordering, commands, and wrong result.
6. What was verified safe and why. Cover every review dimension; do not use
   generic statements.
7. Verification evidence: every command, pass/fail/skip result, platform, and
   whether the result came from real execution, simulation, or inspection.
8. Required remediation before release/merge, ordered by risk and dependency.
9. Residual risks and unverified platform paths, especially native Windows
   process behavior and installed-distribution behavior.

If you find no defects in a dimension, explain the exact adversarial cases you
tried and why the implementation resisted them. Do not accept comments,
docstrings, test names, catalog claims, or green status as substitutes for
runtime evidence. Be specific or be silent.
