# Task 8 report: atomic winner-only typed publications

## Outcome

Task 8 is implemented. The scheduler converts a successful declared
`PrimaryOutputCandidate` into the immutable store-owned
`TypedPublicationCandidate` immediately before `RunStore.complete_node()`.
Under the run lock, the store validates the active claim first, corroborates
the candidate against exactly one executor artifact, rereads the contained
regular file through the existing descriptor-relative no-follow primitive,
and verifies its byte count and SHA-256.

The store writes a private same-filesystem staging bundle containing
`content.json` or `content.md` plus canonical `metadata.json`, fsyncs both
files and the staging directory, rejects an existing final destination,
renames to `publications/<opaque-id>`, and fsyncs the parent. The resulting
immutable `TypedPublicationRef` decorates the same canonical artifact entry
that is appended to the node-completion journal and projected in `run.json`.
Task 7 therefore continues to consume `descriptor["publication_id"]` without
a second artifact system.

Publication remains Archon-only and winner-only. Failed, paused,
interrupted, cancelled, stale, and undeclared-output completions do not
publish. Hermes legacy behavior is unchanged. Recovery, mirrors, APIs,
Desktop, and Task 9+ behavior were not added.

## Implementation details

- Added the exact frozen/slotted `TypedPublicationCandidate` and
  `TypedPublicationRef` store types.
- Added `typed_publication: TypedPublicationCandidate | None = None` to
  `RunStore.complete_node()` without changing the executor result boundary.
- Preserved case-sensitive open `output_type` values and nullable schema and
  session identities in stable snake_case publication metadata.
- Bounded canonical UTF-8 metadata serialization to 65,536 bytes.
- Used opaque UUID publication IDs and additive artifact descriptor fields:
  `publication_id`, `content_name`, `output_type`, `media_type`, `size_bytes`,
  `sha256`, and `metadata_sha256`.
- Covered command, prompt, Bash, script, loop, approval, empty Markdown,
  canonical JSON, cancellation, stale completion, and a concurrent winner
  race through real scheduler/store behavior.

## Files changed

- `plugins/workflow/store.py`
- `plugins/workflow/scheduler.py`
- `tests/plugins/workflow/test_typed_publication.py`
- `.superpowers/sdd/2026-07-30-workflow-language-phase-2-structured-data/task-8-report.md`

The adjacent node-kind test files required by the acceptance command needed
no compatibility edits; they pass unchanged.

## Strict TDD evidence

### Primary publication RED

Exact command:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py
```

Observed expected RED after correcting test-fixture setup: 1 passed, 6
failed. Every successful node-kind row reached durable node success and then
failed with `KeyError: 'publication_id'` because `RunStore.complete_node()`
only registered executor artifact refs. The cancellation control passed.

### Stale/concurrent RED

Exact command:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py -k 'stale_typed or concurrent_completions'
```

Observed expected RED: 1 passed, 1 failed. The stale pre-staging control
passed, while the concurrent-winner assertion found zero decorated
publication descriptors because the new completion parameter had no bundle
implementation yet.

### Focused GREEN

Exact command:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py
```

Result: 1 file, 9 passed, 0 failed.

### Task 7 interface regression gate

Exact command:

```text
scripts/run_tests.sh tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_resources.py tests/plugins/workflow/test_ai_e2e.py
```

Result: 3 files, 67 passed, 0 failed.

### Exact Task 8 acceptance command

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_loop_executor.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_bash_e2e.py
```

Fresh final result: 7 files, 95 passed, 0 failed.

Static verification:

```text
.venv/bin/ruff check plugins/workflow/store.py plugins/workflow/scheduler.py tests/plugins/workflow/test_typed_publication.py
git diff --check
```

Result: Ruff passed and the diff whitespace check was clean.

## Self-review

- Re-read every Task 8 bullet and mapped it to an observable assertion or a
  line-level implementation check.
- Confirmed claim validation precedes creation of `publications/` or any
  staging directory.
- Confirmed the losing attempt's content and path are absent from winner
  metadata, and only the active attempt's decorated descriptor appears in the
  completion event and projection.
- Confirmed empty content is retained exactly, JSON bytes are copied without
  reparsing, Markdown uses `content.md`, and publication metadata contains no
  canonical body.
- Confirmed both bundle files and directories receive the required fsyncs and
  existing final paths are rejected rather than deliberately replaced.
- Confirmed cancellation never reaches publication and no legacy branch,
  recovery path, mirror, API, or UI was changed.
- Mutation review: removing scheduler candidate conversion, store publication,
  artifact decoration, stale-claim ordering, content copying, or metadata
  hashing would fail at least one focused test.

## Concerns

None within Task 8 scope. Crash recovery for a bundle renamed immediately
before a later journal/projection failure remains intentionally deferred to
Task 9 as specified.
