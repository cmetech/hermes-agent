# Adversarial remediation batch 3 implementation report

## Scope

Closed AR-07 and AR-08 only: attempt-owned zero-artifact workspaces for v6
Bash and Script nodes with sealed `artifacts: false`. Recovery, public
publication, schema, persistence, and the filesystem snapshot mechanism were
not redesigned.

Candidate base: `0d69eb4c04c17e384531644e230677e40435fc11`.

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

## Verification

Focused GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_scheduler.py -q \
  -k 'attempt_private_rendered_workspace or retry_keeps_failed_residue or ignores_concurrent_child_publication or filesystem_checks_fail_closed'
```

Result: 1 file, 16 tests passed, 0 failed, 30 deselected.

Required Phase 6 context/scheduler/recovery gate:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_execution_context.py \
  tests/plugins/workflow/test_phase6_scheduler.py \
  tests/plugins/workflow/test_phase6_interactions_recovery.py -q
```

Result: 3 files, 95 tests passed, 0 failed.

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

Result: 9 files, 356 tests passed, 0 failed.

Required historical compatibility gate:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_execution_semantics.py \
  tests/plugins/workflow/test_phase4_loops.py \
  tests/plugins/workflow/test_phase5_execution_authority_continuity.py -q
```

Result: 3 files, 124 tests passed, 0 failed.

Static gates:

```bash
.venv/bin/ruff check \
  plugins/workflow/scheduler.py \
  tests/plugins/workflow/test_phase6_scheduler.py
git diff --check
```

Result: Ruff reported `All checks passed!`; `git diff --check` reported no
errors.

## Changed files

- `plugins/workflow/scheduler.py`
- `tests/plugins/workflow/test_phase6_scheduler.py`
- `.superpowers/sdd/2026-08-29-workflow-language-phase-6-durable-loop-groups/adversarial-remediation-batch-3-report.md`

## Commit

`fix(workflow): isolate artifact-free attempts` — the atomic Batch 3 commit
containing the scheduler fix, behavioral regressions, and this report. Its
final SHA is returned in the task handoff because a commit cannot embed its own
SHA.

## Concerns

None.
