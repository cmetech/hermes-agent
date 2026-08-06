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
