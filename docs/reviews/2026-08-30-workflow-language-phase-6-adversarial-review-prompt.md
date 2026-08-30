# Adversarial code-review prompt — Workflow Language Phase 6 durable loop groups

Paste this prompt into a fresh, capable coding session that did not implement
Phase 6. The reviewer gets read and shell access to one detached checkout at
the exact production candidate. This is a correctness and authority review,
not implementation work.

Do not modify production code, tests, generated files, Git history, branches,
refs, or worktrees. Do not merge, push, publish, deploy, use credentials, or
contact Jira, GitLab, providers, or any other live service. Disposable probes
must use synthetic data, isolated temporary paths, and no network. The only
authorized persistent write is the model-specific review report named by the
launcher.

This is an ordinary **functional-correctness** review of code the user owns.
It is not cybersecurity work: do not perform threat modeling, vulnerability
research, exploit development, penetration testing, credential testing,
security scanning, or live-service probing. References to authority,
containment, privacy, and fail-closed behavior mean normal product invariants
to inspect with benign synthetic fixtures only.

## Role

You are a hostile principal reviewer of Python/TypeScript durable workflow
engines, language normalization, SQLite and append-only recovery, filesystem
authority, concurrency, approval/effect policy, public projections, and
installed packaging.

Try to falsify Phase 6, not bless it. Treat plans, comments, commit messages,
green test totals, prior reports, and test names as claims rather than proof.
Trace the final production tree and its unchanged callers. A finding requires
a realistic trigger, a complete production path, and a concrete wrong result.
Do not report style, preferred refactors, speculative hardening, or test gaps
without a demonstrated production defect.

## Immutable scope

```text
Repository: /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
Development base: base (literal main is synchronization-only)
Merge base: 1001a6705563a2f2a001b4ad8a608a2d12a6ad33
Production candidate: d850707a25d0eb161d3bedd2db935d01f3573255
Review range: 1001a6705563a2f2a001b4ad8a608a2d12a6ad33..d850707a25d0eb161d3bedd2db935d01f3573255
Commits: 29
Changed paths: 113
Diff summary: 20,613 insertions, 1,351 deletions
```

Review immutable commits, never a mutable branch name. Verify ancestry, exact
HEAD, clean status, range counts, and `git diff --check` before reviewing.
Stop with a scope error if the detached checkout differs from the candidate.

An immutable combined package is available in the feature worktree at:

```text
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-loop-groups-phase-6/.superpowers/sdd/2026-08-29-workflow-language-phase-6-durable-loop-groups/review-1001a67055..d850707a25.diff
```

Use it as an inventory, not as a substitute for reading final production
files and relevant unchanged callers. Do not read prior reviewer verdicts or
the SDD progress ledger before reaching your independent findings.

## Binding sources — read completely

1. `AGENTS.md`
2. `apps/desktop/AGENTS.md`
3. `docs/superpowers/specs/2026-08-29-workflow-language-phase-6-durable-loop-groups-design.md`
4. `docs/superpowers/plans/2026-08-29-workflow-language-phase-6-durable-loop-groups.md`
5. `docs/superpowers/specs/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience-design.md`
6. `docs/superpowers/specs/2026-08-05-workflow-language-phase-4-ordinary-loops-immutable-includes-design.md`
7. `docs/superpowers/specs/2026-08-06-workflow-language-phase-5-provider-portability-design.md`
8. `docs/design/portable-workflow-orchestration.md`
9. `website/docs/user-guide/features/workflow-yaml-reference.md`
10. `skills/software-development/workflow-builder/references/portable-schema.md`
11. `skills/software-development/workflow-builder/references/authoring-checklist.md`

The Phase 6 spec is binding. Earlier phase specs define inherited behavior
that Phase 6 must preserve. The plan is an execution guide, not authority to
override the spec.

## Delivered behavior to falsify

Phase 6 claims to add one-level bounded `loop_group` nodes under normalizer v6
while preserving snapshot format 2 and exact v1-v5 replay. A durable outer
controller advances sequential iterations; body children use the existing
fair scheduler and existing `worker_claims`; the waiting controller consumes
no worker. Body execution reuses existing executors, resource sealing,
provider authority, effects, interactions, recovery, evidence, and profile
routing.

It also claims authenticated current, previous-iteration, and outer reference
scopes; exact crash/restart continuation; bounded public parent-only progress;
profile-isolated APIs and Desktop state; deterministic Jira Defect Loop
migration as the sole legacy iterative consumer; exact approval for every
Jira/GitLab write; structured expected ticket outcomes; and terminal
reconciliation for ambiguous effects.

## Locked invariants

Produce PASS / FAIL / UNPROVEN for every invariant. A matching test name is
not proof.

1. Normalizer v6 is current only for Archon; legacy remains v2; v1-v5 replay
   exact recorded semantics; snapshot format remains 2.
2. Admission rejects nested groups, includes, runtime workflows, group retry,
   invalid scopes, invalid products, and unavailable resources before run
   creation or connector/provider work.
3. All group/body/iteration/attempt/publication identities are authenticated,
   bounded, canonical, and never accepted as caller/model filesystem paths or
   worker keys.
4. The controller is durable and workerless while waiting. Body claims consume
   the existing profile-global and per-run capacity, with no second scheduler,
   executor pool, or bypass around fairness.
5. Iterations never overlap. Source-order admission, dependency topology,
   primary-sink selection, `maxIterations`, and terminal/continue decisions
   are deterministic across restart and competing coordinators.
6. Current body output, `$LOOP_PREV`, and outer output are distinct immutable
   scopes on every admitted surface: prompts, `when`, Bash/script, ordinary
   loop prompts and `until_bash`, approvals, and rejection text.
7. Iteration one has deterministic previous-output absence semantics. Later
   iterations and reopened runs resolve only authenticated winning
   publications from the immediately previous iteration.
8. Child executor context keeps exact existing top-level paths and semantics
   while isolating nested attempts, publications, processes, artifacts,
   interactions, resource authority, and evidence.
9. Claim, publication, event, predicate, cancellation, and reconciliation
   transitions have one generation-fenced winner. Crash boundaries cannot
   duplicate work, lose a required result, accept stale authority, or finish
   early.
10. `artifacts:false` permits unrelated unchanged prior publications but no
    artifact attributable to that node across initial execution, crash,
    retry, resume, or reconciliation may survive a successful attempt.
    Symlinks, hardlinks, non-regular entries, replacement, mutation, removal,
    and check/use races fail closed.
11. Structured AI output carrying an exact tool-call contract cannot be
    replaced by unconstrained repair. Manifest identities remain corroborated
    by immutable tool results; ordinary non-contract repair remains intact.
12. Every outward Jira/GitLab write consumes exact current approval and effect
    authority once. Fallback, repair, retry, restart, inline work, and sibling
    nodes cannot replay or widen it.
13. Expected Jira outcomes remain structured success. Unknown or ambiguous
    writes stop for reconciliation and are never blindly replayed or silently
    converted into expected results.
14. Jira Defect Loop reducers consume bounded authenticated predecessor
    publications, enforce schemas, retain actual issue/branch/commit/MR/write
    evidence, and publish required aggregate JSON/Markdown deterministically.
15. Public projections are bounded parent-only summaries with no prompts,
    outputs, paths, commands, tool data, feedback, credentials, or private
    authority. Hidden children cannot corrupt counts or leak through errors.
16. Profile B cannot list, inspect, mutate, acknowledge, cache, or receive late
    results/actions for Profile A. No cross-profile board or cache key exists.
17. Desktop consumes backend truth additively, remains non-authoritative, and
    settles late mutations against their origin profile.
18. Existing core tools, prompt prefix, message alternation, executors,
    provider routing, scheduler, tables, migrations, and public API versions
    remain unchanged except where the spec explicitly extends existing data.
19. Only Jira Defect Loop is migrated. The other seven assessed legacy flows
    remain explicit deferrals and do not accidentally gain v6 syntax.
20. Generated schema, installed distribution, website guidance, workflow
    builder references, customization ownership, and runtime agree on v6
    fields, bounds, codes, current version, and v1-v5 compatibility.

## Attack campaign 1 — versioning, admission, and sealed bounds

- Trace YAML and companion resources through profile selection, v6
  normalization, canonicalization, semantic keys, bounds, package digest,
  snapshot creation, installed loading, resume, and public version metadata.
- Compare new Archon, explicit recorded v1-v5, and legacy paths. Search sibling
  loaders, catalog verification, schedules, Desktop endpoints, and installed
  imports for a version bypass or live reinterpretation.
- Exercise exact boundary and one-over values for group/body counts,
  iterations, attempts, processes, output bytes, journal reserve, schema size,
  resources, and nested products. Prove structural rejection occurs before
  resolver, connector, provider, filesystem, or database side effects.
- Attack nested group/include/workflow/retry aliases, malformed nodes, hidden
  513th nodes, duplicate IDs, ambiguous primary sinks, cycles, and invalid
  cross-scope references.

## Attack campaign 2 — scheduler, claims, fairness, and controller lifecycle

- Trace controller initialization, readying, iteration creation, candidate
  selection, claim acquisition, child dispatch, result publication, predicate
  evaluation, iteration close, next iteration, and terminalization.
- Use deterministic barriers for two coordinators, cancellation, lease expiry,
  claim turnover, controller-generation change, stale wake, delayed event,
  and profile-global saturation.
- Prove a waiting controller consumes zero workers and all body work competes
  through existing fair cursors/claims. Search for direct executor invocation,
  nested pools, hidden capacity, recursive scheduling, or starvation.
- Prove different iterations cannot overlap even after crash, interaction,
  retry wait, process uncertainty, or a late child completion.

## Attack campaign 3 — crash recovery, publication, and filesystem authority

- Enumerate every durable cut between spawn intent, process identity,
  execution, result recording, publication, event append, predicate decision,
  cancellation, claim release, and controller advance.
- Reopen real `RunStore` state at each cut. Prove exactly one safe outcome and
  no provider/effect replay, duplicate publication, stale result adoption, or
  premature completion.
- Attack `artifacts:false` with unrelated prior group artifacts and with
  same-node residue from a crashed initial attempt. Cover retry, resume,
  changed/unchanged content, create/remove/replace, symlink, hardlink, FIFO,
  directory, rename, inode reuse, and check/use races for both Bash and Script.
- Verify no-follow traversal and exact authenticated roots on macOS/POSIX;
  mark native Windows-only paths UNPROVEN rather than inventing a pass.

## Attack campaign 4 — scoped references, conditions, and ordinary loops

- Enumerate every reference in authored order and reverse order. Combine
  current and `$LOOP_PREV` references to the same node/path, whole outputs,
  nested fields, missing fields, scalars, mappings, sequences, nulls, and
  transient/corrupt publications.
- Trace static admission and runtime resolution through prompts, `when`,
  Bash/script, approval text, ordinary loop prompt, and `until_bash`.
- Prove current/previous scope participates in immutable reference identity;
  no first-match cache or textual ordering collision may change meaning.
- Prove body-only v6 condition grammar does not widen top-level or v1-v5
  grammar, and reopened later iterations use authenticated previous results.

## Attack campaign 5 — tool authority, effects, and Jira/GitLab migration

- Trace manifest AI calls, tool-call contracts, structured schema validation,
  repair/fallback, exact ticket identities, deterministic reducers, approvals,
  outward-action sidecars, connector calls, effect records, ambiguity, and
  aggregate publication.
- Attempt schema-invalid but tool-correlated output, schema-valid identity
  substitution, multiple calls, missing calls, reordered tickets, duplicate
  IDs, partial writes, expected Jira statuses, unknown outcome, retry,
  cancellation, crash, and resume.
- Prove each Jira/GitLab mutation consumes exact current approval once and the
  terminal record preserves actual issue/branch/commit/review/MR/write
  identities and evidence.
- Run only synthetic connector fixtures. Never contact Jira or GitLab.

## Attack campaign 6 — public surfaces, privacy, and profile isolation

- Trace store/controller/child state through evidence, REST codecs, Desktop
  adapters, cards, drawer, notifications, caches, acknowledgements, and
  mutations.
- Use two profiles with colliding run IDs and delayed responses. Switch
  profiles between request and response, mutation and acknowledgement, and
  list/detail refresh. No cross-profile observation or action is acceptable.
- Fill hidden children and private values with unique canaries. Check payload
  bounds, truncation, localized headers, errors, logs, and serialization.
- Verify malformed/unknown additive fields do not turn Desktop into execution
  authority or break an older backend/client combination.

## Attack campaign 7 — compatibility, packaging, and test integrity

- Search all changed generic seams and every non-Phase-6 caller. Confirm no
  new table, migration, pool, model tool, prompt mutation, route/action/schema
  version, or cross-profile registry.
- Inspect wheel/sdist/capability/vendor paths without borrowing source-tree
  files. Confirm exact distributed digests and one migrated flow.
- Use mutation reasoning on load-bearing tests: would removal of the actual
  production guard fail? Identify mocks hiding filesystem, SQLite, process,
  provider, profile, or recovery composition.
- Distinguish candidate defects from byte-identical baseline failures.

## Required verification

Use `scripts/run_tests.sh` for Python. Record commands, exit codes, skips,
warnings, retries, and unavailable platforms. Do not silently substitute a
narrower command and claim equivalence.

```bash
git status --short --branch
git rev-parse HEAD
git merge-base base HEAD
git diff --check 1001a6705563a2f2a001b4ad8a608a2d12a6ad33..d850707a25d0eb161d3bedd2db935d01f3573255
git diff --name-status 1001a6705563a2f2a001b4ad8a608a2d12a6ad33..d850707a25d0eb161d3bedd2db935d01f3573255

scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_language.py \
  tests/plugins/workflow/test_phase6_admission.py \
  tests/plugins/workflow/test_phase6_execution_context.py \
  tests/plugins/workflow/test_phase6_store.py \
  tests/plugins/workflow/test_phase6_scheduler.py \
  tests/plugins/workflow/test_phase6_interactions_recovery.py \
  tests/plugins/workflow/test_phase6_public_projection.py \
  tests/plugins/workflow/test_phase6_jira_defect_loop.py -q

scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_language.py \
  tests/plugins/workflow/test_phase3_execution_semantics.py \
  tests/plugins/workflow/test_phase4_language.py \
  tests/plugins/workflow/test_phase4_snapshot.py \
  tests/plugins/workflow/test_phase4_loops.py \
  tests/plugins/workflow/test_phase4_loop_interactions.py \
  tests/plugins/workflow/test_phase5_language.py \
  tests/plugins/workflow/test_phase5_provider_snapshot.py \
  tests/plugins/workflow/test_phase5_execution_authority_continuity.py \
  tests/plugins/workflow/test_parallel_scheduler.py \
  tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_fault_injection.py \
  tests/plugins/workflow/test_cancel_node.py -q

scripts/run_tests.sh \
  tests/plugins/workflow/test_ai_executor.py \
  tests/plugins/workflow/test_script_executor.py \
  tests/plugins/workflow/test_loop_executor.py \
  tests/plugins/workflow/test_phase6_execution_context.py \
  tests/plugins/workflow/test_phase6_jira_defect_loop.py -q

cd apps/desktop && npm test -- --run \
  src/lib/workflow-public-codec.test.ts \
  src/app/workflows/adapter.test.ts \
  src/app/workflows/workflow-run-drawer.test.tsx \
  src/app/workflows/index.test.tsx
cd apps/desktop && npm run typecheck
node --test scripts/__tests__/vendor-ericsson.test.mjs
```

Run only additional small tests or disposable probes needed to prove or refute
a candidate finding. Do not run live services or broad destructive/release
commands.

Known baseline: the canonical full suite currently has unrelated failures.
One reproduced example is
`tests/agent/test_prompt_cache_ttl_propagation.py`; that test and
`agent/conversation_loop.py` are byte-identical to merge base. Do not attribute
baseline failures to Phase 6 without changed-code causality.

## Severity and proof standard

- **CRITICAL**: cross-profile/authority breach, unauthorized or duplicate
  outward write, execution of unauthenticated bytes, durable corruption/data
  loss, wrong-run result, secret/private disclosure, systemic unbounded
  exhaustion, or a realistic race causing those outcomes.
- **IMPORTANT**: violated locked invariant with realistic production impact;
  fail-open admission/recovery; duplicate/incorrect execution; lost durable
  state; terminal success with unresolved obligation or forbidden artifact;
  wrong scoped substitution; broken installed/runtime path; materially false
  public evidence; or v1-v5 semantic regression.

Every finding must include:

1. stable ID and severity;
2. exact production file/line at the candidate;
3. violated invariant;
4. realistic trigger and step-by-step production path;
5. concrete wrong result and consequence;
6. code evidence plus bounded reproduction or rigorous interleaving proof;
7. why existing tests miss it;
8. smallest safe root-cause remediation; and
9. required regression test.

If any element is missing, omit the finding. Do not include Minor, stylistic,
or speculative observations in the findings table.

## Deliverable

Write the model-specific report path provided by the launcher. Use this exact
structure:

1. Scope verification and starting state.
2. Verdict: `BLOCK` if any CRITICAL/IMPORTANT finding exists, otherwise
   `NO CRITICAL OR IMPORTANT FINDINGS`.
3. Findings table sorted by severity.
4. Full nine-element proof for every finding.
5. Twenty-row invariant matrix: PASS / FAIL / UNPROVEN with evidence.
6. Top adversarial reproductions and wrong observable results.
7. Test-integrity assessment.
8. Verification ledger with exact commands/results.
9. Unverified platforms/dependencies.
10. Final worktree status proving the detached review checkout remains clean.

Do not stop after the first defect. Be specific or be silent.
