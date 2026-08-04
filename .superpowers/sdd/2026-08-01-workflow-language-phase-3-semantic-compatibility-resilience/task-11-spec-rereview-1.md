# Task 11 Specification Rereview — Fix Round 1

## Reviewed identities

- Fix base: `75a7523be9243eb3f300f7ec729ebb10beb03ba2`
- Head: `9e0e6cb3aa87aef2051d966a8a5a4383f55c6991`
- Head tree: `f3cfa393c04902319b17cab9ad253724ae0f89df`
- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Package: `review-75a7523be..9e0e6cb3a.diff`
- The checked-out HEAD/tree and supplied review package matched these identities; the worktree was clean before this permitted report write.
- Scope was the three prior Important findings and the fix diff. The Fix Round 1 report names its covering commands and outputs: the two-file RED/GREEN run (`68 passed, 16 failed` then `84 passed`), the substitution-only ignored-reference RED/GREEN (`72 passed, 2 failed` then `74 passed`), the six-file final command (`219 passed`), and the two-file supplemental command (`173 passed`). Those aggregate results remain report claims rather than independently rerun evidence.
- I independently ran only the two subtle I-3 tests through `scripts/run_tests.sh ... -k 'native_windows_inline_executor or shell_read_failure_after_launch'`; result: `2 passed, 0 failed`. An initial attempt using two node selectors was not accepted by the wrapper (`No test files to run`) and supplied no evidence.

## Finding verdicts

### I-1 — ADDRESSED

`NodeExecutionContext` now carries `normalizer_version` (`plugins/workflow/executors/base.py:77-78`), and the scheduler passes the sealed package value beside the effective profile (`plugins/workflow/scheduler.py:3168-3171`). `BashExecutor` derives one `secure_v3` predicate requiring both Archon and normalizer v3 (`plugins/workflow/executors/bash.py:39-43`) and uses it for the spill directory, strict renderer/wrapper, and Bash evidence (`plugins/workflow/executors/bash.py:56-97`). Thus Archon v1/v2 retain legacy rendering instead of activating Task 11 solely from profile. The tests exercise v2/v3 through a real store/scheduler run and assert the distinct output/evidence contracts (`tests/plugins/workflow/test_bash_e2e.py:320-367`); the historical v1 snapshot shape is read through scheduler variable authority and executed by the real Bash executor (`tests/plugins/workflow/test_bash_e2e.py:370-421`).

### I-2 — NOT ADDRESSED

The admission half is fixed: v3 Bash surfaces now pass parsed output-reference spans through the shared classifier before dependency/path validation, and only admitted spans survive (`plugins/workflow/schema.py:1096-1146`). The direct executor renderer likewise filters ignored spans before resolving them (`plugins/workflow/resources.py:868-896`, `plugins/workflow/resources.py:952-973`).

However, the real scheduler execution path still rescans every strict template with context-blind `iter_output_references()` (`plugins/workflow/scheduler.py:1521-1532`). It then loads outputs only for declared dependencies (`plugins/workflow/scheduler.py:1538-1551`) and attempts to resolve every rescanned reference (`plugins/workflow/scheduler.py:1558-1567`). Consequently an escaped/comment-only `$producer.output`, correctly admitted with no dependency, is rediscovered during preflight and fails as a missing output before `BashExecutor` can ignore it. The added tests cover admission alone (`tests/plugins/workflow/test_phase3_bash_substitution.py:138-152`) and direct `BashExecutor` execution (`tests/plugins/workflow/test_phase3_bash_substitution.py:155-192`), not `RunScheduler`; they cannot expose this sibling path. Unsafe contexts are now rejected at admission, but the required ignored-reference behavior is still broken end to end.

### I-3 — ADDRESSED

The Windows-gated inline executor test selects `_find_bash`, captures the exact `[bash, "-c", rendered_command]` argv and empty inherited-descriptor set, then runs and reaps the real contained process (`tests/plugins/workflow/test_phase3_bash_substitution.py:715-763`). The shell-read-failure test replaces the generated spill descriptor with an open directory descriptor, launches the production-generated prologue through the real executor, and proves nonzero `process_exit`, empty stdout, stderr evidence, parent descriptor closure, and process reaping (`tests/plugins/workflow/test_phase3_bash_substitution.py:766-837`). Both focused tests passed independently. Native Windows Job Object behavior remains owned by the existing managed-process platform suite; this Task 11 test behaviorally connects the inline Windows argv branch to the same containment seam without requiring spill descriptors.

## New breakage with severities

- Critical: 0
- Important: 0 new. The scheduler preflight defect above is the unresolved remainder of I-2, not a newly introduced finding.
- Minor: 0

## Out-of-scope observations

- None. No untouched implementation was re-reviewed beyond the named scheduler preflight authority required to verdict I-2.

## Final verdict

**FAIL.** I-1 and I-3 are addressed, but I-2 remains materially incomplete because the scheduler's pre-claim reference inventory still contradicts the newly shared Bash lexer and rejects escaped/comment-only output references during real runs. Fix the scheduler preflight to use the same Bash span authority and add scheduler-level ignored-reference execution coverage before the next rereview.
