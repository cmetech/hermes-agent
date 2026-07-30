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
- `.superpowers/sdd/2026-07-30-workflow-language-phase-2-structured-data/task-9-report.md`

## Concerns

None within Task 9 scope.
