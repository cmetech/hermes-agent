# Workflow Language Phase 6 final fix report

## Scope

This single fix wave closes the four Important findings from the whole-branch review at `d704bae88a`:

1. A structured response carrying an exact tool-call contract now fails terminally when schema validation fails. It cannot enter the generic tool-free repair path, while ordinary action-free, non-contract structured repair remains unchanged.
2. `artifacts: false` Bash and Script attempts now compare no-follow publication-tree identities before and after the attempt. Unchanged pre-existing group artifacts are permitted; new, removed, modified, linked, non-regular, or race-inconsistent entries fail with the existing `artifact_limit` boundary. Exact legacy top-level attempt and publication paths are unchanged.
3. Strict substitution snapshots now key resolved outputs by current/previous scope in addition to node and path, so authored reference order cannot substitute a previous-iteration value for a current value.
4. Phase 6 loop-group body conditions now parse and evaluate `$LOOP_PREV.<body-node>.output[.<path>]` through the authenticated previous-iteration resolver. Iteration 1 has the existing deterministic empty whole-output value, later iterations resolve the immediately previous durable publication, and invalid forward/outer scopes remain admission failures. Top-level and v1-v5 condition grammar/evaluation are unchanged.

No dependency, table, migration, pool, model tool, route, action, snapshot format, or new publication path was introduced. The two accepted deferred Minors were not changed.

## RED evidence

The focused new/control selection initially produced five expected failures:

- the schema-invalid tool-correlated manifest made an unauthorized second repair request;
- an unchanged pre-existing publication caused an artifact-free executor failure;
- current/previous output resolution collided in both affected rendering paths;
- a valid Phase 6 body condition containing `$LOOP_PREV` failed normalization;
- the real distributed Jira aggregate executor failed when loop-group artifacts already existed.

The ordinary non-contract structured repair and unchanged top-level condition controls remained green during RED.

## GREEN implementation evidence

- Modified-suite gate: 291 passed.
- AI/plugin-agent, structured-output, script/artifact, strict-reference, ordinary-loop, and condition gate: 741 passed, 1 skipped.
- Phase 6 language, admission, execution-context, store, scheduler, interaction/recovery, public projection, and Jira gate: 230 passed.
- Historical v3-v5 language, execution, loop, provider-authority, scheduler, crash/fault, and cancellation replay gate: 408 passed.
- Jira/vendor/capability/showcase distribution gate: 155 passed.
- Activation, installed/Desktop distribution, language snapshot, typed-publication recovery, multiprocess, lifecycle, and performance gate: 232 passed, 1 skipped.
- Focused Ruff check: clean.
- `git diff --check`: clean.

All tests used the repository runner with the shared project interpreter. No external Jira or GitLab call was made.
