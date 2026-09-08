# Workflow Marketplace Transaction Workspace Isolation Amendment

**Date:** 2026-09-06

**Status:** Approved, including the portable trusted-directory threat boundary

**Amends:** [Workflow Marketplace Lifecycle Recovery Amendment](2026-09-04-workflow-marketplace-lifecycle-recovery-amendment.md)

## 1. Problem and evidence

Task 15 real-backend testing proved that two independent authorities use the same profile paths with incompatible invariants:

| Owner | Shared paths | Required mode | Ownership marker and cleanup rule |
| --- | --- | --- | --- |
| Workflow `RunStore` | `<profile-home>/workflows/.staging`, `.quarantine` | Existing run-store policy, commonly created as `0755` | `.snapshot-owner.json`; unknown or abandoned children may be quarantined during admission-index reconciliation |
| Marketplace transaction store | The same paths | Exact private `0700` directories | `owner.json`; only identity-bound marketplace envelopes may be recovered or removed |

The collision fails in both initialization orders:

- When `RunStore` initializes first, marketplace preparation rejects the shared `0755` directory with `transaction_destination_invalid`.
- When marketplace preparation initializes first, subsequent `RunStore` reconciliation treats its live `owner.json` envelope as an orphan run snapshot and moves it. Marketplace confirmation then returns `transaction_candidate_changed` before mutation.

These are genuine production integration failures, not browser-fixture failures. Sharing the directories also violates the existing rule that cleanup code may act only on artifacts whose ownership it can prove.

## 2. Decision

Each subsystem owns a disjoint workspace. Existing `RunStore` paths and behavior remain unchanged. Marketplace transaction scratch state moves beneath the already private marketplace state root:

```text
<profile-home>/workflows/.staging                 RunStore only
<profile-home>/workflows/.quarantine              RunStore only

<profile-home>/marketplace/workflows/.staging     Marketplace transactions only
<profile-home>/marketplace/workflows/.quarantine  Marketplace transactions only
```

`WorkflowSourceStore.root`, currently `<profile-home>/marketplace/workflows`, remains the shared locked marketplace state authority. The marketplace transaction store derives both scratch roots from that exact root rather than reconstructing a path under `<profile-home>/workflows`.

The new marketplace directories retain the transaction store's existing security contract:

- directory mode `0700` on platforms with POSIX mode enforcement;
- no symlink or reparse-point traversal;
- descriptor/identity rechecks at every observable transaction phase;
- bounded identity-owned `owner.json` envelopes;
- atomic writes, directory fsyncs, journal correlation, and existing transaction-lock ordering;
- cleanup and recovery limited to validated marketplace-owned envelopes.

No public API field, operation schema, package format, installed package destination, trust model, source-cache schema, or Desktop correlation rule changes.

### 2.1 Portable threat boundary

The private profile home, its `0700` marketplace ancestors, and the marketplace lock form the supported concurrency boundary. The implementation must safely handle crashes, restarts, multiple cooperating Hermes processes, RunStore activity, stale or foreign artifacts, symlinks/reparse points present when a path is examined, and ownership changes injected between observable transaction phases.

A separate hostile process already running as the same OS account can modify any file that Hermes can modify. Portable POSIX and Windows APIs do not provide one cross-platform operation that both creates a directory and returns its identity-pinned handle, nor one that conditionally deletes a directory name only if it still resolves to a previously observed inode. Replacement inside the unobservable interval between those individual filesystem calls is outside this feature's threat model. Closing that interval would require an archive/blob transaction format or OS-specific native filesystem implementation and is not part of this release.

This boundary does not permit optimistic success:

- every durable transaction-state publication and installed-package mutation still requires the expected marketplace root, scratch root, envelope marker, journal and package identity to validate;
- a detected authority change fails the operation before it can claim committed state;
- candidate scratch bytes are never current installed-package authority;
- cleanup validates the current identity and exact ownership marker immediately before removal; a mismatch or incomplete marker is retained and reported for recovery/diagnosis rather than guessed or deleted;
- confirmation, rollback and recovery continue to rely on locked journals and verified installed bytes, not on the apparent presence of a staging directory.

Descriptor-relative operations and Windows directory guards remain defense in depth. They should be used where supported and kept bounded, but review acceptance must not claim or test a stronger guarantee than the portable trusted-directory boundary above.

## 3. Rejected alternatives

### Teach `RunStore` to recognize marketplace envelopes

This would require the mature run store to understand another subsystem's marker schema, lifetimes, transaction IDs, and recovery states. It would still leave incompatible directory permissions and would couple future cleanup changes across authorities.

### Move or relax the `RunStore` workspace

Run storage predates the marketplace and has existing admission, recovery, and evidence semantics. Moving it creates a much larger migration and regression surface. Relaxing marketplace privacy would weaken storage containing candidate and previous package bytes.

### Share directories with name prefixes

Prefixes do not solve permission disagreement or unknown-marker reconciliation. Correctness would still depend on both subsystems permanently coordinating naming and cleanup logic.

## 4. Lifecycle and recovery behavior

New marketplace preparation, confirmation, rollback, cancellation, removal, abandoned-staging cleanup, and `recover-packages` inspect only the marketplace-owned roots. Creating or reconciling `RunStore` before, during, or after a marketplace operation cannot enumerate, move, delete, chmod, or reinterpret marketplace envelopes.

Likewise, marketplace recovery never enumerates or classifies run snapshots. Foreign files in either marketplace scratch root remain subject to the existing fail-closed bounded ownership checks; the implementation must not broaden cleanup authority merely because the parent directory is now isolated.

The installed destination remains under the existing workflow package layout. Only temporary transaction and rollback workspaces move. Transaction journals continue to record exact absolute staging/quarantine paths and must match the newly derived roots before any recovery mutation.

## 5. Migration and compatibility

The marketplace lifecycle is unreleased on this isolated feature branch, so the former shared scratch location is not a supported deployed storage contract. There is deliberately no automatic migration from `<profile-home>/workflows/.staging` or `.quarantine`:

- those directories are now exclusively `RunStore` authority;
- marketplace code must not scan them to guess whether an entry is a pre-release transaction;
- moving an entry based only on its name or `owner.json` would cross an ownership boundary and could race `RunStore` reconciliation.

If pre-release marketplace `transactions.json` or transaction journals contain the former absolute paths, existing strict path-consistency validation must fail before filesystem mutation. The state remains available for diagnosis; the implementation does not rewrite paths or claim recovery succeeded. No released V1/V2 wire compatibility is affected.

Existing installations with only ordinary workflow runs are unchanged. Existing marketplace sources, catalog cache, installed package provenance, trust grants, receipts, and operation history are unchanged because none live in the moved scratch roots.

## 6. Required tests

The Task 15 integration suite must prove user-visible and filesystem truth with real temporary homes:

1. `RunStore` initializes first; marketplace prepare and confirm succeed, and the operation reaches committed terminal truth.
2. Marketplace prepare occurs first; initializing and reconciling `RunStore` leaves the exact live marketplace envelope unchanged; confirm succeeds.
3. Restart/journal recovery uses only the new roots and converges for install, update, and remove.
4. Run-store orphan reconciliation cannot observe or quarantine marketplace envelopes; marketplace abandoned-staging recovery cannot observe or remove run snapshots.
5. Both marketplace roots and ownership envelopes satisfy private-mode, no-follow, bounded-marker, exact-root, and foreign-artifact refusal tests.
6. A persisted pre-release record naming the former shared root is rejected before mutation and is never silently migrated, deleted, or reported as recovered.
7. The real Desktop lifecycle fixture, which initializes the actual workflow runtime and marketplace API together, reaches install preparation/confirmation instead of `transaction_destination_invalid` or `transaction_candidate_changed`.
8. Phase-boundary fault tests replace roots/envelopes before validation, before durable state publication, and before installed mutation; each detected change fails without a success claim and preserves unproven artifacts. Tests must not monkeypatch inside a filesystem call and present same-account syscall-boundary replacement as a portable guarantee.

These tests supplement rather than replace the existing transaction fault-injection, rollback, journal, trust, and run-store recovery suites.

## 7. Acceptance boundary

This amendment is complete when both initialization orders and restart recovery pass independently, all existing run-store and marketplace transaction tests remain green, the real-backend Desktop lifecycle proceeds through confirmation, and a fresh reviewer verifies that neither subsystem normally enumerates or mutates the other's scratch workspace and that every detected phase-boundary authority change fails before durable success or installed mutation.

Any need to read, migrate, or delete an artifact in the former shared directories must stop as a new ownership-design issue. It must not be implemented as a filename, timestamp, or marker-shape heuristic.
