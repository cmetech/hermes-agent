# Adversarial remediation batch 3 implementation report

## Scope

Closed AR-07 and AR-08 only: attempt-owned zero-artifact workspaces for v6
Bash and Script nodes with sealed `artifacts: false`. Recovery, public
publication, schema, persistence, and the filesystem snapshot mechanism were
not redesigned.

Candidate base: `0d69eb4c04c17e384531644e230677e40435fc11`.

The scoped-review follow-up is based on Batch 3 commit
`246304d4be31c940ef4a5d57b113f0f03b820913` and closes only the verified
process-visible run-directory/cwd escape.

The supported-path re-review follow-up is based on scoped-review commit
`375283411899ce51774bd8ffcb3fb18f094cf1cf` and closes only the verified
`DOCS_DIR/../artifacts` escape.

## RED evidence

The focused regressions were added before production edits and run with:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_scheduler.py -q \
  -k 'attempt_private_rendered_workspace or retry_keeps_failed_residue or ignores_concurrent_child_publication or filesystem_checks_fail_closed'
```

Result: 0 passed, 16 failed, 30 deselected. Every row failed on the same
accounting-boundary mismatch:

- a top-level artifact-free process received the stable run-wide
  `artifacts/` root instead of `<attempt>/artifacts`;
- a loop-group child received its stable public iteration/body root instead of
  `<attempt>/artifacts`; and
- rendered/exported `ARTIFACTS_DIR` therefore also identified those shared
  roots.

This reproduced cross-attempt residue attribution and concurrent public
publication attribution before any scheduler edit. The same focused command
passed 16 tests after the production change.

The scoped-review regressions were then added before follow-up production edits
and run with:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_scheduler.py -q \
  -k 'watches_run_env_and_relative_cwd or publishing_process_keeps_real_run_cwd_env_and_exact_publication'
```

Result: 8 passed, 6 failed, 46 deselected. All Bash, inline Script, and named
Script artifact-free rows failed for both top-level and scoped execution: the
node succeeded because `HERMES_WORKFLOW_RUN_DIR` and the process cwd still
identified the actual run root, so writes through either
`$HERMES_WORKFLOW_RUN_DIR/artifacts` or relative `artifacts/` bypassed the
attempt-private snapshot. All v6 publishing and v5 compatibility rows passed
before the follow-up change. The same command passed all 14 rows after the
follow-up change.

The supported-path regressions were added before the second follow-up
production edit and run with:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_scheduler.py -q \
  -k 'watches_docs_traversal or publishing_process_keeps_real_run_cwd_env_and_exact_publication'
```

Result: 8 passed, 6 failed, 52 deselected. All Bash, inline Script, and
authenticated named Script artifact-free rows failed for both top-level and
scoped execution because the node succeeded after writing through
`$DOCS_DIR/../artifacts`. All v6 publishing and v5 compatibility rows passed
before the production edit and showed the exact real-run `DOCS_DIR`. The same
command passed all 14 rows after the production change.

## Root-cause changes

- At the scheduler's existing execution-context construction junction, detect
  only v6 Bash/Script nodes whose sealed `artifacts` option is false.
- Derive their private generated-artifact workspace from the already-unique
  physical attempt directory, using `<attempt>/artifacts`; no new table,
  migration, persisted baseline, cleanup inference, scheduler, pool,
  dependency, or artifact abstraction was added.
- Pass that one path through both
  `NodeExecutionContext.publication_directory` and
  `VariableContext.artifacts_dir` before Bash or inline/named Script
  rendering. The existing `max_artifact_bytes=0` path and
  `publication_tree_snapshot()` before/after enforcement remain unchanged.
- Preserve top-level attempt directory identity, every ordinary publishing
  path, the loop-group public iteration/body path, and all v1-v5 behavior.
- Added real Bash and Script coverage for top-level and child execution,
  inline and named Script rendering/environment alignment, fresh retry
  workspaces with forensic failed-attempt residue, deterministic unrelated
  child publication, and private-workspace fail-closed filesystem behavior.
- For a Bash/Script context with the existing zero-artifact ceiling, derive one
  process-visible directory from `effective_attempt_directory`. Bash and Script
  now export that directory as `HERMES_WORKFLOW_RUN_DIR` and spawn with it as
  cwd, so its `artifacts/` child is the already-watched private publication
  directory. Normal publishing contexts still derive the actual run root.
- Keep `NodeExecutionContext.run_directory` unchanged for authenticated named
  Script resource planning, stdout/stderr and artifact descriptors, callbacks,
  journal correlation, and persistence. Named resources are still resolved
  from authenticated real-run bytes before the process is launched in its
  private view.
- At the same scheduler context junction, replace the process VariableContext
  path view for every artifact-free Bash/Script context, top-level or scoped,
  so both `artifacts_dir` and `docs_dir` identify the already-watched
  attempt-private publication directory. Thus `$DOCS_DIR/../artifacts` resolves
  back into the same private tree without adding a scanner, sandbox, cleanup,
  baseline, persistence, or filesystem abstraction.
- Preserve the real run directory as the internal `NodeExecutionContext`
  resource authority. Publishing nodes and v1-v5 nodes continue to expose the
  actual run cwd/run environment, exact public artifact path, and exact
  `<run>/docs` path.

## Verification

Focused GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_scheduler.py -q \
  -k 'attempt_private_rendered_workspace or retry_keeps_failed_residue or ignores_concurrent_child_publication or filesystem_checks_fail_closed'
```

Result: 1 file, 16 tests passed, 0 failed, 30 deselected.

Scoped-review GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_scheduler.py -q \
  -k 'attempt_private_rendered_workspace or retry_keeps_failed_residue or ignores_concurrent_child_publication or filesystem_checks_fail_closed or watches_run_env_and_relative_cwd or publishing_process_keeps_real_run_cwd_env_and_exact_publication'
```

Result: 1 file, 30 tests passed, 0 failed.

Supported-path focused GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_scheduler.py -q \
  -k 'watches_docs_traversal or publishing_process_keeps_real_run_cwd_env_and_exact_publication'
```

Result: 1 file, 14 tests passed, 0 failed, 52 deselected.

Combined Batch 3 GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_scheduler.py -q \
  -k 'attempt_private_rendered_workspace or retry_keeps_failed_residue or ignores_concurrent_child_publication or filesystem_checks_fail_closed or watches_run_env_and_relative_cwd or publishing_process_keeps_real_run_cwd_env_and_exact_publication or watches_docs_traversal'
```

Result: 1 file, 36 tests passed, 0 failed.

Required Phase 6 context/scheduler/recovery gate:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_execution_context.py \
  tests/plugins/workflow/test_phase6_scheduler.py \
  tests/plugins/workflow/test_phase6_interactions_recovery.py -q
```

Result after the supported-path re-review follow-up: 3 files, 115 tests passed,
0 failed.

Required full Phase 6/scheduler/executor gate:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_language.py \
  tests/plugins/workflow/test_phase6_scheduler.py \
  tests/plugins/workflow/test_phase6_interactions_recovery.py \
  tests/plugins/workflow/test_phase6_execution_context.py \
  tests/plugins/workflow/test_parallel_scheduler.py \
  tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_fault_injection.py \
  tests/plugins/workflow/test_bash_e2e.py \
  tests/plugins/workflow/test_script_executor.py -q
```

Result after the supported-path re-review follow-up: 9 files, 376 tests passed,
0 failed.

Required historical compatibility gate:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_execution_semantics.py \
  tests/plugins/workflow/test_phase4_loops.py \
  tests/plugins/workflow/test_phase5_execution_authority_continuity.py -q
```

Result after the supported-path re-review follow-up: 3 files, 124 tests passed,
0 failed. An earlier scoped-review run of the same command hit the unchanged
Phase 5 file's existing exact-float assertion (`900.0000000000073 == 900`) and
passed on its automatic file retry; a retry-disabled diagnostic rerun
reproduced that one unrelated failure at 123 passed, 1 failed. The current run
was clean, and no changed file participates in that deadline calculation.

Static gates:

```bash
.venv/bin/ruff check \
  plugins/workflow/executors/base.py \
  plugins/workflow/executors/bash.py \
  plugins/workflow/executors/script.py \
  plugins/workflow/scheduler.py \
  tests/plugins/workflow/test_phase6_scheduler.py
git diff --check
```

Result: Ruff reported `All checks passed!`; `git diff --check` reported no
errors.

## Changed files

- `plugins/workflow/scheduler.py`
- `plugins/workflow/executors/base.py`
- `plugins/workflow/executors/bash.py`
- `plugins/workflow/executors/script.py`
- `tests/plugins/workflow/test_phase6_scheduler.py`
- `.superpowers/sdd/2026-08-29-workflow-language-phase-6-durable-loop-groups/adversarial-remediation-batch-3-report.md`

## Commit

`fix(workflow): isolate artifact-free attempts` — the atomic Batch 3 commit
containing the scheduler fix, behavioral regressions, and this report. Its
SHA is `246304d4be31c940ef4a5d57b113f0f03b820913`.

`fix(workflow): contain artifact-free process paths` — the atomic scoped-review
follow-up containing the shared process-view derivation, executor wiring,
behavioral regressions, and this report update. Its final SHA is returned in
the task handoff because a commit cannot embed its own SHA.

`fix(workflow): contain artifact-free variable paths` — the atomic
supported-path re-review follow-up containing the scheduler VariableContext
mapping, six-row real-executor regression, compatibility controls, and this
report update. Its final SHA is returned in the task handoff because a commit
cannot embed its own SHA.

## Concerns

The unchanged Phase 5 deadline-continuity test has an exact floating-point
equality flake described above. It is outside this focused remediation.
