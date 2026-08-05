# Adversarial review — Workflow Language Foundation (Phase 1)

**Reviewer:** Cursor Grok 4.5 (independent hostile review)  
**Date:** 2026-07-27  
**Platform:** macOS 26.5 (Darwin arm64), Python 3.11.15, Node v22.22.3, npm 10.9.8  
**Method:** read-only analysis of immutable commits via detached worktrees; production-path reproductions; required gates; controlled upstream overlap/rehearsal. Shared checkout was not switched, reset, stashed, or cleaned.

---

## 1. Scope and immutable refs

| Meaning | Commit |
|---|---|
| Approved implementation baseline | `854a66a882a20129a6a53c675210328d277498fb` |
| Final feature tip under review | `de8a8082fbac10651652cc268dab43c0739ac90a` |
| Concurrent `base` parent | `f61b8adb7fe059361dbd34b9a5f1c5ce5b925b0a` |
| Local merge commit on `base` | `cf470f332e458047987e18527f53ce3699f86998` |

**Merge parents (verified):** `f61b8adb7…` then `de8a8082f…`  
**Feature range diff (verified):** 83 files, +14758 / −663; `git diff --check` clean.  
**Worktrees used:** `/tmp/wf-lang-adv-tip` @ `de8a8082f`, `/tmp/wf-lang-adv-merge` @ `cf470f332`  
**Shared checkout at review start:** `base` @ `a34a50875` (merge + review-prompt docs only; unrelated dirty file: modified prompt). Preserved.

**Sources of truth read:** design + Phase 1 plan, upstream-customizations README/ledger/schema, website YAML reference + workflows.md, workflow-builder skill + references, Archon authoring shape (local contract preferred over live web skew).

**Author claims checked independently:** tip base gate 1451 / installed 1/1 / Desktop gate 113/113 at `de8a8082f` — **reproduced**. Author claim of “no Critical/Important/Minor findings” — **contradicted** (see findings).

---

## 2. Verdict

### **DO NOT SHIP**

Phase 1’s language/admission/MCP/Desktop foundation is largely solid, and the tip base gate is green. It still violates non-negotiable Archon truthfulness on the local CLI admission path: packages that doctor marks `runnable=false` for deferred Archon fields can be trusted and executed, and those fields are **accidentally active** under legacy semantics while sealed as `archon-2026-07`. A second HIGH defect drops machine-contract exit codes at `hermes_cli.main`. The controlled upstream rehearsal also failed ledger soak under this host (**F8**). Until F1/F2 are fixed and regression-gated (and rehearsal re-run to green including brands), the Phase 1 foundation must not be treated as release-complete.

---

## 3. Findings (severity-sorted)

| ID | Sev | Task | Location | Violated invariant | Failure scenario | Evidence | Minimal fix | Missing regression |
|---|---|---|---|---|---|---|---|---|
| **F1** | **HIGH** | 2/3/5 | `plugins/workflow/cli.py:1515-1556` (`_cmd_trust`), `cli.py:1672-1689` (`_cmd_run`); contrast `api_admission.py:374-378` | Non-negotiable #2 (Archon truthful; deferred fields must block admission); “no accepted YAML is runtime-ineffective” | Author opts into `language_compatibility: archon-2026-07` and adds `timeout: 1` (or other Phase-1-blocked fields). `workflow doctor --json` → `runnable=false` / `archon_timeout_semantics_unavailable`. `workflow trust` still records trust. `workflow run --foreground` admits and executes under sealed `effective_profile: archon-2026-07`. | **Executed** on tip and merge trees. Tip: sleep-3/`timeout:1` produced `error_code=timeout` / `waiting_retry` (legacy seconds semantics **active**). Merge: `runnable=false` → trust ok → run `succeeded` with Archon language snapshot. Desktop/API gate on `compatibility.runnable`. | Gate `_cmd_trust` and `_cmd_run` on `assess_compatibility(...).runnable` (same as API); refuse trust of non-runnable digests | CLI integration test: Archon+deferred field → doctor non-runnable → trust fails → run fails; API already covered |
| **F2** | **HIGH** | 5/7 | `hermes_cli/main.py:16682-16683` (`args.func(args)` discards return); `plugins/workflow/cli.py:2321-2333` returns non-zero | Machine/automation contract (doctor documents exit 7 for blocking findings) | `python -m hermes_cli.main workflow doctor <blocking> --json` prints `ok:false` / `blocking_doctor_findings` but **process exit 0**. `hermes_main.main()` returns `None`. | **Executed.** Shell `doctor` exit=0 with `ok:false`. Direct `workflow_command` path returns 7; top-level main drops it. | `sys.exit(args.func(args) or 0)` (or equivalent) for plugin commands that return exit codes | Process-level test invoking `hermes_cli.main` argv for blocking doctor |
| **F3** | **MEDIUM** | 3 | `plugins/workflow/store.py:8855-8863` (`resume_run` loads live `definition.yaml` for `always_run`) | Admission/resume fail-closed for control-plane meaning; sealed bytes should drive resume decisions | Same-user writer flips `always_run` on sealed `definition.yaml` after failure. Resume resets node state from live YAML. Execute path then fail-closes on tree identity (`WorkflowLanguageCompatibilityError`). | **Reproduced by independent verifier:** live mutation changes resume control-plane; execution refuses. No silent outward re-exec observed. | Load `always_run` from sealed verified package / authenticated definition bytes, not live `load_workflow` | Resume mutation test asserting no state transition from unauthenticated live YAML |
| **F4** | **MEDIUM** | 7 | `scripts/check_upstream_customizations.py` overlap classes; ledger prose `owned_symbols`; `merge-evidence.schema.json` hardcodes brand ancestry booleans | Upstream ownership completeness; silent merge loss | Overlap for tip→`main`: 4 language entries including `workflow-language-admission-pinning` classified **`same_file`** (no mandatory decision). 35 `same_file` overall. Prose-heavy owned_symbols evade AST symbol hits. Evidence schema requires `contains_tested_base: true` etc. by construction. | **Executed** overlap report: `owned_symbol=6`, `possible_upstream_equivalent=1`, `same_file=35`, `none=53`. Language pinning is `same_file`. | Require decisions for security-class `same_file` entries; convert load-bearing prose to exact identifiers; stop hardcoding ancestry booleans | Gate test that rewriting `verify_language_snapshot` without named-symbol hit still forces a decision |
| **F5** | **LOW** | 1/9 | `plugins/workflow/executors/ai.py:63-88` fingerprint; `sessions.py` keying | Prompt/session identity completeness (Phase 1 session reuse) | Skills/hooks/agents omitted from persist-session fingerprint → stale shared session after package text change with same tools/model. | **Code + reproduction:** fingerprint unchanged when skills/hooks/agents change; changes when model changes. Same-operator only; not cross-tenant sealed-authority bypass. | Include skills/hooks/agents (or package/semantic digest) in fingerprint/session key | Fingerprint mutation test |
| **F6** | **LOW** | 2/6 | `plugins/workflow/language.py:187-233` vs `:267-282`; docs tables | Authoring honesty for legacy budget/sandbox warnings | Under `hermes-legacy`, language findings skip budget/sandbox codes (loop `continue`). Overall `assess_compatibility` still blocks via provider matrix (`provider_field_unsupported`). Docs omit legacy codes. | **Inspected + assessed.** Not a runnable bypass. | Add legacy warning codes or document intentional silence | Schema/doctor fixture asserting legacy warning or documented absence |
| **F7** | **LOW** | 1 | `plugins/workflow/models.py` `freeze_value` key stringification (related) | Canonical collision resistance at YAML edge | Non-string map keys collapsed via `str(key)` in freeze path; typed `_json_safe` is stronger for normalize path. | **Partial.** Typed normalize digests for bool/int/float/str showed **no** collisions in direct probe. Residual freeze_value risk remains for non-normalize consumers. | Align freeze_value with typed encoding or reject non-string keys | Collision fixture for `{1:…,"1":…}` |
| **F8** | **MEDIUM** | 7 | `tests/plugins/workflow/test_process_lifecycle_soak.py` + `plugins/workflow/locks.py:81`; rehearsal ledger runner | Reliability / gate truthfulness under concurrent load | Controlled rehearsal merged `main`→tip cleanly and passed tip base gate, then ledger-declared invariants failed: `test_hundred_fast_cycles_release_every_claim_and_scheduler_thread` raised `WorkflowLockTimeout` on `.admission.lock` (5s). Brand phases never ran. | **Executed.** `commands.tsv`: merge passed; `base-invariant-gate` passed (514567 ms); `ledger-declared-invariants` failed (368641 ms). Exit 9. Protected refs unchanged (delta 0). | Determine whether soak is load-flaky or a real lock/reentrancy bug; make ledger runner isolate/retry policy explicit; do not claim rehearsal green without re-run | Reproduce soak under CPU contention; fix lock holder or test budget |

No **CRITICAL** finding was reproduced (no cross-tenant authority breach, no sealed-byte substitution on the execute path, no unbounded allocation bypass found in required suites).

---

## 4. Task 1–7 coverage matrix

| Task | Status | Production evidence | Behavioral evidence |
|---|---|---|---|
| 1 Profiles / typed normalize / fingerprints | **proven** (with F7 residual) | `language.py` resolve/normalize/`_json_safe`; identity normalizer v1 | Path-independent normalized digests across two roots; bool/int/float/str digests distinct; absent sidecar → `hermes-legacy` |
| 2 Companion / findings / cache | **partial** | Findings inventory blocks Archon deferred fields; companion unknown keys fail; discovery cache keys companion mtime/size/sha | Doctor emits `archon_timeout_semantics_unavailable` + `runnable=false`; **F1** shows findings do not bind CLI admission |
| 3 Admission pin / resume / historical auth | **partial** | `make_language_snapshot` / `verify_language_snapshot`; scheduler `_load_verified_run_package` fail-closed; sealed language on prepare | Tip/merge seal Archon language on admitted runs; historical/pre-language path fail-closed on digest mismatch; **F3** resume control-plane uses live YAML |
| 4 Schema / inventory / CLI schema | **proven** | `workflow schema --profile archon-2026-07 --json`; inventory-driven codes | Schema CLI wrote **no** files under fresh `HERMES_HOME`; 6 Archon blocking codes present; `loop_group` absent from schema payload |
| 5 API / Desktop projections | **proven** (API) / **proven** (Desktop read-only) | `api_admission` runnable gate; catalog/detail language projection; Desktop `compatibility.runnable` disables Run | API blocks non-runnable; Desktop displays language status only (no parser). **F1/F2** are CLI/main holes, not Desktop authority |
| 6 Docs / skill honesty | **partial** | Website + skill state Phase 2+ blocks; design migration example still says “convert timeout to ms” | Skill/docs generally honest; design/plan migration footguns remain; legacy budget/sandbox warning codes missing (**F6**) |
| 7 Gates / ledger / rehearsal | **partial** / **contradicted** (author green rehearsal) | Tip base gate green; ledger checker exit 0; overlap tooling | Tip gate reproduced author counts; **F4** same_file gap; controlled rehearsal **failed** ledger soak (**F8**); brands not reached |

---

## 5. Field-capability verdict

| Field / property | `hermes-legacy` | `archon-2026-07` | Verdict |
|---|---|---|---|
| Absent language declaration | defaults legacy | n/a | **delivered** |
| Explicit `language_compatibility` | warn `legacy_language_profile` | required for Archon | **delivered** |
| Unknown companion fields | reject | reject | **delivered** |
| Unknown Archon top-level | warn (nonblocking) into options | block | **delivered** (asymmetric by design) |
| `nodes[].timeout` | warn + **runtime seconds** | findings block; **CLI still executes with legacy seconds** | **accidentally active** via F1 |
| `nodes[].retry` | warn on `max_attempts` | findings block | **diagnostic-only/deferred** (CLI bypass same class as F1) |
| `nodes[].output_format` | warn; post-exec validation exists in AI executor | findings block | **accidentally active risk** via F1 + existing post-validation path |
| `nodes[].output_type` | warn (not published) | findings block | **diagnostic-only/deferred** (CLI bypass class) |
| `nodes[].maxBudgetUsd` | silent in language findings; provider matrix can block | findings block | **diagnostic-only/deferred** (+ F6) |
| `sandbox` (node/top-level) | same as budget | findings block | **diagnostic-only/deferred** (+ F6) |
| `loop_group` | unknown field reject | unknown field reject | **delivered** (not silently accepted) |
| Timeout/retry unit reinterpretation | not reinterpreted as Archon | not implemented | **deferred** (correct) |
| Structured output repair / typed artifacts | not Phase 1 | not Phase 1 | **deferred** |
| Loops/includes/model aliases/cost budgets/sandbox portability | not Phase 1 | not Phase 1 | **deferred** |
| Semantic fingerprint / package pin | delivered | delivered | **delivered** |
| Discovery cache + companion | delivered | delivered | **delivered** |
| Sealed resource bytes / MCP private closure | delivered on execute path | delivered | **delivered** (spot-check path traversal rejects) |
| Desktop language status | read-only projection | read-only | **delivered** |
| Prompt-cache / system prompt mutation | no global mutation found | shared+systemPrompt blocked in compat | **delivered** (session fingerprint gap = F5) |

---

## 6. Concrete reproductions (highest risk)

### F1 — Archon deferred field executes via CLI

```bash
export PYTHONPATH=/tmp/wf-lang-adv-tip
export HERMES_HOME=$(mktemp -d)
mkdir -p "$HERMES_HOME/workflows"
cat > "$HERMES_HOME/workflows/archon-sleep.yaml" <<'EOF'
name: archon-sleep
description: prove timeout effect under archon
nodes:
  - id: nap
    bash: sleep 3
    timeout: 1
EOF
cat > "$HERMES_HOME/workflows/archon-sleep.hermes.yaml" <<'EOF'
language_compatibility: archon-2026-07
execution_environment: trusted_local
EOF
# doctor → runnable false, code archon_timeout_semantics_unavailable
# trust with package digest → ok true
# run --foreground → ok true, nap attempt error_code=timeout, health=retry_wait
# language.effective_profile remains archon-2026-07
```

Observed (tip): `ok true`, `health retry_wait`, attempt `error_code: timeout`, message `bash node exceeded its timeout`.

Observed (merge @ `cf470f332`): Archon+`timeout:5` bash echo → doctor non-runnable → trust ok → run `succeeded` with Archon language snapshot and artifact.

### F2 — exit code drop

```bash
python -m hermes_cli.main workflow doctor archon-timeout --json
echo $?   # observed 0 while JSON ok=false / blocking_doctor_findings
```

`hermes_cli.main.main()` returned `None` after printing the error envelope.

---

## 7. What was verified safe (and how)

| Dimension | Why it resisted attack |
|---|---|
| **Legacy default** | Absent companion → `hermes-legacy`; doctor emits `legacy_language_profile` without rewriting definitions. |
| **Typed canonicalization** | `_json_safe` wraps bool/int/float/string/null/NaN/inf; direct bool/int/float/str probes produced distinct digests; no collisions observed. |
| **Path independence** | Identical package bytes under two temp roots → equal `normalized_definition_digest` and package digest. |
| **Companion as metadata** | Sidecar forbids trust/topology keys; unknown sidecar fields fail closed; no process spawn from companion parse. |
| **API admission** | `api_admission.py` raises `workflow_compatibility_blocked` when `not compatibility.runnable`. |
| **Desktop** | React only displays `language` / gates on `compatibility.runnable`; no YAML parse / normalize / schedule authority under `apps/desktop/src/app/workflows`. |
| **Sealed execute auth** | Scheduler verifies projection digests, language snapshot, tree digest, re-parse from authenticated bytes. Live definition mutation fails closed on advance. |
| **Historical / missing language** | New-format missing language fails; Archon without snapshot fails; legacy historical seals require authenticated bytes before parse (code trace + tip tests). |
| **MCP local default-deny** | Spot-check: `..`, absolute paths, undeclared sealed keys rejected by `ResourceResolver` / trust containment. Materializer overwrites forged authority keys. |
| **Shared budget** | `WorkflowResourceReadBudget` enforces caps; concurrent admissions use separate budgets (inspection). |
| **Schema CLI purity** | Fresh temp home: schema JSON emitted; **zero** new files under `HERMES_HOME`. |
| **loop_group** | Not present in generated Archon schema payload; unknown node fields rejected by loader. |
| **Prompt-cache sacredness** | No global system-prompt mutation introduced; `systemPrompt`+`shared` blocked in compat. |
| **Tip vs merge language surface** | `git diff cf470f332 de8a8082f -- plugins/workflow` empty; merge integration risk is concurrent non-workflow parent, not language drift. |
| **Required tip tests** | 13 files / **658 passed** / 0 failed. |
| **Tip base gate** | **1451** backend + **1** installed distribution + Desktop gate **113/113**; seal `TESTED_BASE_SHA=de8a8082f`. |
| **Tip full Desktop** | **2975 passed** / 3 skipped; `tsc` all configs clean. (Author claimed 3006 on merged tree — count differs; tip suite measured here.) |
| **Merge subset** | language/admission/catalog/detail: **284 passed** / 0 failed @ `cf470f332`. |
| **Ledger checker** | `check_upstream_customizations.py --manifest workflow-orchestration.yaml` exit **0** on tip. |

---

## 8. Verification ledger

| Command / activity | Commit | Result | Evidence kind |
|---|---|---|---|
| `git cat-file` / merge parents / diff stat+check | range + `cf470f332` | match prompt (83 / +14758/−663; parents OK) | execution |
| Tip required workflow tests (`run_tests.sh` 13 files) | `de8a8082f` | 658 passed / 0 failed | execution |
| Tip `test_workflow_merge_gate.sh --phase base` | `de8a8082f` | 1451 + 1 installed + 113 desktop; seal tip | execution |
| Tip `npm test` (full desktop) | `de8a8082f` | 2975 passed / 3 skipped | execution |
| Tip `npm run typecheck` | `de8a8082f` | pass | execution |
| Tip ledger checker | `de8a8082f` | exit 0 | execution |
| Tip `workflow schema --profile archon-2026-07 --json` | `de8a8082f` | 6 blocking codes; no HERMES_HOME writes | execution |
| F1 CLI bypass + timeout active | tip + merge | reproduced | execution |
| F2 exit-code drop via `hermes_cli.main` | tip | reproduced | execution |
| Path independence / typed digests | tip | no collisions; digests equal across roots | execution |
| Merge subset tests | `cf470f332` | 284 passed | execution |
| Upstream overlap report `baseline..main` | tip tooling | 95 overlaps; 7 require decisions; language pinning=`same_file` | execution |
| Controlled upstream rehearsal (`main` × tip × otto × loop24) | tip base-ref `de8a8082f` | merge+base-gate passed; ledger invariants **failed** (soak lock timeout); brands skipped; exit 9; refs unchanged | execution |
| Native Windows FS/subprocess races | n/a | **unavailable** on this host | skip |
| Live Archon website vs July 2026 local contract | n/a | not used to redefine `archon-2026-07`; local docs/skill treated as contract | inspection |
| Mutation of production guards inside disposable tree | limited | not performed on every security test; F1 proven by absence of runnable gate | partial |

### Rehearsal result (final)

Commands recorded in `/tmp/workflow-language-adversarial-review-evidence/commands.tsv`:

| Command | Result | Duration (ms) |
|---|---|---:|
| `merge-upstream-into-base` | passed | 35 |
| `base-invariant-gate` | passed | 514567 |
| `ledger-declared-invariants` | **failed** | 368641 |

- Explicit `--decision <id>=preserve` for all 7 required overlaps (OTTO-owned surfaces; not reused from a prior evidence blob).
- Language pinning / schema / desktop / regression entries were `same_file` (**F4**).
- Failure: `WorkflowLockTimeout` in `test_hundred_fast_cycles_release_every_claim_and_scheduler_thread`.
- Brand otto/loop24 phases **did not run**.
- Protected refs before/after: **identical** (59/59, delta 0). Message: `declared ledger invariant failed; no refs were advanced`.
- Author claim of rehearsal “332 evidence / 0 failures” is **not verified** and is inconsistent with this run.

---

## 9. Required remediation (ordered)

1. **F1 (blocker):** In `_cmd_trust` and `_cmd_run`, refuse when `not assess_compatibility(...).runnable` (mirror API). Prefer also refusing `prepare_run_snapshot` / `start_run` for non-runnable packages so all local entrypoints share one gate. Add CLI regression covering Archon+`timeout`/`output_format`/`retry`/`maxBudgetUsd`/`sandbox`.
2. **F2 (blocker for automation):** Propagate plugin command exit codes from `hermes_cli.main` (`sys.exit` on returned int). Add process-level doctor blocking test.
3. **F3:** Resume must derive `always_run` from authenticated sealed definition bytes.
4. **F4:** Tighten overlap policy for language/admission/MCP/`same_file` security seams; exact-own normalizer version symbols; stop hardcoding merge-evidence ancestry booleans.
5. **F8:** Re-run controlled rehearsal to green after soak/lock diagnosis; brand phases must execute before citing rehearsal success.
6. **F5/F6/F7:** Session fingerprint completeness; legacy budget/sandbox warning honesty; freeze_value key typing — before relying on authoring docs as complete.

---

## 10. Residual risks / unverified paths

- **Native Windows** hardlink/case-fold/locked-file MCP cleanup and installer path races: not executed here.
- **Full controlled rehearsal** brand phases: not reached after ledger soak failure (**F8**).
- **Old Desktop ↔ new backend** skew: reasoned from models (`extra=forbid`, runnable gating); not a live mixed-version install test.
- **Installed distribution beyond e2e gate’s 1 test:** tip installed e2e passed; broader package layouts not exhaustively attacked.
- **Upstream `same_file` silent loss** on `scheduler.py` / `store.py` / `trust.py` / `resources.py` remains the dominant merge residual even when gates are green.
- **Concurrent shared-checkout dirty prompt file** and local `base` ahead of `origin/base` by 58 commits: unrelated to feature tip contents; refs were snapshotted and not mutated by this review’s shared checkout operations.

---

## 11. Author-claim reconciliation

| Claim | Independent result |
|---|---|
| Tip base gate 1451 / installed 1/1 / Desktop 113 @ `de8a8082f` | **Confirmed** |
| Merged-base same workflow counts @ `cf470f332` | Subset confirmed; full merge gate not re-run end-to-end on merge worktree (tip gate already covers feature tree; merge language diff empty) |
| Merged full Desktop 3006 | Tip full Desktop measured **2975** passed — do not treat 3006 as verified on tip |
| Controlled rehearsal 11 commands / 332 evidence / 0 failures | **Contradicted** on this host: 3 commands recorded; ledger invariants failed (exit 9); brands skipped |
| Prior review: no Critical/Important/Minor | **False.** This review finds **2 HIGH**, **3 MEDIUM**, **3 LOW** |

---

*End of adversarial review.*
