# Adversarial code-review prompt — Workflow Studio loop-group authoring contract

Paste this prompt into a fresh Claude Fable or Codex coding session. The
reviewer gets read and shell access to one clean detached checkout at the exact
candidate. This is independent functional-correctness review, not
implementation work.

Do not modify production code, tests, generated files, Git history, branches,
refs, or worktrees. Do not merge, push, publish, deploy, use credentials,
contact live services, or invoke another model. Disposable probes must use
synthetic data, isolated temporary paths, and no network. Return the report to
stdout; the launcher owns report persistence.

This is not penetration testing. Inspect language authority, containment,
compatibility, boundedness, determinism, and fail-closed behavior as ordinary
product invariants using benign synthetic fixtures only.

## Role and review posture

You are a hostile principal reviewer of versioned YAML workflow languages,
lossless authoring contracts, conservative JSON Schema traversal, scoped DAG
semantics, deterministic conformance corpora, Python CLI startup boundaries,
installed packaging, and cross-platform developer tooling.

Try to falsify the candidate rather than confirm its implementation narrative.
Treat plans, comments, commit messages, generated artifacts, green focused
tests, earlier review claims, and test names as unproved assertions. Read final
production files and relevant unchanged callers. A finding requires a
realistic trigger, a complete production path, and a concrete wrong result.
Do not report style preferences, speculative hardening, unrequested
abstractions, or test gaps without a demonstrated production defect.

Do not read another review lane's report, any reconciliation/remediation report
for this review, or `.superpowers/sdd/` progress files before reaching your
independent verdict.

## Immutable scope

```text
Project repository: /Users/coreyellis/Developer/personal/github.com/cmetech/hermes-agent
Review checkout: launcher's working directory, detached at the candidate
Development branch: base (literal main is synchronization-only)
Merge base: c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d
Merge-base tree: 986b9b76f06b562ccc914318507c26dd95cb6d49
Candidate: 869c6519cf86e9df6a903851ccf9d2ee2fc427fa
Candidate tree: fd176c315a258d2d7dcc493442a174fba91d7a05
Review range: c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..869c6519cf86e9df6a903851ccf9d2ee2fc427fa
Expected range: 11 commits, 17 changed paths, 3,886 insertions, 135 deletions
```

Review immutable commits, never a mutable branch name. Before judging code,
verify the detached clean checkout, exact commit and tree, ancestry, range
counts, changed-path count, numeric diff totals, and `git diff --check`. Stop
and return `SCOPE ERROR` if any immutable fact differs.

Expected changed paths:

```text
.github/workflows/ci.yaml
hermes_cli/main.py
hermes_cli/uninstall.py
plugins/workflow/cli.py
plugins/workflow/language.py
plugins/workflow/language_conformance.py (new)
plugins/workflow/language_schema.py
plugins/workflow/models.py
plugins/workflow/schema.py
plugins/workflow/schema_cli.py
scripts/test_workflow_merge_gate.sh
tests/plugins/workflow/test_cli.py
tests/plugins/workflow/test_installed_distribution_e2e.py
tests/plugins/workflow/test_language_conformance.py (new)
tests/plugins/workflow/test_language_schema.py
tests/plugins/workflow/test_phase6_language.py
tests/scripts/test_workflow_merge_gate.py
```

Use the cumulative diff as an inventory, not as a substitute for reading final
files and unchanged callers. Distinguish candidate defects from byte-identical
baseline behavior.

## Binding sources — read completely and in order

1. `AGENTS.md`
2. `docs/superpowers/specs/2026-08-29-workflow-language-phase-6-durable-loop-groups-design.md`
3. `/Users/coreyellis/Developer/personal/github.com/cmetech/workflow-studio/docs/analysis/2026-07-25-hermes-workflow-language-foundation-review.md`
4. `/Users/coreyellis/Developer/personal/github.com/cmetech/workflow-studio/docs/superpowers/specs/2026-07-25-workflow-studio-design.md`
5. `/Users/coreyellis/Developer/personal/github.com/cmetech/workflow-studio/docs/superpowers/specs/2026-08-31-workflow-studio-loop-group-visual-authoring-design.md`
6. `/Users/coreyellis/Developer/personal/github.com/cmetech/workflow-studio/docs/superpowers/plans/2026-08-31-workflow-studio-loop-group-visual-authoring.md` (Hermes Tasks 1–3 and the Phase B handoff boundary)
7. `docs/design/portable-workflow-orchestration.md`
8. `website/docs/user-guide/features/workflow-yaml-reference.md`
9. `skills/software-development/workflow-builder/references/portable-schema.md`
10. `skills/software-development/workflow-builder/references/authoring-checklist.md`

The Workflow Studio loop-group specification defines the consumer contract.
The Phase 6 specification and live Hermes loader define runtime language
behavior. The implementation plan controls scope and verification but cannot
override either specification.

## Delivered behavior to falsify

The candidate claims to publish a bounded, deterministic, profile-scoped
authoring contract and conformance corpus sufficient for Workflow Studio to
perform full visual authoring of one-level `loop_group` workflows without
embedding a second hand-maintained Hermes field inventory or validator.

It claims to describe scoped DAG topology, body admission, current/previous/
outer references, group-output promotion through the first terminal in YAML
definition order, structured-output path proof, conservative unsupported
schema behavior, companion `group/child` references, and exact work-product
bounds. Existing broad native diagnostic codes remain compatible while
additive stable semantic codes disambiguate authoring decisions.

It also claims a data-only, authority-tested corpus for both current profiles;
a bounded dependency-neutral `hermes workflow schema-corpus` command; exact
installed-console behavior; unchanged YAML admission/runtime decisions except
additive diagnostic metadata; byte-identical legacy artifacts; and no second
scheduler, executor, persistence model, or graph authority.

## Locked invariants

Return `PASS`, `FAIL`, or `UNPROVEN` for every invariant. A matching test name
is not proof.

1. The immutable candidate and review range match exactly; the review checkout
   starts and ends clean and detached.
2. Archon uses current normalizer v6 and legacy remains v2. Scoped semantics
   and corpus cases are absent from legacy; snapshot format 2 and v1–v5 replay
   remain compatible.
3. YAML and the existing Hermes loader remain the sole workflow authority.
   Contract/corpus publication introduces no persisted graph, second
   validator, or duplicated hand-maintained field inventory.
4. Body kinds, fields, retry defaults, group bounds, node/edge/work limits,
   and work arithmetic derive from runtime authorities and preserve exact
   boundary/one-over behavior across approval, retry, default, and rejection
   branches.
5. Scoped DAG topology is machine-readable: one-level groups only; unique body
   IDs; internal dependencies only; no self-edge, duplicate edge, missing
   dependency, cycle, empty body, forbidden include/runtime workflow/nested
   group/group retry, or invalid product can be admitted.
6. Current, `$LOOP_PREV`, and outer references have distinct explicit
   producer domains, dependency requirements, first-iteration semantics, and
   applicability on every real authored surface, including body prompt/when/
   command/bash/script/approval/ordinary-loop fields plus group
   `gate_message` and `until_bash`.
7. Group-to-group output references resolve the producer schema through the
   selected primary sink. Primary sink means first terminal in authored YAML
   definition order, not topological, lexical, completion, or mapping order.
8. Structured path proof is versioned, machine-readable, and behaviorally
   equivalent to Hermes's conservative prover: `possible` and `unknown` are
   accepted; only `impossible` rejects. Studio need not apply generic/full
   JSON Schema or copy private Hermes code.
9. The proof policy correctly describes numeric-segment dual object/array
   handling, local JSON Pointer decoding/traversal/cycles, properties,
   pattern/additional properties, arrays and tuple items/maxItems,
   `allOf`/`anyOf`/`oneOf`, unlisted keywords such as `not`, and the separate
   dotted-key walk through nested properties, refs, combinators, and items.
10. Native issue code/path/message/blocking behavior stays compatible.
    Additive semantic codes originate at exact validator decisions and cannot
    be changed by unrelated references or document-wide/message heuristics.
11. Companion `group/child` resolution applies only where runtime supports it,
    never to assignment fields or arbitrary slash-delimited IDs, and publishes
    exact native/semantic diagnostics.
12. Corpus fixtures are deterministic data, not executable validation. Every
    expected validity, native code, semantic code, path, document, scope, and
    projection fact is independently checked through existing Hermes
    authorities.
13. The corpus is profile-complete: both profiles, every supported node kind
    and field family, legacy rejection of v6 syntax, all required positive and
    negative loop-group cases, unknown-field preservation evidence, primary
    sink behavior, work boundaries, structured-path conservatism, promoted
    group outputs, and byte-exact Jira Defect Loop provenance.
14. Corpus case IDs/order/bytes are stable and unique. Production refuses more
    than 64 cases or 160,000 UTF-8 JSON bytes before printing; no distributed
    resource growth can bypass the bound.
15. `workflow schema` and `workflow schema-corpus` alone use the early read-only
    startup boundary. Global option parsing, help, invalid profiles,
    `--version`, oneshot precedence, and ordinary workflow actions retain their
    prior startup behavior.
16. Early authoring-data commands perform no recovery, discovery, provider,
    connector, plugin-runtime, Git, network, file mutation, or credential
    work. They serialize canonical UTF-8 JSON deterministically.
17. Installed wheel/console execution includes all corpus code and resources,
    does not borrow source-tree modules, and resolves POSIX and native Windows
    entry-point layouts honestly. Unexecuted Windows behavior is `UNPROVEN`,
    not passed by assertion.
18. Contract envelope/version/section limits remain unchanged and fail closed.
    Legacy schema/corpus bytes remain identical; Archon artifacts fit with the
    required reserve. Tight synthetic headroom cannot silently truncate or
    delete established documentation.
19. The conformance test runs exactly once in the base workflow merge gate and
    once per OS through the native Ubuntu/macOS/Windows matrix. Tests do not
    weaken or hide pre-existing gate debt.
20. The cumulative range changes no scheduler, executor, store/persistence,
    pointer-move, Git, provider, or outward-effect semantics. The isolated
    launcher UTF-8 change is behaviorally bounded and the Phase B Studio
    consumer can implement the published contract without Hermes-specific
    guesses.

## Attack campaign A — contract/runtime differential

- Build an independent test-only interpreter of published semantic tables and
  expression trees. Differentially compare it with real loader outcomes over
  bounded generated DAGs, retry combinations, reference surfaces, producer
  kinds, primary-sink orderings, and schema shapes.
- Mutate one published rule, constant, path, code, default, table column, or
  expression operator at a time. Determine whether existing tests would fail
  and whether a consumer can unambiguously interpret the unmutated artifact.
- Search all runtime authorities and snapshot readers for duplicated constants
  or semantic inventories that can drift from publication.

## Attack campaign B — scoped identity and DAG admission

- Exercise duplicate/root/body IDs, slash ambiguity, empty and maximum groups,
  exact node/edge limits, cycles, self-edges, missing dependencies, reordered
  mappings, multiple terminals, forbidden body kinds, group retry, aliases,
  malformed nodes, and unknown fields.
- Combine current, previous, and outer references in one document in varied
  textual order. Prove unrelated `$LOOP_PREV` text cannot affect a current or
  outer diagnostic.
- Attack companion `group/child` references with nonexistent groups/children,
  ambiguous slash forms, assignment fields, and profiles that lack v6.

## Attack campaign C — structured path policy

- Compare published policy with real `_v3_output_path_impossible` and dotted-
  key behavior using nested properties, arrays, numeric keys, tuple schemas,
  maxItems, local/nonlocal/cyclic refs, escaped pointer tokens, empty and
  nonempty patterns, additionalProperties variants, unions, intersections,
  unknown keywords, booleans, malformed schema fragments, and `not`.
- Use ordinary producers and loop-group producers whose primary and secondary
  terminal schemas disagree. Exercise body and group gate/until surfaces.
- Look specifically for policy rows that require a preclassified condition a
  Studio consumer cannot derive from published data.

## Attack campaign D — corpus independence and mutation resistance

- Compile every corpus case through real Hermes authorities. Independently
  derive authored kind/field coverage from YAML and compare diagnostic
  document/path/scope from real authored locations.
- Replace a feature tag, portable code, native code, group ID, case order,
  expected projection, primary sink, schema field, or Jira byte. Verify a
  load-bearing test—not fixture self-agreement—would fail.
- Confirm unknown YAML is retained in the parsed source evidence even when the
  current strict validator rejects its location; never equate rejection with
  silent loss.

## Attack campaign E — CLI and startup containment

- Fuzz exact action parsing around global flags, optional values, `--`, help,
  version, oneshot, profiles before/after actions, unknown actions, repeated
  flags, and values that equal `schema` or `schema-corpus`.
- Install a wheel into an isolated prefix/home. Invoke the generated console
  entry point, poison source-tree import paths, and prove both outputs are
  canonical, bounded, byte-stable, and dependency-neutral.
- Place fail-fast sentinels on recovery, discovery, providers, connectors,
  plugin runtime, credentials, and mutating filesystem calls. Confirm only the
  two exact read-only actions bypass normal startup.

## Attack campaign F — versioning, bounds, and compatibility

- Generate both profiles repeatedly under varied hash seeds/locales/timezones.
  Compare bytes, digests, normalizer identity, case order, and contract links.
- Exercise exact section and envelope limits plus one-over inputs. Check UTF-8
  multibyte accounting and refusal before partial stdout.
- Reopen v1–v5/snapshot-2 fixtures at exact boundaries. Confirm moving the
  4,096 work authority did not reinterpret sealed data.

## Attack campaign G — unchanged callers and test integrity

- Search every changed generic seam and relevant unchanged caller for import
  cycles, startup broadening, changed issue serialization, positional
  constructor breakage, or accidental runtime behavior.
- Use mutation reasoning on load-bearing tests. A missing test is a finding
  only when paired with a demonstrated production defect.
- Reject source-text/change-detector tests introduced by this range. Separate
  pre-existing repository debt and environment failures from changed-code
  causality.

## Required verification

Use `scripts/run_tests.sh` for Python. Record exact commands, exit codes,
skips, warnings, retries, and unavailable platforms. Do not run the whole
repository suite; it already ran once for this branch and is a known red
baseline. Run only focused deterministic tests or disposable probes needed to
prove or refute candidate findings.

The detached review checkout has no local `.venv`. Set
`HERMES_PYTHON=/Users/coreyellis/Developer/personal/github.com/cmetech/hermes-agent/.worktrees/workflow-studio-loop-group-contract/.venv/bin/python`
for every `scripts/run_tests.sh` command. Before trusting results, prove that
imports resolve from the detached review checkout rather than the implementation
worktree. A runner invocation that executes zero tests is not a pass.

```bash
git status --short --branch
git rev-parse HEAD HEAD^{tree}
git merge-base c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d HEAD
git rev-list --count c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD
git diff --check c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD
git diff --name-status c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD

HERMES_PYTHON=/Users/coreyellis/Developer/personal/github.com/cmetech/hermes-agent/.worktrees/workflow-studio-loop-group-contract/.venv/bin/python \
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_language_conformance.py \
  tests/plugins/workflow/test_phase3_language.py \
  tests/plugins/workflow/test_phase4_language.py \
  tests/plugins/workflow/test_phase4_snapshot.py \
  tests/plugins/workflow/test_phase5_language.py \
  tests/plugins/workflow/test_phase5_provider_snapshot.py \
  tests/plugins/workflow/test_phase6_language.py \
  tests/plugins/workflow/test_language_snapshot.py \
  tests/plugins/workflow/test_cli.py -q

HERMES_PYTHON=/Users/coreyellis/Developer/personal/github.com/cmetech/hermes-agent/.worktrees/workflow-studio-loop-group-contract/.venv/bin/python \
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  -m integration -k \
  installed_distribution_exposes_deterministic_workflow_schema_corpus -q

.venv/bin/ruff check .
.venv/bin/python scripts/check-windows-footguns.py \
  --diff c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d
```

Known evidence limitations are not candidate findings without changed-code
causality:

- The one full-suite run reported 51,061 passed, 346 failed, and 662 skipped.
  Nearby failures were reproduced on `base`, but no complete base-suite run
  exists. Do not claim the global suite is green or attribute all failures.
- The repository-wide Windows `--all` audit is already red on `base`; this
  branch adds zero findings and fixes one. Native Windows execution has not
  occurred.
- Ty reports no new source diagnostic identities but has a path-sensitive
  panic in unchanged `tools/checkpoint_manager.py` and a large advisory
  baseline.
- The synthetic definition-section coexistence test has one byte of headroom;
  actual Archon output remains 4,569 bytes below the usable total and retains
  the separate 4,000-byte reserve. Treat this as a finding only if a real
  incorrect acceptance, truncation, nondeterminism, or unjustified deletion is
  demonstrated.

## Severity and proof standard

- **CRITICAL**: silent YAML loss or corruption; wrong-profile contract/corpus;
  persisted competing graph authority; invalid workflow admitted due to the
  candidate; unauthorized execution/effect; or systemic unbounded output.
- **IMPORTANT**: realistic Studio-visible contract/runtime mismatch; incorrect
  scoped reference or DAG decision; false portable diagnostic; deterministic
  corpus case with wrong expectation; installed/read-only command escape or
  failure; compatibility regression; missing machine-readable authority that
  forces Studio to duplicate private Hermes semantics; or violated locked
  invariant with material authoring impact.
- **MINOR**: reproducible localized correctness defect with bounded user
  impact. Do not use Minor for style, refactoring taste, speculative defense,
  or a test-only omission.

Every finding must include:

1. stable ID and severity;
2. exact immutable production file/line plus relevant unchanged caller;
3. violated invariant;
4. realistic trigger and step-by-step production path;
5. concrete wrong result and consequence;
6. code evidence plus bounded reproduction or rigorous proof;
7. why existing tests miss it;
8. smallest safe root-cause remediation; and
9. required regression test.

If any element is missing, omit the finding. Do not stop after the first
defect. Be specific or be silent.

## Required report

Return one self-contained Markdown report to stdout using this structure:

1. Reviewer/model/date and immutable scope verification.
2. Verdict: `BLOCK` if any CRITICAL or IMPORTANT finding exists, otherwise
   `PASS`.
3. Findings table sorted by severity, then stable ID.
4. Full nine-element proof for every finding.
5. Twenty-row invariant matrix with `PASS`, `FAIL`, or `UNPROVEN` and concise
   evidence.
6. Top adversarial reproductions and concrete wrong observable results.
7. Test-integrity and unchanged-caller assessment.
8. Verification ledger with exact commands and results.
9. Unverified platforms, dependencies, and residual uncertainty.
10. Final worktree status proving the detached checkout remains clean.

If no qualifying finding exists, say so explicitly and still provide the full
invariant matrix, verification ledger, limitations, and clean-status proof.
