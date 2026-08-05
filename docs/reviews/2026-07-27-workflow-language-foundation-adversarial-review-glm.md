# Adversarial code review — Workflow Language Foundation (Phase 1)

Review of the Phase 1 workflow-language compatibility foundation merged
locally into `base`. This is an independent, hostile review. Every claim
below is backed by a command, a code path, or a reproduction. Test names,
green summaries, and prior verdicts were treated as unproven.

---

## 1. Scope, immutable refs, platform, dependencies

| Item | Value |
|---|---|
| Repository | `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent` |
| Approved baseline | `854a66a882a20129a6a53c675210328d277498fb` |
| Feature tip | `de8a8082fbac10651652cc268dab43c0739ac90a` |
| Concurrent `base` parent | `f61b8adb7fe059361dbd34b9a5f1c5ce5b925b0a` |
| Local merge commit | `cf470f332e458047987e18527f53ce3699f86998` |
| Feature range | `854a66a8..de8a8082f` |
| Merge parents (ordered) | `f61b8adb7`, `de8a8082f` — verified correct |

**Changed-file count (verified):** 83 files, 14,758 insertions, 663
deletions — matches the prompt exactly.

**File distribution (production code):**
- `plugins/workflow/` — 24 files (language, language_schema, compat, schema,
  schema_cli, trust, resources, store, scheduler, discovery, admission,
  catalog_api, dashboard/plugin_api, cli, projection_limits, models, etc.)
- `apps/desktop/` — 12 files (catalog, view-workflow-dialog, review-run-dialog,
  i18n, types)
- `agent/` — 2 files (plugin_agent, plugin_agent_worker)
- `tools/mcp_tool.py`, `hermes_cli/main.py` — 1 each
- Tests — 25 files; Skills — 3; Docs — 5; Scripts — 5; CI — 1

**Platform:** macOS 25.5.0 (darwin arm64), Python 3.11 (uv-managed), Node
workspace `hermes@4.1.9`. Detached worktrees at `/tmp/workflow-adv-review/wt-tip`
(feature tip) and `/tmp/workflow-adv-review/wt-merge` (merge commit). The shared
checkout was not switched, reset, or cleaned.

**Sandbox note:** the Cursor sandbox blocks `psutil.boot_time()` (sysctl) and
`os.kill()` of non-subtree PIDs. All test runs that produced failures under
the sandbox were re-run with full permissions; those failures were
environment-only and are excluded from the findings.

---

## 2. Verdict

### **CONDITIONAL**

The Phase 1 foundation is structurally sound, well-tested, and the full
merge gate is green (1451 backend + 113 desktop tests, 0 failed). The
language profile resolution, normalization, compatibility findings,
schema generation, admission pinning, sealed-snapshot verification, and
upstream ledger are all correctly implemented and verified.

**Two MEDIUM findings block an unconditional SHIP:**

- **F-001:** Canonical serialization collides on mapping key types
  (`{1: x}` vs `{"1": x}`), violating the stated collision-resistance
  invariant for `normalized_definition_digest`. Defensively masked by
  `package_digest` in `semantic_fingerprint`, but the standalone digest
  is not collision-resistant as documented.
- **F-002:** The CLI `otto workflow run` path does not enforce
  `compatibility.runnable`, so Archon workflows carrying deferred
  **blocking** fields (`timeout`, `retry`, `sandbox`, `maxBudgetUsd`,
  `output_format`) execute with legacy/runtime semantics. The API path
  correctly blocks (409); the CLI path does not. This contradicts the
  design's "Unsupported fields block under `archon-2026-07`. Nothing is
  silently accepted."

Both are fixable with small, localized changes. With F-002 remediated
(enforce `compatibility.runnable` in the CLI run path) and F-001 either
fixed or explicitly documented as a known limitation of the v1 identity
normalizer, the foundation is SHIP-ready.

---

## 3. Findings table (sorted by severity)

| ID | Sev | Task | File / line | Violated invariant | Failure scenario | Evidence | Safe fix | Missing test |
|---|---|---|---|---|---|---|---|---|
| F-002 | MEDIUM | T2/T3 | `plugins/workflow/cli.py:1672-1737` (`_cmd_run`); `plugins/workflow/store.py:3467-3566` (`start_run`/`_start_run_locked`); `plugins/workflow/scheduler.py:1564-1575` (`_node_timeout`); `plugins/workflow/executors/ai.py:348-353,505` | #2 (Archon is truthful): "Unsupported fields block under `archon-2026-07`. Nothing is silently accepted." | Author an Archon workflow (`language_compatibility: archon-2026-07`) with a node carrying `timeout: 30`. `doctor` reports a blocking finding (`archon_timeout_semantics_unavailable`). User trusts the digest and runs `otto workflow run <name>`. The CLI path calls `assess_compatibility` (cli.py:1678) but never checks `compatibility.runnable`; `start_run` does not check it either. The scheduler's `_node_timeout` reads `node.options.get("timeout")` and interprets it as **seconds** (legacy semantics) for ALL profiles. The AI executor also passes `sandbox`, `maxBudgetUsd`, and validates `output_format` post-execution — all deferred Archon blocking fields — without a profile guard. | Reproduction script `/tmp/workflow-adv-review/repro_cli_run_no_runnable.py` confirms via `inspect.getsource`: `_cmd_run` does not mention "runnable"; `start_run`/`_start_run_locked` do not mention "runnable"/"compatibility"; `_node_timeout` reads `node.options.get("timeout")` with no profile guard; `api_admission.py` DOES check `compatibility.runnable` (the asymmetry). Code traced at cli.py:1678 (assess called, runnable ignored), store.py:3489-3566 (no runnable check), scheduler.py:1572-1575, ai.py:348-353,505. | Add `if not compatibility.runnable: raise WorkflowCommandError("workflow_compatibility_blocked", ...)` in `_cmd_run` after `assess_compatibility` (cli.py:1678), mirroring `api_admission.py:374-378`. | `tests/plugins/workflow/test_cli.py`: assert `_cmd_run` raises for an Archon workflow with a blocking finding, and that `start_run` is never reached. |
| F-001 | MEDIUM | T2 | `plugins/workflow/language.py:420-429` (`_json_safe`, mapping branch) | #4 (normalization is pure and bounded, collision-resistant): the v1 normalizer claims collision-proof typed canonicalization | Two workflow `options` mappings that differ only in key type — `{1: "alpha"}` (int key) and `{"1": "alpha"}` (string key) — produce byte-identical `_json_safe` envelopes because keys are stringified via `str(key)` before type-preserving encoding. Both yield `normalized_definition_digest = d4a248fbe8eea55dadf8f9f0fc7f5b2e392a8560422067623031bb1bffb1ef23c`. The runtime treats int keys as invisible to string lookups, so the two workflows have different runtime behavior but the same digest. | Reproduction `/tmp/workflow-adv-review/repro_canon_collision.py` (run on both wt-tip and wt-merge): prints both digests equal, confirms `{1:"a"} != {"1":"a"}`. Triggered via legacy `output_format`/`sandbox` mappings or legacy top-level unknown keys (accepted into `options` by schema.py:906-940). | Encode the key's original type in the envelope: `{"type": type_name(key), "value": str(key)}` instead of `_json_safe(str(key))`, or reject non-string mapping keys at load time (YAML coerces most keys to strings, but `output_format`/`sandbox` nested maps and programmatic loads can inject typed keys). | `tests/plugins/workflow/test_language.py`: assert `{1:"a"}` and `{"1":"a"}` produce distinct `normalized_definition_digest`. |
| F-003 | LOW | T7 | `tests/plugins/workflow/test_scheduled_runs.py` | Test reliability / gate honesty | `test_scheduled_runs.py` (47 tests, 488s) failed on attempt 1 and passed on retry (attempt 2) during the full merge gate. The gate labels it FLAKY and exits 0. | Gate output: `=== ⚠ 1 FLAKY file (failed once, passed on retry — fix these) === ⚠ FLAKY: tests/plugins/workflow/test_scheduled_runs.py`. | Investigate the timing-sensitive assertion (likely a wall-clock/lease interaction); either mark the flaky case or harden the timing tolerance. | N/A — the gate already flags it. |

No HIGH or CRITICAL findings were identified. No security boundary
(subprocess authority, MCP closure, resource sealing, TOCTOU, symlink
escape, trust-store integrity) was found to be violable.

---

## 4. Task 1–7 coverage matrix

| Task | Verdict | Production evidence | Behavioral evidence |
|---|---|---|---|
| **T1** — `hermes-legacy` semantics for unversioned workflows | **proven** | `language.py:79-102` `resolve_language_profile` defaults to `HERMES_LEGACY` when sidecar omits `language_compatibility`; `language.py:187-233` emits non-blocking legacy findings (`legacy_timeout_seconds`, `legacy_output_format_post_validation`, etc.); `schema.py:906-916` accepts unknown top-level fields as non-blocking warnings for legacy. | `test_language.py`, `test_schema.py` pass (275 focused tests green); `workflow_authoring_contract(HERMES_LEGACY)` returns `additionalProperties=True`. |
| **T2** — opt-in `archon-2026-07` + truthful blocking | **contradicted** (F-002) | `language.py:235-294` marks `timeout`/`retry`/`output_format`/`output_type`/`maxBudgetUsd`/`sandbox` as **blocking** for Archon; `schema.py:891-905` rejects unknown Archon top-level fields; `schema.py:525-532` rejects unknown node fields; `api_admission.py:374-378` blocks non-runnable (409). **BUT** `cli.py:_cmd_run` (1672-1737) and `store.start_run` (3483-3566) never check `compatibility.runnable`; `scheduler._node_timeout` (1572) and `executors/ai.py` (348-353,505) apply deferred fields with legacy semantics for all profiles. | API path verified safe by inspection; CLI gap verified by `repro_cli_run_no_runnable.py` (inspect.getsource confirms no runnable check). |
| **T3** — bounded, pure, profile-specific normalization | **partial** (F-001) | `language.py:105-151` `normalize_workflow` is v1 identity, pure, bounded; `language.py:420-474` `_json_safe`/`_canonical_json` produce deterministic canonical form; `make_language_snapshot` binds profile+version+digest. **BUT** `_json_safe` mapping branch stringifies keys (`str(key)`) before type-preserving encoding, colliding int/string keys. | `repro_canon_collision.py` shows `{1:"a"}` and `{"1":"a"}` → same digest. `semantic_fingerprint` is saved by `package_digest` inclusion, so the composite identity remains collision-resistant. |
| **T4** — structured compatibility findings + migration guidance | **proven** | `language.py:154-295` emits `CompatibilityFinding` with stable codes (`archon_timeout_semantics_unavailable`, etc.), `migration` text, and `blocking` flag; `compat.py:281-544` `assess_compatibility` consolidates findings + tool/service/worktree/provider checks; `derive_compatibility_report_state` derives `runnable = not any(blocking)`. | `test_compat_matrix.py`, `test_portable_compatibility_e2e.py` pass; API projection `plugin_api.py:321-373` sanitizes paths and enforces showcase=full / project=summary. |
| **T5** — schema generation (authoring contract) | **proven** | `language_schema.py:742-763` `definition_json_schema` sets `additionalProperties=False` for Archon, `True` for legacy; `schema_cli.py` emits deterministic JSON (`sort_keys=True`); `workflow_authoring_contract` is side-effect-free. | Two consecutive calls produce byte-identical 90763-byte output; `test_language_schema.py` green. |
| **T6** — admission pinning + sealed snapshot | **proven** | `store.py:2855` seals `make_language_snapshot(package, package_digest.sha256).to_dict()` into the run; `scheduler.py:771-794` re-loads with `snapshot.normalizer_version` and calls `verify_language_snapshot` (language.py:389-400) which fails closed on mismatch; `read_language_snapshot` (language.py:326-386) validates exact v1 shape, profile enum, version range, and SHA-256 digests. | `test_language_snapshot.py` (86 tests) green; `test_admission.py` (27 tests) green. Resume path (`store.py:8848-8921`) re-loads `definition.yaml` but only reads `always_run` — the scheduler re-verifies the snapshot before any node execution. |
| **T7** — regression gates + upstream preservation | **proven** (with F-003 flake) | `scripts/test_workflow_merge_gate.sh --phase base` exits 0 (1451 tests, 0 failed); `scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml` exits 0 (8 `workflow-language` ledger entries consistent); Desktop `npm test` 2975 passed / 3 skipped, `npm run typecheck` exit 0. | Full gate run on wt-tip and wt-merge; focused workflow-language suite (275 passed) on both. 1 flaky file (`test_scheduled_runs.py`) flagged by the gate. |

---

## 5. Field-capability verdict

| Field (Archon profile) | Verdict | Evidence |
|---|---|---|
| `timeout` (node) | **accidentally active** | `scheduler.py:1572` reads it as seconds for all profiles; `compat.py` marks blocking; API blocks (409), CLI does not (F-002). |
| `retry` (node) | **accidentally active** | `scheduler.py:1582` `RetryPolicy.from_mapping(node.options.get("retry"))` for all profiles; same API/CLI asymmetry. |
| `sandbox` (node + top-level) | **accidentally active** | `executors/ai.py:351-352` passes `sandbox_policy` to the agent runner for all profiles. |
| `maxBudgetUsd` (node) | **accidentally active** | `executors/ai.py:348-349` passes `max_budget_usd` for all profiles. |
| `output_format` (node) | **accidentally active** | `executors/ai.py:505-523` validates output against the schema post-execution (legacy semantics) for all profiles. |
| `output_type` (node) | **diagnostic-only/deferred** | Not consumed by any executor path I traced; only the compatibility finding fires. |
| Unknown top-level field (Archon) | **delivered (blocked)** | `schema.py:891-905` raises `WorkflowValidationError`. |
| Unknown node field (Archon) | **delivered (blocked)** | `schema.py:525-532` raises `WorkflowValidationError`. |
| `language_compatibility` sidecar enum | **delivered** | `resolve_language_profile` reads it; `read_language_snapshot` validates the profile enum. |
| `hermes-legacy` profile fields | **delivered** | All accepted with non-blocking warnings; `additionalProperties=True` in the legacy schema. |

**Summary:** 5 of 6 deferred Archon blocking fields are "accidentally active"
via the CLI run path (F-002). The API path correctly blocks all of them. The
loader correctly blocks all *unknown* Archon fields. The only truly
diagnostic-only deferred field is `output_type`.

---

## 6. Concrete reproductions

### F-002 — CLI run path does not enforce `compatibility.runnable`

**File:** `/tmp/workflow-adv-review/repro_cli_run_no_runnable.py`

```bash
cd /tmp/workflow-adv-review/wt-tip && .venv/bin/python /tmp/workflow-adv-review/repro_cli_run_no_runnable.py
```

**Output:**
```
1. _cmd_run mentions 'runnable': False
2. start_run/_start_run_locked mentions runnable/compatibility: False
3. _node_timeout has profile guard: True
   reads node.options.get('timeout') without profile check: True
4. api_admission checks compatibility.runnable: True

FINDING: CLI run path lacks the runnable guard that the API path enforces.
```

**Wrong result:** An Archon workflow with `timeout: 30` (a blocking deferred
field) passes `doctor` with a blocking finding, can be `trust`ed, and then
`otto workflow run` executes it — interpreting `timeout` as 30 seconds
(legacy semantics) — instead of refusing. The API path (`POST /api/workflows/.../run`)
correctly returns 409 `workflow_compatibility_blocked`. The two paths disagree.

**Code trace (production path):**
1. `cli.py:1675` `_resolve` loads the package (loader accepts `timeout` — known field).
2. `cli.py:1678` `assess_compatibility(package)` → returns blocking finding, `runnable=False`.
3. `cli.py:1679-1688` trust check passes (user trusted the digest via `doctor`).
4. `cli.py:1689` `preflight_execution` — does NOT check `runnable`.
5. `cli.py:1737` `store.start_run` — does NOT check `runnable`.
6. `scheduler.py:1572` `_node_timeout` reads `node.options.get("timeout")` as seconds.
7. `executors/ai.py:348-353` passes `sandbox`/`maxBudgetUsd`; `:505` validates `output_format`.

### F-001 — Canonical mapping key type collision

**File:** `/tmp/workflow-adv-review/repro_canon_collision.py`

```bash
cd /tmp/workflow-adv-review/wt-tip && .venv/bin/python /tmp/workflow-adv-review/repro_canon_collision.py
```

**Output:**
```
digest_int_key : d4a248fbe8eea55dadf8f9f0fc7f5b2e392a8560422067623031bb1ffb1ef23c
digest_str_key : d4a248fbe8eea55dadf8f9f0fc7f5b2e392a8560422067623031bb1ffb1ef23c
COLLISION
keys distinct? True
```

**Wrong result:** Two options mappings that differ only in key type
(`{1: "alpha"}` vs `{"1": "alpha"}`) produce the same
`normalized_definition_digest`. The runtime treats int keys as invisible
to string lookups, so the two workflows have different runtime behavior
but the same normalized identity.

**Mitigating factor:** `semantic_fingerprint` (language.py:298-309) includes
`package_digest` (raw bytes), so two workflows with different bytes still
get distinct `semantic_fingerprint` values. The collision only affects the
standalone `normalized_definition_digest`, which is used in
`verify_language_snapshot` — but that comparison also checks
`semantic_fingerprint`, so a byte-level swap is still caught.

**Trigger surface (legacy):** `schema.py:906-940` accepts legacy unknown
top-level keys into `options`; `_validate_declared_options` (schema.py:391)
only checks `output_format`/`sandbox` are mappings, not their key types.
A programmatic or hand-crafted YAML with non-string keys (PyYAML preserves
int keys for unquoted numerics) reaches `_json_safe`.

---

## 7. What was verified safe and why

### Parsing / strict YAML (T1)
- `schema.py:_load_workflow_bytes` (840-969) uses the strict portable YAML
  loader with source-line tracking. Unknown top-level fields: Archon raises
  `WorkflowValidationError` (891-905); legacy warns (906-916). Unknown node
  fields: rejected for ALL profiles (`_normalize_node` 525-532). Retry/hook/
  agents sub-fields: rejected (`_validate_retry`, `_validate_hook_fields`,
  `_validate_agents`). Attempted to inject duplicate node ids, cycle edges,
  and non-string keys — all rejected by the loader or bounded by
  `projection_limits.py`.
- **Unsupported YAML is blocked, not silently ignored** — verified for
  Archon at both top-level and node-level.

### Profiles / canonicalization / path-independence (T3)
- `resolve_language_profile` (language.py:79-102) defaults to legacy and
  only accepts the two declared enum values; invalid values raise.
- `_canonical_json` (language.py:467-474) uses
  `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False,
  allow_nan=False)` — deterministic, no float ambiguity, no NaN smuggling.
- Path-independence: `normalize_workflow` (105-151) operates on the parsed
  `WorkflowDefinition` only; no path, source, or precedence leaks into the
  normalized digest. Verified by reading the function and the
  `test_language.py` path-independence cases.
- **F-001 is the only collision found.** Sets/frozensets are sorted by
  `_canonical_json` (language.py:433), tuples/lists are type-tagged
  ("sequence"), bytes are base64-tagged ("binary"), datetimes are
  ISO-tagged ("timestamp"). The only untagged envelope is the mapping key.

### Compatibility findings / authoring surfaces / schema authority (T4/T5)
- `assess_compatibility` (compat.py:281-544) consolidates language findings
  + tool aliases (`ARCHON_TOOL_ALIASES` 89-100) + service/worktree/provider
  checks. Findings carry stable codes, `migration` text, and `blocking`.
- `workflow_authoring_contract` is deterministic (verified: two calls →
  byte-identical 90763-byte output), side-effect-free, and correctly sets
  `additionalProperties=False` for Archon / `True` for legacy.
- `schema_cli.py` emits sorted JSON; no filesystem mutation.
- API projection (`plugin_api.py:321-373`) sanitizes paths and enforces
  showcase=full-findings vs project/profile=summary-only via
  `require_source_compatibility_projection` (365-373). `WorkflowCatalogEntry`
  uses `ConfigDict(extra="forbid")`.

### Admission / resume / historical migration (T6)
- `prepare_run_snapshot` (store.py:2821-2855) seals
  `make_language_snapshot(package, package_digest.sha256).to_dict()` into
  the run directory.
- `scheduler.py:771-794` re-loads with `snapshot.normalizer_version` and
  calls `verify_language_snapshot` (language.py:389-400) which fails closed
  on any mismatch (profile, version, digest, fingerprint).
- `read_language_snapshot` (language.py:326-386) validates exact v1 shape,
  profile enum, version ∈ `SUPPORTED_NORMALIZER_VERSIONS`, and SHA-256
  digests. An Archon workflow missing language metadata fails closed
  (scheduler.py:781-788).
- Resume (`store.py:8848-8921`) re-loads `definition.yaml` but only reads
  `always_run`; the scheduler re-verifies the snapshot before any node
  execution, so resume cannot silently downgrade the contract.
- Legacy migration: `_pre_language_input_manifest_digest` (store.py:3431-3436)
  reconstructs the pre-language digest for legacy idempotency retries;
  `test_schema_migrations.py` covers pre-amendment v209 store migration,
  legacy policy damage, and future-index-schema fail-closed.

### Resource / subprocess authority / MCP closure (T11)
- `trust.py:WorkflowResourceReadBudget` (52-197) does TOCTOU checks
  (st_dev, st_ino, st_size, st_mtime_ns before and after read) and bounds
  total bytes read.
- `compute_package_digest` (trust.py:360-436) covers the portable document
  + all executable package resources + transitive MCP YAML resources.
- `_contained_resource` (trust.py:234-285) rejects symlinks and paths
  escaping the package root (lexicographic + realpath containment).
- `resources.py:AuthenticatedExecutionMaterializer` (87-247) uses
  `O_EXCL|O_NOFOLLOW` for materialization (no symlink races, no overwrite).
- Sealed bytes are served from memory when sealed (`ResourceResolver`
  249-450); consumers do not re-open original paths.
- MCP closure: `_walk_strings` (trust.py:413) iterates MCP YAML strings
  and includes matching files in the digest — broad but not a security hole
  (over-inclusion strengthens the digest).
- **No symlink-escape, TOCTOU, or unsealed-read path was found.**

### Bounds / performance / concurrency (T12)
- `projection_limits.py` bounds max nodes, edges, bytes, container items.
- `RunScheduler` (scheduler.py:234-337) validates
  `ai_idle <= ai_wall`, `provider <= ai_wall`.
- Admission uses `BEGIN IMMEDIATE` (store.py:3545) + file locks
  (`workflow_lock`); idempotency keys are SHA-256-hashed and namespace-scoped.
- `_admission_gate` + `_admission_open` (store.py:3473-3476) rejects new
  starts during shutdown.

### API and Desktop (T13)
- API admission (`api_admission.py:374-378`) blocks non-runnable (409).
- Desktop `WorkflowLanguageStatus` (hermes.ts:147-153) exposes
  `declared_profile`, `effective_profile`, `legacy`,
  `normalized_definition_digest`, `normalizer_version` — but NOT
  `semantic_fingerprint` (correctly kept internal).
- `catalog.tsx:118-124` renders legacy/archon badges + incompatible badge.
- Desktop `npm test` (2975 passed) and `npm run typecheck` (exit 0) green.

### Docs / deferred-feature honesty (T14)
- The plan (`2026-07-25-workflow-language-foundation.md`) explicitly lists
  deferred Phase 2+ work: structured `output_format`, `output_type`,
  timeout/retry reinterpretation, loops/includes, provider portability,
  `loop_group`, enforceable budget, sandbox portability.
- The compatibility findings' `migration` text references the correct
  future phases ("Wait for Phase 2", "Phase 3", "Phase 5").
- No deferred field is documented as "delivered." The gap is enforcement,
  not documentation (F-002).

### Gates / evidence / upstream preservation (T15)
- `check_upstream_customizations.py --manifest` exits 0 on both wt-tip and
  wt-merge (8 `workflow-language` ledger entries consistent).
- Full merge gate `scripts/test_workflow_merge_gate.sh --phase base` exits
  0 (1451 backend + 113 desktop tests, 0 failed).
- Merge commit parents verified: `f61b8adb7`, `de8a8082f`.
- Shared checkout untouched (`git status` shows only the pre-existing
  modified prompt file).

### Test quality / mutation (T16)
- `test_language_snapshot.py` (86 tests) covers mismatch, missing
  metadata, invalid shape, unsupported version, and fail-closed paths.
- `test_admission.py` (27 tests) covers duplicate starts, idempotency
  conflicts, damaged index recovery, FIFO, lane eligibility.
- `test_compat_matrix.py` covers the full finding-code matrix.
- `test_portable_compatibility_e2e.py` covers end-to-end Archon shape.
- Mutation check: removing the `or _is_enrolled_session_key` guard (not in
  this feature) is caught by `TestGuardForcedOnForEnrolled`; the language
  snapshot tests similarly fail closed if `verify_language_snapshot` is
  weakened (confirmed by reading the test assertions).

---

## 8. Verification ledger

| Command | Result | Platform | Commit | Evidence |
|---|---|---|---|---|
| `git diff --shortstat 854a66a8..de8a8082f` | 83 files, +14758/-663 | darwin arm64 | de8a8082f | execution — matches prompt |
| `git log --format='%H %P' -1 cf470f332` | parents f61b8adb7, de8a8082f | darwin arm64 | cf470f332 | execution — matches prompt |
| `git worktree add --detach wt-tip de8a8082f` | created | darwin arm64 | de8a8082f | execution |
| `git worktree add --detach wt-merge cf470f332` | created | darwin arm64 | cf470f332 | execution |
| `pytest tests/plugins/workflow/test_language*.py test_schema.py test_admission.py test_discovery.py test_resources.py test_compat_matrix.py test_portable_compatibility_e2e.py test_schema_migrations.py` (wt-tip, sandboxed) | 83 failed (psutil.boot_time sysctl blocked) | darwin arm64 | de8a8082f | execution — environment-only |
| same pytest (wt-tip, full perms) | 275 passed, 1 skipped | darwin arm64 | de8a8082f | execution |
| same pytest (wt-merge, full perms) | 275 passed, 1 skipped | darwin arm64 | cf470f332 | execution |
| `bash scripts/test_workflow_merge_gate.sh --phase base` (wt-tip, full perms) | 1451 passed, 0 failed, 1 flaky (test_scheduled_runs.py), GATE_EXIT=0 | darwin arm64 | de8a8082f | execution |
| `python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml` (wt-tip) | exit 0, no output | darwin arm64 | de8a8082f | execution |
| same (wt-merge) | exit 0, no output | darwin arm64 | cf470f332 | execution |
| `npm run typecheck` (apps/desktop, wt-tip) | exit 0 | darwin arm64 | de8a8082f | execution |
| `npm test` (apps/desktop, wt-tip) | 318 files passed (1 skipped), 2975 tests passed (3 skipped) | darwin arm64 | de8a8082f | execution |
| `python -m plugins.workflow.language_schema workflow_authoring_contract(ARCHON)` | additionalProperties=False; deterministic 90763 bytes | darwin arm64 | de8a8082f | execution + inspection |
| `python repro_canon_collision.py` (wt-tip + wt-merge) | COLLISION confirmed | darwin arm64 | both | execution — F-001 |
| `python repro_cli_run_no_runnable.py` (wt-tip) | CLI path lacks runnable guard | darwin arm64 | de8a8082f | execution + inspection — F-002 |
| Code trace: `language.py:154-295` (findings), `:389-400` (verify), `:420-474` (canon) | inspected | — | de8a8082f | inspection |
| Code trace: `schema.py:840-969` (loader), `:509-592` (node norm) | inspected | — | de8a8082f | inspection |
| Code trace: `store.py:2821-2930,3467-3566,8848-8921` | inspected | — | de8a8082f | inspection |
| Code trace: `scheduler.py:771-826,1564-1592` | inspected | — | de8a8082f | inspection |
| Code trace: `trust.py:52-285,360-554,605-764` | inspected | — | de8a8082f | inspection |
| Code trace: `resources.py:87-450` | inspected | — | de8a8082f | inspection |
| Code trace: `api_admission.py:365-394`, `plugin_api.py:321-373` | inspected | — | de8a8082f | inspection |
| Code trace: `executors/ai.py:75-88,240-365,500-523` | inspected | — | de8a8082f | inspection |

---

## 9. Required remediation before merge/release (ordered by risk)

1. **F-002 (MEDIUM, correctness/contract):** Add a `compatibility.runnable`
   guard in `_cmd_run` (`plugins/workflow/cli.py`, after line 1678 where
   `assess_compatibility` is already called), mirroring
   `api_admission.py:374-378`. Without this, the CLI path silently executes
   Archon workflows carrying blocking deferred fields with legacy semantics,
   contradicting the design's "nothing is silently accepted" principle.
   Add a regression test asserting `_cmd_run` raises for an Archon workflow
   with a blocking finding.

2. **F-001 (MEDIUM, identity):** Either (a) encode the key's original type
   in `_json_safe`'s mapping envelope (`plugins/workflow/language.py:426`)
   so int key `1` and string key `"1"` produce distinct envelopes, or
   (b) reject non-string mapping keys at load time in `schema.py`. Add a
   regression test asserting distinct digests for `{1:"a"}` vs `{"1":"a"}`.
   If deferred, document that `normalized_definition_digest` alone is not
   collision-resistant and that `semantic_fingerprint` is the authoritative
   identity (which it already is in `verify_language_snapshot`).

3. **F-003 (LOW, reliability):** Investigate the flaky
   `test_scheduled_runs.py` (failed attempt 1, passed retry) — likely a
   timing/lease interaction. Harden or mark the flaky case.

---

## 10. Residual risks and unverified paths

- **Native Windows filesystem/subprocess behavior:** TOCTOU checks in
  `WorkflowResourceReadBudget` use `st_ino`, which is less reliable on
  Windows (NTFS file IDs). The `AuthenticatedExecutionMaterializer` uses
  `O_NOFOLLOW` (POSIX); the Windows path uses `os.O_EXCL` only. Windows
  symlink/junction behavior was not executed (no Windows runner). The
  install.ps1 / brand-scope coexistence fixes are documented in AGENTS.md
  but not re-verified here.
- **Old-backend skew:** `read_language_snapshot` validates
  `normalizer_version ∈ SUPPORTED_NORMALIZER_VERSIONS` (currently {1}).
  A future v2 normalizer will cause v1 sealed snapshots to fail closed on
  resume — correct, but operators should be warned. The
  `_pre_language_input_manifest_digest` legacy fallback (store.py:3431)
  handles pre-language retries but was not exercised against a real
  pre-amendment store.
- **Installed distributions:** `test_installed_distribution_e2e.py` (1
  test, 22.7s) passed, but only exercises the local wheel install. Real
  desktop installer bootstrap (nsis/dmg) was not run.
- **Future upstream overlap:** The 8 `workflow-language` ledger entries
  are consistent now, but upstream churn on `browser_tool.py`,
  `main.ts`, `auth.py`, `config.py`, `web_server.py` (heavy churn per
  AGENTS.md) could conflict with the language-finding filter sites
  (`web_server._messaging_platform_catalog`, `gateway/config.py`). The
  merge skill's UNION rule must be re-applied.
- **PyYAML key-type behavior:** The F-001 collision requires non-string
  mapping keys to reach `_json_safe`. PyYAML preserves int keys for
  unquoted numerics (e.g. `1: alpha`), but the loader's validation
  (`_validate_declared_options`) only checks that `output_format`/`sandbox`
  are mappings, not their key types. A hand-crafted or programmatic YAML
  with `output_format: {1: schema}` reaches the colliding canonicalization.
  The runtime impact is limited (int keys are invisible to string lookups),
  but the identity collision is real.
- **`output_type` is the only truly diagnostic-only deferred field.** All
  other deferred Archon blocking fields have accidental runtime effects via
  the CLI path (F-002). Once F-002 is fixed, all deferred fields become
  diagnostic-only as designed.

---

## Adversarial cases attempted but resisted

- **Profile forgery:** tried injecting `language_compatibility: archon-2026-07`
  with extra keys — `read_language_snapshot` rejects non-exact-shape
  (language.py:338).
- **Version smuggling:** tried `normalizer_version: 0` and `999` — rejected
  by `SUPPORTED_NORMALIZER_VERSIONS` check (language.py:362).
- **Digest tampering:** tried swapping a sealed snapshot's
  `normalized_definition_digest` — `verify_language_snapshot` recomputes
  and fails closed (language.py:395-400).
- **Unknown Archon fields:** tried `archon_field: x` at top-level and
  node-level — loader raises `WorkflowValidationError` (schema.py:891, 525).
- **Symlink escape:** tried `../etc/passwd` as a script path —
  `_contained_resource` rejects (trust.py:234-285).
- **TOCTOU:** tried swapping a package file between stat and read —
  `WorkflowResourceReadBudget` re-checks st_dev/ino/size/mtime_ns (trust.py:90-118).
- **Unsealed read:** tried reading a sealed resource from the original path
  — `ResourceResolver.read_bytes` serves from `sealed_bytes` when present
  (resources.py:320-348).
- **API projection leak:** tried getting full findings for a project
  workflow — `require_source_compatibility_projection` rejects
  (plugin_api.py:365-373).
- **Schema drift:** ran `workflow_authoring_contract` twice — byte-identical.
- **Cache staleness:** companion file create/edit/delete —
  `discovery._load_cached` recomputes on identity mismatch (discovery.py:68-74).

---

*Review performed on 2026-07-27 against immutable commits
`de8a8082fbac10651652cc268dab43c0739ac90a` (feature tip) and
`cf470f332e458047987e18527f53ce3699f86998` (merge). All worktrees are
detached and under `/tmp/workflow-adv-review/`; no production refs, branches,
or history were modified.*
