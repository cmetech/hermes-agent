# Task 9 report: recover typed publications and persistent mirrors

## Outcome

Task 9 is implemented. Checked journal projections are the sole recovery
authority for typed publication identity, winning attempt, source-relative
path, byte count, content digest, metadata digest, schema fingerprint,
canonicalization version, production time, and optional session identity.
Recovery removes incomplete staging and unjournaled finals, verifies complete
bundles, and reconstructs a missing or corrupt journaled bundle only from the
exact corroborated winning-attempt file with the same size and SHA-256. An
unavailable or mismatched winner records stable
`typed_publication_integrity` repair evidence and never invents success.

Persistent Archon AI outputs now also produce profile-local, content-addressed
mirrors. A journaled `typed_mirror_required` obligation precedes immutable
staging. The dedicated mirror lock atomically points the scope index at the
staged entry, `typed_mirror_completed` is then journaled, and an immutable
verification marker finally makes the entry visible. This preserves the
required index-before-completion ordering while ensuring a pending mirror is
invisible to cold-session reads. Restart recovery completes either side of the
index replacement from verified run-bundle bytes only.

## Implementation details

- Extended journaled artifact descriptors with the Task 8 metadata required
  for exact reconstruction and validated partial/malformed typed descriptors,
  Archon authority, succeeded winner identity, bounded paths, media/content
  names, sizes, digests, schema fingerprints, and loop-attempt ownership.
- Added locked publication reconciliation to both current-projection loads and
  projection rebuilds. Staging/discard remnants and unjournaled bundles are
  removed as whole directories; journaled bundles are verified as exact
  two-file units or reconstructed with the existing no-follow descriptor read.
- Reused canonical metadata serialization for initial publication and recovery
  so the checked `metadata_sha256` can be reproduced exactly.
- Applied run/profile/free-disk quota checks before initial and recovery bundle
  writes and retained the secure same-filesystem, no-replace directory commit.
- Added `TypedMirrorStore` below the effective profile Hermes home with
  immutable hash-addressed content, immutable entry documents, invisible
  staged entries, atomic scope indexes, immutable completion markers, bounded
  no-follow reads, and a dedicated scope-index lock.
- Kept mutable provider session IDs out of mirror entries. Mirror identity
  contains only workflow, node, operator scope, run, attempt, publication,
  output/media identity, size, and content hash.
- Added `NodeSessionRegistry.get_mirror()` and `list_mirror_history()` without
  changing the legacy generation-CAS session registry behavior.

## Strict TDD evidence

### Publication recovery RED

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_fault_injection.py
```

Observed expected RED: 2 files, 27 passed and 10 failed. The failures covered
the publication write/fsync/rename/journal/projection boundaries, winner-only
reconstruction, orphan cleanup, quota enforcement, and path/non-regular-file
defenses before recovery existed.

### Mirror visibility/order RED

```text
scripts/run_tests.sh tests/plugins/workflow/test_security_boundaries.py tests/plugins/workflow/test_shutdown_recovery.py
```

Observed expected RED after adding the pending-index contract: 18 passed and 2
failed because `TypedMirrorStore.stage()` and `activate()` did not yet exist.
The completed-journal-before-visibility recovery test also failed until mirror
pointing and verification were separated.

### Stable repair-evidence RED

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication_recovery.py -k invalid_journaled_descriptor
```

Observed expected RED: 0 passed and 1 failed. Rebuild rejected the malformed
checked descriptor but recorded only generic run evidence; the implementation
now records `typed_publication_integrity` as well.

### Focused GREEN

```text
scripts/run_tests.sh tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_security_boundaries.py tests/plugins/workflow/test_persisted_sessions.py
```

Result: 4 files, 44 passed, 0 failed.

Task 8 publication/loop regression:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_loop_executor.py
```

Result: 2 files, 29 passed, 0 failed.

## Exact Task 9 acceptance

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_retention.py tests/plugins/workflow/test_security_boundaries.py
```

Final fresh result: 7 files, 108 passed, 0 failed.

Static verification:

```text
.venv/bin/ruff check plugins/workflow/store.py plugins/workflow/sessions.py tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_retention.py tests/plugins/workflow/test_security_boundaries.py
git diff --check
```

Final result: Ruff passed and the whitespace check was clean.

## Self-review

- Re-read every Task 9 bullet and mapped it to a focused assertion or a
  line-level invariant in the store/session implementation.
- Confirmed every requested before/after publication boundary is injected and
  journal authority stays monotonic across projection loss.
- Confirmed recovery never searches by mtime, latest file, or sibling attempt;
  it uses only the checked descriptor's exact path, size, and digest.
- Confirmed malformed checked descriptors and unreconstructable bundles retain
  stable `typed_publication_integrity` evidence.
- Confirmed bundle cleanup, archive, restore, and quarantine operate on the
  content/metadata directory as one unit.
- Confirmed symlink, reparse-point, traversal, FIFO, size, digest, and profile
  boundaries fail closed without following attacker-controlled targets.
- Confirmed the run lock holds while `typed_mirror_required`, index pointing,
  `typed_mirror_completed`, and visibility verification occur.
- Confirmed a scope index is atomic, pending entries lack verification markers,
  both concurrent immutable history entries survive, and completed replay does
  not overwrite a newer complete pointer.
- Confirmed deterministic entitlement, explicit fresh/shared context, legacy
  runs, and nonpersistent nodes do not create mirror obligations.
- Confirmed mirror documents contain no provider session identifier or other
  mutable provider-session path.

## Files changed

- `plugins/workflow/store.py`
- `plugins/workflow/sessions.py`
- `tests/plugins/workflow/test_typed_publication_recovery.py`
- `tests/plugins/workflow/test_crash_recovery.py`
- `tests/plugins/workflow/test_fault_injection.py`
- `tests/plugins/workflow/test_shutdown_recovery.py`
- `tests/plugins/workflow/test_persisted_sessions.py`
- `tests/plugins/workflow/test_retention.py`
- `tests/plugins/workflow/test_security_boundaries.py`
- `tests/plugins/workflow/test_typed_publication.py`
- `.superpowers/sdd/2026-07-30-workflow-language-phase-2-structured-data/task-9-report.md`

## Concerns

None within Task 9 scope.

## Spec Fix Round 1 — sealed publication authority and reparse coverage

The recovery validator now derives typed-publication obligations from each
successful Archon node's `output_type` in the sealed `definition.yaml`, then
requires exactly one matching checked-journal descriptor. Structured outputs
must carry the exact schema fingerprint and canonicalization version from the
sealed language projection; schemaless outputs must carry no fingerprint.
Missing, demoted, duplicate, wrong-type, and wrong-fingerprint descriptors all
enter stable `typed_publication_integrity` repair state before publication
cleanup, preserving an existing valid bundle.

Publication-root, publication-bundle, winning-source, mirror-directory,
mirror-content, and mirror-index reparse branches now have injected stat-result
coverage. The bundle branch was tightened to fail closed instead of treating a
reparse-marked bundle as replaceable corruption. The existing Task 8
case-sensitive output-type concurrency fixture was aligned so its sealed node
declaration and publication candidate exercise the same valid contract.

### Strict TDD evidence

Focused RED:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_security_boundaries.py -k 'checked_journal_requires_exact_sealed_typed_publication_contract or reparse_point'
```

Observed expected RED: 2 files, 5 passed and 8 failed. All seven forged
checked-journal variants were accepted, and the publication-bundle reparse
point was silently discarded and rebuilt. The already-secure publication
root/source and mirror branches passed as controls.

Focused GREEN: the same command passed 13 tests with 0 failures.

Exact Task 9 acceptance:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_retention.py tests/plugins/workflow/test_security_boundaries.py
```

Result: 7 files, 121 passed, 0 failed.

Task 8 typed-publication and loop regression:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_loop_executor.py
```

Result: 2 files, 29 passed, 0 failed.

Static verification:

```text
.venv/bin/ruff check plugins/workflow/store.py plugins/workflow/sessions.py tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_retention.py tests/plugins/workflow/test_security_boundaries.py
git diff --check
```

Result: Ruff and the whitespace check passed.

### Spec-fix self-review

- Confirmed obligation discovery is independent of optional descriptor marker
  fields and therefore catches whole-descriptor omission and demotion.
- Confirmed output type is compared case-sensitively with the sealed workflow
  declaration and schema identity is compared exactly with the language
  snapshot.
- Confirmed validation precedes orphan/staging cleanup, so malformed authority
  cannot delete a valid publication bundle.
- Confirmed every requested publication and mirror reparse branch fails closed
  while an external sentinel remains unchanged.

Concerns: none within the spec-fix scope.

## Quality Fix Round 1 — anchored recovery, strict replay, and bounded mirrors

All six Important findings from the independent quality review are addressed.

- Publication cleanup now opens the run and publication roots once with
  no-follow directory descriptors. Enumeration, quarantine rename, recursive
  inspection, unlink, and directory removal are descriptor-relative. A
  post-validation root swap cannot redirect cleanup into an external target,
  and unsupported hosts fail closed before any traversal.
- Mirror roots, immutable content and entries, activations, indexes, atomic
  replacement, enumeration, and the scope lock are anchored to a verified
  no-follow descriptor chain. The store has an explicit POSIX capability gate,
  revalidates recorded directory identities, and rejects unsafe or swapped
  parents.
- Scope indexes now have one strict parser: exact keys, schema version,
  timezone-aware update time, generation at least one, and a valid entry ID.
  Completed replay replaces a pending current pointer, preserves only a
  verified activated current entry, and resets malformed generations instead
  of inheriting them.
- Mirror reconciliation parses journal event types before any descriptor-empty
  fast path. Required and completed obligations must remain backed by the
  sealed, verified publication-derived set even when that expected set is
  empty. A mismatch durably enters `typed_mirror_integrity` and removes a
  matching current pointer while retaining immutable history.
- Aggregate profile accounting now includes `workflows/typed-mirrors` for
  admission, publication, recovery, and mirror writes. Mirror staging checks
  free disk and reserves content, entry, activation, index, and temporary-write
  overhead under the mirror lock before its first mutation. Immutable activated
  history is intentionally retained; capacity is bounded by rejecting new
  writes rather than collecting entries that may still be journal-referenced.
- Typed publication descriptors now carry an explicit version. Genuine base
  Task 8 descriptors are upgraded only after bounded descriptor-relative reads
  prove the checked metadata digest, content digest, winning-attempt ownership,
  sealed schema identity, and exact canonical metadata bytes. Recovery appends
  a checked `typed_publication_migrated` projection; versioned forged demotions
  remain rejected.

### Strict TDD evidence

Focused RED for publication cleanup, legacy migration, mirror-obligation
demotion, and aggregate quota:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication_recovery.py -k 'swapped_root or base_typed or mirror_obligation or mirror_profile_quota'
```

Observed expected RED: 5 selected, 0 passed and 5 failed.

Focused RED for descriptor anchoring, capability gating, pending-index replay,
and malformed-index recovery:

```text
scripts/run_tests.sh tests/plugins/workflow/test_security_boundaries.py -k 'swapped_parent or descriptor_relative_io or pending_current or malformed_scope'
```

Observed expected RED: 9 selected, 0 passed and 9 failed.

Final focused GREEN: the two selectors passed 5/5 and 9/9 respectively.

### Verification

Exact Task 9 acceptance:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_retention.py tests/plugins/workflow/test_security_boundaries.py
```

Result: 7 files, 136 passed, 0 failed.

Task 8 typed-publication and loop regression:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_loop_executor.py
```

Result: 2 files, 29 passed, 0 failed.

Static verification:

```text
.venv/bin/ruff check plugins/workflow/store.py plugins/workflow/sessions.py tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_retention.py tests/plugins/workflow/test_security_boundaries.py
git diff --check
```

Result: Ruff and the whitespace check passed.

### Quality-fix self-review

- Confirmed cleanup holds the descriptor it enumerates and recursively removes
  without following symlinks or reparse points.
- Confirmed every mirror operation and lock uses the same verified profile-local
  descriptor chain, while parent swaps leave external sentinels unchanged.
- Confirmed a completed journal can recover over a pending pointer and that
  malformed same-entry and negative-generation indexes are replaced safely.
- Confirmed required-only and completed obligations cannot be silently demoted
  to an empty expected set, and an old activation is no longer current.
- Confirmed quota failure occurs before any content, entry, activation, or index
  file appears in an initially empty mirror store.
- Confirmed genuine base-format journals migrate and retain exact metadata
  bytes, while all versioned forged-demotion regressions continue to fail
  closed.

Concern: native non-POSIX mirror semantics were not executed on this macOS
host; the implementation deliberately fails closed when the required
descriptor-relative and directory-durability primitives are unavailable.

## Spec Fix Round 2 — mirror-index write fail-closed behavior

`TypedMirrorStore.point()` now distinguishes a genuinely absent scope index
from an existing index that cannot be read safely. A missing index can still be
created, but a reparse-marked or otherwise unsafe existing index propagates
`TypedMirrorIntegrityError` before `_atomic_bytes()` can replace it.

The mirror-index coverage now exercises the public `complete()` write path
against an existing reparse-marked index. Its replacement trap is bound to the
exact index target and mutates an external sentinel if execution reaches
`os.replace`; the corrected path raises first, preserves the original index
bytes, and leaves the sentinel untouched. The publication root/bundle/source
and mirror directory/content/read-index cases likewise install target-bound
continuation traps, so their sentinel assertions prove that iteration,
replacement, following, or continued writes do not occur after reparse
detection.

### Strict TDD evidence

Focused RED:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_security_boundaries.py -k reparse
```

Observed expected RED: 2 files, 6 passed and 1 failed. Mirror completion did
not raise for the reparse-marked existing index and reached the exact-target
replacement trap. All already-guarded publication and mirror branches passed
with their connected traps.

Focused GREEN: the same command passed 7 tests with 0 failures.

Exact Task 9 acceptance:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_retention.py tests/plugins/workflow/test_security_boundaries.py
```

Result: 7 files, 122 passed, 0 failed.

Task 8 typed-publication and loop regression:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_loop_executor.py
```

Result: 2 files, 29 passed, 0 failed.

Static verification:

```text
.venv/bin/ruff check plugins/workflow/store.py plugins/workflow/sessions.py tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_retention.py tests/plugins/workflow/test_security_boundaries.py
git diff --check
```

Result: Ruff and the whitespace check passed.

### Spec-fix self-review

- Confirmed unsafe existing index reads cannot fall through to atomic
  replacement, including `replace_current=False` callers.
- Confirmed a genuinely missing index still reaches normal first-generation
  creation.
- Confirmed the write-side regression uses `complete()` rather than only the
  read-side `get()` path and retains the original index bytes on failure.
- Confirmed each reparse sentinel is connected to the exact operation that
  would demonstrate continued traversal or mutation after the guard.

Concerns: none within the spec-fix scope.
