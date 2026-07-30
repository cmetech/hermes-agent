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

## Fix Round 1 — specification compliance

Addressed all three Important findings in `task-8-spec-review.md` with a
separate TDD cycle.

### RED evidence

Legacy-profile scheduler gate:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py -k hermes_legacy_primary_output
```

Result: 0 passed, 1 failed. The scheduler attempted typed publication for a
legacy run and surfaced `ArchonOutputIntegrityError('typed publication
requires the Archon language profile')`, proving the store defense existed
but the scheduler gate was missing.

Active-attempt locality:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py -k another_attempt
```

Result: 0 passed, 1 failed. The direct-store regression did not raise
`ArchonOutputIntegrityError`, proving that a contained path owned by another
attempt could be published.

Canonical media type and UTF-8 content contract:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py -k 'each_successful_output_node or noncanonical_text_media_type or invalid_utf8_markdown'
```

Result: 2 passed, 7 failed. Four text-producing node-kind rows retained
`text/plain` instead of `text/markdown; charset=utf-8`; direct-store
regressions accepted `text/markdown`, `application/octet-stream`, and invalid
UTF-8 Markdown bytes.

### Implementation changes

- Passed the sealed `WorkflowLanguageProfile` through every scheduler
  persistence path and only constructs a typed-publication candidate for
  `ARCHON_2026_07`; the store's independent profile defense remains intact.
- Canonicalized known executor `text/plain` primary outputs to
  `text/markdown; charset=utf-8` for Archon typed publication, updating the
  retained candidate and matching artifact descriptor together so Task 7
  corroboration remains consistent.
- Required candidate paths to belong to the active claim's node and attempt,
  recognizing both raw executor paths and the existing securely hashed AI
  attempt paths.
- Restricted typed publication to exact `application/json` or
  `text/markdown; charset=utf-8`, and validated canonical Markdown bytes as
  UTF-8 before staging a bundle.
- Preserved legacy completion behavior: the original `text/plain` artifact is
  retained and no publication directory is created.

### Added and updated coverage

- Updated all successful output-node rows to assert executor media separately
  from the canonical published media.
- Added a legacy-profile primary-output regression covering scheduler
  completion and unchanged artifact shape.
- Added a contained-but-foreign attempt-path rejection regression.
- Added rejection coverage for `text/markdown`,
  `application/octet-stream`, and invalid UTF-8 canonical Markdown.

### GREEN evidence

Focused typed-publication suite:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py
```

Result: 1 file, 14 passed, 0 failed.

Task 7 interface regression gate:

```text
scripts/run_tests.sh tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_resources.py tests/plugins/workflow/test_ai_e2e.py
```

Result: 3 files, 67 passed, 0 failed.

Exact Task 8 acceptance command:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_loop_executor.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_bash_e2e.py
```

Result before final documentation/commit verification: 7 files, 100 passed,
0 failed.

Static verification:

```text
.venv/bin/ruff check plugins/workflow/store.py plugins/workflow/scheduler.py tests/plugins/workflow/test_typed_publication.py
git diff --check
```

Result: Ruff passed and the diff whitespace check was clean.

### Fix-round self-review

- Confirmed scheduler gating uses the sealed enum, not mutable or inferred
  runtime metadata, and every `_persist_result` call from execution passes it.
- Confirmed the default on `_persist_result` keeps direct legacy callers safe
  while the store remains the final enforcement boundary.
- Confirmed path ownership is checked before any publication staging/final
  directory can be created and rejects a sibling attempt within the run root.
- Confirmed media validation is exact rather than prefix-based, JSON continues
  to publish as `content.json`, and all other accepted content is canonical
  UTF-8 Markdown published as `content.md`.
- Confirmed scheduler normalization keeps the candidate identity, artifact
  corroboration, and decorated completion descriptor aligned.
- Confirmed no Task 9 recovery behavior was introduced.

### Fix-round concerns

None within Task 8 scope. Scheduler normalization intentionally covers the
known legacy executor contract (`text/plain`); all other noncanonical media
types are rejected by the store instead of being guessed or silently coerced.

## Fix Round 2 — preserve executor artifact corroboration

Addressed the one new Important finding in
`task-8-spec-rereview-1.md` with a single RED/GREEN cycle.

### RED evidence

Exact command:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py -k candidate_artifact_media_disagreement
```

Result: 1 file, 0 passed, 1 failed. The regression failed with `DID NOT
RAISE ArchonOutputIntegrityError`, proving that a `text/plain` primary-output
candidate sharing path, size, and digest with an `application/json` artifact
was rewritten into manufactured Markdown corroboration and published.

### Change

- Captured the candidate's pre-normalization media type before canonicalizing
  it.
- Required each artifact's original media type to equal that source media type
  before rewriting the matching artifact descriptor to canonical Markdown.
- Added
  `test_typed_publication_rejects_candidate_artifact_media_disagreement`, an
  end-to-end scheduler regression that uses the same path, size, and digest but
  conflicting candidate/artifact media types, expects the existing store
  integrity error, and verifies that no publication directory is created.

### GREEN evidence

Focused regression:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py -k candidate_artifact_media_disagreement
```

Result: 1 file, 1 passed, 0 failed.

Complete typed-publication file:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py
```

Result: 1 file, 15 passed, 0 failed.

Static verification:

```text
.venv/bin/ruff check plugins/workflow/scheduler.py tests/plugins/workflow/test_typed_publication.py
git diff --check
```

Result: Ruff passed and the diff whitespace check was clean.

### Fix-round self-review

- Confirmed normalization now requires agreement across all four executor
  artifact identity fields: relative path, original media type, size, and
  digest.
- Confirmed an inconsistent artifact remains unmodified, so the store's exact
  corroboration check rejects it before publication staging.
- Confirmed valid `text/plain` candidate/artifact pairs still normalize
  together and retain the canonical Markdown contract introduced in Fix Round
  1.
- Confirmed the store enforcement boundary and Task 9 recovery behavior are
  unchanged.

### Fix-round concerns

None within Task 8 scope.

## Quality Fix Round 1 — production paths and atomic integrity

Addressed all four Important findings from `task-8-quality-review.md`. The
user authorized the minimal file-map expansion to
`plugins/workflow/scheduled_revalidation.py` and
`tests/plugins/workflow/test_schedule_revalidation.py` after the production
approval restart exposed the required mutable `publications/` namespace.

### RED evidence — real output-producing node paths

Real Bash executor and scheduler:

```text
scripts/run_tests.sh tests/plugins/workflow/test_bash_e2e.py -k archon_bash_declared_output
```

Result: 1 file, 0 passed, 1 failed. The real Bash node succeeded but produced
zero published descriptors.

Real script executor and scheduler:

```text
scripts/run_tests.sh tests/plugins/workflow/test_script_executor.py -k scheduler_executes_snapshotted_named_script
```

Result: 1 file, 0 passed, 1 failed with `KeyError: 'publication_id'` on the
real JSON stdout artifact.

Real loop executor with only the model boundary stubbed:

```text
scripts/run_tests.sh tests/plugins/workflow/test_loop_executor.py -k scheduler_journals_each_loop_iteration
```

Result: 1 file, 0 passed, 1 failed. Both iterations were durably projected,
but the successful loop had zero published descriptors.

Real approval pause/restart/decision transaction:

```text
scripts/run_tests.sh tests/plugins/workflow/test_approval.py -k approval_survives_restart_captures_trimmed_output
```

Result: 1 file, 0 passed, 1 failed with `KeyError: 'publication_id'` on the
captured approval artifact.

### RED evidence — canonical descriptor authority

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py -k conflicting_same_path_artifact
```

Result: 1 file, 0 passed, 1 failed with `DID NOT RAISE`, proving one exact
artifact plus a conflicting same-path artifact was accepted.

The amended real-loop test also pinned the pre-projected case: after candidate
integration first reached publication, it still found zero decorated projected
artifacts because the completion payload and projection used different
descriptor authorities.

### RED evidence — sealed restart compatibility

Corrected focused command:

```text
scripts/run_tests.sh tests/plugins/workflow/test_schedule_revalidation.py -k 'publication_runtime_root or unsafe_publication_runtime_entries'
```

Result: 1 file, 0 passed, 3 failed. A regular publication bundle was rejected
as an unsealed path, while the symlink/FIFO cases stopped at that same root
error before reaching their unsafe-entry guards.

### RED evidence — destination and durability boundaries

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py -k 'preexisting_publications_symlink or atomically_rejects_existing_final_name or directory_swap_at_commit or directory_fsync_failure'
```

Result: 1 file, 1 passed, 4 failed. The pre-existing final-name control already
passed; a `publications` symlink, a directory-identity swap at commit, and
injected staging/post-rename parent fsync failures all completed without the
required integrity failure.

### Implementation changes

- Added scheduler integration that attaches the authoritative
  `NodeExecutionResult.primary_output` candidate for successful Archon Bash,
  script, and loop nodes with a declared `output_type`. Bash/script select the
  executor's primary first artifact; loop selects the exact current
  `loop_state.output_artifact`.
- Normalized typed loop iteration descriptors before their existing durable
  pre-projection, keeping the final candidate, completion artifact, journal
  payload, and projection identity aligned.
- Routed approval capture through its existing store-owned locked decision
  transaction. Typed approval bytes are written with the existing
  descriptor-relative Archon output writer, published before the approval
  event/projection update, and represented by one identical decorated
  descriptor in both.
- Replaced the universal all-node fake test with real command/prompt executors
  using only a fake model runner. Added real Bash, script, loop, approval, and
  cancel coverage in the approved production-path suites.
- Resolved exactly one publication artifact by path/media/size/digest, rejected
  conflicting same-path executor descriptors, verified any pre-projected
  descriptor's full identity, updated it in place, and reused that same
  canonical decorated dict in the completion journal.
- Replaced pathname-based publication staging with descriptor-relative,
  no-follow run/publication/staging opens; exclusive mode-`0600` file creates;
  descriptor-relative bounded cleanup; strict directory fsync; and atomic
  no-replace commit via Darwin `renameatx_np(RENAME_EXCL)` or Linux
  `renameat2(RENAME_NOREPLACE)`. The implementation verifies the publication
  directory identity immediately before and after commit.
- Hosts without the required descriptor-relative and atomic no-replace
  primitives fail closed before publication mutation.
- Classified `publications/` as a non-authoritative mutable run root during
  sealed-tree revalidation. Traversal remains active, so symlinks and special
  files beneath it are still rejected and sealed-resource identity remains
  unchanged.

### Focused GREEN evidence

Destination and fsync regressions:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py -k 'preexisting_publications_symlink or atomically_rejects_existing_final_name or directory_swap_at_commit or directory_fsync_failure'
```

Result: 1 file, 5 passed, 0 failed.

Complete typed-publication suite:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py
```

Result: 1 file, 17 passed, 0 failed.

Complete amended-path and scheduled-revalidation suite:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_bash_e2e.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_loop_executor.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_schedule_revalidation.py
```

Fresh result after the final GREEN refactors: 6 files, 131 passed, 0 failed.

### Exact Task 8 acceptance

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_loop_executor.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_bash_e2e.py
```

Result: 7 files, 104 passed, 0 failed.

### Task 7 interface regression gate

```text
scripts/run_tests.sh tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_resources.py tests/plugins/workflow/test_ai_e2e.py
```

Result: 3 files, 67 passed, 0 failed.

### Static verification

```text
.venv/bin/ruff check plugins/workflow/store.py plugins/workflow/scheduler.py plugins/workflow/scheduled_revalidation.py tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_bash_e2e.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_loop_executor.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_schedule_revalidation.py
git diff --check
```

Result before final report/commit verification: Ruff passed and the whitespace
check was clean.

### Quality-fix self-review

- Confirmed the only model/process stubs are external boundaries; the real
  scheduler and production executor/store paths perform every publication.
- Confirmed `primary_output` remains the single executor-result authority seen
  by persistence; scheduler integration fills that field before conversion to
  the store-owned candidate rather than adding a parallel metadata channel.
- Confirmed approval publication failure leaves the durable node paused and
  emits no approval-success event; a post-rename fsync failure may leave only
  an unjournaled orphan for the explicitly deferred Task 9 recovery path.
- Confirmed source and destination reads/writes reject symlinks, destination
  staging never follows a swappable pathname, cleanup unlinks only the two
  known files through the staging descriptor, and final commit never replaces
  an existing destination.
- Confirmed directory fsync failures propagate and occur before completion
  journal/projection success.
- Confirmed full identity, not path alone, chooses the one decorated artifact;
  a pre-projected loop artifact is replaced in place and the journal/projection
  descriptors are equal.
- Confirmed sealed-tree revalidation still walks mutable publication content
  to reject symlink and special entries while excluding ordinary bundle bytes
  from the immutable snapshot digest.
- Confirmed no Task 9 recovery, mirroring, API, or UI behavior was added.

### Quality-fix concerns

Atomic typed publication is supported on Darwin and Linux with their native
no-replace primitives. Other hosts deliberately fail closed before filesystem
mutation until an equivalent descriptor-safe atomic primitive is implemented.

## Quality Fix Round 2

Addressed the quality re-review's remaining Important approval-retry finding.
Typed approval capture now treats its deterministic descriptor-relative source
as idempotent only when a retry reads the exact expected path and verifies the
expected byte size and SHA-256 digest. A conflicting same-size source is
rejected before publication. The retry path does not delete by pathname and
does not add Task 9 orphan recovery behavior.

### TDD RED evidence

The initial public-API regression injected a staging-directory fsync failure
after `output.md` was created, restored the fsync boundary, and retried the same
pending interaction:

```text
scripts/run_tests.sh tests/plugins/workflow/test_approval.py -k typed_approval_retries_after_publication_fails_post_source_write
```

Initial result: 1 failed. The retry raised
`ArchonOutputIntegrityError: Archon output attempt already exists`.

After adding descriptor-relative source reuse, the same test was extended with
a same-size byte substitution. Its second RED run failed because rejection was
still deferred to bundle publication (`typed publication content digest does
not match`) instead of the approval-source identity boundary.

### GREEN evidence

The focused retry and identity regression passed after exact source verification:

```text
scripts/run_tests.sh tests/plugins/workflow/test_approval.py -k typed_approval_retries_after_publication_fails_post_source_write
```

Result: 1 passed, 0 failed.

Approval and typed-publication suites:

```text
scripts/run_tests.sh tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_typed_publication.py
```

Result: 29 passed, 0 failed.

Exact Task 8 acceptance:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_loop_executor.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_bash_e2e.py
```

Result: 105 passed, 0 failed.

Task 7 interface regression gate:

```text
scripts/run_tests.sh tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_resources.py tests/plugins/workflow/test_ai_e2e.py
```

Result: 67 passed, 0 failed.

Scheduled revalidation was not rerun in this round because neither its
implementation nor its tests changed. Ruff and `git diff --check` were clean
for the amended production and test paths before the final verification gate.
