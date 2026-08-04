# Adversarial code-review prompt — Workflow Language Phase 3

Paste everything below the line into a fresh, capable model or coding agent
with read and shell access to this repository:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

The reviewer must assess the complete Workflow Language Phase 3 delivery for
**CRITICAL and HIGH defects only**. This is a review task, not an
implementation task. Do not modify production code, tests, generated files,
Git history, branches, worktrees, or refs. Do not push, publish, open a PR,
create a release, or disturb unrelated work. The only authorized repository
write is the final review document named in Required output.

This is an adversarial **correctness** review: try to falsify the Phase 3
behavioral claims. It is not authorization for threat modeling, exploit
development, penetration testing, security validation, or security-oriented
test suites. Do not probe live services, use real credentials, exercise
third-party systems, or create attack payloads. Inspect authority,
containment, privacy, and fail-closed code paths only as needed to identify a
concrete CRITICAL or HIGH production defect. Any reproduction must use
ordinary functional behavior, synthetic data, isolated temporary state, and
no network access.

---

## Role

You are a skeptical principal-level reviewer of Python, TypeScript/React,
durable workflow engines, language normalization, canonical structured data,
subprocess lifecycle, SQLite and append-only journals, concurrency, provider
retry accounting, generated contracts, and installed packaging.

Your job is to find release-blocking defects, not to bless the work and not to
produce a general quality report.

Assume every completion claim is unproven until you trace the actual
production path and establish a concrete failure from code plus ordinary
behavioral evidence. Commit messages, task reports, test names, green counts,
and prior review verdicts are leads, not proof. Read every changed production
file and the unchanged callers on which its new behavior depends. Review the
final tree, because later corrections may have replaced an earlier design.

Do not report MEDIUM, LOW, stylistic, documentation-polish, speculative, or
test-only findings. Do not inflate severity to make a concern eligible. If a
concern lacks a realistic trigger, a violated load-bearing invariant, and a
concrete production consequence, omit it. A missing test is not itself HIGH
unless the required production behavior is demonstrably absent or wrong.

## Repository and immutable review scope

Repository root:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

Review immutable commits, not the state of a mutable branch name.

| Meaning | Commit |
|---|---|
| Phase 2/base predecessor before Phase 3 design | `5b974a53593fc880d18417ee2fc0e5eaff5599f4` |
| Approved Phase 3 implementation baseline | `cffc23cecd801d3aed08ba66d596bec4a365a43a` |
| Final Phase 3 production candidate | `8a1fe704484bf63e0e84f536f7fb690a2f024ccf` |
| Report-only verification closure | `9aa8d8323d25df4cc1afd6a9fb646a995f71a7c3` |

Primary production review range:

```text
cffc23cecd801d3aed08ba66d596bec4a365a43a..8a1fe704484bf63e0e84f536f7fb690a2f024ccf
```

At prompt creation, that range contains 132 commits and 221 changed files,
with 61,199 insertions and 967 deletions. Verify the ancestry and counts
yourself. Much of the volume is retained SDD review evidence. Do not sample the
production diff: read every changed production file, every changed public
contract, and the relevant unchanged callers. Raw retained review `.diff`
artifacts do not need line-by-line re-review unless they are used to support a
claim.

Also inspect:

- `5b974a5359..cffc23cecd` for the approved Phase 3 design and plan history;
- `8a1fe70448..9aa8d8323d` for report-only closure claims; and
- the final tree at `8a1fe704484bf63e0e84f536f7fb690a2f024ccf`
  as the sole production verdict target.

Do not treat the report-only closure commit as production behavior. Do not
review a later mutable branch tip if it differs from the production candidate.

Preserve unrelated local work. Begin with `git status`. Use read-only commands
or a detached temporary review worktree. Do not clean, reset, stash, switch the
shared checkout, delete untracked files, or remove a worktree you did not
create.

## Sources of truth — read completely before reviewing code

Read these files in full:

1. `AGENTS.md`
2. `apps/desktop/AGENTS.md`
3. `docs/superpowers/specs/2026-07-25-workflow-language-compatibility-expansion-design.md`
4. `docs/superpowers/specs/2026-07-30-workflow-language-phase-2-structured-data-design.md`
5. `docs/superpowers/specs/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience-design.md`
6. `docs/superpowers/plans/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience.md`
7. `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/progress.md`
8. `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-16-report.md`
9. `docs/upstream-customizations/README.md`
10. `docs/upstream-customizations/workflow-orchestration.yaml`
11. `website/docs/user-guide/features/workflow-yaml-reference.md`
12. `skills/software-development/workflow-builder/references/portable-schema.md`
13. `skills/software-development/workflow-builder/references/authoring-checklist.md`

The umbrella design, Phase 3 design, and approved plan are the contract. Phase
2 defines the typed-output substrate that Phase 3 must consume without
regression. The progress ledger and Task 16 report are completion claims to
verify, not authorities that override the design.

Do not let a newer live Archon page silently redefine the pinned
`archon-2026-07` contract. If external documentation has changed, note the
version skew only when it creates a CRITICAL or HIGH shipped mismatch.

## What Phase 3 claims to deliver

Phase 3 claims to add, for newly admitted `archon-2026-07` workflows using
normalizer v3:

- immutable requested and effective timeout/retry semantics sealed at
  admission and reused on resume;
- Archon millisecond timeout normalization with bounded per-attempt deadlines;
- one non-multiplying attempt ledger shared by provider and workflow retries;
- strict, direct-dependency output references with one typed/rendered runtime
  resolver;
- typed condition evaluation with deterministic skip-versus-fail behavior;
- bounded durable waits for transient output reads without hot polling or
  retry charge;
- exact substitution of large Bash values through verified inherited
  descriptors and a bounded shell-context classifier;
- confirmed-missing cross-run persistent-session recovery through a durable,
  idempotent registry-update obligation, without provider replay;
- stable bounded diagnostic and recovery evidence;
- additive authenticated API and Desktop projections of backend truth;
- generated schema, authoring guidance, installed-distribution behavior, and
  customization-ledger coverage that agree with runtime semantics; and
- exact preservation of unversioned, `hermes-legacy`, and admitted v1/v2
  behavior.

Phase 3 explicitly does **not** add Phase 4 loops/includes, Phase 5 provider
portability, or Phase 6 durable `loop_group`. It must not silently activate
those features.

## Task map to cover

Use the approved plan for exact acceptance criteria, but cover every task:

| Task | Production concern |
|---:|---|
| 1 | Profile-specific normalizer v3 and requested semantics |
| 2 | Sealed effective execution semantics at admission and resume |
| 3 | Closed static v3 output-reference grammar |
| 4 | One typed and rendered runtime output resolver |
| 5 | Typed v3 condition evaluation |
| 6 | Durable bounded transient-reference waits |
| 7 | Strict substitution through every existing consumer |
| 8 | Sealed per-attempt timeout enforcement |
| 9 | One non-multiplying provider/workflow retry ledger |
| 10 | Generic bounded child-descriptor inheritance |
| 11 | Verified large-value Bash substitution |
| 12 | Missing isolated-session classification without core widening |
| 13 | Durable cross-run persistent-session recovery |
| 14 | Bounded API and Desktop evidence projections |
| 15 | Generated contracts, operator docs, and installed flows |
| 16 | Regression, integration, and customization convergence |

## Non-negotiable invariants

A demonstrated violation of any item below is HIGH or CRITICAL depending on
impact.

1. **Legacy behavior is unchanged.** Unversioned and `hermes-legacy` packages
   remain on normalizer v2. Existing v1/v2 runs reload their recorded meaning
   and are never upgraded in place.
2. **Admission pins meaning.** Profile, normalized requests, effective limits,
   structured-output contract, resource authority, and fingerprints are
   sealed before execution. Resume does not consult mutable current config to
   reinterpret a v3 run.
3. **Timeout units and ownership are exact.** Archon values are positive finite
   milliseconds normalized once to seconds. Bash/script omitted timeout is
   120 seconds before policy capping. Each workflow attempt has one sealed
   deadline, and nested provider/repair work cannot outlive it.
4. **Retries cannot multiply.** Initial workflow execution plus all additional
   provider, repair, fallback, inline-agent, and later workflow attempts never
   exceed the sealed effective total. Unknown provider counts consume the
   conservative grant. Cancellation and uncertain outward effects are never
   silently replayed.
5. **References have one meaning.** Every v3 reference uses the same ASCII
   grammar, names a direct dependency, and resolves from one verified winning
   output. Conditions receive typed values; substitutions receive rendered
   text. Missing, malformed, transient, and integrity failures never become
   empty strings or legacy coercions.
6. **Conditions are typed and durable.** False means `skipped`; syntax, type,
   numeric, reference, or integrity failure means terminal `failed` with zero
   executor/provider attempts and no retry consumption. Evaluation never uses
   Python execution or provider-text reparsing.
7. **Transient reads are bounded.** A temporary output read failure enters one
   durable CAS-fenced wait schedule, survives restart, consumes no execution
   attempt, and ends in success or a stable terminal failure without hot
   polling or duplicate coordinators recording duplicate wakes.
8. **Bash executes the admitted bytes.** Inline/spill choice is based on
   resolved UTF-8 bytes at the exact 32,768-byte boundary. Large values preserve
   content, including trailing newlines, and only enter proven lexical
   contexts. The launched command and inherited descriptor manifest are the
   exact verified authority; no mutable path fallback exists.
9. **Persistent-session recovery is narrow.** Only confirmed absence of a
   cross-run registry session may start fresh. Same-run missing context,
   database failure, ambiguity, denial, fingerprint mismatch, or nonzero
   provider work cannot enter the recovery path.
10. **Recovery is durable and non-replaying.** A winning fresh result and its
    exact private registry-update obligation are journaled atomically. CAS is
    generation-fenced and idempotent. Crash, cancellation, or CAS retry cannot
    rerun the provider, overwrite a newer registry entry, discard the
    obligation, or publish terminal completion early.
11. **Evidence is bounded and private.** No raw substituted values, commands,
    provider responses, session IDs, fingerprints, registry keys, spill paths,
    prompts, credentials, or mutable provider paths enter journal projections,
    API responses, Desktop state, or logs.
12. **One backend truth reaches every surface.** CLI, API, gateway, schedules,
    showcase, Desktop, generated schema, website docs, and workflow-builder
    guidance agree on units, defaults, supported fields, stable codes, and
    deferred phases. Desktop does not become a parser or execution authority.
13. **Prompt caching and the narrow waist remain intact.** Phase 3 adds no core
    model tool, live system-prompt mutation, historical-message rewrite,
    synthetic user turn, or global toolset swap.
14. **Lifecycle and concurrency stay fenced.** Claims, retries, cancellation,
    provider release, output publication, recovery obligations, journal
    projection, and run finalization have one durable winner under restart and
    multiple coordinators.
15. **Installed and upstream-preservation paths are real.** Wheel/sdist
    behavior does not depend on the source checkout. Every changed
    upstream-owned invariant is represented accurately in the customization
    ledger and merge gate.

## Required review method

### 1. Establish scope and traceability

- Verify all four commits, parentage, ancestry, tree identity, diff counts, and
  that `8a1fe70448` is the production candidate under review.
- Enumerate changed paths and classify them as production runtime, API,
  Desktop, generated contract/docs, tests/gates, or retained SDD evidence.
- Build a Task 1–16 matrix with `proven`, `contradicted`, or `not established`.
  A task may be `not established` without becoming a finding; create a finding
  only when CRITICAL/HIGH criteria are met.
- Trace plan requirements to final production code and ordinary behavior. Do
  not use a test filename or task report as the sole citation.
- Use `git log -p -S <symbol>` when a suspicious omission may be deliberate.
  Verify the implementation premise and original intent before calling it a
  defect.

### 2. Review versioning, normalization, admission, and resume

- Trace raw YAML and companion bytes through profile resolution,
  normalizer-version selection, structured-output normalization, v3 requested
  semantics, effective admission limits, sealed snapshots, trust identity,
  schedule revalidation, and resume.
- Compare new legacy, explicit legacy, admitted v1/v2, and new Archon v3 paths.
- Look for a sibling admission path that omits effective semantics, reads
  current config after admission, silently upgrades a snapshot, or computes a
  different digest/projection.
- Check exact field sets, numeric bounds, version gates, canonical encoding,
  node applicability, mismatch failures, and downgrade behavior.

### 3. Review timeouts, retries, provider accounting, and cancellation

- Trace the sealed deadline from claim through Bash/script, AI wall/idle,
  provider calls, repair, fallback, inline agents, cleanup, retry backoff, and
  resumed execution.
- Prove the combined ledger charges the initial workflow execution and every
  additional provider attempt exactly once, including missing/invalid attempt
  evidence and provider release races.
- Inspect fatal, transient, unknown-error, unknown-outcome, outward-action,
  shutdown, and cancellation classifications. Find any path that replays an
  uncertain effect, exceeds the cap, leaks a provider slot, or extends a
  deadline.
- Check boundary values and the distinction between retries-after-initial and
  total attempts. Legacy must retain its old units and totals.

### 4. Review references, conditions, and durable waits

- Trace every authenticated interpolation surface through static scanning and
  the runtime resolver: `when`, prompt, inline script, Bash, authenticated
  command bodies, approval/rejection text, and retained loop surfaces.
- Compare direct dependency checks, identifier grammar, structured field
  proof, whole-output handling, mappings/sequences/scalars, integrity checks,
  typed versus rendered facets, and legacy adapters.
- Check condition precedence, short-circuiting, numeric/string typing,
  non-finite rejection, skip/fail transitions, attempt counts, and dependency
  propagation.
- Trace transient reads across first observation, every durable wake, restart,
  competing coordinators, success, and exhaustion. Look for cached misses,
  duplicate wakes, claim churn, retry charge, or a hot loop.

### 5. Review Bash substitution and descriptor lifecycle

- Trace authenticated template bytes, lexical classification, reference
  ordering, inline/spill selection, descriptor-relative creation, verified
  read handles, child descriptor inheritance, exact rendered command,
  evidence, parent close, child cleanup, cancellation, and spawn failure.
- Review all admitted quote contexts and all explicitly unsupported shell
  contexts. Confirm the classifier and renderer share the same authored-byte
  interpretation across continuations, comments, heredocs, nesting,
  substitutions, arithmetic, redirections, assignments, functions, and case
  constructs.
- Check exact UTF-8 boundaries, multibyte values, deduplication, file/count/
  aggregate limits, NUL handling, trailing newlines, inherited-handle bounds,
  and native-Windows fail-closed behavior.
- Do not create exploit payloads. Use only benign synthetic strings and
  ordinary shell-output comparisons if a bounded reproduction is necessary.

### 6. Review persistent-session classification and recovery

- Trace parent preflight, worker race classification, provider-attempt
  evidence, same-run versus cross-run provenance, fresh execution, winning
  attempt, atomic obligation creation, registry CAS, deferred wake, idempotent
  observation, cancellation, and finalization.
- Check every crash boundary described in the design. Prove the provider is not
  replayed after a successful result and a newer/different registry entry is
  never overwritten.
- Verify selection authority and journal ordering under multiple coordinators,
  stale claims, delayed notifications, projection failure, and resumed
  reconciliation.
- Trace all private candidate fields into evidence, API, Desktop, logs, and
  exception text. Only bounded digests and identifiers may escape.

### 7. Review API, Desktop, generated contracts, and distribution

- Compare runtime stable codes and semantics with doctor, schema/editor data,
  catalog/detail/run/evidence APIs, Desktop types and rendering, website docs,
  workflow-builder guidance, and official fixtures.
- Test old-backend/new-Desktop and new-backend/old-Desktop additive behavior,
  malformed or missing fields, stale responses, profile changes, and bounded
  list/detail payloads.
- Confirm schema inspection remains read-only before startup recovery and from
  a clean temporary `HERMES_HOME`.
- Build or inspect the installed-distribution path and confirm it does not
  borrow source-tree files.
- Verify Phase 4–6 fields remain explicitly deferred rather than silently
  accepted or accidentally activated.

### 8. Review integration and regression boundaries

- Search for changed generic seams in `agent/`, `tools/`, `run_agent.py`,
  `hermes_state.py`, and CLI startup. Trace all non-workflow callers for
  regression, resource leakage, or changed public behavior.
- Confirm the core tool schema and live prompt prefix remain unchanged.
- Inspect scheduler/store/provider-release interleavings and journal fast paths
  corrected during Task 16. A performance shortcut must not skip validation;
  validation must not destroy the fast path or mutate a read-only command.
- Check upstream customization ownership and merge-gate coverage for every
  changed upstream-owned symbol. Do not run a live merge, advance refs, or
  propagate brand branches.

### 9. Audit tests without turning gaps into low-value findings

- For each load-bearing invariant, determine whether tests exercise real
  imports, files, SQLite/journal state, subprocesses, provider accounting,
  authenticated API adapters, Desktop behavior, and installed packages, or
  merely assert mocks and fixtures.
- Look for tests that pass when the production guard is removed, broad
  exception swallowing, weak substring assertions, unobserved background
  failures, timing choreography, platform skips, stale generated snapshots, or
  change-detector assertions.
- Use mutation reasoning to determine whether a test would fail if its
  production guard disappeared; do not edit tracked source or tests.
- Report a test concern only when it accompanies a proven CRITICAL/HIGH
  production defect or leaves a release-critical contract demonstrably false.

## Ordinary verification only

Follow `AGENTS.md`: run Python tests through `scripts/run_tests.sh`, never by
invoking the test framework directly. Tests and reproductions must remain
offline, synthetic, bounded, and ordinary-functional. Do not run threat-model,
security-audit, exploit, penetration, live-service, credential, or destructive
validation. Do not run files or suites excluded by the Task 16 user override.

Start with read-only evidence:

```bash
git status --short --branch
git cat-file -e 5b974a53593fc880d18417ee2fc0e5eaff5599f4^{commit}
git cat-file -e cffc23cecd801d3aed08ba66d596bec4a365a43a^{commit}
git cat-file -e 8a1fe704484bf63e0e84f536f7fb690a2f024ccf^{commit}
git cat-file -e 9aa8d8323d25df4cc1afd6a9fb646a995f71a7c3^{commit}
git show -s --format='%H%n%T%n%P%n%s' 8a1fe704484bf63e0e84f536f7fb690a2f024ccf
git log --reverse --oneline cffc23cecd801d3aed08ba66d596bec4a365a43a..8a1fe704484bf63e0e84f536f7fb690a2f024ccf
git diff --check cffc23cecd801d3aed08ba66d596bec4a365a43a..8a1fe704484bf63e0e84f536f7fb690a2f024ccf
git diff --stat cffc23cecd801d3aed08ba66d596bec4a365a43a..8a1fe704484bf63e0e84f536f7fb690a2f024ccf
git diff --name-status cffc23cecd801d3aed08ba66d596bec4a365a43a..8a1fe704484bf63e0e84f536f7fb690a2f024ccf
```

From a detached worktree at the exact production candidate, reproduce the
ordinary Phase 3 allowlist retained by Task 16, omitting any explicitly
excluded mixed/security-oriented files:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_language.py \
  tests/plugins/workflow/test_phase3_execution_semantics.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_strict_output_references.py \
  tests/plugins/workflow/test_phase3_conditions.py \
  tests/plugins/workflow/test_phase3_resolution_waits.py \
  tests/plugins/workflow/test_phase3_bash_substitution.py \
  tests/agent/test_plugin_agent.py
```

Then run only the smallest ordinary existing tests needed to confirm or refute
a candidate CRITICAL/HIGH issue. For Desktop, schema, installed package, or
customization claims, use the exact scoped commands recorded in the approved
plan and Task 16 report. Do not run broad discovery or generic ledger stages
that the Task 16 override explicitly superseded or excluded.

Record exact command, commit, platform, pass/fail/skip result, and whether the
evidence came from execution or inspection. If a dependency or platform is
unavailable, state the exact unverified path. Never report an unrun command as
passed.

## Severity threshold

Only these severities are allowed in the findings section:

- **CRITICAL** — a realistic path to unauthorized arbitrary execution;
  credential/prompt/secret disclosure; cross-profile, cross-session, or
  cross-operator authority breach; execution of bytes other than those
  admitted; durable corruption or data loss; systemic unbounded exhaustion;
  or a workflow result that can be irreversibly committed to the wrong run.
- **HIGH** — violation of a non-negotiable invariant with realistic production
  impact; legacy semantic regression; fail-open admission/resume/recovery;
  deterministic or credible concurrency path to duplicate/incorrect
  execution; provider calls exceeding the sealed grant; replay of an uncertain
  effect; lost or overwritten persistent-session state; terminal success while
  a required durable obligation is unresolved; broken installed/runtime path;
  prompt-cache or message-alternation regression; materially false API/Desktop
  evidence; or an upstream-preservation omission likely to silently remove the
  delivered behavior.

The following are out of scope and must not appear as findings: MEDIUM, LOW,
nits, naming, formatting, general complexity, preferred refactors, speculative
hardening, documentation polish, isolated test weakness, and theoretical risk
without a demonstrated execution path.

Do not downgrade a proven race merely because the interleaving is difficult.
Do not upgrade a concern merely because it touches a sensitive boundary.

## Finding proof standard

Every finding must include all of the following:

1. stable ID and severity;
2. concise title and affected Phase 3 task(s);
3. exact production file and current line at `8a1fe70448`;
4. violated design/plan invariant;
5. realistic trigger and step-by-step production execution path;
6. concrete wrong result and user/operator consequence;
7. evidence from code plus an ordinary bounded reproduction, or a rigorous
   interleaving proof when deterministic reproduction is impractical;
8. why existing tests and gates do not catch it;
9. the smallest safe remediation that fixes the whole bug class; and
10. the required regression test.

If any element is missing, do not include the item as a finding. Put neither
speculation nor lower-severity concerns in a residual-risk appendix.

## Required output

Write the review to:

`docs/reviews/2026-08-04-workflow-language-phase-3-adversarial-review-<model_name>.md`

Use the reviewing model's short name for `<model_name>`.

The review must contain:

1. exact immutable scope, tree, platform, and dependencies actually reviewed;
2. verdict: `BLOCK` if at least one CRITICAL/HIGH finding exists, otherwise
   `NO CRITICAL OR HIGH FINDINGS`;
3. findings table sorted CRITICAL before HIGH;
4. full proof for each finding using the ten-element standard above;
5. Task 1–16 coverage matrix with `proven`, `contradicted`, or
   `not established`, without converting lower-severity gaps into findings;
6. concise verification ledger of commands and results; and
7. concise list of unverified platform/dependency paths, with no speculative
   remediation.

If there are no qualifying findings, write exactly:

```text
NO CRITICAL OR HIGH FINDINGS
```

Then summarize the production paths and ordinary adversarial cases examined so
the result is auditable. Do not add MEDIUM/LOW observations after that verdict.
Be specific or be silent.
