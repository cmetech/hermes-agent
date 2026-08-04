# Phase 3 Adversarial Review Remediation Plan

> **Execution note:** Follow this plan test-first with `superpowers:executing-plans`. Do not run or add threat-model/security validation. Use only ordinary offline functional, crash-recovery, accounting, and release-contract tests.

**Goal:** Resolve the one CRITICAL and seven HIGH Phase 3 review findings without weakening prompt-cache stability, provider-call accounting, execution fencing, or the user's validation restriction.

**Architecture:** Keep the fixes at their existing ownership boundaries: Bash admission/rendering in `bash_rendering.py`, durable accounting/transitions in scheduler/store, session transport typing in `agent/plugin_agent.py`, AI recovery classification in the AI executor, and upstream/release protection in the customization ledger and focused merge gate. Treat every durable interaction pause as continuation of the same logical attempt; charge the retry ledger only when that attempt reaches a non-paused outcome.

**Test discipline:** For each task, add the smallest ordinary regression test, run it to observe the intended failure, implement the fix, and rerun the focused file. The two suites prohibited by the active user override (`test_phase3_bash_lexer_security.py` and `test_persistent_session_recovery.py`) remain outside automated release gates; targeted ordinary tests in the latter may be run by exact node ID only when needed for crash/accounting remediation.

---

## Task 1: Close arithmetic-command Bash admission gaps (C1)

**Files:**
- Modify: `plugins/workflow/bash_rendering.py`
- Test: `tests/plugins/workflow/test_phase3_bash_substitution.py`

1. Add table-driven admission and executor tests for references used as operands to `let` and to `declare -i`, `typeset -i`, and `local -i`, including `command`/`builtin`, quoted command words, and combined option spellings.
2. Run the new tests and confirm the classifier currently admits those references.
3. Extend the authored Bash lexer state so arithmetic-imposing command operands are rejected with the existing `bash_substitution_context` contract, while ordinary declarations and ordinary command arguments remain unchanged.
4. Run the focused tests plus the existing Bash substitution file.
5. Commit as `fix(workflow): reject arithmetic builtin substitutions`.

## Task 2: Bound final rendered Bash command size and stop loop re-rendering (H6, H7)

**Files:**
- Modify: `plugins/workflow/language_schema.py`
- Modify: `plugins/workflow/bash_rendering.py`
- Modify: `plugins/workflow/executors/loop.py`
- Modify: `skills/software-development/workflow-builder/references/portable-schema.md`
- Modify: `website/docs/user-guide/features/workflow-yaml-reference.md`
- Test: `tests/plugins/workflow/test_language_schema.py`
- Test: `tests/plugins/workflow/test_phase3_bash_substitution.py`
- Test: `tests/plugins/workflow/test_loop_executor.py`

1. Add a contract test for a platform-independent final rendered-command UTF-8 byte ceiling and an executor test showing repeated individually-inline substitutions fail as `bash_substitution_limit` before launch.
2. Add a v3 `until_bash` regression proving model-produced reference-like text remains literal after the first render.
3. Run the new tests to expose aggregate growth and double rendering.
4. Add and export a conservative final-command byte limit, enforce it before spill materialization/launch, expose it in generated language metadata, and document it on both portable-schema surfaces.
5. Pass the already-rendered `until_bash` command to `BashExecutor` without a second variable context.
6. Run the focused Bash, schema, and loop tests.
7. Commit as `fix(workflow): bound and single-render bash commands`.

## Task 3: Preserve retry history through durable reference transitions (H2)

**Files:**
- Modify: `plugins/workflow/store.py`
- Test: `tests/plugins/workflow/test_phase3_resolution_waits.py`

1. Add store/scheduler regressions where a v3 consumer has prior attempt history and retry consumption before a transient wait and before a terminal reference failure.
2. Run them to reproduce the rejected transition/livelock condition.
3. Remove the invalid zero-history precondition from reference-only transitions and preserve, rather than reset, `retry_consumed`; keep the separate condition-transition zero-attempt invariant unchanged.
4. Run the focused resolution-wait suite.
5. Commit as `fix(workflow): retain retry history across reference waits`.

## Task 4: Exempt interaction-only pauses without undercharging providers (H1)

**Files:**
- Modify: `plugins/workflow/scheduler.py`
- Test: `tests/plugins/workflow/test_approval.py`
- Test: `tests/plugins/workflow/test_loop_executor.py`

1. Add v3 scheduler tests for approval reject/rework and interactive loop input proving pause/resume does not consume their one-attempt workflow grant.
2. Add a v3 AI action-grant assertion proving its pause remains a resumable continuation rather than exhausting a zero-retry workflow.
3. Run the tests to reproduce approval/loop exhaustion.
4. Skip retry-ledger charging for paused results; feature-specific approval/rework, loop-iteration, and agent-iteration bounds continue to govern those resumable continuations.
5. Run the focused approval, loop, and retry-accounting tests.
6. Commit as `fix(workflow): exclude interaction waits from retry grants`.

## Task 5: Make private session authority durable before journal activation (H3)

**Files:**
- Modify: `plugins/workflow/store.py`
- Test: `tests/plugins/workflow/test_persistent_session_recovery.py`

1. Change the existing fenced selection crash-window test to require the private authority row and successful restart after journal fsync.
2. Add the symmetric fenced winning-completion crash-window regression.
3. Run those exact ordinary crash-recovery test node IDs and confirm current rollback leaves the journal unloadable.
4. Add a store helper that commits private authority, immediately begins a new transaction, and revalidates the same execution fence before the activation journal append. Use it at selection and winning completion; retain the existing no-fence precommit behavior.
5. Rerun the exact crash-window tests and adjacent authority-corruption/precommit tests.
6. Commit as `fix(workflow): durably anchor session journal transitions`.

## Task 6: Separate pre-provider session failures from post-provider protocol failures (H4)

**Files:**
- Modify: `agent/plugin_agent.py`
- Modify: `plugins/workflow/executors/ai.py`
- Test: `tests/agent/test_plugin_agent.py`
- Test: `tests/plugins/workflow/test_persistent_session_recovery.py`

1. Add runner tests for a typed preflight session-store failure and a typed post-worker result/protocol failure.
2. Add exact AI-executor tests proving only the preflight failure maps to `persistent_session_recovery_unavailable` with exact zero attempts; post-provider failure and an empty returned session ID must carry conservative unknown-outcome accounting and must not claim `known_no_effect`.
3. Run the new exact test node IDs and observe the overbroad mapping.
4. Introduce public typed exceptions for preflight session unavailability and post-worker result/protocol invalidity. Wrap only their true source boundaries in `PluginAgentRunner`.
5. Narrow the AI recovery catch to the preflight type and map post-provider/empty-session outcomes conservatively while retaining recorded recovery outcomes.
6. Run the focused plugin-agent tests and exact recovery test node IDs.
7. Commit as `fix(workflow): distinguish session preflight from outcome uncertainty`.

## Task 7: Protect provider-attempt transport seams during upstream merges (H5)

**Files:**
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Modify: `scripts/test_workflow_merge_gate.sh`
- Modify: `tests/scripts/test_workflow_merge_gate.py`
- Create: `tests/agent/test_provider_attempt_transport.py`

1. Add focused ordinary offline tests exercising the production transport boundaries for chat completions, Anthropic fallback attempts, Codex Responses/app-server, and the central run-agent bridge; assert one reservation immediately before each outward transport attempt.
2. Run the new file and confirm any uncovered path.
3. Add one `agent-core-generic` customization-ledger entry owning `run_agent.py`, `agent/anthropic_adapter.py`, `agent/chat_completion_helpers.py`, `agent/codex_runtime.py`, and `agent/provider_attempts.py`, with the exact transport seams and focused test listed.
4. Add the focused test to the base gate. Remove the prohibited Bash lexer suite from the regression-gate ledger claim/test list and explicitly keep both prohibited suites excluded from the executable gate.
5. Strengthen the gate contract test to require the provider transport file exactly once and to reject either prohibited suite.
6. Run the focused transport and merge-gate contract tests, then run `scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml --diff $(git merge-base base HEAD)..HEAD` if the local dependency contract is available.
7. Commit as `test(workflow): protect provider attempt transport seams`.

## Task 8: Reconcile the BLOCK verdict and verify the repaired phase

**Files:**
- Preserve: `docs/reviews/2026-08-04-workflow-language-phase-3-adversarial-review-fable-5.md`
- Modify: Phase 3 continuation/progress/verification documents only if their current contract requires the remediation result.

1. Run all directly changed test files in isolated per-file invocations.
2. Run the retained Phase 3 allowlist/base gate with the active prohibited-suite exclusions and no threat-model/security validation.
3. Inspect `git diff --check`, worktree status, and the full commit range.
4. Record which findings were confirmed, which were scope-corrected, the fix commit for each, and exact verification evidence.
5. Commit documentation reconciliation as `docs(workflow): record adversarial remediation` if artifacts changed.
