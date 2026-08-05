# Adversarial review — Workflow language foundation (Phase 1)

**Reviewer:** independent adversarial review (Claude Fable 5), executed 2026-07-27.
**Review prompt:** `docs/reviews/2026-07-27-workflow-language-foundation-adversarial-review-prompt.md`
(read from `base`; the working copy differs from `base` only in the output filename
instruction, which this document follows).

---

## 1. Scope, refs, platform

| Meaning | Commit | Verified |
|---|---|---|
| Approved implementation baseline | `854a66a882a20129a6a53c675210328d277498fb` | `git cat-file -e` OK |
| Final feature tip under review | `de8a8082fbac10651652cc268dab43c0739ac90a` | `git cat-file -e` OK |
| Concurrent `base` parent | `f61b8adb7fe059361dbd34b9a5f1c5ce5b925b0a` | merge parent 1 |
| Local merge commit on `base` | `cf470f332e458047987e18527f53ce3699f86998` | parents in order `f61b8adb…`, `de8a8082…`; ancestor of current `base` |

`git diff --stat 854a66a88..de8a8082f` → **83 files changed, 14,758 insertions, 663 deletions** —
matches the prompt exactly. `git diff --check` clean. 51 commits in range.

File grouping (83): production runtime 26, API 2, Desktop 12, docs/skill 6, tests 29,
CI/gates 6, upstream ledger 2.

**Platform / dependencies actually used.** macOS 15 (Darwin arm64), CPython 3.11.15 (uv-managed).
Two **detached** review worktrees were created (`git worktree add --detach`) at the feature tip
and at the merge commit, each with its own venv and its own `node_modules`. The shared checkout
was never switched, reset, cleaned, or stashed; `git status` at start showed one unrelated
modified file (the prompt itself) plus 29 pre-existing worktrees, all left untouched.

**One dependency deviation, and it mattered.** `uv pip install -e '.[dev]'` (unlocked) resolved
**FastAPI 0.140.1**; `uv.lock` pins **0.133.1** and the shared checkout has 0.133.1. Under
0.140.1 — a version `pyproject.toml:105` (`fastapi>=0.104.0,<1`) explicitly permits — the base
gate **fails on both trees with 16 test failures**. Re-pinning to 0.133.1 makes both gates pass.
This is reported as finding **M-7**, not as a merge regression.

Node: npm workspaces installed at each worktree root; Vitest 4.1.9.

---

## 2. Verdict

# CONDITIONAL

The durable core of this feature is genuinely strong and resisted every tampering attack I
constructed: admission sealing, fail-closed resume, path-independent semantic digesting,
companion-aware cache invalidation, authenticated execution bytes, and the private MCP closure
are correct, deeply defended, and better than the tests demonstrate. The merge into `base` is
clean and I could reproduce the author's gate numbers exactly.

It does not ship as **SHIP** because two of the prompt's non-negotiable invariants are violated
by the production code, both reproducible in one command each:

- **H-1** — invariant 2 ("Archon is truthful"): a declared `archon-2026-07` package containing
  Phase-1-deferred fields **validates, trusts, and runs to success** on the CLI, and those fields
  are then consumed at runtime with *legacy* semantics. Blocking exists only in the `doctor`
  report and in the catalog/Desktop projection.
- **H-2** — invariant 1 ("legacy preservation"): an **existing, unversioned** workflow whose
  bash/script node writes a file into `$HERMES_WORKFLOW_RUN_DIR` — a path the runtime still
  exports to every node — now fails every subsequent node load with
  `workflow_snapshot_integrity_mismatch`. The repository's own `resilience` showcase used this
  pattern and was rewritten in-range (`9c5c586a9`) rather than the rule being relaxed.

Neither is a containment or credential breach, so neither is Critical. Both are Phase-1 contract
violations with concrete, reproducible failure paths, and H-2 breaks previously-working user
workflows mid-run. Remediation is bounded (§9) and does not require redesign.

Two further results shape the condition. **M-13**: two load-bearing guards — resume-time
`verify_language_snapshot` and the 512-file shared-resource bound — survive deletion with every
relevant suite still green, so the invariants they protect are currently asserted by code alone.
**M-7**: the feature's headline Desktop E2E, plus five sibling middleware suites, cannot execute
at all on a FastAPI version `pyproject.toml` explicitly permits — so part of the claimed evidence
is configuration-dependent in a way the gate does not surface.

The claims I was asked to reproduce rather than accept all held, with one caveat: the base-gate
numbers (1,451 / 1 / 113) are exact on both trees, the ledger checker is clean, and the merge is
genuinely disjoint from the feature — but the "controlled rehearsal, zero failures" claim rests on
a rehearsal whose merge step is a **no-op**, because upstream `main` is already an ancestor of the
feature tip (§8.2).

---

## 3. Findings

Sorted by severity. "Evidence" states whether the finding came from **execution**, **mutation**,
or **inspection**.

### HIGH

---

#### H-1 — Archon-profile blocking findings never block validation, trust, or execution

| | |
|---|---|
| **Task** | 2 (findings) / 5 (projections) |
| **File** | `plugins/workflow/cli.py:901-934` (`_cmd_validate`), `:1515-1546` (`_cmd_trust`), `:1672-1680` (`_cmd_run`); `plugins/workflow/schema.py:1014-1019` (`validate_package`) |
| **Invariant** | Prompt invariant 2; design §"Compatibility findings" ("Under `archon-2026-07`, unsupported or runtime-ineffective fields are blocking"); design acceptance criterion 4; plan Task 2 Step 5 (`Blocking: true`) |
| **Evidence** | **Execution** (reproduced independently of the subagent that first flagged it) |

`load_workflow()` produces two independent collections: `package.validation_issues` (unknown
top-level fields only) and `package.compatibility_findings` (the profile-aware blocking findings
this feature exists to deliver). `validate_package()` returns **only the first**, and
`_cmd_validate` consults only `validate_package()`. `_cmd_trust` computes `assess_compatibility`
solely to build the risk digest and never refuses on `runnable == False`. `_cmd_run` gates on the
trust store and `preflight_execution` (execution-environment only). No admission, store, or
scheduler code re-checks compatibility (`grep assess_compatibility` → `cli.py`, `catalog_api.py`
only).

**Failure scenario (reproduced end to end):** a package with
`language_compatibility: archon-2026-07` and a node declaring `timeout: 5` and
`retry: {max_attempts: 2}`:

```
workflow validate blockedrun --json   → "valid": true, "ok": true, "issues": []   exit 0
workflow trust  blockedrun --digest … → Trusted blockedrun at digest 2c9f9eaa…    exit 0
workflow run    blockedrun --foreground --json --idempotency-key rev1
                                      → "status": "succeeded"                     exit 0
```

`assess_compatibility()` on the *same* package returns `runnable=False`, `level=unsupported`, with
`archon_timeout_semantics_unavailable` and `archon_retry_semantics_unavailable`, both
`blocking=True`. So the compatibility engine is correct; the CLI admission path simply does not
ask it.

The blocked fields are not inert once the run starts. `scheduler.py:1573` reads
`node.options.get("timeout", …)` and `_effective_retry_policy` reads `node.options["retry"]`,
both profile-blind. An author who declared the Archon profile — under which `timeout` is
milliseconds and `max_attempts` counts retries *after* the first — silently gets 5 **seconds**
and 2 **total attempts**. This is precisely the ms/seconds and retry-counting confusion the
profile machinery was built to prevent, occurring inside a package that declared the profile.

Enforcement is asymmetric, which makes the gap easy to miss: the **Desktop/catalog** path *is*
fail-closed (`compatibility.runnable` → Run disabled → server-side admission rejection at
`api_admission.py`), and `doctor` reports `ok:false` with `blocking_doctor_findings`. Only the
CLI validate/trust/run triad is fail-open.

**Minimal safe fix:** have `_cmd_validate` fold `package.compatibility_findings` into its
`valid` computation, and gate `_cmd_trust`/`_cmd_run` on
`assess_compatibility(package).runnable` (with an explicit `--allow-blocked` escape hatch if
operators need one). Keep legacy permissive — legacy findings are non-blocking by construction.

**Missing regression test:** no test asserts that `workflow validate`/`trust`/`run` refuse a
declared-Archon package carrying a deferred field. `test_compat_matrix.py` proves the *findings*
are blocking; nothing proves any command acts on them.

---

#### H-2 — Sealed-tree verification breaks existing legacy workflows that write into the run directory

| | |
|---|---|
| **Task** | 3 (admission pinning) / review-hardening series |
| **File** | `plugins/workflow/scheduler.py:641-696` (`_load_verified_run_package` sealed-tree branch); `plugins/workflow/scheduled_revalidation.py:43-52` (`_MUTABLE_RUN_FILES`, `_MUTABLE_RUN_ROOTS`); `plugins/workflow/executors/bash.py:59` and `executors/script.py:170` (still export `HERMES_WORKFLOW_RUN_DIR`) |
| **Invariant** | Prompt invariant 1 ("existing workflow behavior does not change merely because diagnostics now exist"); plan Global Constraints ("do not reinterpret or reject a workflow that is runnable before this phase") |
| **Evidence** | **Execution** (real `RunStore` + `RunScheduler`, no companion file → `hermes-legacy`) |

New admissions seal an exact path set (`resources.json["sealed_paths"]` +
`sealed_snapshot_digest`). On every subsequent package load, `sealed_snapshot_digest()` walks the
run directory and raises on any file outside the sealed set, tolerating only
`_MUTABLE_RUN_FILES` = {`.lock`, `.snapshot-owner.json`, `events.jsonl`, `run.json`} and
`_MUTABLE_RUN_ROOTS` = {`artifacts`, `nodes`}. Meanwhile both node executors continue to export
`HERMES_WORKFLOW_RUN_DIR` to every child process.

**Failure scenario (reproduced):** an unversioned workflow (no `.hermes.yaml`, therefore
`hermes-legacy`) admitted on the new runtime:

```
before any script write:              LOADS OK (legacy profile)
after run-root write '.showcase-failed-once': FAILS code=workflow_snapshot_integrity_mismatch
after run-root write 'state.txt':             FAILS code=workflow_snapshot_integrity_mismatch
after artifacts/ write:               LOADS OK
```

A multi-node legacy workflow whose first node drops a state/marker/lock file next to the run
metadata now dies at the next node with an integrity error naming no user-fixable cause. Before
this feature the write was inert.

The failure is also undiagnosable by design. Every integrity message in this path is a constant
string with no path or content interpolation — good for the "no unsafe data in migration errors"
requirement (verified: the only interpolations are a fixed metadata field name and the literal
`script`/`MCP`), but it means the operator sees `workflow_snapshot_integrity_mismatch` with
nothing pointing at the file their own script wrote. Unlike
`workflow_legacy_snapshot_unverifiable`, which does carry actionable guidance ("re-trust the
installed workflow and start a new run"), this branch offers none.

That the pattern is real and used is settled by the repository itself: the bundled `resilience`
showcase wrote `.showcase-failed-once` into the run root, and commit `9c5c586a9`
("fix(workflow): preserve sealed resilience retries") moved it to `$ARTIFACTS_DIR` and re-stamped
`showcases/digests.json`. The in-tree example was changed to satisfy the new rule; user workflows
carrying the same pattern get no such migration. Pre-language runs take the
`_LEGACY_NON_PACKAGE_*` path, whose allow-list is broader (`inputs`, `node-skills`,
`node-agent-skills` too) but still rejects an arbitrary `state.txt`.

**Minimal safe fix:** either (a) exclude run-root files created after admission from the sealed
comparison by comparing only the sealed path set for *equality on sealed members* rather than
whole-tree exclusivity, or (b) — safer, preserving the shadow-file defense the check exists for —
keep whole-tree exclusivity but stop advertising the run root as writable: point
`HERMES_WORKFLOW_RUN_DIR` at a mutable subdirectory (or add a `scratch/` mutable root) and
document the change. Option (b) is the smaller behavioral surface but still needs a release note,
because it changes what an existing script's `$HERMES_WORKFLOW_RUN_DIR` resolves to.

**Missing regression test:** no test admits a *legacy* package, writes an arbitrary file into the
run root the way a node would, and asserts the next load still succeeds. `9c5c586a9` added the
opposite assertion (marker must be under `artifacts/`), which locks in the regression rather than
detecting it.

---

### MEDIUM

---

#### M-1 — Every workflow CLI command exits 0, including `doctor` on blocking findings

**Task 4 · `hermes_cli/main.py:16682` (`args.func(args)`), `plugins/workflow/machine_contract.py:18`
(`EXIT_BLOCKING_FINDING = 7`) · Evidence: execution.**

`_cmd_doctor` and `_cmd_validate` correctly return `EXIT_BLOCKING_FINDING`, and doctor's own
`command_contract` payload advertises the exit map to automation — but the top-level dispatcher
discards the handler's return value, so the process always exits 0. Verified: doctor on the H-1
package emits `ok:false, error.code=blocking_doctor_findings` and **exits 0** in both JSON and
text mode; `validate` on a structurally invalid package also exits 0. Only argparse errors exit
non-zero.

The discard predates this range (baseline `hermes_cli/main.py:16562` behaves identically), so this
is pre-existing — but it is load-bearing *for this feature*, because `doctor` is the only surface
that reports Archon blocking, and a CI script gating on the documented exit contract passes a
blocked package silently. Fix: `sys.exit(args.func(args) or 0)`. Missing test: no test asserts a
non-zero process exit for any workflow command.

---

#### M-2 — Compatibility-finding deduplication is O(n²) and runs before the catalog's projection caps

**Task 2 · `plugins/workflow/compat.py:229` · Evidence: execution (measured) + inspection.**

This range replaced the baseline's `findings.append(...)` (`854a66a88:plugins/workflow/compat.py:162`,
O(1)) with a linear membership scan on every insertion. Independently measured with real
`load_workflow` + `assess_compatibility`:

| tool aliases | findings | assess time |
|---:|---:|---:|
| 500 | 501 | 0.005 s |
| 1,000 | 1,001 | 0.018 s |
| 2,000 | 2,001 | 0.064 s |
| 4,000 | 4,001 | 0.244 s |

Exactly 4× per doubling. Nothing caps `allowed_tools` length or node count at load; only YAML
bytes are capped (2 MiB, `schema.py:80`). A 2 MiB file of one-character tool aliases yields
~130k findings → extrapolated **~4 minutes of CPU for a single file**, incurred on every
`GET /api/plugins/workflow/workflows`, because `assess_package_execution` runs *before* the
512-node/512-item/512 KB projection caps (`catalog_api.py:609` vs `:621`; detail `:924` vs `:937`).
Trigger requires only write access to a workflows directory — which a workflow's own script node
already has. Availability-only, no privilege effect, hence Medium rather than High.

Fix: dedupe with a `set[(code, path)]`. Missing test: no performance-bound test on
`assess_compatibility` findings volume.

---

#### M-3 — The published JSON Schema is stricter than the loader (node-type scoping)

**Task 4 · `plugins/workflow/language_schema.py:710-739` vs `plugins/workflow/schema.py:379-413`
· Evidence: execution (jsonschema Draft 2020-12 vs real `load_workflow`).**

The generated `nodes` variants apply `additionalProperties: false` per node type, so
`output_format`, `systemPrompt`, and `maxBudgetUsd` on a `bash` node are schema-invalid. The
loader accepts all three (`_validate_declared_options` gates only `timeout` on node type); the
mismatch surfaces later as a doctor `field_not_applicable` finding. Verified under **both**
profiles:

```
hermes-legacy    output_format on bash node   schema_valid=False loader_accepts=True
archon-2026-07   maxBudgetUsd  on bash node   schema_valid=False loader_accepts=True   (…6/6 disagree)
```

An editor or CI validating against `workflow schema --json` and `workflow validate` therefore
disagree — a direct dent in invariant 15 ("one schema authority"). The parity parametrization in
`test_language_schema.py` does not cover cross-node-type field placement.

---

#### M-4 — Detail findings amplification: the model's findings cap is nominal

**Task 5 · `plugins/workflow/compat.py:193-216`, `plugins/workflow/projection_limits.py` ·
Evidence: inspection + arithmetic.**

`WORKFLOW_COMPATIBILITY_FINDINGS_MAX` computes to **1,078,800** (512 × 1,082 + 524,816 — the
package term reuses `WORKFLOW_DEFINITION_MAX_BYTES` as a *count*), so it never binds. What binds
is the 512 KB definition projection cap; within it ~45–50k tool-alias findings are reachable at
~170 B of JSON each → a **~8 MB** detail response from one *valid* 512 KB workflow, plus the M-2
CPU. List rows for project/profile sources are immune (summary only, `catalog_api.py:670`).

---

#### M-5 — Showcase list rows carry full findings through a silent 200-item clip

**Task 5 · `plugins/workflow/catalog_api.py:667-668`, `plugins/workflow/dashboard/plugin_api.py:468`,
`sanitize.py:120` vs the detail-path workaround at `plugin_api.py:540-553` · Evidence: inspection.**

Showcase entries put `_compatibility_projection` (full findings) into **list** rows, and
`list_workflows` passes them through the generic sanitizer, which silently truncates lists to 200
items. The response model then re-derives level/runnable from the clipped findings
(`require_authoritative_report_state`), so a bundle producing >200 findings with the blocking ones
beyond index 200 fails model validation and **500s the entire catalog**. Below that threshold
findings vanish with no truncation marker. The detail endpoint explicitly works around this exact
clip; the list endpoint does not. Mitigated by showcases being digest-verified vendor bundles —
a supply-side foot-gun, not user-reachable.

---

#### M-6 — Unknown `run_support.reason` fails open on the Desktop Run affordance

**Task 5 · `apps/desktop/src/app/workflows/catalog.tsx:62-77`,
`view-workflow-dialog.tsx:103-124` · Evidence: inspection.**

`runDisabledReason` is a ternary chain whose second branch is
`runSupportCopy[item.run_support.reason]`. When a newer backend returns `supported: false` with a
reason outside the four known literals, that lookup yields `undefined`, which is falsy — so the
Run button renders **enabled**. Worse than a missing label: because the chain *short-circuits* at
that branch, an unknown reason also skips the two checks below it, so a workflow that is
**incompatible** (`compatibility.runnable === false`) or **untrusted** likewise shows an enabled
Run button and no reason text.

Nothing actually runs — `catalog-run-policy.ts:7-13` requires `supported === true`, and the
server rejects at admission — so this is a wrong affordance under forward skew, not a fail-open
execution path. But it is the one place where the Desktop's otherwise-strict "server decides"
posture degrades to "unknown means allowed." Fix: default the lookup
(`runSupportCopy[reason] ?? t.operations.workflowRunSupportUnavailable`). No fixture exercises an
unknown reason.

---

#### M-7 — The Phase-1 E2E and five sibling middleware suites cannot execute on a permitted FastAPI version

**Task 5 / 7 · `tests/plugins/workflow/test_workflow_language_desktop_e2e.py:59` and the same
idiom in `test_workflow_catalog_desktop_e2e.py`, `test_workflow_showcase_desktop_e2e.py`,
`test_laptop_diagnostic_middleware_e2e.py`, `test_scheduling_middleware_e2e.py`,
`test_ai_extensions_middleware_e2e.py` · Evidence: execution on both trees.**

These tests locate the mounted plugin by scanning `web_server.app.routes` for a flattened
`APIRoute` whose `.path` matches. FastAPI 0.140.1 registers lazy `_IncludedRouter` objects with no
`.path`, so `next(...)` raises `StopIteration` **before any request is issued**. The endpoint
itself is fine — a `TestClient` GET against the real app returns 200.

Measured: with unlocked deps (FastAPI 0.140.1) the base gate fails on the feature tip **and** the
merge commit with the identical 16 failures across those 6 files; after pinning `fastapi==0.133.1`
(the `uv.lock` value) both gates pass fully. `pyproject.toml:105` permits `>=0.104.0,<1`, so this
is a supported configuration in which the feature's headline E2E evidence does not run at all.
This also violates `AGENTS.md`'s rule against asserting framework/implementation shape. Fix:
resolve the app through `TestClient` and assert on responses, not on `app.routes` internals.

---

#### M-8 — `idle_timeout` unit divergence from the live Archon contract, with no diagnostic

**Task 2 · `plugins/workflow/language_schema.py:163` (no compatibility entry),
`plugins/workflow/executors/ai.py:254-257` (seconds, default 300),
`website/docs/user-guide/features/workflow-yaml-reference.md:153` ("seconds") ·
Evidence: inspection + WebFetch of `https://archon.diy/guides/authoring-workflows/` (reachable
2026-07-27; carries no version marker).**

The live Archon guide expresses `idle_timeout` in **milliseconds** (alongside `timeout` defaults
of `120000`). Hermes classifies `idle_timeout` as supported under **both** profiles with no
finding, and consumes it as seconds. An author porting `idle_timeout: 120000` from Archon gets a
120,000-second intent silently — the exact ms/seconds class the profile machinery blocks for
`timeout`. Recorded as **version skew**, not a redefinition of `archon-2026-07`: the live page is
undated, so it may postdate the July 2026 snapshot the profile pins. Either way the local contract
diverges from the live guide on this field with zero diagnostic. Fix: give `idle_timeout` an
Archon-profile finding (blocking or an explicit unit warning), or document the deliberate
divergence in the YAML reference.

---

#### M-9 — Ledger checker cannot detect churn to phrase-form owned symbols, and never verifies symbols exist

**Task 7 · `scripts/check_upstream_customizations.py:163-167`, `:357-414`, `:522-527` ·
Evidence: inspection + execution.**

Overlap detection matches owned symbols two ways: AST def/class names, and `\b<symbol>\b` over
changed diff lines. A symbol containing spaces (e.g. `plugin-agent worker post-interpolation
lexical default-deny path policy`) matches neither, so churn to it classifies as `same_file`,
which requires no acknowledgment and no `--decision`. Phrase share in the new entries:
`workflow-language-admission-pinning` 41/59, `workflow-language-regression-gates` 10/10, all five
testing-class entries 100%. Separately, `load_and_validate_manifest` only type-checks symbols as
non-empty strings — a renamed-away symbol passes forever and silently degrades that entry to
file-level detection. (Audited at the tip: **every literal symbol in all 15 added entries does
exist**; the defect is the absent guard.) Residual protection is the executed ledger invariant
tests, which do run post-merge — so this weakens the review-forcing layer rather than losing
behavior outright.

---

#### M-10 — The base gate can seal a dirty tree; the documented `--diff` completeness gate is unrunnable

**Task 7 · `scripts/test_workflow_merge_gate.sh:142`;
`scripts/check_upstream_customizations.py:254-264` · Evidence: inspection + execution.**

(a) The gate emits `TESTED_BASE_SHA=$(git rev-parse HEAD)` after testing the **working tree**, with
no `git status --porcelain` check — standalone use on a dirty checkout produces a tested-SHA claim
for content that was never tested. Bounded: the rehearsal always runs the gate inside a fresh
detached worktree. (b) With a `coverage:` block present, `validate_diff_coverage` ignores the
supplied range's left side and sweeps `coverage.base_commit..<tip>`; `c2c02e1e` is 2,578 commits
and 132 merges behind the tip and crosses commits owned by other ledgers, so the README's
documented command exits 1 with a 67 KB error listing upstream files like `CONTRIBUTING.md`. No
gate invokes `--diff`, so range-level completeness enforcement is manual and currently broken on
this history. My own range-scoped sweep confirms the ledger **is** complete for this range (§6).

---

#### M-11 — Acknowledged-report replay can record `owned_symbol` overlap with `decision: not-required`

**Task 7 · `scripts/check_upstream_customizations.py:500-516`;
`docs/upstream-customizations/merge-evidence.schema.json` · Evidence: inspection.**

`--upstream-diff --report` carries `acknowledged` forward from a pre-existing report file. If
`--report-dir` is reused with a hand-acknowledged `overlap.json`, the checker exits 0, the
rehearsal's decision loop is skipped entirely, and the emitted `merge-evidence.json` records
`overlap_class: owned_symbol` with `decision: not-required` — and validates, because the schema
has no conditional coupling between the two. Requires deliberate operator action. Fix: a schema
`if/then` (owned_symbol ⇒ decision ≠ not-required).

---

#### M-12 — The upstream-rehearsal gate is flake-prone under CPU load, has no retry, and its flake is indistinguishable from a real invariant regression

**Task 7 · `scripts/run_workflow_ledger_invariants.py:110-120` (8-way `ThreadPoolExecutor`, no
retry logic anywhere in the file); `tests/plugins/workflow/test_script_executor.py:254`
(`timeout_seconds: float = 3`); `plugins/workflow/locks.py:81` (5 s lock timeout) ·
Evidence: execution.**

My controlled rehearsal (`--upstream-ref main --base-ref de8a8082f --brand-ref otto --brand-ref
loop24`, 7 history-justified `preserve` decisions) **failed** at the
`ledger-declared-invariants` step with **2 of 134** records failing:

```
merge-upstream-into-base      passed       60 ms
base-invariant-gate           passed  194,197 ms
ledger-declared-invariants    failed  263,227 ms
  tests/plugins/workflow/test_desktop_api.py      (111,640 ms) — WorkflowLockTimeout after 5 s
    test_attention_cursor_traverses_more_than_100_tied_items_and_is_scope_bound
  tests/plugins/workflow/test_script_executor.py   (7,996 ms) — status 'failed' != 'succeeded'
    test_named_script_child_reads_authenticated_bytes_not_raced_original[bun-…]
```

Both are load-induced timing failures, not regressions. Proven by re-running the **same runner,
same manifest, same repo** on an otherwise idle machine: **134 records, 130 executed, 130 passed,
0 failed** — including `test_desktop_api.py` (136,120 ms, passed) and `test_script_executor.py`
(3,657 ms vs 7,996 ms under load, passed). The individual files also pass **22/22** and
**112/112** standalone, and the whole 44-file base gate passed minutes earlier inside the same
rehearsal. The script-executor case spawns python → `bun` under a **3-second** executor
bound; the desktop-api case waits **5 seconds** for an advisory lock while its own file takes 110 s
under contention. `AGENTS.md` explicitly calls this class out ("timing-sensitive tests must not
assume a quiet runner (loose wall-clock bounds ≥ 2s…)").

What makes it a finding rather than noise: `run_tests.sh` auto-retries a failing file once and
reports `⚠ FLAKY`; `run_workflow_ledger_invariants.py` contains **no** retry and **no** flake
reporting, so a single load-induced failure aborts the entire upstream merge rehearsal — the gate
that must pass before any upstream sync. An operator seeing this cannot tell a flake from a real
lost invariant without re-running by hand. The fail-closed behavior itself is correct ("declared
ledger invariant failed; no refs were advanced").

Fix: loosen the two wall-clock bounds, and give the ledger runner the same single retry +
explicit flake reporting the canonical runner has.

**Missing regression test:** none of the meta-tests exercise the runner under contention.

---

#### M-13 — Two load-bearing guards survive removal untested (mutation-verified)

**Tasks 1, 3 · `plugins/workflow/language.py:389-400` (`verify_language_snapshot`);
`plugins/workflow/trust.py:36` (`WORKFLOW_RESOURCE_MAX_FILES = 512`) · Evidence: **mutation**,
run in a disposable detached worktree.**

I mutated six production guards one at a time and re-ran the suites that claim to own them. Four
were caught immediately; **two were not**:

- **`verify_language_snapshot` made a no-op** (`if False and snapshot != expected:` — control flow
  otherwise unchanged): `test_language_snapshot.py` passes **86/86**, and widening to
  `test_admission.py`, `test_scheduled_runs.py`, `test_schedule_revalidation.py`,
  `test_schedule_store_identity.py` and `test_language.py` still gives **259 passed, 0 failed**.
  The reason is instructive: every tamper the suite performs *also* breaks an earlier byte-level
  check (`input_manifest_digest` or the sealed-tree digest), so nothing isolates this guard's own
  contribution. Its unique job — catching a runtime whose normalizer produces a *different*
  digest for *identical* admitted bytes, i.e. the upgrade-safety case the design calls out ("a
  future runtime… fails closed… does not guess or silently migrate the active run") — has **no
  test at all**. This is the highest-risk untested path in Task 3.
- **`WORKFLOW_RESOURCE_MAX_FILES` raised 512 → 100,000**: `test_trust_policy.py`,
  `test_resources.py`, `test_language_snapshot.py` and `test_installed_distribution_e2e.py` all
  still pass. Invariant 12 names 512 explicitly; the only `512` literals in the tests are
  hand-written `max_files=512` arguments constructing their own budgets, not assertions about the
  constant, so the shared bound can be raised silently.

**Minimal safe fix:** add (a) a test that admits a run, then re-normalizes with a deliberately
perturbed normalizer output for the same bytes and asserts
`workflow_language_snapshot_mismatch`; and (b) a boundary test that materialises 512 and 513
canonical files and asserts acceptance/refusal against the module constant rather than a literal.

---

### LOW

| ID | Finding | File | Evidence |
|---|---|---|---|
| L-1 | `archon_unknown_top_level_field` — a real blocking code — is absent from `compatibility_code_catalog`, the website codes table, and both skill references, so an author "starting from the contract" cannot discover it. Same gap, lesser weight, for `workflow_language_profile_unsupported` and `workflow_normalizer_version_unsupported`. | `schema.py:899`; `language_schema.py:790-816`; `workflow-yaml-reference.md:95-107` | inspection |
| L-2 | `doctor` **text** mode prints digest/level/remediation only — zero codes, paths, or migration text. The honesty payload exists only behind `--json`. | `cli.py:1502-1513` | execution |
| L-3 | Every load-time rejection is re-wrapped as `MachineError("invalid_request", …)`, discarding the stable code (`archon_unknown_top_level_field`, `unknown_node_field`, `workflow_language_profile_unsupported` all surface as `invalid_request`). | `cli.py:2341-2347` | execution |
| L-4 | Badges key only off the boolean `legacy`, so a future `{effective_profile: 'archon-2099-01', legacy: false}` — or a payload missing `legacy` — renders the hardcoded label "Archon 2026-07". Wrong version claim; no crash. | `catalog.tsx:118-124`, `view-workflow-dialog.tsx:152-159`, `review-run-dialog.tsx:670-689` | inspection |
| L-5 | Locale parity is not compile-enforced: `defineLocale` types every key optional for ja/zh/zh-hant and merges over `en`. All four locales carry all five new keys today; a future omission degrades to English silently and neither `tsc` nor Vitest catches it. | `i18n/define-locale.ts`, `i18n/types.ts:1526-1530` | inspection + `tsc --noEmit` clean |
| L-6 | `_DirectoryScanBudget.consume` raises when `entries_seen >= max_entries` *after* increment, so the effective budget is 4,095, not `CATALOG_MAX_SCAN_ENTRIES` = 4,096. | `catalog_api.py:159-164` | inspection |
| L-7 | Top-level `sandbox` under legacy gets no legacy warning code (node-scope deferred fields all have one), while the website's status vocabulary implies one. | `language_schema.py:127-135`, `language.py:284-294` | inspection |
| L-8 | Value constraints are written twice — `TRIGGER_RULES`/`CONTEXT_VALUES`/`SCRIPT_RUNTIMES`, the effort set, retry bounds 1–5/1000–60000, loop 1–100, approval 1–10, `worktree {"enabled"}` — in `schema.py` and again in `_schema_for_shape`. Consistent today; only spot-covered, so a one-sided edit drifts silently. | `schema.py:47-54,209-231,397,461-465,496-501,820,824`; `language_schema.py:516-646` | inspection |
| L-9 | Historical-run tests simulate a pre-language run by monkeypatching `store.load_run`, which bypasses the store's own projection-integrity path — so nothing proves a *real* on-disk historical projection survives ingestion. (My attempts to build one failed for a good reason; see §7.) | `test_language_snapshot.py:223-233` | execution |
| L-10 | Inbound mutation models (`ActionRequest`, `NotificationReceiptRequest`, `NotificationPruneRequest`, `CleanupExecutionRequest`) lack `extra="forbid"`; unknown request fields are silently ignored. `StartRunRequest` and every Phase-1 **response** model are closed. | `dashboard/plugin_api.py:1533` | inspection |
| L-11 | Rehearsal defaults `PYTHON_BIN` to bare `python3` (unlike the gate's venv autodetect), so a dependency-less system Python fails at the ledger runner (exit 9) *after* the full base gate has run. Fail-closed, wasted work. | `test_workflow_upstream_merge.sh:30` | inspection |
| L-12 | Brand-phase auto-reconciled conflicts are invisible in structured evidence: `conflict_files` is hardcoded `[]`; only `<slug>-merge.log` records them. | `test_workflow_upstream_merge.sh:160-182` | inspection |
| L-13 | The guarantee that the new language suites "cannot silently disappear" from the base gate is implemented by **reading the gate shell script as text** and regex-matching suite paths — the antipattern `AGENTS.md` bans outright ("Never read source code in tests"). Because the regex runs over the raw file, a suite path appearing only in a *comment* satisfies it, and a correct refactor that moved the suite list into a variable or a file would fail it. (The sibling `ci.yml` assertions use `yaml.safe_load`, which is legitimate data parsing, not source reading.) | `tests/scripts/test_workflow_merge_gate.py:107,115,127,133,139,147` | inspection |

---

## 4. Task 1–7 coverage matrix

| Task | Concern | Status | Production + behavioral evidence |
|---|---|---|---|
| 1 | Profiles, canonical typed normalization, fingerprints | **proven** | `language.py:420-460` `_json_safe` tags every scalar class; measured: bool/int/float/str/NaN/±inf/−0.0/bytes/date/null all distinct, NFC≠NFD, sets order-independent, string key-order irrelevant. Path independence proven end-to-end across different roots, a symlinked root, and a renamed file (identical digests). `normalize_workflow` rejects non-int / bool / unsupported versions before doing anything. |
| 2 | Companion parsing, findings, cache invalidation | **partial** | Findings themselves are correct and complete (`assess_compatibility` → `runnable=False` + stable codes + migration text, verified). Cache invalidation proven for create/edit/delete **and** a same-size same-mtime swap (signature includes the companion SHA-256, `discovery.py:57-68`). **Contradicted** on enforcement: H-1. Performance regressed: M-2. |
| 3 | Admission pinning, sealed snapshots, fail-closed resume | **proven** (with H-2 as a side effect) | 11-case tamper matrix against a real `RunStore`/`RunScheduler`, all blocked (§7). Anti-downgrade marker is carried in the **hash-chained append-only journal**, not just `run.json` — three progressively deeper forgery attempts failed. H-2 is the cost of this strength, not a weakness in it. |
| 4 | Authoring inventory, generated schema, read-only schema CLI | **partial** | Parser field-name sets genuinely derive from `language_schema` accessors (`schema.py:55-66`, test-asserted). `workflow schema` in a fresh `HERMES_HOME` wrote **nothing** (temp home never created), byte-identical across runs, 83,031 B < the 256,000 B bound. Divergences: M-3 (node-type scoping), L-1, L-8. |
| 5 | Strict API projections and Desktop language status | **partial** | List rows carry exactly `{effective_profile, legacy}` + `{level, runnable}`; closed response models with `extra="forbid"`; findings paths reduced to basenames; Desktop does no YAML parsing and gates Run only on server fields (asserted by a dedicated test that feeds `runnable:true` + a blocking finding + a suspicious definition). Gaps: M-4, M-5, M-6, M-7, L-4. |
| 6 | Website and workflow-builder authoring contract | **proven** | All 11 codes, their fields, severities, and phase numbers in `workflow-yaml-reference.md:95-107` match the generated envelopes **exactly**; every inventory table (top-level 17, node-common, retry, loop, approval, agent 7, hook events 21, sidecar 15) matches. The skill stops an author needing a deferred field and offers exactly two explicit choices without silent downgrade. Caveat: its guarantees are procedural, and the CLI beneath enforces none of them (H-1). |
| 7 | CI/base gates, ledger, rehearsal, installed distribution | **proven** | Base gate reproduced on both trees: **44 files / 1,451 backend tests / 0 failed**, installed distribution **1/1**, Desktop **113/113**, `TESTED_BASE_SHA` = the exact commit. Ledger checker exits 0; meta-suites 59/59. Every changed upstream-owned symbol has a ledger owner (§6). Weaknesses: M-9, M-10, M-11, L-11, L-12. |

---

## 5. Field-capability verdict

| Field | Archon | Legacy | Runtime reality | Verdict |
|---|---|---|---|---|
| `language_compatibility` | enforced enum; every malformed value typed-rejected | warning when explicit/absent | drives normalization + digest | **delivered** |
| `nodes[].timeout` | `archon_timeout_semantics_unavailable` (report-only) | `legacy_timeout_seconds` warning | **consumed** as seconds, `scheduler.py:1573` | delivered (legacy) / **accidentally active under Archon** (H-1) |
| `nodes[].retry` | `archon_retry_semantics_unavailable` (report-only) | `legacy_retry_total_attempts` warning | **consumed**, `_effective_retry_policy` | delivered (legacy) / **accidentally active under Archon** (H-1) |
| `nodes[].output_format` | `archon_output_format_unavailable` (report-only) | `legacy_output_format_post_validation` | **consumed** — post-generation validation, `executors/ai.py:505` | delivered (legacy) / **accidentally active under Archon** (H-1) |
| `nodes[].output_type` | `archon_output_type_unavailable` (report-only) | `legacy_output_type_not_published` | **zero consumers** in production code | **silently ineffective — but honestly disclosed** by the legacy warning; correct Phase-1 posture |
| `nodes[].maxBudgetUsd` | `archon_budget_enforcement_unavailable` (report-only) | provider-conditional (`provider_field_unsupported`) | passed to the isolated runner, `ai.py:349` | provider-conditional / accidentally active under Archon |
| `sandbox` (top-level + node) | `archon_sandbox_enforcement_unavailable` (report-only) | no top-level legacy code (L-7) | passed through, `ai.py:81,351` | provider-conditional / accidentally active under Archon |
| `idle_timeout` | **supported, unclassified** | supported | seconds, default 300 | delivered — **unit-skewed vs the live Archon guide** (M-8) |
| `when` conditions | enforced | enforced | static grammar + upstream-reference DAG checks | **delivered** — no new condition semantics introduced |
| `loop` | Phase-1 shape enforced | same | existing loop executor | **delivered**; `loop.command`/`signal_completes` correctly absent |
| hooks | 14 mapped / 7 blocked per event | same | entry `timeout` consumed by `shell_hooks.py:393` | **delivered**, matches the doc table exactly |
| tool aliases | 10 published; unknown capitalized alias blocks | same | resolved at doctor + risk summary | **delivered** |
| `include`, model tiers/`@alias`, `loop_group`, cost budgets, sandbox portability, typed output references | fields do not exist; unknown fields rejected | — | — | **honestly absent** — correctly deferred |

---

## 6. Reproductions for the highest-risk findings

### H-1 — Archon blocking is report-only

```bash
mkdir -p /tmp/h1/{home,proj/.hermes/workflows} && cd /tmp/h1/proj
cat > .hermes/workflows/blocked.yaml <<'YAML'
name: blockedrun
description: archon package declaring phase-1 deferred fields
nodes:
  - id: shell
    bash: "echo hello"
    timeout: 5
    retry:
      max_attempts: 2
YAML
echo 'language_compatibility: archon-2026-07' > .hermes/workflows/blocked.hermes.yaml
export HERMES_HOME=/tmp/h1/home
PY=<repo>/.venv/bin/python
$PY -m hermes_cli.main workflow validate blockedrun --json          # "valid": true   exit 0
DIG=$($PY -m hermes_cli.main workflow doctor blockedrun --json | $PY -c \
      'import json,sys;print(json.load(sys.stdin)["result"]["digest"])')
$PY -m hermes_cli.main workflow trust blockedrun --digest "$DIG"    # Trusted …       exit 0
$PY -m hermes_cli.main workflow run blockedrun --foreground --json --idempotency-key rev1
                                                                   # "status": "succeeded"
```

Contrast, same package:

```python
from plugins.workflow.compat import assess_compatibility
c = assess_compatibility(load_workflow(path))
# runnable: False  level: unsupported
#   archon_timeout_semantics_unavailable blocking=True nodes[0].timeout
#   archon_retry_semantics_unavailable   blocking=True nodes[0].retry
```

**Wrong result:** the package the compatibility engine declares unrunnable is validated, trusted,
and executed, and its Archon-declared millisecond `timeout` runs as 5 seconds.

### H-2 — Legacy workflow broken by a run-root write

```python
# no .hermes.yaml -> hermes-legacy
store = RunStore(tmp/"home"); pkg = load_workflow(d/"w.yaml")
prep = store.prepare_run_snapshot(pkg); rid = store.start_run(...).run_id
rd = store.run_directory(rid); sched = RunScheduler(store)

sched._load_verified_run_package(rid)          # OK
(rd/"state.txt").write_text("x\n")             # what a bash/script node does with
                                               # $HERMES_WORKFLOW_RUN_DIR
sched._load_verified_run_package(rid)          # WorkflowLanguageCompatibilityError
                                               #   workflow_snapshot_integrity_mismatch
(rd/"state.txt").unlink()
(rd/"artifacts"/"m.txt").write_text("x\n")
sched._load_verified_run_package(rid)          # OK  -> only artifacts/ and nodes/ tolerated
```

**Wrong result:** a previously-working legacy workflow fails at its second node with an integrity
error that names no user-fixable cause.

### M-7 — Gate outcome depends on an unpinned but permitted FastAPI

```bash
uv pip install 'fastapi==0.140.1'   # permitted by pyproject (>=0.104.0,<1)
PYTHON_BIN=.venv/bin/python scripts/test_workflow_merge_gate.sh --phase base
#   16 tests failed across 6 middleware/E2E files (StopIteration on app.routes)
uv pip install 'fastapi==0.133.1'   # the uv.lock value
PYTHON_BIN=.venv/bin/python scripts/test_workflow_merge_gate.sh --phase base
#   44 files, 1451 passed, 0 failed; installed 1/1; Desktop 113/113
```

Identical on the feature tip and the merge commit.

---

## 7. What was verified safe, and exactly how

**Typed canonicalization (invariant 4).** `_json_safe` wraps every value in a `{"type": …}`
envelope. Probed for collisions across `True`/`1`/`1.0`/`"1"`/`"true"`, `0.0` vs `-0.0`, NaN,
±inf, `None`, `b"1"`, dates, timestamps, sets, tuples: **zero collisions**. NFC "é" and NFD
"e+◌́" digest differently. Sets sort by canonical JSON, so `{1,2,3}` and `{3,2,1}` agree. String
key order is irrelevant. `_canonical_json` uses `allow_nan=False`, so a nonfinite float can never
reach `json.dumps` un-tagged. I specifically attacked the one apparent weakness — `_json_safe`
stringifies mapping keys, so `{1: "x"}` and `{"1": "x"}` digest identically — and constructed the
end-to-end YAML pair. It is **not** a defect: `freeze_value` (`models.py:60-63`, pre-existing)
already stringifies keys during parsing, so both documents produce the *same executed definition*.
The digest matches execution semantics, which is what it must do.

**Path independence and location neutrality (invariant 5).** Identical bytes loaded from
`installed/`, from `sealed/deep/nested/`, through a symlinked parent directory, and under a
different filename all produce the same `normalized_definition_digest`. `normalize_workflow`
excludes `source_path`, `source_index`, and `source_line` by construction
(`language.py:126-141`).

**Companion-aware discovery cache (invariant 6).** `_load_cached` keys on
`(workflow size, mtime_ns, sha256)` × `(present, size, mtime_ns, sha256)` of the companion.
Verified live: create → `archon-2026-07`; edit → `hermes-legacy`; delete → `hermes-legacy`;
recreate → `archon-2026-07`; and a **same-length, mtime-restored** companion swap was still
detected, because the signature carries the content digest, not just stat.

**Profile downgrade via the companion (attack 3).** Six malformed declarations —
`archon-2099-01`, a YAML list, `true`, `null`, `' archon-2026-07'` (leading space),
`ARCHON-2026-07` — all rejected with `workflow_language_profile_unsupported`. No value silently
resolves to legacy; only *absence* does, per contract.

**Admission and resume tampering (invariants 7, 8).** Eleven independent mutations against a real
`RunStore` + `RunScheduler`, all blocked with `workflow_snapshot_integrity_mismatch`: strip
`language` from `resources.json`; bump `normalizer_version` to 99; downgrade
`effective_profile` to legacy; forge `normalized_definition_digest`; forge `semantic_fingerprint`;
drop `sealed_paths`; rewrite sealed `policy.yaml` from Archon to legacy; edit the sealed
`definition.yaml`; add an unsealed shadow file; replace `definition.yaml` with a symlink. Editing
the **installed source** after admission correctly does *not* affect the run — resume uses the
sealed snapshot. Authentication of `definition.yaml`/`policy.yaml`/`resources.json` digests
happens **before** `load_workflow_snapshot` parses any YAML (`scheduler.py:602-696` precedes
`:771`), satisfying "authenticate before parsing."

**Anti-downgrade to the historical path (invariant 8, the sharpest test).** I attempted three
progressively deeper forgeries to make a current-format run masquerade as pre-language:
(1) strip `language`/`sealed_paths` from `resources.json` and repair its digest; (2) additionally
rewrite `run.json` and repair `projection_sha256` in `admission.sqlite3`; (3) additionally strip
the markers from the DB `provenance_json`. **All three failed** — `load_run` still returned
`snapshot_format_version: 1`. Root cause established by inspection: the projection is also carried
in the **hash-chained append-only journal** (`events.jsonl`, with `frame_sha256` and
`projection_sha256` per frame), so the anti-downgrade marker is multi-sourced. This is stronger
than the test suite demonstrates (L-9) and is the single best-defended property in the feature.

**Private MCP closure and default-deny classification (invariant 11).** Against the real
`AuthenticatedExecutionMaterializer` + `_finalize_authenticated_mcp_config`, 17 launch shapes:
declared relative and absolute-under-source Python entries are rewritten into the private payload
root with `cwd` inside `hermes-workflow-authority-*`; **blocked** — undeclared path argument,
`../../etc/passwd`, `/etc/passwd`, `npx ./local.js`, a non-Python declared local binary as
`command`, `file://` URL, env var pointing outside the closure, undeclared `runtime_files`;
**allowed and correct** — `python -c`, `python -m <installed module>`, `npx -y @scope/pkg`, remote
`https://` URL, declared env path, declared `runtime_files`. Python entries are launched
`-I` (isolated: no cwd, no user site) through a loader that `chdir`s into the payload root and
sets `sys.path` to `[dirname(entry), payload_root, *sys.path]` — so there is no mutable-workflow
`cwd` or `sys.path` fallback. Classification consults only string structure, never filesystem
existence: 45 probe values (encoded `%6d`, `%2e`, zero-width and RTL-override characters,
`a@b@host`, `host:`, `host:80:90`, `1.2.3.4.5`, `999.1.1.1`, Windows separators, scoped packages,
compound `--opt=value`) classify deterministically, with every non-network URI scheme treated as
unsafe. Interpolation runs **before** classification (`plugin_agent_worker.py:1194-1198`), which is
the correct order.

**Authority descriptor validation.** `_validate_authority_descriptor` re-checks device/inode of
root, payload, control and manifest before *and after* reading; requires the exact two-directory
layout and exactly one control file; verifies the manifest digest against an **IPC-carried**
`manifest_sha256` + `nonce` (so rewriting the on-disk manifest cannot help); walks the payload
rejecting symlinks and special files; enforces per-file size, count, and 8 MiB total; and requires
the observed file *and directory* sets to equal the manifest exactly. Fails closed on every branch.

**Authenticated bytes are the consumed bytes (invariant 10).** Script nodes pipe
`resource.authenticated_bytes` to the interpreter over **stdin** (`bun run -` / `python -`,
`executors/script.py:94-122`) rather than passing a path, so the child cannot be handed swapped
content. Command bodies, node skills, inline-agent skills, and `inputs.json` all route through
`ResourceResolver` with `sealed_bytes`, which serves from the authenticated map and raises
`resource is not authenticated` otherwise (`resources.py:310-318`) — the direct-`read_text` calls
that existed at the baseline were replaced in-range.

**Shared bounded authority (invariant 12).** `WorkflowResourceReadBudget` enforces 512 files /
1 MiB per file / 8 MiB total, checks `st_size` **before** reading (refusal precedes allocation),
re-stats after reading to detect mid-read swaps, caches by canonical logical key with alias
support, and `verify_cached_identity=True` re-validates dev/ino/size/mtime on cache hits. The
fire-time path threads one budget through `verify_sealed_snapshot` → the promotion authorization →
`_load_verified_run_package` (`scheduler.py:1512-1540`, `store.py:_scheduled_promotion_read_budget`),
so scheduled verification, revalidation, authorization and preparation genuinely share one budget
and cache. `from_authenticated` builds a *sealed* budget sized to already-authenticated bytes —
no second independent bound, because those bytes were already read under the real one.

**Prompt caching (invariant 13).** No change in range touches system-prompt construction, message
history, or toolset composition. Skills are read and prepended to the node's user prompt exactly
as before (`ai.py:107-116` changes only *where the bytes come from*, not where they go). No
structured repair, no synthetic user turn, no global toolset swap exists in this range.

**Desktop read-only posture (invariant 14).** No YAML parsing anywhere under
`apps/desktop/src/app/workflows/` or `lib/hermes-api.ts`. Run gating reads only
`compatibility.runnable`, `run_support`, `trust_state`, `coordinator.healthy`. The view dialog
issues GETs only; its test stub throws on any unexpected request and asserts no refetch on tab
toggles. A dedicated test feeds `runnable: true` alongside a blocking finding and a suspicious
definition, and asserts the client neither disables Run nor surfaces the finding — proving the
client is not a compatibility authority. Old-backend skew (no `language`) renders exactly the
prior source row, covered by two named tests.

**Old-backend behavior with a new companion.** Confirmed from the baseline tree:
`854a66a88:plugins/workflow/schema.py` has no `language_compatibility` in `_SIDECAR_FIELDS` and
raises a **blocking** `unknown_sidecar_field`. A pre-Phase-1 backend therefore fails clearly on an
Archon companion and cannot corrupt a package — matching the documented backend-version floor.

**Merge integration (`cf470f332`).** The concurrent first parent brought 42 paths; the feature
changed 83; the **intersection is empty**. `apps/desktop/package.json` test entry points are
byte-identical across tip and merge, and `workflow-orchestration.yaml` is identical. The merged
tree reproduces the gate exactly (1,451 / 1 / 113). Integration risk is genuinely low, and it is
low for a structural reason, not by luck.

**Schema CLI hygiene.** `workflow schema` in a fresh `HERMES_HOME` exits 0, writes nothing (the
temp home was never created), and is byte-identical across repeated runs for both profiles;
`--profile bogus` exits 2 via argparse.

**Bounded work in sweeps (design §Performance).** Instrumented `normalize_workflow` and counted
calls: **10** discovery sweeps over one workflow produce exactly **1** normalization (the
`_PARSE_CACHE` holds), 10 sweeps over an empty tree produce **0**, and a verified sealed load costs
~3.1 ms and normalizes exactly once per load. Coordinator polling does not reopen the installed
workflow. This is the one performance claim in the design that held up under measurement — M-2
is the one that did not.

**CI coverage across platforms.** The three new backend unit suites *and*
`test_workflow_language_desktop_e2e.py` are in slice 4 of the `workflow-portability` job, whose
matrix is `['ubuntu-latest', 'macos-latest', 'windows-latest']` and which invokes
`scripts/run_tests.sh` (per-file subprocess isolation). So the prompt's "per-file isolation on
Linux, macOS and Windows" holds — verified by parsing `ci.yml`, not by reading it. Caveat: that
same slice carries two of the six suites that break on FastAPI 0.140.1 (M-7), so CI's correctness
here depends on it resolving dependencies from `uv.lock`.

**Diagnostic-message safety.** Every `WorkflowLanguageCompatibilityError` message in
`scheduler.py` is a constant string; the only interpolations are a fixed metadata field name
(`sealed_definition_digest` etc.) and the literal `script`/`MCP`. No filesystem path, workflow
name, or definition content reaches an error message.

---

## 8. Verification ledger

| # | Command / probe | Result | Tree | Method |
|---|---|---|---|---|
| 1 | `git cat-file -e` ×3, `git show -s cf470f332`, `merge-base --is-ancestor` | parents + ancestry confirmed | shared (read-only) | execution |
| 2 | `git diff --stat/--name-status/--check 854a66a88..de8a8082f` | 83 files, +14,758/−663, no whitespace errors | shared | execution |
| 3 | `check_upstream_customizations.py --manifest …` | **exit 0**, silent | tip **and** merge | execution |
| 4 | `run_tests.sh` × the 13 prompt-named files | **658 passed, 0 failed** (13 files, 147 s) | tip | execution |
| 5 | same 13 files | **passed, 0 failed** | merge | execution |
| 6 | `test_workflow_merge_gate.sh --phase base` (FastAPI 0.140.1) | **16 failed / 6 files** | tip **and** merge | execution → M-7 |
| 7 | `test_workflow_merge_gate.sh --phase base` (FastAPI 0.133.1 = lock) | **44 files, 1,451 passed, 0 failed**; installed **1/1**; Desktop **113/113**; `TESTED_BASE_SHA=de8a8082f…` | tip | execution |
| 8 | same | **44 files, 1,451 passed, 0 failed**; installed **1/1**; Desktop **113/113**; `TESTED_BASE_SHA=cf470f332…` | merge | execution |
| 9 | `workflow schema --profile {archon-2026-07,hermes-legacy} --json`, text, default; ×2 for determinism; fresh `HERMES_HOME` | exit 0; 83,031 B / 81,987 B; byte-identical; **home never created** | tip, merge | execution |
| 10 | `apps/desktop`: `npx vitest run` (full) | **318 files, 2,975 passed, 3 skipped, 0 failed** | tip | execution |
| 11 | `apps/desktop`: `npm run typecheck` (3 tsconfigs) | clean | tip | execution |
| 12 | `npx vitest run` the 3 workflow desktop suites | **83 passed** (index 34, review-run 30, view 19) | tip | execution |
| 13 | Canonicalization collision probe (12 scalar classes + keys + unicode + sets) | 0 collisions; int-vs-str key collapse traced to pre-existing `freeze_value` | tip | execution |
| 14 | Path-independence probe (3 roots + symlink + rename) | identical digests | tip | execution |
| 15 | Companion cache probe (create/edit/delete/recreate/same-size-same-mtime) | all invalidated correctly | tip | execution |
| 16 | Profile-downgrade probe (6 malformed declarations) | all rejected | tip | execution |
| 17 | Resume tamper matrix (11 mutations, real store+scheduler) | 10 blocked, 1 correctly unaffected (installed-source edit) | tip | execution |
| 18 | Historical-downgrade forgery (3 depths incl. SQLite `provenance_json`) | all blocked; marker traced to the hash-chained journal | tip | execution |
| 19 | MCP classification probe (45 values) | deterministic, default-deny, no filesystem consultation | tip | execution |
| 20 | MCP closure finalization probe (17 launch shapes, real materializer) | 8 blocked / 9 allowed, all correct | tip | execution |
| 21 | Legacy run-root write probe | **fails** on run-root write, OK under `artifacts/` | tip | execution → H-2 |
| 22 | Archon validate/trust/run reproduction | validate `valid:true`, trust OK, run **succeeded** | tip | execution → H-1 |
| 23 | `assess_compatibility` scaling (500→4,000 findings) | 4× per doubling (quadratic) | tip | execution → M-2 |
| 24 | jsonschema vs `load_workflow` parity (3 fields × 2 profiles) | **6/6 disagree** | tip | execution → M-3 |
| 25 | `run_tests.sh tests/scripts/test_workflow_{merge_gate,upstream_merge}.py test_check_upstream_customizations.py` | **59 passed, 0 failed** | tip | execution |
| 26 | `run_tests.sh test_language_schema.py test_language.py test_compat_matrix.py` | **87 passed, 0 failed** | tip | execution |
| 27 | Overlap preflight `aaf5691..b7a05b6` | exit 2; 6 `owned_symbol` + 1 `possible_upstream_equivalent`; 35 `same_file`, 53 `none` | tip | execution |
| 28 | `test_workflow_upstream_merge.sh --upstream-ref main --base-ref de8a8082f --brand-ref otto --brand-ref loop24` with 7 justified `preserve` decisions | **FAILED** at `ledger-declared-invariants` (2/134 records, both load-induced) — see §8.2, M-12 | tip | execution |
| 29 | `git show-ref` + `git worktree list` snapshot before/after rehearsal | **byte-identical** (83 refs, 36 worktrees); shared checkout still on `base`, no reset/stash | shared | execution |
| 30 | `run_workflow_ledger_invariants.py` on an idle machine (same manifest, same repo) | **134 records, 130 executed, 130 passed, 0 failed** — both rehearsal failures pass when unloaded → confirms M-12 is a flake, not a regression | tip | execution |
| 31 | Coordinator-sweep / cache normalization counting probe | 10 discovery sweeps of 1 workflow → **1** `normalize_workflow` call; 5 sealed loads → 5 (one per verified load), 3.1 ms each | tip | execution |
| 32 | WebFetch `https://archon.diy/guides/authoring-workflows/` | reachable; ms `idle_timeout`/`timeout`; no version marker | — | execution → M-8 |
| 33 | Ledger completeness sweep (range-scoped reimplementation of the coverage check) | every changed upstream-owned file owned by an entry; per-symbol map produced (§6 of the completeness note) | tip | inspection + execution |
| 34 | Per-entry symbol-existence sweep over all 15 added ledger entries | **0 missing** literal symbols | tip | execution |
| 35 | `ci.yml` parsed: which job/OS matrix carries the new suites | slice 4 of `workflow-portability`, matrix `[ubuntu, macos, windows]`, invoked via `run_tests.sh` | tip | execution |
| 36 | Diagnostic-message leak audit (all `WorkflowLanguageCompatibilityError` messages) | constant strings only; no path, name, or definition content | tip | inspection |
| 37 | Merge-integration diff: feature-changed paths ∩ merge-introduced paths | **empty** (83 ∩ 42 = 0); desktop `test`/`test:workflow-ui` and `workflow-orchestration.yaml` byte-identical across tip and merge | shared | execution |

### 8.1 Mutation ledger

Six production guards, each reverted or weakened one at a time in a **disposable detached
worktree** (`git worktree add --detach de8a8082f`, removed afterwards; the review worktrees and the
shared checkout were never mutated). A guard is "held" only if a test **fails** when it is removed.

| # | Mutation (file → edit) | Suite run | Outcome |
|---|---|---|---|
| MUT-1 | `language.py:112-116` — drop `normalizer_version not in SUPPORTED_NORMALIZER_VERSIONS` | `test_language.py` | **held** — 1 failed (`test_unknown_normalizer_version_fails_closed`) |
| MUT-2 | `language.py:126` — add `source_path` into the normalized document | `test_language.py` | **held** — 4 failed, incl. `test_normalized_digest_excludes_source_location_and_diagnostics` |
| MUT-3 | `language.py:396` — `verify_language_snapshot` never raises | `test_language_snapshot.py` (+5 admission/resume suites) | **NOT held** — 86/86, then 259/259 pass → **M-13** |
| MUT-4 | `plugin_agent_worker.py:450` — disable `_contains_forbidden_network_characters` | `test_node_mcp.py` | **held** — ≥6 failed (surrogate, whitespace, `%20`, tab variants) |
| MUT-5 | `discovery.py:68` — drop companion identity from the cache signature | `test_discovery.py` | **held** — 1 failed (`test_parse_cache_invalidates_when_companion_is_created_edited_and_deleted`) |
| MUT-6 | `trust.py:36` — `WORKFLOW_RESOURCE_MAX_FILES` 512 → 100,000 | `test_trust_policy.py`, `test_resources.py`, `test_language_snapshot.py`, `test_installed_distribution_e2e.py` | **NOT held** — all pass → **M-13** |

Note on MUT-6's fourth suite: `test_installed_distribution_e2e.py` is `@pytest.mark.integration`
and `pyproject.toml:389` sets `addopts = "-m 'not integration'"`, so it collects **0 tests** under a
plain `run_tests.sh` invocation; the base gate re-enables it with an explicit `-m integration`
(`test_workflow_merge_gate.sh:111`). That is deliberate and correct — I verified it rather than
reporting a phantom gap — but it does mean a developer running that file directly sees a green
"0 tests passed" summary.

### 8.2 Upstream rehearsal — result and an honesty caveat

Decisions were derived from history, not reused: for each of the 7 entries requiring one I
confirmed the owned symbols are **absent from upstream `main`** (`version_agent_label`,
`board_summary`, `_mutation_precondition`, `_stage_workflow_package`,
`plugin_background_services`, `classify_execution_runtime`, `starts_request_mcp`,
`PackageMCPUnavailable`, `test:workflow-ui` → 0 files each), so each entry's
`removal_condition` ("remove when upstream exposes an equivalent…") is unmet and `preserve` is the
only defensible decision. `format_banner_version_label` appears in upstream, which is why that
entry's *other* symbols carry the OTTO-specific behavior.

**Caveat that materially limits what this rehearsal proves:** `main` (`b7a05b6b`) is **already an
ancestor** of the feature tip — `git rev-list --count de8a8082f..b7a05b6b` = **0** — and the
rehearsal's first step logged `Already up to date.` So the rehearsal validates the *harness*
(gate execution, ledger invariants, brand fast-forwards, evidence emission) and exercises **zero
real upstream overlap resolution**. The author's reported "all 11 commands, 332 executed evidence
records, zero failures" is therefore true but does not demonstrate conflict resolution. The 7
overlap decisions arise from the ledger's `last_verified_upstream` baseline (`aaf5691`) lagging
`main`, not from unmerged upstream work.

**Outcome.** The rehearsal **failed**, at the third of its commands:

```
merge-upstream-into-base      passed       60 ms
base-invariant-gate           passed  194,197 ms   (the full 44-file base gate)
ledger-declared-invariants    failed  263,227 ms   (2 of 134 records failed)
declared ledger invariant failed; no refs were advanced
```

Both failures are load-induced timing flakes, analysed in **M-12** — not lost invariants. Re-running
the same runner on an idle machine produced **130/130 executed records passed**. The harness
behaved correctly on failure: it emitted complete structured evidence (134 records: 130
`executed`, 4 `reference`; the 4 references correctly carry no `result`/`duration_ms`) and
advanced nothing.

So the rehearsal's *substance* — every declared ledger invariant executes and passes at the
feature tip — is confirmed; what failed is the harness's tolerance for a loaded machine.

**Ref safety — verified, and the strongest single claim in the rehearsal's favour.**
`git show-ref` was captured before (83 refs, sha256
`53554ed513717ebf448cc5a34a18dd3f805d23415659f4dd0bd6ab4467bbc92d`) and after: **byte-identical**.
`git worktree list` before/after: **byte-identical** (36 entries — every private worktree the
rehearsal created was removed). The shared checkout remained on `base` with only the pre-existing
unrelated modification. Reading the script confirms the mechanism: it only ever runs
`git worktree add --detach` under `mktemp`, confines merges and commits to those worktrees, and
removes them in an `EXIT` trap; there is no `update-ref`, `push`, `branch -f`, or checkout of a
real branch anywhere in it.

---

## 9. Required remediation before merge/release

Ordered by risk, then dependency.

1. **H-1 — make Archon blocking real at the CLI.** Fold `compatibility_findings` into
   `_cmd_validate`'s `valid`, and gate `_cmd_trust`/`_cmd_run` on
   `assess_compatibility(package).runnable`. Add regression tests asserting each of the three
   commands refuses a declared-Archon package carrying a deferred field, and that legacy remains
   permissive. Until this lands, the Phase-1 claim "no accepted YAML is runtime-ineffective" is
   not true on the CLI path.
2. **M-1 — stop swallowing exit codes** (`sys.exit(args.func(args) or 0)`). Without it, fix 1 is
   still unusable from automation. Ship with 1.
3. **H-2 — restore legacy run-directory behavior.** Choose the mutable-scratch option or relax the
   whole-tree exclusivity check, add the missing legacy regression test, and write a release note
   either way — some users' workflows will need to move their writes.
4. **M-2 — set-based finding dedupe** plus a bound (or an early cap) on findings volume, and move
   `assess_package_execution` behind the projection caps in both catalog paths.
5. **M-7 — rewrite the six route-scanning E2E suites** to assert on `TestClient` responses instead
   of `app.routes` internals, then re-run the gate on both the locked and the newest permitted
   FastAPI. Consider narrowing the `pyproject.toml` FastAPI ceiling if 0.140.x is not intended to
   be supported.
6. **M-3 — reconcile schema/loader node-type scoping** (make the loader enforce it, or relax the
   generated schema), and extend the parity parametrization to cross-node-type field placement.
7. **M-4 / M-5 — bound findings in payloads:** give the detail response a real findings cap, and
   apply the detail endpoint's existing sanitizer workaround to showcase list rows.
8. **M-13 — cover the two guards that survive removal.** Add the normalizer-drift test for
   `verify_language_snapshot` (the upgrade-safety case the design promises and nothing currently
   exercises) and the 512/513-file boundary test for the shared resource budget. This is cheap and
   directly protects two invariants the prompt calls non-negotiable.
9. **M-8 — classify `idle_timeout`** under Archon (finding or documented divergence).
10. **M-12 — make the rehearsal gate reliable.** Loosen the 3 s script-executor bound and the 5 s
    lock wait in the two named tests, and give `run_workflow_ledger_invariants.py` the single
    file-level retry plus flake reporting that `run_tests.sh` already has. Until then the gate that
    must pass before every upstream sync fails intermittently on a loaded machine, and the operator
    cannot distinguish that from a lost invariant.
11. **M-9 / M-10 / M-11 — harden the ledger tooling:** verify owned symbols exist; make phrase-form
    symbols either machine-checkable or explicitly documented as narrative-only; add a
    `git status --porcelain` guard before sealing `TESTED_BASE_SHA`; fix or retire the `--diff`
    coverage path; add the schema `if/then` coupling `owned_symbol ⇒ decision ≠ not-required`.
12. **M-6, and the L-series** — schedule as follow-ups; none blocks release on its own.

---

## 9.1 Highest-risk untested path, per task area

Required by the review method. Each is the path most likely to break in production with no test
noticing.

| Task | Highest-risk untested path | Why it is the worst one |
|---|---|---|
| 1 | A future normalizer version producing different output for identical bytes | `verify_language_snapshot` is the only guard, and MUT-3 proves no test fails when it is removed (M-13). This is exactly the upgrade case the design promises to fail closed on. |
| 2 | `assess_compatibility` on a pathological findings volume | No performance or count bound is asserted anywhere; M-2's quadratic regression reached `base` unnoticed. |
| 3 | Resume of a **real** pre-language run produced by an older release | Simulated only by monkeypatching `store.load_run` (L-9); my three forgery attempts could not synthesize one, so neither the suite nor this review has executed the genuine path. |
| 4 | Cross-node-type field placement (schema vs loader) | M-3: six of six probes disagree, and the parity parametrization does not cover the axis at all. |
| 5 | Any `run_support.reason` or `effective_profile` value a future backend invents | M-6 and L-4: no fixture supplies an unknown literal, and the failure is silent-and-wrong rather than loud. |
| 6 | Drift between the website/skill tables and the emitted contract | Enforced only by substring assertions in `test_workflow_builder_skill.py`; a code or phase renumber would not fail anything, and nothing at all guards `workflow-yaml-reference.md`. |
| 7 | The shared 512-file / 1 MiB / 8 MiB budget bounds | MUT-6 raises the file bound 200× with every relevant suite still green (M-13); the `512` literals in tests construct their own budgets rather than asserting the constant. |

---

## 10. Residual risks and unverified paths

- **Native Windows filesystem and subprocess behavior is entirely unverified here.** Everything in
  this review ran on macOS/arm64. The authority materializer's `O_NOFOLLOW`/`0o400` semantics,
  locked-file deletion during `cleanup()` retries, `Scripts/python.exe` interpreter selection, and
  the case-insensitive-filesystem collision surface in `_canonical_relative` are unexercised. CI
  claims a 3-OS matrix; I did not run it.
- **A genuine pre-language run could not be synthesized** (§7). The historical path was reviewed by
  code reading and by three failed forgery attempts, and the test suite exercises it via a
  monkeypatched `load_run` (L-9). Nothing in this review — or in the suite — proves a *real* run
  admitted by a pre-Phase-1 backend resumes correctly on this code. That is the highest-value
  untested path in Task 3, and a real archived run directory from an older release would settle it.
- **Old-backend skew is verified only by reading the baseline parser**, not by executing a
  pre-Phase-1 backend against a new companion file.
- **Installed distributions** are covered by exactly one test (`test_installed_distribution_e2e.py`,
  1/1). Packaging paths beyond that fixture — notably the Nix build guard interaction — were not
  independently exercised.
- **TOCTOU between authority validation and MCP child startup.** The worker validates the private
  closure, then the MCP child re-opens those files. The window is real but bounded by a `0700`
  `mkdtemp` root with `0400` files, and any attacker inside it is already the same POSIX user, who
  has stronger options. This is a correct threat-model boundary, not a defect — but it is the one
  place where "authenticated bytes are the consumed bytes" rests on filesystem permissions rather
  than on the bytes never leaving process memory (as scripts do, via stdin).
- **Future upstream overlap is untested by this rehearsal** (§8.2). The next real `main` advance
  will be the first genuine exercise of the merge machinery, and M-9's phrase-symbol blind spot
  will apply to the 41-phrase `workflow-language-admission-pinning` entry — the one guarding the
  most security-sensitive code in the feature.
- **Performance under concurrency** was partly load-tested and partly reasoned about. Measured:
  the discovery cache holds (10 sweeps of one workflow → exactly **1** `normalize_workflow` call),
  sealed loads cost ~3 ms each and normalize exactly once per verified load, and the fire-time
  budget is genuinely shared. Not measured: any sustained multi-run scheduler load test. M-2 and
  M-12 were both found by targeted measurement rather than a sweep, so other superlinear or
  timing-fragile paths plausibly remain.

---

*Prepared without reliance on prior review verdicts, commit messages, ledger assertions, or test
names. Every claim above is traceable to a command in §8 or a cited file and line.*
