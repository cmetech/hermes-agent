# Phase 4 final whole-branch review fix report

Status: **COMPLETE ON FEATURE BRANCH; BASE INTEGRATION NOT PERFORMED**

## Outcome

The two Important whole-branch review findings are repaired:

1. `workflow trust` and `workflow untrust` now resolve the same immutable
   `WorkflowCompilation` authority used by current-v4 doctor, detail, and run
   admission. Their digest and risk decisions use the full include-closure composite
   for current v4, while v1-v3 retain their existing package-digest behavior.
2. Workflow catalog list construction no longer discards discovered compilations.
   Current-v4 list rows now assess risk, trust, and projection against the same
   composite authority as workflow detail. Legacy packages and current legacy
   showcases retain the established package-only path.

No push, merge, rebase, publication, `base` integration, release action, or worktree
cleanup was performed. The current Phase 4 `progress.md` ledger was deliberately not
modified.

## Files changed

Production:

- `plugins/workflow/cli.py`
- `plugins/workflow/catalog_api.py`

Behavior-contract tests:

- `tests/plugins/workflow/test_cli.py`
- `tests/plugins/workflow/test_catalog_api.py`

Evidence:

- `docs/reviews/2026-08-05-workflow-language-phase-4-validation.md`
- this report

## Strict RED/GREEN evidence

The CLI RED selected the real current-v4 lifecycle and dependency-mutation cases:

```text
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_cli.py -k 'current_v4_operator_trust_lifecycle_uses_exact_composite_authority or current_v4_trust_rechecks_dependency_composite_before_recording or current_v4_untrust_rechecks_dependency_composite_before_revoking'
```

Result: 0 passed / 3 failed. The lifecycle failed with `include_not_found` because
trust resolved only the root package. The trust race returned digest mismatch with
exit 2 instead of conflict, and the untrust race succeeded and reported
`revoked=false` instead of rejecting the changed include closure.

The catalog RED was:

```text
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_catalog_api.py -k current_v4_composite_trust_is_consistent_between_catalog_and_detail
```

Result: 0 passed / 1 failed. The catalog list reported `untrusted` while detail
reported `trusted` for the same exact current-v4 compilation.

After the production repairs, the CLI selection passed 3/3 and the catalog selection
passed 1/1. The expanded CLI relationship, including both current-v4 unsupported-field
controls, passed 5/5.

## Implementation details and invariants

- Trust material is derived once per resolved compilation: exact admission digest,
  compatibility, and compilation-aware risk summary.
- Trust still performs a second resolution before recording authority. It rejects a
  changed composite digest, covered-path identity, package/risk digest mismatch, or
  changed risk digest with stable `package_changed` conflict semantics.
- Untrust now performs the equivalent second-resolution check before revocation, so a
  dependency mutation cannot revoke stale authority or silently target the wrong
  closure.
- Current-v4 trust rejects the legacy root-only digest. A successful doctor → trust →
  foreground run → untrust lifecycle uses one exact composite digest, and the sealed
  run records that digest as `definition_digest`.
- Catalog discovery keeps `WorkflowCompilation` values through list projection.
  Compilation-aware assessment and qualification are used only for packages whose
  actual profile/version supports Phase 4 semantics.
- Current legacy showcases deliberately remain on their established package-only
  assessment path. A verified showcase compilation will be passed only when an actual
  Phase 4 showcase exists.
- The operator commands and catalog remain pre-admission/read-only compilation
  surfaces. Runtime execution still consumes sealed compilation bytes; no post-
  admission live source read or weaker trust boundary was introduced.
- Error payloads, trust data, and catalog/detail responses contain stable workflow
  identifiers and digests, not external include paths or source contents.

## Verification

All commands used the repository wrapper with retries disabled.

- Exact eleven-file Phase 4 gate: 225 passed / 0 failed.
- Activation language/schema/snapshot gate: 766 passed / 0 failed.
- Surface/catalog/detail/doctor/defensive/evidence gate: 179 passed / 0 failed.
- Fresh installed-distribution integration: 1 passed / 0 failed.
- Final combined operator/catalog relationship gate: 6 passed / 0 failed.
- Relevant CLI/trust/catalog/detail gate: 222 passed / 5 failed. The five failures
  are the exact-base packaged-schema CLI cases already documented in the validation
  artifact; passing counts were CLI 84, trust policy 22, catalog API 78, catalog CLI
  2, and detail API 36.
- Full `tests/plugins/workflow` no-retry gate: 103 files, 4,941 passed / 6 failed in
  187.4s. All six failures match exact base: the five packaged-schema CLI cases plus
  `test_recomputed_contiguous_pre_activation_order_damage_is_value_safe[prefix-delete]`.
- Ruff on all four changed Python files: passed.
- `git diff --check`: passed.

## Review disposition

The two Important findings are closed by behavior-contract coverage and the gates
above. The Desktop Minor remains deferred: it concerns only the presentation-side
malformed exact-type guard; backend `next_actions` remains the authority, and this
bounded operator/catalog fix does not change that contract.

The earlier Task 12 concern that Phase 4 coverage was vacuous is obsolete after Task
14 activation: ordinary Archon authoring selects v4, the exact Phase 4 gate passes
225/225, and the new operator/catalog tests exercise actual current-v4 root-plus-
dependency compilations.

## Self-review

- One digest authority now spans doctor, trust, run admission, untrust, catalog list,
  and detail for current v4.
- Legacy package identity is preserved rather than silently migrated.
- TOCTOU checks cover dependency-only mutation, not merely root-file mutation.
- No tool schema, core tool, environment variable, prompt, dependency, lockfile,
  Desktop source, or plugin boundary changed.
- The changes do not weaken the previously reviewed catalog containment, resource
  budgets, compilation sealing, or trust-store integrity checks.
