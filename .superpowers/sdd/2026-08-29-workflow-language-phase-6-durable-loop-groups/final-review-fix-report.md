# Workflow Language Phase 6 final-review fix report

Date: 2026-08-30

## Outcome

The Jira Defect Loop manifest fetch now seals zero workflow retries and one total
workflow attempt. An eligible transient failure cannot claim or execute a second
attempt. The opt-out is restricted to Archon normalizer v6 command/prompt nodes;
v1-v5 normalization and Bash/Script deterministic retry validation remain
unchanged.

## Root cause

The two distributed workflow copies authored
`fetch-ticket-manifest.retry.max_attempts: 1`. Archon normalizer v3-v6 defines
`max_attempts` as retries after the initial attempt, so the existing v3 semantic
projection sealed:

- `requested_retries: 1`
- `requested_total_attempts: 2`
- `effective_total_attempts: 2`

The workflow prose and per-attempt tool audit required exactly one
`jira_my_tickets({"max_results": 25})` call, but neither changed the admitted
two-attempt scheduler grant. The existing workflow test asserted only the
authored YAML value and therefore reinforced the wrong interpretation instead
of checking sealed and scheduled behavior.

## RED evidence

The scheduler regression was added before the language change and executed
against the real distributed workflow, run admission, sealed execution
semantics, store, and scheduler:

```text
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_phase6_jira_defect_loop.py -k manifest_fetch_has_one_total_attempt_and_cannot_retry -v
```

Result: **RED**, 1 failed and 65 deselected. The executor call-count assertion
reported `assert 2 == 1`, proving the eligible first failure scheduled and
executed a second workflow attempt.

The schema/loader boundary matrix was then added before the shared language and
schema implementation:

```text
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py -k v6_ai_retry_opt_out_has_schema_loader_version_and_node_parity -v
```

Result: **RED**, 4 failed and 4 passed. Top-level and loop-group-body v6
command/prompt nodes rejected `max_attempts: 0`; v5 command/prompt and v6
Bash/Script rejection controls already passed.

The stale legacy-to-current migration guidance was revised as a test first:

```text
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_phase3_language.py::test_phase3_archon_fields_are_implemented_and_legacy_guidance_is_exact -v
```

Result: **RED**, 1 failed. The guidance still said an AI node with one total
attempt could not migrate because no explicit opt-out existed.

## Implementation

- `normalize_workflow()` passes a v6-only `allow_ai_retry_opt_out` flag through
  the existing v3 retry normalizer. The lower bound becomes zero only for
  command/prompt nodes under v6. The default remains false, so recorded v1-v5
  behavior is unchanged.
- The same flag is passed through the existing one-level v6 loop-group body
  normalization path. No second retry mechanism or snapshot version was added.
- The v6 authoring schema exposes zero for retry attempts while the existing
  command/prompt versus Bash/Script node variants keep deterministic nodes at a
  minimum of one when a retry block is present. Both top-level and body schemas
  are covered by schema/loader parity tests.
- Current migration guidance now explains that a legacy AI node requiring one
  total attempt is authored as `max_attempts: 0` under Archon v6.
- Only `fetch-ticket-manifest` changed from `max_attempts: 1` to
  `max_attempts: 0` in the authoritative package workflow and vendored workflow.
  The other retrying Jira/GitLab nodes were not changed.
- The package composite digest was regenerated with the same
  `parse_workflow_source_bytes` → `WorkflowCatalogSnapshot.capture` →
  `compile_workflow(..., normalizer_version=6).composite_digest` path used by
  `_verified_workflow_package`. The Jira Defect Loop digest changed from
  `a6e147fd406436703e7eaf0dedfb6c3915838cf4fbab3938e1b19b7769d25d5a`
  to `f83ebf3f2eb48ae91c20193896ccb08f6d03c8a1c62a9133305f83faefb0b037`.

## GREEN and verification evidence

Focused new schema/loader matrix:

```text
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py -k v6_ai_retry_opt_out_has_schema_loader_version_and_node_parity -v
```

Result: **8 passed, 0 failed**.

Focused sealed workflow and scheduler regression:

```text
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_phase6_jira_defect_loop.py -k 'manifest_fetch_has_one_total_attempt_and_cannot_retry or distributed_v6_workflow_seals_one_immutable_bounded_manifest' -v
```

Result: **2 passed, 0 failed**. The admitted attempt metadata is
`requested_retries == 0`, `requested_total_attempts == 1`, and
`effective_total_attempts == 1`; two scheduler advances still produce only one
executor call.

Focused amended-path plus historical replay/retry gate:

```text
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_language.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_phase3_language.py tests/plugins/workflow/test_phase3_execution_semantics.py tests/plugins/workflow/test_phase4_language.py tests/plugins/workflow/test_phase5_language.py tests/plugins/workflow/test_phase6_language.py tests/plugins/workflow/test_retry.py tests/plugins/workflow/test_scheduler.py -v
```

Result: **1,082 passed, 0 failed** across 10 files. This includes the recorded
v1-v5 language/replay gates, snapshot readers, retry semantics, execution
semantics, current v6 language, and scheduler paths.

Exact Task 8 Python gate:

```text
scripts/run_tests.sh tests/plugins/workflow/test_phase6_jira_defect_loop.py tests/hermes_cli/test_ericsson_connector_distribution.py tests/plugins/workflow/test_ericsson_connector_toolsets.py -v
```

Result: **101 passed, 0 failed** across 3 files (66 + 7 + 28).

Exact Task 8 vendor gate:

```text
node --test scripts/__tests__/vendor-ericsson.test.mjs
```

Result: **48 passed, 0 failed**.

Focused Ruff:

```text
.venv/bin/ruff check plugins/workflow/language.py plugins/workflow/language_schema.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_phase3_language.py tests/plugins/workflow/test_phase6_jira_defect_loop.py
```

Result: **All checks passed**.

The two workflow copies pass
`cmp -s capabilities/workflow-packages/ericsson/workflows/jira-defect-loop.yaml capabilities/workflows/jira-defect-loop.yml`.
Final `git diff --check` result: **passed with no output**.

## Files

- `plugins/workflow/language.py`
- `plugins/workflow/language_schema.py`
- `capabilities/workflow-packages/ericsson/workflows/jira-defect-loop.yaml`
- `capabilities/workflows/jira-defect-loop.yml`
- `capabilities/workflow-packages/ericsson/digests.json`
- `tests/plugins/workflow/test_language_schema.py`
- `tests/plugins/workflow/test_phase3_language.py`
- `tests/plugins/workflow/test_phase6_jira_defect_loop.py`
- `.superpowers/sdd/2026-08-29-workflow-language-phase-6-durable-loop-groups/final-review-fix-report.md`

## Commit

Atomic commit subject: `fix(workflow): seal one-attempt Jira manifest fetch`.
The final SHA is reported by the controller handoff because a Git commit cannot
embed its own content-derived SHA.

## Concerns

None. The fix adds no schema version, snapshot version, scheduler path, table,
migration, dependency, or retry mechanism.
