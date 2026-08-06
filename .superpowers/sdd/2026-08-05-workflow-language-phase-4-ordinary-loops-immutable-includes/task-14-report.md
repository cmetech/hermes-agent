# Task 14 report: Activate Phase 4 language semantics

Status: **COMPLETE ON FEATURE BRANCH; BASE INTEGRATION NOT PERFORMED**

## Outcome

Phase 4 language semantics are active for new Archon packages. The single public
selection mapping now chooses normalizer v4 for `archon-2026-07`, keeps
`hermes-legacy` at v2, and preserves explicit/sealed v1-v3 readers. Generated
contracts, CLI/runtime admission, installed-wheel behavior, trust identity, Desktop
responses, and old-snapshot execution follow the same version authority.

Activation implementation commit:
`7bf55d5d680faea8d82474c1d3e2a3dd8f69a096`
(`feat(workflow): activate phase 4 language semantics`). The documentation commit is
`docs(workflow): record phase 4 activation evidence`, the commit containing this
report.

No push, merge, rebase, publication, `base` integration, branch deletion, or worktree
cleanup was performed.

## Files changed

Production activation and bounded corrections:

- `plugins/workflow/language.py`
- `plugins/workflow/language_schema.py`
- `plugins/workflow/dashboard/plugin_api.py`
- `plugins/workflow/dependency_manifest.py`

Activation/compatibility tests:

- `tests/plugins/workflow/test_phase4_language.py`
- `tests/plugins/workflow/test_phase3_language.py`
- `tests/plugins/workflow/test_language_schema.py`
- `tests/plugins/workflow/test_language_snapshot.py`
- `tests/plugins/workflow/test_cli.py`
- `tests/plugins/workflow/test_portable_compatibility_e2e.py`
- `tests/plugins/workflow/test_installed_distribution_e2e.py`
- `tests/plugins/workflow/test_phase4_defensive_invariants.py`
- `tests/plugins/workflow/test_catalog_api.py`
- `tests/plugins/workflow/test_api_runtime.py`
- `tests/plugins/workflow/test_loop_executor.py`
- `tests/plugins/workflow/test_phase3_code_catalog.py`
- `tests/plugins/workflow/test_phase3_execution_semantics.py`
- `tests/plugins/workflow/test_runner_binding.py`
- `tests/plugins/workflow/test_schedule_revalidation.py`
- `tests/plugins/workflow/test_scheduler.py`
- `tests/plugins/workflow/test_script_executor.py`
- `tests/plugins/workflow/test_showcase_schedule_e2e.py`
- `tests/plugins/workflow/test_strict_output_references.py`
- `tests/plugins/workflow/test_structured_output_language.py`
- `tests/plugins/workflow/test_typed_publication.py`
- `tests/plugins/workflow/test_workflow_language_desktop_e2e.py`
- `tests/skills/test_workflow_operator_behavior.py`

`tests/plugins/workflow/test_trust_policy.py` and
`tests/plugins/workflow/test_workflow_detail_api.py` were unchanged but remained in
their exact verification gates.

Evidence files:

- `docs/reviews/2026-08-05-workflow-language-phase-4-validation.md`
- this report
- the Phase 3 `continue.md` and `progress.md` supersession notes

The current Phase 4 `progress.md` ledger was deliberately not modified.

## Strict TDD evidence

1. Exact four-file RED before the mapping change: 758 passed / 8 failed, exclusively
   on expected current-Archon v3 observations.
2. Exact Step 3 after migration: 874 passed / 5 failures. All five are the documented
   exact-base packaged-schema CLI failures.
3. Exact eleven-file Phase 4 no-retry gate: first 224/225 because a historical v3
   defensive leg followed moving current; explicit-v3 correction produced 225/225.
4. First full no-retry gate: 32,302 passed / 66 failed in 662.6s, comprising the exact
   27 base failures plus 39 activation-only failures across 15 files and the known
   Anthropic setup-error file.
5. Exact 15-file migration gate: 535/535, then 536/536 after adding the serializer
   relationship test.
6. Focused response/serializer relationships: 5/5; full language,
   structured-output, and catalog API files: 148/148.
7. Fresh installed-wheel integration: 1/1.

## Authorized deviations and production defects

- Generated v4 compatibility codes required 15,714 bytes. The authoritative section
  ceiling increased from 15,000 to 16,000; the 256,000-byte total, 4,000-byte reserve,
  and other section limits are unchanged.
- The workflow-detail response model's valid normalizer maximum increased from 3 to
  4 without weakening strict integer, lower-bound, digest, or extra-field checks.
- Manifest canonicalization gained exact bounded huge-integer support after a valid
  5,000-digit schema integer hit Python's decimal conversion guard.
- Focused reproduction then prevented a false test-only workaround: package hashing,
  not normalization, had regressed historical non-finite YAML values. The scoped
  manifest encoder preserves prior non-finite bytes and collision distinction while
  the core structured-output encoder remains strict.
- Existing Phase 3 executor/reference tests now explicitly select sealed v3 instead
  of following moving current. Current/default tests assert v4 and compilation
  composite trust identity.

## Final verification

- Exact Step 3: 874 passed / 5 exact-base failures in 15.9s.
- Exact Phase 4 gate, retries disabled: 225/225 in 6.7s.
- Exact migration set, retries disabled: 536/536 in 12.4s.
- Full language/structured-output/catalog API: 148/148 in 7.8s.
- TUI diagnostic whole file: 517/517; upstream-merge diagnostic whole file: 104/104.
- Ruff over all 27 activation Python files: passed.
- `git diff --check`: passed.
- Branch/pin check: required feature branch, Archon current 4, legacy current 2,
  supported versions `{1, 2, 3, 4}`.

The final full no-retry run reproduced only the exact baseline: 27 failures across the
same 16 files plus the same four Anthropic setup errors in one additional file. No
activation or transient failure remained. Transport truncated the middle summary, so
the pass aggregate is transparently reconstructed: 32,369 prior-run outcomes plus one
new relationship test, less 27 final failures, equals 32,343 passes. Command lifecycle
timestamps measured 688.4s wall time; no hidden runner duration is asserted.

The full failure inventory and attribution are recorded in the validation report.
Task 13's functional review remains clean and the bounded security conclusion remains
**PASS_AFTER_FIX**.

## Self-review

- The core version change is one mapping entry; legacy/default and sealed readers are
  unchanged.
- The manifest encoder preserves ordinary bytes, prior non-finite bytes, exact huge
  integers, string-key ordering, tuple/list shape, and the existing 8 MiB bound.
- Trust tests authenticate the same resolved compilation used for admission rather
  than trusting an incomplete package-only digest.
- Historical Phase 3 tests explicitly pin v3; moving-current behavior is asserted as
  v4 rather than silently rewriting old contracts.
- No new core model tool, environment variable, prompt mutation, telemetry, generated
  build output, or unrelated product surface was added.
- No remote or integration operation was performed.
