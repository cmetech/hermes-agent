# Task 16 Quality Review — Phase 3 Final Candidate

## Verdict

**PASS**

| Severity | Count |
| --- | ---: |
| Critical | 0 |
| Important | 0 |
| Minor | 0 |

Reviewed production identity:

```text
commit  8a1fe704484bf63e0e84f536f7fb690a2f024ccf
tree    94f4fd4572b63ba6dd496213b603e67748b41b46
```

No ordinary functional-correctness, concurrency/lifecycle, crash-consistency,
descriptor/data-integrity, bounds, prompt-caching/role-alternation,
maintainability, compatibility, or test-quality finding blocks this candidate.

## Scope and exclusions

This was a read-only independent review of the exact Task 16 candidate. I read
the Task 16 brief and report, the approved Phase 3 plan and design, the Task 16
production/test diff, and the relevant surrounding implementation paths.

Per the active user override, this review performed no threat-model work,
security-focused analysis, exploit/adversarial/privacy analysis, or related
validation. It did not inspect or run the explicitly excluded suites, including
`test_phase3_bash_lexer_security.py` and the mixed
`test_persistent_session_recovery.py` file. It also did not use the discarded
broad/generic ledger results.

## Review evidence

### Functional correctness and compatibility

- The final schema-startup ownership pre-scan is dependency-light and limited
  to schema-shaped invocations, while the bounded parser remains authoritative
  for success, help, profile placement, and parse errors
  (`hermes_cli/main.py:85-181`, `hermes_cli/main.py:597-630`,
  `hermes_cli/main.py:811-816`). Normal and update startup continue through
  early recovery.
- `NodeExecutionContext` retains all established positional fields before the
  newly appended provider-lifecycle callbacks, preserving its prior positional
  construction contract (`plugins/workflow/executors/base.py:36-96`).
- Effective Phase 3 limits and per-node retry/timeout projections are validated
  against the normalized package rather than accepted as independent mutable
  state (`plugins/workflow/execution_semantics.py:267-426`).
- Showcase catalog/detail assertions now test the intended relationship:
  common compatibility findings remain ordered and equal while detail alone
  carries migration guidance; the truncation sentinel remains unchanged.

### Concurrency and lifecycle behavior

- Provider release is a single explicit linearization point. Cancellation wins
  before release; after the release callback is delivered and flushed, the
  provider result wins while wall, idle, and resource limits remain active
  (`agent/plugin_agent.py:1168-1461`).
- Strict output references and v3 conditions are resolved before a node claim;
  retained producer identities are revalidated before use, and temporary read
  failures defer resolution without consuming an execution attempt
  (`plugins/workflow/scheduler.py:1285-1667`).
- Attempt deadlines are captured from the claim-time monotonic sample and
  handed unchanged to execution, preventing queue/dispatch time from silently
  resetting the sealed wall budget (`plugins/workflow/scheduler.py:2853-3261`,
  `plugins/workflow/scheduler.py:3848-3864`).
- Persistent session publication uses a compare-and-set-or-observe outcome,
  including an idempotent already-applied result, and the durable obligation is
  reconciled independently of node execution
  (`plugins/workflow/sessions.py:1278-1345`,
  `plugins/workflow/scheduler.py:659-696`).

### Crash consistency, descriptors, data integrity, and bounds

- Fast-path projection validation reads the bounded private session-authority
  index directly, while full journal rebuild still binds authorities against
  journal events. The Task 16 correction therefore restores ordinary load
  performance without removing rebuild validation
  (`plugins/workflow/store.py:4752-5050`,
  `plugins/workflow/store.py:7087-7186`,
  `plugins/workflow/store.py:7200-7350`).
- Scheduled snapshot scans exclude only the exact root-level recovery-artifact
  names emitted by the store; malformed and nested lookalikes remain part of
  normal snapshot accounting (`plugins/workflow/scheduled_revalidation.py:38-73`).
- Output publication identities require exact fields and bounded canonical
  values, and resolution caches are keyed by the complete producer/descriptor
  identity and kept under a byte-bounded LRU
  (`plugins/workflow/output_resolution.py:100-176`,
  `plugins/workflow/scheduler.py:784-1115`).
- Subprocess output uses file-backed descriptors, closes both streams, and
  deterministically truncates the combined retained output to the configured
  limit (`plugins/workflow/executors/base.py:192-238`). Process resource and
  shutdown limits are finite and validated.
- Condition parsing and evaluation enforce finite byte/token/nesting bounds and
  preserve typed versus rendered output semantics
  (`plugins/workflow/conditions.py:62-343`).

### Prompt caching and role alternation

- Strict substitutions are rendered into the isolated node's initial request
  before launch (`plugins/workflow/executors/ai.py:611-730`). The implementation
  does not rewrite prior conversation messages or inject a synthetic mid-loop
  user turn. Persistent/shared context is selected by a sealed cache
  fingerprint, with changed fingerprints creating fresh context rather than
  mutating a cached prefix (`plugins/workflow/executors/ai.py:799-974`).

### Maintainability and test quality

- The six Task 16 corrections are narrowly scoped and reuse existing ownership,
  lifecycle, store, and snapshot helpers rather than adding parallel control
  paths.
- New assertions are behavioral: they test schema-startup mutation boundaries,
  provider cancellation ordering, exact store-owned recovery names, positional
  compatibility, and catalog/detail relationships. The model-catalog fixtures
  are isolated from the moving live curated catalog rather than snapshotting
  external state.
- The Task 16 report's retained final base-gate evidence is consistent with the
  exact reviewed SHA/tree. I treated it as secondary context, not a substitute
  for the fresh focused commands below.

## Fresh verification

All commands used the repository test runner with file retries disabled.

```text
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_execution_semantics.py \
  tests/plugins/workflow/test_phase3_conditions.py \
  tests/plugins/workflow/test_phase3_resolution_waits.py \
  tests/plugins/workflow/test_strict_output_references.py -q

Result: 4 files, 250 passed, 0 failed.
```

```text
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/agent/test_plugin_agent.py \
  tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_schedule_revalidation.py \
  tests/plugins/workflow/test_script_executor.py \
  tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py \
  -q -k 'worker_exchange_orders_cancellation_at_true_execute_boundary or \
  packaged_schema_alone_skips_early_recovery_marker_probe or \
  root_mutable_file_names_include_only_store_owned_recovery_artifacts or \
  sealed_snapshot_ignores_store_owned_root_recovery_artifact or \
  node_execution_context_preserves_pre_sealed_resource_positional_order or \
  bundled_showcase_catalog_detail_and_admission_cross_real_middleware'

Result: 5 files, 12 passed, 0 failed.
```

The second command selected the provider-release boundary, packaged schema
startup, exact recovery-artifact naming/digest, execution-context positional
compatibility, and showcase catalog/detail relationship regressions. Parameter
expansion accounts for the 12 passing cases.

## Deviations and limitations

- I did not rerun the canonical broad suite, the generic ledger rehearsal, or
  any excluded test file. This is intentional under the active review scope.
- I did not independently rerun the Desktop typecheck/lint/test gates; the
  retained Task 16 report records those exact-candidate results, and the final
  production commit changes only Python CLI startup behavior.
- This report makes no security-review or threat-model-validation claim.
- No production or test code was modified by this review.
