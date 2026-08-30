# Adversarial remediation batch 1 implementation report

## Scope

Closed AR-01 through AR-03 only: v6 loop-group predicate references, closed
`$LOOP_PREV` parsing, and scoped Script predecessor evidence.

## RED evidence

The focused regressions were added before production edits and run with:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_language.py \
  tests/plugins/workflow/test_phase6_scheduler.py -q \
  -k 'group_until_bash or malformed_previous_reference or scoped_renderer_rejects or scoped_script_predecessor'
```

Result: 13 failed, 0 passed. The failures reproduced all three findings:

- group `until_bash` rejected current-body references and could not execute the
  current/outer/previous combination;
- malformed `$LOOP_PREV` continuations were admitted and partially rendered;
- inline and named scoped Scripts with outer-only or body-plus-outer
  dependencies failed the exact predecessor-evidence check.

The same focused command passed 13 tests after the production changes.

The scoped review follow-up added its regressions before the follow-up
production edits and ran:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_language.py \
  tests/plugins/workflow/test_phase6_scheduler.py -q \
  -k 'outside_bash_reference_contexts or outside_references or outer_evidence_wins_undeclared_body_id_collision'
```

Result: 6 failed, 0 passed. Two admission cases and two runtime-rendering cases
showed that malformed previous-reference text in Bash comments and escaped
literals was parsed before lexical exclusion. Inline and named Script cases
showed that an undeclared, skipped body node incorrectly replaced approved
outer predecessor evidence when their IDs collided. The expanded focused
GREEN command was:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_language.py \
  tests/plugins/workflow/test_phase6_scheduler.py -q \
  -k 'outside_bash_reference_contexts or outside_references or outer_evidence_wins_undeclared_body_id_collision or malformed_previous_reference or scoped_renderer_rejects or scoped_script_predecessor'
```

Result: 15 passed, 0 failed.

## Root-cause changes

- Reused the canonical v3 output-reference parser to parse the private v6
  `$LOOP_PREV` prefix with the same closed token boundary. Schema validation,
  prompt/Script rendering, and Bash scanning now consume those canonical token
  spans instead of independent permissive regular expressions.
- Moved group `until_bash` reference admission into the v6 loop-group scope
  validator. It admits current body, approved outer, and previous body outputs,
  validates structured paths and Bash lexical context, and rejects references
  outside those scopes.
- Executed the workerless group predicate with the compiled package's v6
  language profile, normalizer, and body-plus-outer dependency identity.
  Previous outputs remain at iteration N-1 while the predicate evaluates and
  advance only after the predicate decision is recorded.
- Built v6 scoped Script predecessor evidence from the same runtime dependency
  identity as the Script node. A current body state wins an ID collision only
  when it is a declared current-body dependency; otherwise approved outer state
  wins, matching scoped variable resolution. The Script executor's exact-set
  check and predecessor-file cleanup contract remain unchanged.
- For Bash surfaces, reused the existing reference-candidate scanner and Bash
  lexer to admit actual `$LOOP_PREV` candidates before applying the canonical
  strict parser. Comments and escaped literals remain untouched, while genuine
  malformed executable references still fail admission.

## Verification

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_language.py \
  tests/plugins/workflow/test_phase6_scheduler.py \
  tests/plugins/workflow/test_phase6_interactions_recovery.py \
  tests/plugins/workflow/test_phase6_execution_context.py \
  tests/plugins/workflow/test_script_executor.py \
  tests/plugins/workflow/test_phase6_jira_defect_loop.py -q
```

Result after the scoped review follow-up: 6 files, 234 tests passed, 0 failed.

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_language.py \
  tests/plugins/workflow/test_phase3_execution_semantics.py \
  tests/plugins/workflow/test_phase4_language.py \
  tests/plugins/workflow/test_phase4_loops.py \
  tests/plugins/workflow/test_phase5_language.py \
  tests/plugins/workflow/test_phase5_execution_authority_continuity.py -q
```

Result: 6 files, 191 tests passed, 0 failed.

```bash
.venv/bin/ruff check \
  plugins/workflow/language_schema.py \
  plugins/workflow/bash_rendering.py \
  plugins/workflow/resources.py \
  plugins/workflow/schema.py \
  plugins/workflow/scheduler.py \
  plugins/workflow/store.py \
  tests/plugins/workflow/test_phase6_language.py \
  tests/plugins/workflow/test_phase6_scheduler.py
git diff --check
```

Result: Ruff passed and `git diff --check` reported no errors.

## Changed files

- `plugins/workflow/language_schema.py`
- `plugins/workflow/bash_rendering.py`
- `plugins/workflow/resources.py`
- `plugins/workflow/schema.py`
- `plugins/workflow/scheduler.py`
- `plugins/workflow/store.py`
- `tests/plugins/workflow/test_phase6_language.py`
- `tests/plugins/workflow/test_phase6_scheduler.py`
- `.superpowers/sdd/2026-08-29-workflow-language-phase-6-durable-loop-groups/adversarial-remediation-batch-1-report.md`

## Commit

`fix(workflow): align loop group scoped references` — the atomic commit
for the initial batch.

`fix(workflow): preserve scoped collision semantics` — the atomic follow-up
commit containing the review fixes and this report update. Its final SHA is
returned in the task handoff because a commit cannot embed its own SHA.

## Concerns

None.
