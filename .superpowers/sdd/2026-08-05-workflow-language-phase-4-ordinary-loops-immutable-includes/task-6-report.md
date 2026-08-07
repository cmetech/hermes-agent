# Task 6 report: sealed dependency snapshots

## Status

DONE_WITH_CONCERNS

Snapshot format 2 now seals and reloads the complete Phase 4 compilation closure,
all admission surfaces carry one immutable compilation, and future scheduled
occurrences recompile and compare the exact recorded catalog closure. The two
concerns are test-process/baseline concerns, not known product defects:

1. The dedicated child-precedence/shadowing and defensive mirror tests were
   added after the revalidation implementation. They are valid permanent
   coverage, but they did not provide a pre-implementation RED.
2. The mandatory surface suite has five pre-existing packaged-schema isolation
   failures. The same temporary-home population failure reproduces at the
   untouched starting commit `8644bd9d8`.

## TDD chronology

Authentic RED evidence:

- `scripts/run_tests.sh tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_language_snapshot.py`
  initially produced **96 passed, 6 failed**. All six format-2 tests failed at
  the missing `compilation` argument to `prepare_run_snapshot()`.
- `scripts/run_tests.sh tests/plugins/workflow/test_phase4_snapshot.py -k compilation_canonicalizes_absent_root_policy`
  produced **0 passed, 1 failed** because the compiler sealed `b""` instead of
  the approved canonical empty policy `b"{}\n"`.
- The focused source-deletion reload test produced **0 passed, 1 failed** because
  the legacy loader applied the root outward-action policy to the expanded graph
  without reconstructing the unbound closure identity.
- `scripts/run_tests.sh tests/plugins/workflow/test_phase4_snapshot.py -k catalog_resolution`
  produced **0 passed, 1 failed** with `ImportError` for the required
  `resolve_workflow_catalog_compilation` interface.
- `scripts/run_tests.sh tests/plugins/workflow/test_showcase_schedule_e2e.py -k materialized_bundle_context`
  produced **0 passed, 1 failed** with `FileNotFoundError`: showcase compilation
  reopened a workflow after the `resources.as_file()`-style materialization
  context had cleaned up. Compilation was moved inside the context lifetime.

Focused GREEN evidence:

- Canonical empty policy: **1 passed, 0 failed**.
- Catalog compilation resolution: **1 passed, 0 failed**.
- Materialized showcase compilation: **1 passed, 0 failed**.
- Child command/script/two-MCP/local-resource reload: **1 passed, 0 failed**.
- Dedicated child-precedence shadowing revalidation: **1 passed, 0 failed**.
- Defensive no-live-read and precedence mirrors: **2 passed, 0 failed**.

The executable-resource and child-shadowing tests were added after their main
implementation slices. Their initial failures were test-fixture issues (the
authoring schema intentionally accepts one MCP reference per node, and command
bodies preserve a trailing newline), not production REDs. They are not counted
as authentic implementation RED evidence.

## Implementation and contract decisions

### Snapshot writer and public identity

- `RunStore.prepare_run_snapshot(..., compilation=...)` accepts only an exact
  explicit-v4 `WorkflowCompilation` and writes format 2.
- Format 2 writes compiled/bound `definition.yaml`, root-only `policy.yaml`
  (always present, `b"{}\n"` when absent), canonical `dependencies.json`, and
  every origin-namespaced sealed file from the compilation.
- `resources.json` records format 2, the dependency-manifest digest, v4 language
  semantics, and the complete sealed-path inventory. The existing sealed
  snapshot digest authenticates every listed path.
- Published `definition_digest` is the compilation composite digest. Published
  metadata includes snapshot format 2, dependency manifest digest, active
  policy digest, normalizer 4 via the existing language snapshot, and expanded
  node IDs.
- When `compilation is None`, the existing format-1 byte paths and projection
  shape remain unchanged for v1-v3 callers.

### Exact reload: identity versus runtime

The format-2 loader deliberately separates two authorities:

1. Authenticated origin definition/sidecar files reconstruct the **unbound
   logical identity** and its exact catalog source, precedence, include edges,
   ignored child policies, expanded node origins, language snapshot, and
   composite digest. No discovery or live package read occurs.
2. Authenticated bound `definition.yaml`, root policy, and manifest resource
   bindings authorize the **runtime graph** rooted at the run snapshot.

The loader compares exact origin tuples and policy expansion, verifies all
manifest bindings before replacing runtime language metadata, checks the bound
runtime graph against the reconstructed identity after applying only authorized
command/script/MCP substitutions, and verifies local MCP resource paths occur in
their authenticated rewritten MCP definitions. Any mismatch raises the existing
`workflow_snapshot_integrity_mismatch` and follows recovery isolation.

The regression test deletes both root and child source trees, then reloads a
child command, two named scripts, two MCP definitions, and two rewritten local
MCP resources solely through snapshot paths and authenticated bytes.

### Admission and scheduled revalidation

- Added `resolve_workflow_catalog_compilation(...) -> WorkflowCompilation | None`.
  Catalog discovery compiles each selected root once and read-only package
  resolution consumes `.package`.
- CLI foreground/background, API, Gateway, and showcase admission carry one
  compilation. Only explicit v4 semantics pass it to the format-2 writer;
  current v1-v3 admissions retain format 1.
- Showcase verification compiles against the whole bundled showcase catalog and
  remains inside a possibly temporary materialized resource context.
- Future scheduled occurrences reselect the recorded exact root/source, request
  the sealed normalizer version, compile the closure once, and compare composite
  package, risk, execution, trust, source, and precedence identities.
- A new scheduled test activates v4 only within the test, admits a project root
  with a profile child, adds a higher-precedence project child, and proves fire
  time fails before any worker claim.
- `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` remains `3`; activation is not
  part of this task.

## Files changed

- `plugins/workflow/admission.py` — prepared-snapshot format/manifest carrier.
- `plugins/workflow/api_admission.py` — compilation-aware API/showcase admission.
- `plugins/workflow/catalog_api.py` — compilation resolver and single-pass catalog compilation.
- `plugins/workflow/cli.py` — `_resolve_compilation` and compilation-aware run admission.
- `plugins/workflow/compilation.py` — canonical empty root policy sealing.
- `plugins/workflow/gateway_command.py` — compilation-aware Gateway admission.
- `plugins/workflow/scheduled_revalidation.py` — exact closure recompilation and composite/risk comparison.
- `plugins/workflow/scheduler.py` — authenticated format-2 loader and identity/runtime consistency checks.
- `plugins/workflow/showcase.py` — bundled-catalog compilations and materialization lifetime safety.
- `plugins/workflow/store.py` — format-2 writer and projection.
- `tests/plugins/workflow/test_phase4_snapshot.py` — layout, tamper, reload, catalog, and executable-resource coverage.
- `tests/plugins/workflow/test_phase4_defensive_invariants.py` — independent no-live-read and precedence mirrors.
- `tests/plugins/workflow/test_schedule_revalidation.py` — child-precedence fire-time rejection.
- `tests/plugins/workflow/test_showcase_schedule_e2e.py` — materialized bundle lifetime regression.

The `admission.py` and `compilation.py` edits are narrow adjacent requirements:
the former carries format-2 identity from staging to publication; the latter
ensures the approved empty-policy bytes and manifest digest are identical.

## Final verification

Required gates:

1. `scripts/run_tests.sh tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_language_snapshot.py`
   — **106 passed, 0 failed**.
2. `scripts/run_tests.sh tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_shutdown_recovery.py`
   — **44 passed, 0 failed**.
3. `scripts/run_tests.sh tests/plugins/workflow/test_cli.py tests/plugins/workflow/test_api_runtime.py tests/hermes_cli/test_authenticated_plugin_commands.py tests/plugins/workflow/test_notification_delivery.py tests/plugins/workflow/test_scheduled_runs.py tests/plugins/workflow/test_showcase_schedule_e2e.py`
   — **166 passed, 5 failed**. The five failures are the packaged-schema
   temporary-home isolation cases in `test_cli.py`; an untouched detached
   `8644bd9d8` checkout reproduces them.
4. `scripts/run_tests.sh tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_schedule_revalidation.py tests/plugins/workflow/test_scheduled_runs.py`
   — **125 passed, 0 failed**.
5. `scripts/run_tests.sh tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_schedule_revalidation.py tests/plugins/workflow/test_crash_recovery.py`
   — **117 passed, 0 failed**.

Additional verification:

- `scripts/run_tests.sh tests/plugins/workflow/test_catalog_api.py tests/plugins/workflow/test_showcase_catalog.py tests/plugins/workflow/test_showcase_distribution_e2e.py`
  — **135 passed, 2 failed**. Both catalog failures reproduce unchanged at
  `8644bd9d8` and are pre-existing.
- Ruff on every touched Python file — **all checks passed**.
- `git diff --check` — **passed**.

## Self-review

- No live discovery or mutable-source repair exists in the format-2 loader.
- No root-only fallback exists for dependency resources.
- Format-2 identity authority remains the canonical manifest/composite rather
  than duplicated projection fields.
- Child policies are authenticated but ignored for execution; root policy is
  the only active policy.
- Source/precedence and resource binding comparisons fail closed before worker
  claims or node execution.
- Format-1 branches remain structurally isolated and their mandatory legacy,
  crash, shutdown, scheduled, CLI, API, Gateway, and notification suites pass
  except for the confirmed unrelated baseline failures above.
- No Task 7 loop semantics, activation, core tools, prompt/tool schema changes,
  telemetry, or security-review work was added.

## Fix round 1

### Status

DONE_WITH_CONCERNS

All three important review findings are fixed with authentic regressions. The
only remaining concerns are the same unrelated baseline failures already
documented above: five packaged-schema temporary-home isolation cases and two
catalog error-classification expectations.

### TDD chronology

1. API dependency-resource admission:
   `scripts/run_tests.sh tests/plugins/workflow/test_api_runtime.py -k v4_admission_assesses_child_executable`
   first produced **0 passed, 1 failed**. `start_api_run()` raised
   `workflow_invalid_definition` because the initial root-only risk pass looked
   for a profile child's `child.py` beneath the project root. After the shared
   assessment accepted the compilation and API admission removed its second
   risk build, the same command produced **1 passed, 0 failed**.
2. Scheduled dependency-resource revalidation:
   `scripts/run_tests.sh tests/plugins/workflow/test_schedule_revalidation.py -k unchanged_scheduled_revalidation_assesses_child_resources`
   first produced **0 passed, 1 failed**. An unchanged admitted closure failed
   at fire time with `schedule_revalidation_failed`. After revalidation passed
   the compilation into the one shared assessment, the same command produced
   **1 passed, 0 failed**. The API and scheduled regressions now also count the
   risk builder and assert exactly one build per path.
3. Explicit-path include resolution:
   `scripts/run_tests.sh tests/plugins/workflow/test_cli.py -k explicit_phase4_path_resolves_includes`
   first produced **0 passed, 1 failed** with
   `include_not_found: explicit-phase4-root -> explicit-phase4-child`. After
   explicit roots were compiled against the bounded project/profile source
   snapshot with explicit precedence 0, the same command produced
   **1 passed, 0 failed**.
4. Real showcase admission under temporary materialization:
   `scripts/run_tests.sh tests/plugins/workflow/test_showcase_schedule_e2e.py -k prepares_admission_while_materialized`
   first produced **0 passed, 1 failed** with `FileNotFoundError` from
   `_tree_digest(package.root)` after the bundle context exited. After keeping
   compilation, distribution-risk verification, and immutable snapshot
   preparation inside the materialization context, the same command produced
   **1 passed, 0 failed**.

### Implementation

- `assess_package_execution()` now accepts an optional immutable
  `WorkflowCompilation` and passes it to its single risk-summary construction.
  API admission and both scheduled revalidation branches supply that
  compilation directly; their later replacement risk builds were removed.
- Catalog discovery now factors its bounded project/profile reads into a
  shared immutable source-snapshot helper. Explicit paths add their parsed root
  at precedence 0 and compile against that same catalog closure. A real
  authenticated Gateway regression proves the shared CLI/Gateway resolver
  seals a profile include as format 2.
- `_scenario_compilation()` is now a context manager. `run_showcase()` keeps
  the context alive through tree hashing, risk construction, fixture handling,
  and `prepare_run_snapshot()`; only in-memory and sealed values escape into
  final admission.

### Verification

Focused regressions:

- API exact compilation-aware risk build: **1 passed, 0 failed**.
- Scheduled exact compilation-aware risk build: **1 passed, 0 failed**.
- Explicit CLI path with profile include: **1 passed, 0 failed**.
- Authenticated Gateway explicit path with profile include: **1 passed, 0 failed**.
- Real showcase admission under deleting materialization: **1 passed, 0 failed**.

Required gates after the fixes:

1. Snapshot + language: **106 passed, 0 failed**.
2. Snapshot + crash + shutdown: **44 passed, 0 failed**.
3. CLI/API/authenticated command/Gateway notification/scheduled/showcase:
   **169 passed, 5 failed**. The five failures are the already-confirmed
   packaged-schema temporary-home baseline cases.
4. Snapshot + scheduled revalidation + scheduled runs: **126 passed, 0 failed**
   (10 + 69 + 47; the scheduled-run file took 90 seconds under the harness).
5. Snapshot + defensive invariants + scheduled revalidation + crash recovery:
   **118 passed, 0 failed**.

Additional catalog/showcase verification remained **135 passed, 2 failed**;
both failures are the already-confirmed baseline catalog error-classification
expectations. Ruff on every changed Python file and `git diff --check` passed.

### Scope audit

No Task 7 ordinary-loop semantics, v4 activation, security-review work, core
tool or prompt-schema changes, telemetry, or unrelated cleanup was added.

## Fix round 2

### Status

DONE_WITH_CONCERNS

Explicit standalone pre-Phase-4 admissions no longer depend on project/profile
catalog availability. The only observed failures remain the same five
pre-existing packaged-schema temporary-home isolation cases documented above.

### Authentic RED/GREEN

Regression command:

`scripts/run_tests.sh tests/plugins/workflow/test_cli.py -k explicit_pre_phase4_run_ignores_unavailable_unrelated_catalog`

- RED: **0 passed, 2 failed**. Both an unversioned v2 foreground run and an
  `archon-2026-07` v3 foreground run returned exit 70 / `internal_error` with
  `WorkflowCatalogUnavailableError` when an unrelated project catalog could
  not be enumerated.
- GREEN: **2 passed, 0 failed** after explicit resolution selected the language
  profile and normalizer from the already parsed root source, used a root-only
  immutable catalog snapshot for versions below v4, and captured the bounded
  project/profile catalog only when `supports_phase4_semantics(...)` was true.

The combined preservation check:

`scripts/run_tests.sh tests/plugins/workflow/test_cli.py -k 'explicit_phase4_path_resolves_includes or explicit_pre_phase4_run_ignores_unavailable_unrelated_catalog'`

produced **3 passed, 0 failed**, proving the existing explicit-v4 profile
include still resolves through the bounded catalog while v2/v3 standalone
admission does not touch it. The root is parsed once and compiled once; the
selected normalizer version is passed into that single compilation.

### Files changed

- `plugins/workflow/cli.py` — language-gated explicit catalog capture with the
  legacy root-only snapshot path preserved.
- `tests/plugins/workflow/test_cli.py` — public foreground execution regression
  for unversioned v2 and current Archon v3 under unavailable unrelated catalog
  enumeration.
- `task-6-report.md` — this fix-round evidence.

### Verification

- Full `test_cli.py`: **81 passed, 5 failed**; all five failures are the
  already-confirmed packaged-schema baseline cases.
- Snapshot + language gate: **106 passed, 0 failed**.
- Snapshot + crash + shutdown recovery gate: **44 passed, 0 failed**.
- Full CLI/API/authenticated command/Gateway notification/scheduled/showcase
  gate: **171 passed, 5 failed** in 91.9 seconds. The scheduled file passed
  **47/47**; the same five packaged-schema baseline cases were the only
  failures.
- Ruff on both changed Python files and `git diff --check`: passed.

### Self-review and residual concerns

- Current authored explicit admissions select v2 for legacy/unversioned and v3
  for Archon; sealed v1 reload remains outside `_resolve_compilation()` and is
  covered by the unchanged snapshot/recovery contracts.
- Explicit v4 continues to capture one bounded project/profile source view,
  preserving include precedence and ambiguity behavior from fix round 1.
- `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` remains 3.
- No Task 7 behavior, activation, security-review work, core tool/prompt schema,
  telemetry, or unrelated cleanup was added.
