# Workflow Showcase Desktop Run — Independent Adversarial Review

Date: 2026-07-20
Reviewed range: `origin/base..aaa5daccbe1bed0c1977ff59284be9ab12361b57`
(final HEAD `aaa5daccb`; implementation SHA tested by gates `65e54b784`; the
single post-gate commit `aaa5daccb` was verified docs/ledger-only)
Reviewer: independent adversarial pass (five parallel reviewers over surfaces
[a] verification/loading/caching/degradation, [b] projection/redaction/read-only,
[c] admission/provenance/background-only/policy, [d] Desktop UI/bridge/i18n,
[e] gate reproduction/evidence audit), synthesized by the orchestrating
reviewer. The implementer's own review and verification docs were treated as
claims to falsify, not as priors.

## Verdict

**READY for merge to `base`** — zero Critical, zero High.

Three Mediums and a set of Lows were found (details below); none crosses the
merge bar, none weakens the digest-verification security boundary for the
*shipped* content, and none regresses a previously-verified v3.0.1 contract.
The most significant finding (M-1) is a **latent** boundary blur that is
unreachable with the currently shipped bundle but should be fixed or
explicitly accepted before a future sidecar makes it live. M-2/M-3 are Desktop
degradation-honesty issues in drift/failure branches only.

Every reproducible evidence claim in the implementer's verification doc
reproduced **exactly** (gate numbers included). Two specific sentences in the
implementer's adversarial review are overstated (see "Overstatements").

## What was reproduced vs assessed by reading

Reproduced (measured, real commands on this worktree):

- **Base Python gate: 773 passed, 1 skipped** (64.99s) — the exact 25-file
  pytest selection from `scripts/test_workflow_merge_gate.sh` with the
  script's own env (`HERMES_OFFLINE=1`, blanked API keys). Matches claim.
- **Installed-distribution integration: 1 passed** (4.20s). Matches claim.
- **745/1 baseline verified**: `git archive origin/base` into a scratch tree,
  `pytest --collect-only` on base's own gate selection → 746 collected
  (745 + 1 skip). **+28 delta reconciled to zero residue** (per-file:
  merge-gate structure +1, catalog API +6, detail API +4, showcase
  catalog/loader +16, new middleware E2E +1).
- **Desktop merge selection: 84 passed across 11 files** (2.04s) — run by the
  orchestrating reviewer with the gate script's exact vitest file list
  (`scripts/test_workflow_merge_gate.sh:95-106`). Matches claim exactly.
  (`npm ci` at the worktree root succeeded; full workflow-UI suite
  **117 passed across 16 files**; `npx tsc --noEmit` exit 0.)
- **Digest self-consistency**: `catalog_sha256` and all five package tree
  digests recomputed from the tree — all match the committed `digests.json`;
  the new `approval-gate.yaml`/`approval-gate.hermes.yaml` are covered by the
  `packages/approval-gate` tree digest.
- **Failure injection (surface [a])**: tampered package bytes, extra package
  file, forbidden token, tampered catalog.yaml, leaf/workflow/catalog/ancestor
  symlinks, cache-poison after a warm hit, absent dir, malformed YAML,
  chmod-000 unreadable file, empty dir — all against copies via a
  monkeypatched bundle root. All fail closed as designed.
- **Read-only proof (surface [b])**: independent sha256+size+mtime_ns
  snapshots of HERMES_HOME, the bundle dir, and the workdir before/after
  valid, tampered, and 16-concurrent list/detail requests — all diffs empty;
  trust store gained no record for bundled content.
- **Redaction attack (surface [b])**: secret-bearing default, absolute-path
  description, and hostile mermaid label (`x"]; click n0 href "javascript:…`)
  driven through the real `_catalog_entry`/`show_package`/`sanitize_projection`
  path — `[REDACTED]` in list and detail; mermaid label neutralized by
  `sanitize_topology_label`.
- **Admission attacks (surface [c], real `TestClient` middleware, no mocks)**:
  `RunScheduler.advance` patched to raise (all admissions still 202/ready);
  forged `trigger_source` in body → 422 (`extra="forbid"`); wrong
  `catalog_source` → typed 404/409; double-submit → same run, `existing`;
  cross-source idempotency-key collision → 409 conflict, not merged;
  ai-extensions/scheduling → 409 `showcase_cli_required`,
  laptop-diagnostic → 422 `workflow_inputs_unsupported`, each with **0 run
  rows, 0 staging dirs, 0 cron jobs**; coordinator-down → typed 503, no
  residue; corrupted coordinator sqlite → typed 503, no 500.
- **Scope-creep checks** (see invariant 11) — all measured.
- **Post-gate delta**: `git diff 65e54b784..HEAD` = two review docs + the
  customization-ledger YAML only. Docs-only confirmed.

Assessed by reading only (not reproducible from this worktree):

- The **paired-brand rehearsal** (`--write` → `--check` 8/8 → brand gate for
  OTTO and LOOP24 in detached worktrees at the exact SHA). The described
  procedure is coherent and correct, and the branch's brand-placement facts
  check out (below), but the rehearsal itself was not re-run — **PLAUSIBLE,
  not independently verified**.
- The **real Electron UAT** (run `22e755bac…`) — detailed, consistent with
  the shipped code paths, inherently unreproducible post-hoc. The doc's own
  honesty note (the UAT predates the final two fix commits; middleware E2E and
  gates were rerun at the final SHA) is accurate.

## Per-invariant findings

### 1. Layered digest verification (integrity atomic; incompatibility scenario-local)

**Integrity direction: HOLDS, reproduced.** Every injected integrity failure
(catalog digest, package tree digest, extra file, forbidden token, every
symlink position including package/`packages` ancestors) fails closed and
omits **all** showcase rows while user rows persist. A tamper can never
degrade to an "incompatible" row: `verified_bundled`/`source="showcase"` is
only assignable when a `VerifiedShowcasePackage` exists
(`plugins/workflow/catalog_api.py:515,527`), and every verification failure
raises before any row is projected. The mid-implementation fixes hold:
SR-1 read-once binding (`plugins/workflow/schema.py:1036` parses
caller-authenticated bytes; `plugins/workflow/showcase.py:404-458` pulls from
the sealed budget cache — the exact bytes `_tree_digest` hashed; a cache-key
miss raises `WorkflowResourceCacheMissError` → typed 409, never a re-read),
and SR-3 uniform early symlink refusal (`showcase.py:145-150,226-240`),
both covered by tests in both directions.

**Incompatibility direction: HOLDS for the shipped bundle; one latent
violation — finding M-1.** The compat-finding class (MCP unavailable) is
correctly scenario-local: `ai-extensions` lists as honestly incompatible
while the other four remain visible (reproduced). But the
`execution_environment` class is not:

- **[M-1] [Medium] Per-scenario `execution_environment:
  isolated_backend_required` blanks the ENTIRE bundle in the list path.**
  `plugins/workflow/showcase.py:486-494` (`_verified_distribution_risk` calls
  `preflight_execution(risk, trusted=True)` even when `enforce_runnable=False`)
  inside the per-scenario loop at `showcase.py:553-558`; the raised
  `ShowcaseCatalogError` is swallowed to `verified_showcases = {}` at
  `catalog_api.py:564-567`. Failure scenario: a future (or vendored) sidecar
  legitimately declares `isolated_backend_required`; on hosts without backend
  isolation, `preflight_execution` raises for that one scenario and **all
  five showcases disappear from the catalog** — environment incompatibility
  degrading to bundle-wide suppression, the exact class of confusion this
  feature's mid-implementation fix was supposed to close (it closed only the
  compat-finding half). **Reproduced** with an integrity-valid injected copy
  (sidecar flipped, package digest and `digests.json` correctly re-stamped):
  showcase rows = `[]` instead of 4 visible + 1 incompatible. Medium, not
  High, because it is unreachable with the shipped bundle (all five sidecars
  declare `trusted_local` — verified) and the failure direction is fail-closed
  (hides content; never trusts anything). Fix: treat a per-scenario
  `preflight_execution` raise on the `enforce_runnable=False` listing path as
  scenario-local incompatibility, exactly as compat findings are treated.

### 2. Rootless verified loading — HOLDS, reproduced + traced

The verified loaders (`load_verified_showcase_package(s)`) take only a
`read_budget`/`force_reverify` — no root, record, or path parameter. The
production bundle path derives from `resources.files("plugins.workflow")`
with zero env/config input (grep: no `environ`/`getenv` in `showcase.py`; no
`HERMES_*` influence). The `bundle_root` parameter on the CLI-facing
`load_showcase_catalog` is unreachable from the HTTP surface
(`dashboard/plugin_api.py` → `catalog_api.py` → verified loader, all no-arg).
A copied bundle gets `verified_bundled_provenance=False` and the verified
path refuses it; admission passes `force_reverify=True`
(`plugins/workflow/api_admission.py:133-137`) so no caller-supplied or cached
record is trusted. Detail accepts only `name` (≤128 chars) +
`catalog_source` Literal — no path reaches the loader.

### 3. Background-only — HOLDS, reproduced

Call graph built from `post_runs` → `start_api_run`: every reachable callee
is read/persist-only; `RunScheduler.advance`, `run_showcase`, and
`_advance_until_wait` are invoked only from `plugins/workflow/cli.py`.
`preflight_execution` (`plugins/workflow/trust.py:406`) reads summary fields
only — no checkpoint/cron/MCP side effects; `preflight_showcase` (which
builds tokens) is not on the admission path. Reproduced: `RunScheduler.advance`
patched to raise `AssertionError`; every admission through the real
middleware still returned 202 with the approval node `ready`.

### 4. Admission re-verifies — HOLDS, reproduced

The showcase branch forces a fresh full verified load (bypassing the list
cache — verified at `showcase.py:527-534`), independently re-derives
`compute_package_digest` + `build_risk_summary` and cross-checks both against
the verified record (`api_admission.py:187-193`), enforces
`compatibility.runnable` (`api_admission.py:213`) precisely because the
loader deliberately lists with `enforce_runnable=False`
(`showcase.py:557`), and runs `preflight_execution`. Every failing check
occurs before `prepare_run_snapshot`; the one post-staging raise
(definition-digest mismatch, `api_admission.py:248`) removes staging first.
Environment-incompatible and consent-gated showcases fail closed with typed
reasons and zero persistence (reproduced: 0 run rows, 0 staging, 0 cron).

### 5. Provenance server-derived; idempotency stable — HOLDS, reproduced

`trigger_source`/assurance derive solely from `_verified_operator` reading
authenticated request state; that function is unchanged in the range.
`catalog_source` selects bytes only and never touches `TriggerProvenance`;
`showcase_provenance` is server-set run metadata after verification. Forged
body `trigger_source` → 422; header forgery is inert. The existing-source
golden start-digest fixtures are untouched in the range (verified via
`git diff`). Double-submit of the same showcase → same digest → `existing`
(reproduced); showcase vs same-named user digests differ (metadata +
`showcase:<id>` concurrency key); a reused idempotency key across the two
sources yields a correct 409 conflict, never a merged run.

### 6. CLI-only decisions honest and server-enforced — HOLDS, reproduced

One server-side helper, `workflow_catalog_run_support`
(`plugins/workflow/catalog_api.py:403`), feeds both projection and admission;
eligibility is metadata-derived (`interaction_mode`/offline/`requires_ai`/
`requires_network`/input kinds), not an ID list. Reproduced refusals:
`ai-extensions` and `scheduling` → 409 `workflow_showcase_cli_required`;
`laptop-diagnostic` → 422 `workflow_inputs_unsupported`. Scheduling cannot
misrepresent: refusal precedes snapshot preparation, the response contains no
"scheduled", and `_schedule_showcase`/cron creation is reachable only from
the CLI wrapper (0 cron jobs after the attack). The Desktop side hardcodes
**no** showcase IDs or policy list (grep clean); the only client-local logic
is the pure trust predicate `workflowTrustAllowsRun`
(`apps/desktop/src/app/workflows/catalog-run-policy.ts:3-5`), which fails
closed on unknown trust states; Run gating consumes server-sent
`run_support.supported` at all three authorization points. Related Desktop
findings M-2/M-3/L-7/L-8 below concern *presentation honesty in drift
branches*, not enforcement.

### 7. Read-only catalog/detail — HOLDS, reproduced

Both endpoints `require("read")` (`plugins/workflow/dashboard/plugin_api.py:304,345`).
Independent byte snapshots across valid, tampered, and 16-concurrent
requests: no mutation of run store, trust store, bundle, or HERMES_HOME; no
trust record/action for bundled content ever (trust `_write` callers are
CLI-only, `plugins/workflow/cli.py:1553,1573`). The API path pins
`allow_repair=False` — proven by the tamper experiment (tampered
`digests.json` bytes still byte-identical afterwards, no repair rewrite).
`_coordinator_projection` reads a temp copy with `PRAGMA query_only=ON`.

### 8. Redaction reuse — HOLDS, reproduced

The range touches none of `sanitize.py`/`topology.py`; no second redactor or
mermaid generator exists. Showcase rows flow through the same
`qualify_workflow_catalog_package` → `show_package` → `_complete_projection`
→ `sanitize_projection` path as user rows (`catalog_api.py:500,750`).
Hostile-content attack through the real functions: secrets/paths `[REDACTED]`
in both list and detail; hostile mermaid label neutralized
(`plugins/workflow/topology.py:28-42`). CF-1 list-description path-redaction
confirmed for the combined catalog (user and showcase-shaped rows). The
shipped showcase YAMLs contain no secrets, absolute/home paths, or hostile
labels (grepped + real projections scanned).

### 9. Performance/caching correctness — HOLDS with notes

The process-lifetime cache is keyed by `_bundle_digest` plus a full tree
signature (path, type, dev/inode, size, `mtime_ns`, `ctime_ns`); reproduced:
post-cache tamper invalidates on the next non-forced list and fails closed;
admission's `force_reverify` genuinely bypasses the cache. Failures are not
cached; the cache holds ≤1 entry (`.clear()` before write). Notes: the cache
lock is held across the whole loader body including hits (throughput only,
L-5); showcase bytes DO charge the shared 16 MiB catalog byte budget and
showcase rows displace up to 5 user rows at the 500 cap — bounded,
non-attacker-controllable, spec-sanctioned for rows, but the implementer's
review sentence denying it is wrong (see Overstatements; L-4/I-1).

### 10. Graceful degradation — HOLDS, reproduced

Absent dir, malformed YAML, chmod-000 unreadable file, empty dir → list
returns 200 with user rows and zero showcase rows; no 500. Size bound is
applied at read (`max_file_bytes+1` cap) and file-count bound during
incremental `os.scandir` (the SR-2 fix), before content reads. An
exception-escape hunt on the catalog path found no route to a 500 (all
reachable raises are `ValueError`-family or explicitly caught/converted).
Observability note L-3: tampered and absent bundles are indistinguishable in
the list response (silent omission — per design, but an operator gets no
signal short of a detail probe returning the typed 409).

### 11. No scope creep / no regression — HOLDS, measured

- `_STORE_SCHEMA_VERSION` = 13, unchanged (`plugins/workflow/store.py:130`).
- No new `HERMES_*` env var (only test `monkeypatch.setenv("HERMES_HOME", …)`).
- No workflow imports in generic hosts: `git diff origin/base..HEAD --
  hermes_cli/ gateway/ tools/ agent/ providers/` is **empty**.
- CLI showcase commands and foreground tour: `plugins/workflow/cli.py` has a
  0-line diff; `run_showcase`/approve/reject/preflight/schedule bodies
  untouched. Note: not literally byte-equivalent at the shared-loader level —
  `showcase.py` hardening (digest-bound parsing, bounded enumeration, symlink
  refusal) also applies to CLI loads; it strictly tightens, never loosens,
  and the CLI e2e tests pass.
- Customization ledger (`docs/upstream-customizations/workflow-orchestration.yaml`)
  updated in **every one of the 13 commits**; checker exit 0 at HEAD.
- No weakened tests: every prior exact-equality assertion on user rows is
  preserved verbatim against a showcase-filtered list; the only relaxations
  are three exact-count→membership/superset changes forced by the 5th
  showcase (L-6), honestly disclosed in the verification doc and offset by
  new digest/provenance invariants. The removed docs test asserted the
  feature *didn't* exist. CI change is +1 line (adds the new E2E to the
  native matrix); the new E2E file is pinned into both the merge gate and CI
  by a meta-test.

### 12. Brand-neutral placement — HOLDS (rehearsal claim plausible, unverified)

`git merge-base origin/base HEAD` = `8eb084137` = origin/base exactly. Zero
`otto`/`loop24` hits under `plugins/workflow/showcases/`. No emitter-owned
file touched (`scripts/brand/`, `brands/`, `skin_engine.py`, `pyproject.toml`,
`electron/main.ts`, `brand.config.json`, `intro.tsx`, `hermes_constants.py`,
install scripts: all zero-diff). `apps/desktop/package.json` changed by one
brand-neutral line (adds `catalog-run-policy.test.ts` to `test:workflow-ui`);
no identity key touched. The 8/8 ×2-brand rehearsal was not re-run here
(would require dirtying worktrees) — judged PLAUSIBLE from the described
procedure, which even documents catching a plan bug (bare `generate.mjs` is
check-only).

## Findings register

Severity ordered. "Reproduced" means demonstrated with running code.

| ID | Sev | Surface | Finding | Reproduced |
|---|---|---|---|---|
| M-1 | Medium | [a] | Per-scenario `execution_environment: isolated_backend_required` blanks the entire bundle on the list path (latent; unreachable with shipped sidecars). `showcase.py:486-494,553-558`; `catalog_api.py:564-567` | Yes (injected integrity-valid copy) |
| M-2 | Medium | [d] | Version-skew crash: `run_support` typed required (`types/hermes.ts:152`) and dereferenced unguarded (`catalog.tsx:62`, `review-run-dialog.tsx:431`, `view-workflow-dialog.tsx:106`). Old backend (failed clone fast-forward — a documented failure class) + new shell → TypeError in Workflows render → root error boundary takes down the renderer | No (read; type-level certain) |
| M-3 | Medium | [d] | New typed refusals (`workflow_showcase_cli_required`, `workflow_showcase_verification_failed`, `workflow_catalog_source_invalid`) fall through `admissionError` mapping (`review-run-dialog.tsx:68-100`) to generic "The workflow inputs were not accepted." A tamper refusal is indistinguishable from a typo; fires exactly in the drift/tamper branches where honesty matters. No auto-retry (safe) | No (read) |
| L-1 | Low | [c] | `CoordinatorStore(...).health()` at `api_admission.py:223` unwrapped (unlike `catalog_api.py:657`); a raising store would 500. Pre-existing on origin/base; occurs pre-persistence; could not be triggered (corrupt DB self-healed to typed 503) | Attempted, not triggerable |
| L-2 | Low | [b] | Default-source resolution: with a user workflow named like a showcase, name-only detail/run (no `catalog_source`) deterministically selects the *user* copy; a legacy client that keys by name can view/run the untrusted copy believing it selected the verified row. Desktop always transports source; trust states stay honest; untrusted admission still requires explicit trust | Yes |
| L-3 | Low | [b] | Tampered distribution indistinguishable from absent in the list (silent `verified_showcases = {}`, `catalog_api.py:564-567`); typed 409 only on detail probe. Fail-closed; observability debt | Yes |
| L-4 | Low | [a] | Shared byte budget: first (cache-miss) list can truncate more user rows than a warm refresh near the 16 MiB ceiling; `resource_bytes_read` seeded from `showcase_budget.bytes_read` (`catalog_api.py:594-606`). Bounded, not attacker-controllable | Partially (charges measured: 20,719 cold / 4,741 warm) |
| L-5 | Low | [a] | `_VERIFIED_SHOWCASE_CACHE_LOCK` (`showcase.py:521`) held across the entire loader body including cache hits — every list request serializes on one process-global lock with disk I/O. Throughput only | No (read) |
| L-6 | Low | [e]/[d] | Three exact-count→membership relaxations (`test_showcase_catalog.py`, `test_portable_compatibility_e2e.py`, gate script brand phase `== 4` → membership+digest/provenance) lose the "no unexpected showcase can appear" exact-count property; content remains pinned by digest verification. Disclosed in the verification doc | Yes (diff audit) |
| L-7 | Low | [d] | Run-disable copy keyed on `source === 'showcase'`, not the server-sent `run_support.reason` (`catalog.tsx:62-66` et al.) — a future non-showcase reason renders wrong text; fail-closed in all combinations | No (read) |
| L-8 | Low | [d] | Catalog Run button ignores `compatibility.runnable === false`: an "Incompatible" badge and enabled Run can coexist; downstream (review dialog + admission `workflow_compatibility_blocked`) fails closed, so it is a dead-end click. Untested combination | No (read) |
| L-9 | Low | [e] | `test_showcase_ai_e2e.py` and `test_showcase_evidence.py` gained tests but are collected by no gate/CI list (pre-existing gap). Run manually: green (55 passed with `test_desktop_api.py`) | Yes |
| L-10 | Low | [a] | Verify-then-reread window on the **no-budget CLI** path only (`showcase.py:404-416` → `schema.py:1029` re-reads from disk). The HTTP/admission path is read-once bound. Local same-process race; CLI/local-admin outside the threat model | No (read) |
| I-1 | Info | [b] | Showcase rows reserve 5 of the 500 list slots, displacing user rows at the cap (exactly `wf0495..wf0499` dropped) — spec-sanctioned (design.md:143); recorded so it is not re-filed as a bug | Yes |
| I-2 | Info | [b] | `_catalog_entry` computes `_input_projection` twice per row (`catalog_api.py:518` + inside `:409`) | No |
| I-3 | Info | [b] | Scenario-id vs definition-name divergence (`scheduling` vs `scheduled-check`) is visible but honest; detail by definition name → typed 404 | Yes |
| I-4 | Info | [a] | `showcases/catalog.schema.json` is not digest-covered — but it is dead at runtime (never read by `showcase.py`/`catalog_api.py`), so tampering it changes nothing | Yes (grep) |
| I-5 | Info | [c] | Showcase and user admissions share the `api:<principal>` idempotency namespace and `runs.workflow_name` column; disambiguation via metadata/concurrency key/digest confirmed correct and fail-closed | Yes |
| I-6 | Info | [e] | Verification-doc wording: the second brand rehearsal describes but does not print its `TESTED_BRAND_SHA`; the first block prints the older `dee1904f6` value — a reader could misread. Ledger entries for the two review docs landed one commit after the gated SHA; checker green in both states | Yes (doc audit) |
| I-7 | Info | [d] | i18n clean: exactly 4 new keys in all four locales + `types.ts`; missing keys are compile errors; no brand strings in the diff. Cosmetic capitalization inconsistency ("Verified bundle" vs "trusted") | Yes (tsc) |

## Implementer's own docs: overstatement audit

Every reproducible numeric claim reproduced exactly (base gate 773/1 at the
tested selection; installed-distribution 1; Desktop 84/11 files with the
gate's exact vitest list; 745/1 baseline; +28 delta decomposition; "no
selected test removed"). One early cross-check that suggested the Desktop 84
figure was wrong turned out to be a reviewer's differing file list — the
implementer's number is correct.

Overstated or omitted:

1. **"Compatibility is scenario-local"** (adversarial review, Required
   invariants + STRIDE DoS row) is proven only for the compat-finding class
   (MCP). The `execution_environment` class violates it latently (M-1). The
   narrower sentence naming MCP is accurate; the generalized heading is not.
2. **"Showcase bytes do not consume the user-row truncation allowance"** /
   "showcase bytes do not incorrectly consume the user-catalog aggregate on
   cache hits" — wrong in both readings: showcase rows displace up to 5 user
   rows at the 500 cap (spec-sanctioned, but the review sentence denies it),
   and showcase verification bytes are charged into the shared 16 MiB request
   budget (small on hits, up to the loader budget on misses; L-4/I-1).
   Materially harmless; textually incorrect.
3. **"Zero Critical, High, Medium, or Low open findings"** — this pass found
   3 Mediums and 10 Lows. None is Critical/High and several are latent or
   pre-existing, but "zero open findings" was an overclaim, chiefly because
   the review did not probe (a) `execution_environment` incompatibility on the
   listing path, (b) old-backend/new-shell version skew, or (c) how its own
   new typed refusal codes render in the Desktop error dialog.
4. Minor: the STRIDE "Denial of service — Closed" row does not mention the
   unwrapped `health()` 500 vector (L-1, pre-existing, untriggerable in
   practice).

The verification doc's honesty is otherwise notable: the count→membership
test relaxations, the plan deviation on compatibility projection, the
rehearsal-command contradiction stop, and the Electron-UAT-predates-final-SHA
caveat are all disclosed accurately.

## Disposition

**Merge blockers: none.**

**Fix-or-accept (recommend fixing in v3.0.2 or first fast-follow; each is
small and none touches the security boundary's trust direction):**

- **M-1** — catch per-scenario `preflight_execution` failures on the
  `enforce_runnable=False` listing path and project them as scenario-local
  incompatibility (mirror the compat-finding handling); add the two-direction
  test for the `execution_environment` class.
- **M-2** — make `run_support` optional in `types/hermes.ts` (like
  `compatibility?`) and fail closed on absence at the three deref sites, or
  add a Workflows-tab error boundary.
- **M-3** — map `workflow_showcase_cli_required`,
  `workflow_showcase_verification_failed`, and
  `workflow_catalog_source_invalid` to honest copy in `admissionError`
  (reuse existing i18n patterns; the CLI-required and verification-failed
  cases deserve distinct messages).

**Post-merge backlog (record in v3.0.3 backlog):** L-1 (wrap `health()`),
L-2 (document name-collision default-source semantics for API consumers, or
warn in list when a user row shadows a showcase name), L-3 (log/telemetry
signal distinguishing tampered from absent bundle), L-4/L-5 (budget seeding
and lock-scope tuning), L-6 (consider re-pinning an exact showcase count via
the digest manifest), L-7/L-8 (derive disable copy from `run_support.reason`;
gate catalog Run on `compatibility.runnable` and test the combo), L-9 (add
the two orphaned test files to a gate), L-10 (extend read-once binding to the
no-budget CLI loads), I-2 (dedupe `_input_projection`), I-7 (label casing).

## Review hygiene

No implementation code or refs were modified. All injections ran against
copies (via monkeypatched bundle roots) or were byte-restored; each of the
five reviewers and the orchestrator verified `git status --porcelain` empty
at completion. This report file is the only addition to the tree.
