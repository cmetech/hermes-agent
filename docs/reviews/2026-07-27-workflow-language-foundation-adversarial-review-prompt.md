# Adversarial code-review prompt — Workflow language foundation

Paste everything below the line into a fresh, capable model or coding agent
with read and shell access to this repository:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

The reviewer must assess the complete Phase 1 workflow-language compatibility
foundation that was developed on the now-deleted
`feature/workflow-language-foundation` branch and merged locally into `base`.
This is a review task, not an implementation task. Do not modify source,
rewrite history, advance refs, push, open a PR, create a release, or disturb
unrelated work. The only authorized repository write is the final review
document named in Required output.

---

## Role

You are a hostile principal-level reviewer of Python, TypeScript/React,
durable workflow engines, YAML and JSON Schema, canonical serialization,
subprocess and MCP isolation, filesystem race resistance, compatibility
layers, prompt-cache invariants, and upstream-merge preservation.

Your job is to break this implementation, not to bless it.

Assume every completion claim is unproven until you trace the production path
and either reproduce the behavior or establish the invariant from code and
tests. Test names, green gate summaries, ledger entries, commit messages, and
prior review verdicts are not proof. Read every changed production file and
the unchanged callers it now depends on. Do not stop at the first defect.

This feature intentionally created a foundation rather than implementing every
Archon-inspired property. Treat both kinds of error as findings:

1. a deferred field that is silently accepted or has an accidental runtime
   effect; and
2. a Phase 1 field or invariant that is documented as delivered but is not
   actually enforced.

Praise is not useful. If an area is safe, state exactly which code path,
boundary, interleaving, and test you checked.

## Repository and immutable review scope

Repository root:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

The feature branch was deleted after its local merge. Review immutable commit
objects, not a branch name.

| Meaning | Commit |
|---|---|
| Approved implementation baseline | `854a66a882a20129a6a53c675210328d277498fb` |
| Final feature tip under review | `de8a8082fbac10651652cc268dab43c0739ac90a` |
| Concurrent `base` parent used for integration | `f61b8adb7fe059361dbd34b9a5f1c5ce5b925b0a` |
| Local merge commit on `base` | `cf470f332e458047987e18527f53ce3699f86998` |

Primary feature review range:

```text
854a66a882a20129a6a53c675210328d277498fb..de8a8082fbac10651652cc268dab43c0739ac90a
```

At prompt creation this range contains 83 changed files, 14,758 insertions,
and 663 deletions. Verify those numbers yourself.

The merge commit must have these parents, in order:

```text
f61b8adb7fe059361dbd34b9a5f1c5ce5b925b0a
de8a8082fbac10651652cc268dab43c0739ac90a
```

Review the feature range first, then review the merged tree at `cf470f332` for
integration effects from the concurrent first parent. A clean textual merge is
not proof that imports, schemas, test collection, Desktop behavior, or runtime
assumptions remain compatible.

Preserve unrelated local work. Start with `git status`. Use read-only commands
or detached temporary worktrees. Do not clean, reset, stash, switch the shared
checkout, delete untracked files, or remove worktrees you did not create.

## Sources of truth — read completely before reviewing code

Read these sources in full:

1. `AGENTS.md`
2. `apps/desktop/AGENTS.md`
3. `docs/superpowers/specs/2026-07-25-workflow-language-compatibility-expansion-design.md`
4. `docs/superpowers/plans/2026-07-25-workflow-language-foundation.md`
5. `docs/upstream-customizations/README.md`
6. `docs/upstream-customizations/workflow-orchestration.yaml`
7. `docs/upstream-customizations/merge-evidence.schema.json`
8. `website/docs/user-guide/features/workflow-yaml-reference.md`
9. `website/docs/user-guide/features/workflows.md`
10. `skills/software-development/workflow-builder/SKILL.md`
11. `skills/software-development/workflow-builder/references/portable-schema.md`
12. `skills/software-development/workflow-builder/references/authoring-checklist.md`
13. Archon's reviewed authoring reference:
    `https://archon.diy/guides/authoring-workflows/`

The Hermes design and Phase 1 plan are the implementation contract. Archon is
the compatibility-shape reference, not permission to replace Hermes runtime
semantics or implement features outside Phase 1.

If the live Archon page differs from the July 2026 contract described locally,
record the version skew. Do not silently redefine `archon-2026-07` from a newer
web page.

## What Phase 1 claims to deliver

The implementation claims to provide:

- `hermes-legacy` semantics for existing/unversioned workflows;
- opt-in `archon-2026-07` compatibility declared in the Hermes companion file;
- bounded, pure, profile-specific semantic normalization;
- structured compatibility findings with stable codes and migration guidance;
- truthful blocking of Archon-profile fields that have no runtime support;
- package, admission, and resume-time language identity pinning;
- a generated machine-readable authoring/schema contract;
- language-aware CLI, doctor, API, catalog, and Desktop projections;
- workflow-builder and website authoring guidance;
- companion-file-aware discovery-cache invalidation;
- exact authenticated execution bytes for commands, scripts, skills, inputs,
  MCP definitions, local MCP runtime closure, and transitive resources;
- bounded shared resource authority across scheduled verification and package
  preparation;
- regression and upstream-merge gates with truthful evidence.

It explicitly does **not** claim Phase 2+ runtime behavior. In particular,
`output_format`, `output_type`, timeout/retry normalization, strict typed output
references, new condition semantics, loops/includes, model aliases, cost
budgets, sandbox portability, and `loop_group` must remain deferred unless the
design identifies a narrow Phase 1 diagnostic or metadata requirement.

## Actual implementation map

Use this map to orient history, but verify every hash and final behavior.

| Task | Concern | Primary commits |
|---|---|---|
| 1 | Profiles, canonical typed normalization, fingerprints | `ded98252b` through `fe1ed2e13` |
| 2 | Companion parsing, compatibility findings, cache invalidation | `072517d5c` through `cddc84aa3` |
| 3 | Admission pinning, sealed snapshots, fail-closed resume | `1b506bdde` through `83a12557f` |
| 4 | Authoring inventory, generated schema, read-only schema CLI | `4c0e6b4f1` through `0e6de05be` |
| 5 | Strict API projections and Desktop language status | `dfd093e00` through `61f9c7101` |
| 6 | Website and workflow-builder authoring contract | `067a35c2e` and `140255d6c` |
| 7 | CI/base gates, ledger, rehearsal, installed distribution | `d3686eb2f` through `eb215f46b` |
| Review hardening | Historical snapshot authentication and exact runtime-byte authority | `25f492dae` through `de8a8082f` |

The hardening series is not ancillary. It changed scheduler, ResourceResolver,
plugin-agent IPC, MCP worker launch, trust traversal, resource budgets, and
test-gate behavior. Review its final state as core Phase 1 production code.

## Non-negotiable invariants

A violation of any item in this section is at least HIGH severity.

1. **Legacy preservation is explicit.** An absent declaration resolves to
   `hermes-legacy`. Existing workflow behavior does not change merely because
   diagnostics now exist. Doctor identifies legacy semantics without rewriting
   definitions or silently opting users into Archon behavior.
2. **Archon is truthful.** Under `archon-2026-07`, any parsed property whose
   runtime meaning is deferred blocks validation/admission with a stable code
   and migration text. No accepted YAML is runtime-ineffective.
3. **The companion is metadata, not a process.** It does not create a daemon,
   sidecar service, worker, second scheduler, or secret store.
4. **Normalization is pure and bounded.** It performs no model call, network
   access, MCP connection, worker launch, session mutation, prompt mutation, or
   filesystem write. Canonicalization preserves type distinctions, nonfinite
   values, nested structures, and collision resistance.
5. **Semantic identity is location-independent.** Identical package bytes at
   different filesystem paths normalize identically. Machine paths,
   `source_path`, and sealed-run renaming do not enter the semantic digest.
6. **The discovery cache covers both files.** Creating, editing, deleting, or
   replacing `<workflow>.hermes.yaml` invalidates cached discovery just as
   changing the portable YAML does.
7. **Admission binds meaning and bytes.** Profile, normalizer version,
   normalized fingerprint, definition, policy, resources, provider/model
   strategy, and all executable/prompt bytes are sealed before execution.
8. **Resume fails closed.** Current-format metadata cannot be stripped to enter
   a legacy fallback. Definition, sidecar, resource, language, normalizer, and
   projection disagreement are rejected before parsing or execution.
9. **Historical compatibility is cryptographic, not hopeful.** A pre-language
   run resumes only when direct seals, whole-tree evidence, or an exact bounded
   historical package identity authenticates every byte. Unverifiable history
   requires explicit retrust/readmission or a new run.
10. **Authenticated bytes are the consumed bytes.** Commands, uv/Bun scripts,
    skills, inline-agent skills, inputs, MCP definitions, imports, configs,
    data files, and transitive server resources cannot be changed between
    scheduler verification and child consumption.
11. **MCP local execution is default-deny.** Interpolation happens before final
    classification. Declared local resources execute from a private validated
    closure; undeclared path-like values, local URI schemes, malformed URLs,
    encoded ambiguity, and mutable-run imports fail before child startup.
    Supported installed/nonlocal forms remain compatible.
12. **Authority is bounded once per run.** The combined authority is limited to
    512 canonical files, 1 MiB per file, and 8 MiB total, with canonical dedupe
    and rejection before expensive allocation. Scheduled verification,
    revalidation, authorization, and preparation share the same budget/cache.
13. **Prompt caching remains sacred.** No global system-prompt mutation,
    historical-context rewriting, global toolset swap, or synthetic user turn
    is introduced. Isolated structured repair is not implemented in Phase 1.
14. **API and Desktop projections are bounded.** Catalog rows carry summaries,
    detail carries bounded findings/metadata, unknown fields fail safely, and
    full authoring schemas are not embedded in list payloads. Desktop does not
    parse or mutate workflow definitions.
15. **One schema authority exists.** CLI schema, doctor, website, skill,
    compatibility codes, and runtime validation derive from or agree with one
    inventory. Schema inspection is read-only and does not create Hermes home
    state.
16. **Reliability paths remain stable.** Coordinator election, scheduling,
    cancellation, retries, resilience showcase behavior, installed packaging,
    and Desktop operation do not regress. Test isolation fixes must not hide
    real races.
17. **Upstream ownership is complete.** Every changed upstream-owned symbol and
    invariant is recorded in the workflow customization ledger with accurate
    preserve/adapt/remove guidance and tests. Rehearsal must not mutate refs.

## Specific decisions to attack

These are judgment calls and prior defect areas, not assurances.

1. **Typed canonicalization.** Try values that Python considers equal but YAML
   does not: `true`, `1`, `1.0`, `-0.0`, strings with Unicode normalization,
   bytes-like tags, timestamps, nulls, NaN, infinities, nested mappings with
   non-string keys, and key-order changes. Look for fingerprint collisions or
   nondeterminism across processes/Python versions.
2. **Path-independent digesting.** Load identical bytes from different roots,
   symlink spellings, catalog sources, installed layouts, and sealed run names.
   Confirm source-line metadata is either intentionally included and stable or
   intentionally excluded.
3. **Profile downgrade.** Strip or alter language fields independently in the
   companion, normalized snapshot, projection, journal, resources manifest,
   or API payload. A current run must not become legacy.
4. **Historical fallback.** Exercise real pre-language fixtures, arbitrary old
   workflow filenames, sidecar renaming, direct seals, whole-tree seals, zero
   matches, and ambiguous candidate matches. Confirm authentication occurs
   before YAML parsing.
5. **Stable byte authority.** Replace, delete, rename, symlink, or race every
   resource after authority creation. Test commands, scripts, skills, inputs,
   MCP entrypoints, imports, config/data reads, and `$ARGUMENTS` substitution.
6. **Private MCP closure.** Attack path traversal, duplicate relative paths,
   manifest/control-name collisions, hardlinks, case-fold collisions, Unicode
   aliases, compound options, environment interpolation, runtime files,
   imports, `cwd`, `sys.path`, and cleanup on success/failure/cancel.
7. **MCP local/nonlocal classification.** Test exact and nested encodings,
   assignment delimiters, Windows separators, local URI schemes, malformed and
   Unicode-confusable URLs, unknown schemes, scoped package names, npx/uvx
   options, Python `-c`/`-m`, remote URLs, ordinary flags, and literals. Ensure
   classification never consults mutable path existence.
8. **Shared budget.** Cross the limit through combinations rather than a single
   set: definition + sidecar + package resources + skills + inputs + MCP
   closure. Verify modern, historical, and fire-time paths cannot allocate
   separate budgets or double-count aliases.
9. **Output-format non-goal.** Under Archon, `output_format` and `output_type`
   must block with stable findings. Under legacy, behavior must match the
   documented warning/compatibility policy. Search for accidental partial
   runtime enforcement, schema repair, output coercion, or provider-specific
   behavior that leaked in early.
10. **Timeout/retry/context non-goals.** Confirm Phase 1 did not reinterpret
    timeout units, retry attempt counts, conditions, shared context, persistent
    sessions, loops, includes, budgets, or sandbox fields. Diagnostics may
    exist; new runtime semantics may not.
11. **Schema authority and drift.** Compare inventory counts, JSON Schema,
    CLI output, website tables, workflow-builder references, compatibility
    codes, and parser behavior. Find fields documented but absent, accepted but
    undocumented, or classified differently by different surfaces.
12. **Old clients/backends.** Feed missing, partial, extra, malformed, and
    future-version language/compatibility payloads through API models and
    Desktop. Older Desktop behavior must remain operational without inventing
    compatibility status; older backends receiving a new companion must fail
    clearly rather than corrupting a package.
13. **Test-gate truthfulness.** Confirm CI uses per-file isolation on Linux,
    macOS, and Windows; relative interpreter selection survives temporary
    worktrees; ledger evidence represents tests actually executed; reference
    records cannot claim pass/duration; command/path labels obey schema bounds.
14. **Upstream rehearsal.** Verify the shared baseline is historically
    justified, overlap decisions are specific, test execution is real, private
    worktrees are cleaned, and no local or remote refs advance.

## Required review method

### 1. Establish scope and task traceability

- Verify all immutable commits, parents, and ancestry.
- Enumerate all 83 changed files and group them into production runtime, API,
  Desktop, docs/skill, tests, CI/gates, and upstream ledger.
- Build a Task 1–7 coverage matrix with `proven`, `partial`, `missing`, or
  `contradicted` status. Cite production code and behavioral evidence, not only
  tests.
- Identify changes outside the approved plan and determine whether each is a
  justified bug-class correction or unapproved scope expansion.
- Compare the feature-tip tree with the merged tree for every changed path.

### 2. Attack parsing, profiles, and canonicalization

- Trace portable YAML plus companion discovery through bounded parse, profile
  resolution, validation, normalization, findings, fingerprint, and cache.
- Exercise YAML aliases/depth, duplicate keys, custom/native scalar types,
  unknown top-level and companion fields, malformed profiles, and backend
  version skew.
- Prove the normalized definition is immutable and independent of source path.
- Mutation-check collision tests by temporarily reverting one production
  distinction at a time in a disposable worktree.

### 3. Attack compatibility findings and authoring surfaces

- For every inventory field, compare legacy severity, Archon severity, stable
  code, message, migration, runtime support state, and generated schema.
- Confirm doctor, validate JSON, catalog, detail, preflight, Desktop, website,
  and workflow-builder do not implement their own divergent field tables.
- Run `workflow schema` in a fresh temporary home and prove it writes nothing.
- Confirm help/errors and `--json` precedence are deterministic and
  machine-readable.

### 4. Attack admission, resume, and historical migration

- Trace raw bytes from discovery through trust, prepared snapshot, admission,
  store projection, scheduler load, resume, retry, and scheduled fire-time
  revalidation.
- Alter each sealed component independently and in coordinated combinations.
- Test same-run concurrency, source replacement, unsealed shadow files,
  hardlinks/symlinks, partial writes, projection/journal disagreement, and
  normalizer-version skew.
- Prove legacy authentication happens before any executable YAML parsing.
- Verify actionable migration errors contain no unsafe path or workflow data.

### 5. Attack resource and subprocess authority

- Trace `sealed_resource_bytes` through scheduler, `NodeExecutionContext`,
  ResourceResolver, executors, plugin-agent request serialization, worker
  interpolation, MCP configuration finalization, and actual child startup.
- Verify consumers use authenticated bytes rather than re-opening original
  paths. Attack the interval before resolve, after resolve, after IPC, after
  interpolation, and immediately before child open/import.
- Confirm local MCP runs in a complete private closure with correct relative
  layout and no mutable workflow `cwd` or `sys.path` fallback.
- Test authority materialization permissions, identity, nonce/digest/count/
  total checks, duplicate use, collisions, cleanup retries, and locked-file
  behavior on available platforms.
- Search all sibling filesystem consumers; do not assume the listed ones are
  exhaustive.

### 6. Attack bounds, performance, and concurrency

- Verify file-count, per-file, aggregate-byte, schema, findings, API payload,
  label, path, and IPC limits at exact boundaries and combined boundaries.
- Confirm refusal occurs before allocation/read/spawn where contracted.
- Measure discovery and catalog behavior for cache hits and companion
  create/edit/delete. Coordinator sweeps must not repeatedly normalize.
- Stress scheduler/coordinator tests under CPU load. Determine whether the
  deterministic test changes prove ordering or merely choreograph a preferred
  interleaving.

### 7. Attack API and Desktop behavior

- Inspect closed response models, sanitization, compatibility summaries, old
  backend fallbacks, and bounded detail payloads.
- Confirm project, profile, and showcase sources use one loader and one
  compatibility truth.
- Test profile switching, stale responses, malformed/future payloads, loading,
  empty, error, and unavailable backend states.
- Prove every Desktop action remains operational/read-only for definitions and
  that no React code becomes a parser, scheduler, or compatibility authority.

### 8. Attack documentation and deferred-feature honesty

- Compare all authoring examples with the emitted schema and parser.
- Attempt to follow the workflow-builder skill as a fresh author who needs a
  timeout, retry, structured output, context sharing, budget, or sandbox today.
  The skill must stop or offer an honest profile choice; it must not generate a
  workflow that validates but behaves differently.
- Verify Phase 2+ text is clearly future work rather than a current promise.
- Look for prose-contract tests that can pass while tables or semantics drift.

### 9. Attack gates, evidence, and upstream preservation

- Read the gate and rehearsal scripts as production code.
- Verify per-file isolation, Windows interpreter handling, environment
  scrubbing, installed-distribution authorization, evidence schema generation,
  exact test execution, dedupe, and cleanup.
- Inspect every ledger entry touched by the feature. Confirm symbols exist,
  every load-bearing changed symbol is owned, tests exercise the invariant, and
  following merge guidance would preserve the final behavior.
- Rehearse realistic upstream overlap. Do not accept majority-baseline or
  preserve/adapt decisions without history.

### 10. Audit test quality

- Identify mocks that bypass the boundary they claim to test.
- Require real filesystem/process/IPC/SQLite/API/Desktop paths for security,
  lifecycle, and integration claims.
- Find sleeps, timing assumptions, swallowed failures, skipped platforms,
  assertions against implementation detail, and tests that pass when the
  production guard is removed.
- Name the highest-risk untested path in each Task 1–7 area.
- Independently mutation-check the most security-sensitive tests.

## Required commands and evidence

Follow `AGENTS.md`: use `scripts/run_tests.sh`, not direct `pytest`, unless an
existing repository gate intentionally owns its invocation.

Start with:

```bash
git status --short --branch
git cat-file -e 854a66a882a20129a6a53c675210328d277498fb^{commit}
git cat-file -e de8a8082fbac10651652cc268dab43c0739ac90a^{commit}
git cat-file -e cf470f332e458047987e18527f53ce3699f86998^{commit}
git show -s --format='%H%n%P%n%s' cf470f332e458047987e18527f53ce3699f86998
git log --reverse --oneline 854a66a882a20129a6a53c675210328d277498fb..de8a8082fbac10651652cc268dab43c0739ac90a
git diff --check 854a66a882a20129a6a53c675210328d277498fb..de8a8082fbac10651652cc268dab43c0739ac90a
git diff --stat 854a66a882a20129a6a53c675210328d277498fb..de8a8082fbac10651652cc268dab43c0739ac90a
git diff --name-status 854a66a882a20129a6a53c675210328d277498fb..de8a8082fbac10651652cc268dab43c0739ac90a
```

Create a detached review worktree at the exact feature tip. At minimum run and
report:

```bash
python3 scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/workflow-orchestration.yaml

scripts/run_tests.sh \
  tests/plugins/workflow/test_language.py \
  tests/plugins/workflow/test_language_snapshot.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_schedule_store_identity.py \
  tests/plugins/workflow/test_scheduled_runs.py \
  tests/plugins/workflow/test_schedule_revalidation.py \
  tests/plugins/workflow/test_ai_entitlement.py \
  tests/plugins/workflow/test_node_mcp.py \
  tests/plugins/workflow/test_trust_policy.py \
  tests/plugins/workflow/test_runner_binding.py \
  tests/plugins/workflow/test_catalog_api.py \
  tests/plugins/workflow/test_workflow_detail_api.py

.venv/bin/python -m hermes_cli.main workflow schema \
  --profile archon-2026-07 --json

PYTHON_BIN=.venv/bin/python scripts/test_workflow_merge_gate.sh --phase base

cd apps/desktop && npm test
cd apps/desktop && npm run typecheck
```

Also test the merged tree at `cf470f332e458047987e18527f53ce3699f86998`
from a separate detached worktree. Do not reuse imported modules, generated
caches, temporary homes, or node state from the feature-tip run.

Run the controlled upstream rehearsal only after reading its help and overlap
preflight. Use explicit decisions supported by history; do not blindly reuse a
prior run's decisions:

```bash
scripts/test_workflow_upstream_merge.sh \
  --upstream-ref main \
  --base-ref de8a8082fbac10651652cc268dab43c0739ac90a \
  --brand-ref otto \
  --brand-ref loop24 \
  --report-dir /tmp/workflow-language-adversarial-review-evidence
```

Snapshot all local heads and remotes before and after rehearsal. Report any
concurrent unrelated ref movement separately; never claim checksum equality if
the global snapshot changed.

For security, concurrency, historical-migration, and cache claims, write small
temporary reproduction tests outside tracked source when necessary. Remove
only temporary files/worktrees you created. If a platform or dependency is
unavailable, record the exact unverified path; never report an unrun gate as
passed.

## Claims to verify, not accept

The implementation author reported these results. Reproduce them independently:

- Final feature-tip base gate: 1,451 backend tests, installed distribution 1/1,
  Desktop gate 113/113, exact seal `de8a8082f...`.
- Merged-base gate: the same workflow counts, exact seal `cf470f332...`.
- Merged full Desktop suite: 3,006 passed and all TypeScript configurations.
- Controlled base/Otto/Loop24 rehearsal: all 11 commands, 332 executed evidence
  records, zero failures, final ancestry, and no protected-ref mutation.
- Final independent review: no Critical, Important, or Minor findings.

None of these claims substitutes for evidence. Gate counts may legitimately
change if the repository has advanced; explain any difference and pin the
commit actually tested.

## Severity

- **CRITICAL**: arbitrary unauthorized execution; credential, prompt, or secret
  disclosure; cross-profile/session authority breach; execution of bytes other
  than those admitted; durable corruption/data loss; or systemic unbounded
  exhaustion.
- **HIGH**: violation of a non-negotiable invariant; fail-open resume/trust;
  profile downgrade; silently accepted ineffective Archon YAML; deterministic
  race causing incorrect execution; broken installed/runtime path; prompt-cache
  regression; or a ledger omission capable of silently losing the behavior in
  an upstream merge.
- **MEDIUM**: bounded correctness, compatibility, recovery, diagnostics,
  performance, API/Desktop, authoring, or backend-skew defect with a realistic
  trigger.
- **LOW**: narrow maintainability, documentation, or test-quality issue that
  does not currently violate a production invariant.

Do not inflate severity without a concrete failure path. Do not downgrade a
race or authentication gap merely because its interleaving is difficult.

## Required output

Write the review to:

`docs/reviews/2026-07-27-workflow-language-foundation-adversarial-review-<model_name>.md`

model_name is the name of the model performing the review (i.e. claude, codex, etc.)

The review must contain:

1. Scope, exact immutable refs, merge parents, platform, and dependencies
   actually reviewed.
2. A verdict: `SHIP`, `CONDITIONAL`, or `DO NOT SHIP` for the Phase 1 foundation
   as merged into `base`.
3. Findings table sorted by severity. Each finding needs a stable ID, task,
   file/current line, violated invariant, concrete failure scenario or
   interleaving, observed/reasoned evidence, minimal safe fix, and missing
   regression test.
4. Task 1–7 coverage matrix with `proven`, `partial`, `missing`, or
   `contradicted`, citing production and behavioral evidence.
5. A field-capability verdict: delivered, diagnostic-only/deferred, accidentally
   active, or silently ineffective for every advertised Archon/Hermes property.
6. Concrete reproductions for the highest-risk findings, with exact commands,
   inputs, ordering, and wrong result.
7. What was verified safe and why, covering every review dimension without
   generic statements.
8. Verification ledger: every command, pass/fail/skip count, platform, exact
   commit, and whether evidence came from execution, simulation, mutation, or
   inspection.
9. Required remediation before merge/release, ordered by risk and dependency.
10. Residual risks and unverified paths, especially native Windows filesystem/
    subprocess behavior, old-backend skew, installed distributions, and future
    upstream overlap.

If no defect is found in an area, explain the adversarial cases attempted and
why the implementation resisted them. Do not use comments, docstrings, test
names, prior reviews, or green status as substitutes for evidence. Be specific
or be silent.
