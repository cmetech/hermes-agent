# Phase 3 Task 2 Final Quality Closure Rereview 2

**Reviewed HEAD:** `849405605ca6b391906b46032a5f4d0c40c0695e`

**Reviewed tree:** `67be8f04c3a2c81d9d86643f2ea36360f56fe2ab`

**Test-closure baseline:** `e8c36c6c5`

**Approved production baseline:** `ad2157a8ef217cbe540ed89f826f96062fa80bcb`

**Verdict:** APPROVED

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 0 |
| Minor | 0 |

## Scope

I inspected the complete `e8c36c6c5..849405605` closure diff and rechecked it
against the remaining scheduled-boundary concern in the prior specification
rereview and against every production-quality closure recorded in
`task-2-quality-rereview-1.md`. The closure commit changes only
`tests/plugins/workflow/test_phase3_execution_semantics.py`. There are no
production changes relative to the already approved `ad2157a8e` production
state, so the canonical-byte, stable-error, atomic failure, v3 sealed-authority,
legacy compatibility, and bounded-evidence conclusions remain intact by exact
production identity.

## Scheduled authorization, revalidation, and promotion coverage

The new `_RecordingAuthorizationRunStore` is a valid observational seam. Its
override calls `super()._scheduled_promotion_authorization()` unchanged and
only records the returned opaque capability. It neither supplies a verifier,
forges authority, relaxes validation, nor replaces consumption.

The successful scheduled fixture now crosses the actual production chain:

1. `start_api_run()` admits the trusted profile package with the production
   runner binding and authentic scheduled metadata.
2. The test changes current profile execution semantics and creates a new
   store/scheduler instance with a newly acquired coordinator lease and real
   `ExecutionFence`.
3. `RunScheduler.advance()` invokes the unmodified
   `_authorize_scheduled_promotion()` path, including sealed-snapshot
   verification, scheduled catalog/trust/execution-context revalidation, and
   store-issued opaque authorization.
4. The unmodified promotion transaction consumes that authorization, records
   `schedule_revalidation`, journals one `run_promoted` event, claims the
   deterministic first Bash node, and executes it successfully.

The assertions verify one authorization was issued and consumed, exactly one
promotion event exists, the scheduled evidence is durable, and the admitted
`resources.json` bytes survive restart/current-config change exactly.

The mismatch fixture also uses real `start_api_run()` admission, production
binding, coordinator fence, restarted store, and `advance()`. It reseals every
authenticated snapshot digest consistently so the intentional semantic
inconsistency reaches the Task 2 semantics verifier after genuine scheduled
authorization/revalidation. That is fixture construction, not a bypass: the
test does not replace `_authorize_scheduled_promotion()`, call the private
authorization factory, or provide a no-op verifier. Its sole scheduler
monkeypatch observes whether `_execute_claim()` is reached. The resulting
assertions prove the stable semantic mismatch code, consumed authorization,
empty node-attempt history, zero indexed worker claims, zero executor calls,
and durable `run_failed` validation evidence.

## Fixture isolation and resilience

- `HERMES_HOME` is explicitly pinned to the temporary profile, aligning
  catalog discovery, trust state, API admission, Gateway, showcase, restart,
  and persistence in one isolated namespace.
- The scheduled parity run is admitted first with `concurrency_policy="allow"`,
  so same-key work admitted by the other parity surfaces cannot create an
  overlap-policy rejection.
- The restarted store uses `max_executing_runs=8`; the five immediate
  boundary runs therefore cannot manufacture a capacity block for the
  scheduled promotion.
- The node id `a-shell` makes the first sorted claim deterministic. It executes
  a harmless real Bash command, avoiding model/provider dependence while still
  proving promotion reached execution.
- The checks assert behavioral relationships (identical bytes/digests,
  capability lifecycle, claim absence, event presence) rather than source
  text, catalog counts, implementation call counts, or unstable timing.

No shortcut or brittle test-only authority was found.

## Prior production and quality closure

All prior Task 2 quality findings remain closed:

- canonical float and complete canonical-resource byte authority is unchanged;
- scheduled semantic failure still uses the bounded stable
  `workflow_execution_semantics_mismatch` code and claim-free atomic failure;
- Archon-v3 resume still takes its five semantic fields from the authenticated
  snapshot without consulting the legacy current-config resolver;
- unversioned and `hermes-legacy` behavior remains on the unchanged legacy
  branch;
- the same authenticated package still proves complete semantics bytes,
  resource digest, and manifest-digest parity across CLI, Gateway, API,
  scheduled API, showcase, and direct-store admission; and
- the change adds no API/model-tool surface, persistence channel, unbounded
  evidence, provider response, Phase 4 loop/include behavior, or later-phase
  scope.

## Verification evidence

The exact Task 2 gate passed through the repository wrapper with retries
disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_execution_semantics.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_language_snapshot.py \
  tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_api_runtime.py \
  tests/plugins/workflow/test_scheduled_runs.py \
  tests/plugins/workflow/test_showcase_schedule_e2e.py \
  tests/plugins/workflow/test_crash_recovery.py

Result: 8 files, 299 tests passed, 0 failed, no retries.
```

The directly adjacent scheduler/store/retry/deadline/revalidation gate also
passed with retries disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_scheduler.py \
  tests/plugins/workflow/test_store.py \
  tests/plugins/workflow/test_deadlines.py \
  tests/plugins/workflow/test_retry.py \
  tests/plugins/workflow/test_schedule_revalidation.py

Result: 5 files, 126 tests passed, 0 failed, no retries.
```

Ruff passed for the modified test file. `git diff --check` passed for the
test-closure range and for the full reviewed range from the approved production
baseline. This rereview made no production or test edits and preserved the
independently created untracked specification rereview present at handoff.

## Conclusion

The final test-only closure supplies genuine scheduled authorization,
revalidation, promotion, immutable-semantics, and pre-claim mismatch evidence.
Task 2 is quality-approved at the reviewed HEAD with 0 Critical, 0 Important,
and 0 Minor findings.
