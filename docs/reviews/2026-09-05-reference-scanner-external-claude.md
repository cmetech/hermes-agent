I have completed a thorough independent review. Compiling the final report.

---

# External adversarial code review — Declarative reference-scanner contract

## 1. Reviewer, scope, and read coverage

- **Reviewer / model:** Claude Code, model `claude-opus-4-8[1m]` (Opus 4.8, 1M context). This is the CLI model itself, not a launching subagent.
- **Date:** 2026-09-05
- **Host:** Darwin 25.5.0; `.venv` = CPython 3.11.16 / Unicode 14.0.0 → profile `python-3.11-unicode-14.0.0`.

**Immutable scope verification (all confirmed):**

| Item | Expected | Observed |
| --- | --- | --- |
| HEAD | `5c3efdbe75…` | `5c3efdbe757a6fa83cf85cec8a95ea3ff96bf6ed` ✓ |
| Tree | `6f7f9d70c3…` | `6f7f9d70c3020fa999e65c21dec636c83fd6d5d7` ✓ |
| Parent/Base | `74fed08f91…` | `74fed08f91014ca7fc80ee9ea4427568ec95b04f` ✓ |
| Range | 34 paths, +12015/−206 | `git diff --stat` → 34 files, 12015 insertions, 206 deletions ✓ |
| Candidate code local mods | none | `git diff --name-only <cand> -- plugins/ tests/ pyproject.toml scripts/` → empty ✓ |

Working tree holds only the intentional untracked/staged **docs** (plan, spec, review records) described by the prompt. **No candidate code path is locally modified.** Scope is valid — not `SCOPE ERROR`.

**Read coverage:** `AGENTS.md` (contribution rubric + relevant sections; file is 1839 lines, remainder not germane and not read — stated explicitly); full replacement spec and plan; complete `reference_scanner_contract.py`; the changed regions and scanner functions of `language_schema.py` (incl. lines 336–589 scanner core, 2559–2678 projection, 4605–4815 limits/bounds/contract assembly); full `language_conformance.py` and `schema_cli.py` diffs; full `reference_scanner_observations.py`; the contract test suite; relevant unchanged runtime in `bash_rendering.py` (constants/classifier scalars) and `resources.py` (scalar/variable regexes, renderer surface). I inspected the literal JSON/manifest indirectly via the executed conformance suite (190 cases run against real APIs) rather than byte-reading all 4878 lines; the immutable baseline captures were verified via the executed baseline suite.

## 2. Verdict

**PASS.**

Every locked requirement provable on this Darwin host is proven. The declarative metadata is faithfully bound to the real runtime constants and, where it makes behavioral claims (Bash classification, candidate discovery, container-major traversal), those claims are executed against the live APIs. Runtime scanner source is byte-identical to base. I found **no Critical, Important, or Minor findings**. Two non-blocking observations are recorded; neither is a defect and I decline to inflate them into findings.

Deferred Studio obligations (reader-rejection enforcement, TS differential runs, canvas performance, UTF-16 boundary, index-once instrumentation) are correctly published-only and are **not** claimed proved by Hermes.

## 3. Findings

**None** (no Critical/Important/Minor). Per instructions I did not manufacture findings to meet a quota.

**Non-blocking observations (explicitly not findings):**

- **CLAUDE-OBS-1 — delimiter metadata asserted against hardcoded copies.** `tests/plugins/workflow/test_reference_scanner_contract.py:337-348` compares `boundaries.candidate_end.*.characters` and `complete_reference_suffix.reject_ascii_characters` to hardcoded string literals rather than binding to `language_schema._REFERENCE_CANDIDATE_END` / `_complete_reference_at`. I independently verified the published strings currently match the runtime set exactly (`_REFERENCE_CANDIDATE_END = frozenset(" \t\r\n'\"(){}<>=!&|,;:")` at `language_schema.py:333`; reject set `.[\/-` + `_` + alnum + non-ASCII at `_complete_reference_at`, `language_schema.py:436-446`). No current defect; a future runtime edit could drift undetected. This is test-hardening, not a correctness bug — the scalar pattern/names/grammar rows *are* bound to source, and the Bash rule classifications *are* executed against the classifier.
- **CLAUDE-OBS-2 — tight scanner-section headroom.** The `reference_scanner_v1` section measures 31,879 bytes against its 32,000 bound (121 bytes free); the whole v6 contract is 315,756 against 324,000 usable. Bounds are correctly enforced pre-output (`_require_contract_bounds`, `language_schema.py:4814`) with one-byte-overflow tests. Not a defect; a growth-margin note.

## 4. Requirements matrix

Studio-deferred portions are separated in each row.

| ID | Verdict | Evidence |
| --- | --- | --- |
| **H1** | **PASS** | `test_reference_scanner_baselines.py` executed (10 passed): asserts legacy corpus fmt 1 / 11 cases / 7265 bytes / SHA `c193…f158a3b` and byte-identity of seven historical pairs; archon-6 handled as characterization-delta only. `_contract_publication_limits` keeps reader 2 + old bounds for all non-v6 (probe: v3/4/5 → reader 2, no scanner metadata). |
| **S1** | **PASS** (Studio reader-rejection deferred) | Probe: v6 → reader 3, 11 required sections exactly; v3/4/5 → reader 2, no `reference_scanner_v1`. `_uses_reference_scanner_contract` gates on ARCHON+6 only. `applicability.unsupported_consumer_action = reject-capability-activation` published. Actual reader-side rejection is Studio's, correctly deferred. |
| **S2** | **PASS** | Grammar `node_id`/`path_segment`/`ordinary_reference` bound to `ARCHON_V3_*` constants (`language_schema.py:4776-4781`); candidate-end (text vs bash `$`), `complete_reference_suffix` reject set, marker-suffix and first-char rules verified against `_reference_candidate_end`/`_bash_reference_candidate_end`/`_reference_like_candidate`/`_complete_reference_at`; malformed suffixes incl. `.output[0]`, `.output.01`, `.output\field`, `.outputé` executed via conformance corpus (190 cases passed). |
| **S3** | **PASS** (TS differential deferred) | `test_every_published_bash_example_matches_the_runtime_classifier` runs each published `_bash_rules` example through `classify_bash_reference_spans`, asserting rejected→`BashRenderingError`, admitted→non-empty, literal→empty-no-raise. All 27 state families present and mapped. Runtime `bash_rendering.py` byte-identical to base. |
| **S4** | **PASS** | `observe_structured_path_case` calls `_v3_output_path_impossible` and `resolve_output_reference` directly; conformance suite passed. Static-proof vs runtime-resolution kept as separate API identifiers. |
| **S5** | **PASS** | Probe confirms **root 18 / body 18 / group-control 2**. `test_complete_surface_is_an_inventory_derived_root_body_and_control_projection` expands `_INTERPOLATION_SURFACE_INVENTORY` independently and asserts equality; `test_grouped_surface_describes_container_major_runtime_order` binds the traversal description to the real `iter_interpolation_surface_templates` order with a two-agent/two-hook fixture. `phase6_interpolation_surface` retained as the scoped view — single inventory, no second handwritten list. |
| **S6** | **PASS** (Studio diagnostics deferred) | Phase-4 root leaves (`systemPrompt`, agents description/prompt, all four hook leaves) present and phase4-gated; workflow cases `reference-root-phase4-surfaces-valid` / `-container-order` executed in `test_language_conformance`. |
| **S7** | **PASS** (Studio parity deferred) | Native-by-scope diagnostic map published; competing/ordered failures exercised by `loop-group-competing-previous-current-errors` and `loop-group-scope-diagnostic-ordering` cases (executed). Previous-before-current and body-before-group-control ordering asserted. |
| **S8** | **PASS** (Studio index-once deferred) | Multi-group (`loop-group-multigroup-scope-isolation`) and multi-surface (`loop-group-scope-diagnostic-ordering`) witnesses published/executed. Index-once instrumentation explicitly a Studio row, not claimed by Hermes. |
| **S9** | **PASS** (Studio canvas deferred) | Runtime unchanged (scanner files byte-identical); publication bounds enforced pre-output. 250-node/500-edge and pointer-frame acceptance explicitly Studio-deferred. |
| **S10** | **PASS on this host; cross-interpreter = recorded evidence** | Three profiles published; `profile_id()` asserts `unicodedata.unidata_version`. I executed the corpus only under 3.11/Unicode-14.0.0. The 3.12/3.13 matrix and UTF-16-boundary deferral rely on the recorded delivery evidence, not independently reproduced here (Darwin single-interpreter limitation). |
| **H2** | **PASS** | `git diff` over `bash_rendering.py`, `resources.py`, `conditions.py`, `schema.py` = empty; scanner functions in `language_schema.py` (336–589) fall outside all changed hunks; only metadata/projection/publication + inert `InterpolationSurfaceSpec` annotations changed. |
| **H3** | **PASS** | Publisher only reads/serializes `conformance/reference_scanner_v1.json`; `corpus_digest` computed over payload with itself omitted (`language_conformance.py:1608-1610`); contract digest bound to `workflow_authoring_contract`. Mutation-isolation + scanner-raises-during-publication tests in the executed suite. Finite coverage acknowledged, not sold as universal equivalence. `compute_package_digest` adapter deliberately returns applicable outcome (hex-validity + covered paths) exercising `surrogateescape`, per spec — not a hidden mismatch. |
| **H4** | **PASS (config verified; install matrix = recorded evidence)** | `pyproject.toml` adds `workflow/conformance/*.json` to `plugins` package-data; resource loaded via `importlib.resources.files("plugins.workflow")`. I did not re-run the expensive sdist→wheel→offline-install integration suite (prompt discourages repeating known-pass builds); relying on recorded real-install evidence + config/source inspection. |
| **H5** | **PASS** | `emit_schema_corpus` validates format/section-counts and both canonical+emitted byte bounds **before** `print` (`schema_cli.py:150-166`); contract bounds enforced inside `workflow_authoring_contract` before return (`language_schema.py:4814`). Three new suites registered in base-phase release gate (`scripts/test_workflow_merge_gate.sh:412-414`). Legacy limits preserved. |

## 5. Verification commands, results, and denials

Executed (via sanctioned runner):
- `scripts/run_tests.sh tests/plugins/workflow/test_reference_scanner_contract.py -q` → **22 passed**.
- `scripts/run_tests.sh …test_reference_scanner_baselines.py …conformance.py …test_language_conformance.py …test_language_schema.py …test_cli.py -q` → **1063 passed, 0 failed** (baselines 10, conformance 190, language_conformance 23, language_schema 705, cli 135).
- `git diff` over the four runtime scanner files → empty; function-boundary diff of `language_schema.py` confirms scanner core unchanged.
- Structural probe (`.venv/bin/python`): reader 3; 11 sections; scopes `{root:18, body:18, group-control:2}`; total 315,756 B (<324,000); scanner section 31,879 B (<32,000); v3/4/5 reader 2 + no scanner metadata.
- `.venv/bin/python --version` → 3.11.16; Unicode 14.0.0.

**Sandbox denials (marked unexecuted; controller may validate):**
- Initial structural probe importing `plugins.workflow` — denied once, later succeeded on retry (recorded above).
- Digest-verification probe (H1 legacy SHA + Archon corpus self-exclusion recompute) — **denied**, not executed. Proposed command:
  ```
  .venv/bin/python -c "from hashlib import sha256; from plugins.workflow.language_conformance import workflow_language_conformance as wlc; from plugins.workflow.language_schema import canonical_contract_json as ccj, workflow_authoring_contract as wac; from plugins.workflow.models import WorkflowLanguageProfile as P; lc=wlc(P.HERMES_LEGACY); enc=ccj(lc).encode(); print(lc['format_version'], len(lc['cases']), len(enc), sha256(enc).hexdigest(), 'corpus_digest' in lc); ac=wlc(P.ARCHON_2026_07); pay={k:v for k,v in ac.items() if k!='corpus_digest'}; print(ac['corpus_digest']=='sha256:'+sha256(ccj(pay).encode()).hexdigest(), ac['contract']['contract_digest']==wac(P.ARCHON_2026_07)['contract_digest'])"
  ```
  These exact assertions are already covered by the executed `test_reference_scanner_baselines.py` (H1 digest/bytes) and `test_language_conformance.py` (corpus self-exclusion + contract binding), which passed.

**Not run (deliberate):** full repository suite (prohibited per prompt); installation integration matrix (`-m integration`, expensive/known-pass); 3.12/3.13 interpreter matrix (host has 3.11 only).

## 6. Final candidate-file status (reviewer changed no source)

`git diff --name-only 5c3efdbe75 -- plugins/ tests/ pyproject.toml scripts/` → **empty**; `git status --short` filtered to non-doc paths → **none**. The only working-tree entries remain the pre-existing intentional docs/plan/spec/review records. The test runner produced its own `test_durations.json` cache (runner artifact, not a candidate source path). **No candidate production, test, corpus, fixture, config, or document was modified by this review.**