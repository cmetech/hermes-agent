# Credential Storage Parity Remediation Design

**Date:** 2026-08-16

**Branch:** `feat/hermes-credential-storage-parity`

**Baseline design:** `docs/plans/2026-08-15-hermes-credential-storage-parity.md`

## Context

The credential-storage parity implementation passed its focused tests, macOS
platform verification, and the complete test suite. A final whole-branch
security review then found defects in the reviewed plan and its implementation:

- legacy plugin PATs can reach external-secret child processes before the
  startup environment is scrubbed;
- backend transitions do not have durable authority or revocation semantics,
  so stale credentials can resurface;
- Windows ACL protection is not reapplied after atomic file replacement and
  does not cover encrypted-file keystore artifacts;
- a fresh persistent container volume cannot initialize its encrypted store;
- migration dry-run can perform a mutating OS-keystore probe;
- ordinary filesystem errors during clear escape the stable persistence-error
  boundary; and
- `uv.lock` does not include the new keyring dependency.

This design amends the baseline plan. It preserves its global constraints,
including legacy `load_env()` compatibility until explicit migration, no
plaintext fallback, synchronous OS mutations, bounded OS probes and reads,
complete cross-process file transactions, and strict test isolation from the
developer's real keychain.

## Goals

1. Prevent plugin secrets from entering child-process environments.
2. Make credential authority and revocation durable across backend failures,
   restarts, and mode changes.
3. Give users safe CLI diagnostics and repair paths without manual file
   deletion.
4. Enforce the intended filesystem boundary on Windows and in containers.
5. Restore strict dry-run, stable API errors, and frozen-lock consistency.

## Non-goals

- Storing shadow copies of every secret in both OS and file backends.
- Silently repairing ambiguous or competing credential values.
- Recovering ciphertext after the only master key and every alternate healthy
  copy have been lost. The CLI can reset that store, but it cannot recreate the
  secret value.
- Removing legacy `.env` resolution before an explicit migration.
- Adding a model tool. Recovery is a CLI responsibility.

## Backend Authority Model

### Durable per-key authority

The secrets directory contains a versioned, atomic authority registry. Each
logical storage key has one of three states:

- `os`: the OS keystore is authoritative;
- `file`: the encrypted file store is authoritative; or
- `cleared`: the key was deliberately revoked and must not be rediscovered
  from an unregistered stale copy.

The registry stores no secret values. It is security-sensitive metadata, so it
uses restrictive POSIX permissions or the user-only Windows DACL and is
replaced atomically under the same profile transaction lock used for keystore
mutations. Invalid schema, truncation, or conflicting state is treated as
corruption and routed to the recovery workflow rather than guessed through.

Read-only credential resolution never creates the secrets directory, a master
key, or authority metadata.

### Backend modes

`secret_keystore` remains a `config.yaml` setting; the existing environment
bridge remains internal-only.

- `off` disables reads and rejects ordinary writes and clears. Diagnostic and
  explicitly requested repair operations remain available.
- `auto` chooses a tier only for an unregistered new key. A healthy OS backend
  is preferred; otherwise a usable encrypted file backend is selected.
- `os` and `file` constrain new-key selection. They do not silently redirect a
  key already registered to the other tier.

Changing an existing key's tier is an explicit repair/move transaction, not a
side effect of a read or a transient probe result. Except for `off`, reads keep
using registered authority and `doctor` reports a configuration/backend
mismatch. Ordinary mutations fail with actionable guidance until the operator
runs the explicit repair/move transaction.

### Reads

Registered keys are read only from their authoritative tier. An OS timeout is
latched for the process lifecycle, and concurrent reads abandon at most one
worker. It does not demote mutation authority or expose an unverified file
copy. The ordinary read facade keeps its existing non-throwing contract and
returns unavailable when the authority cannot be read.

A registered file key remains file-authoritative if an OS keystore later
becomes available. A `cleared` key resolves absent without consulting either
tier.

For pre-registry data, resolution may adopt an authority in memory only when
the available tier states are unambiguous. A completed OS check with a value in
exactly one tier adopts that tier; equal values in both tiers use the configured
preference and report a stale duplicate; differing values fail closed. Reads do
not persist that decision. If OS state cannot be determined and a file value
exists, resolution fails closed and directs mutation/recovery callers to
`hermes secrets doctor` and `repair`.

### Writes

Writes to a registered key use its authoritative tier. OS `set` remains
synchronous because a keyring mutation cannot be cancelled safely. File writes
remain a complete locked, atomic transaction.

A new unregistered key first checks for detectable pre-registry copies. If the
state is unambiguous, it adopts or transactionally moves that value; if it is
ambiguous, it fails closed. Otherwise it selects the configured tier, writes
and verifies the value, then records authority atomically.

Writing a deliberately re-entered `cleared` key selects the configured tier,
writes and verifies the new value, then replaces the tombstone with that tier's
authority.

Moving a key between tiers follows this order under the profile transaction
lock:

1. write and verify the destination;
2. delete and confirm absence from the source;
3. atomically update the authority registry.

Failure triggers best-effort compensation and returns a typed persistence
error. The registry continues pointing at the last committed authority, so a
crash or partial move cannot make the uncommitted copy win. `doctor` detects
and `repair` resolves any abandoned destination copy.

Multi-key writes retain batch compensation. Authority changes are committed
only after the entire service-level write succeeds.

### Clears

Clear succeeds only when every tier that can legitimately hold the key and the
legacy `.env` entry are absent. Registered authority makes that set explicit;
unregistered ambiguous data requires repair rather than a false success.

The clear transaction removes the legacy plaintext entry and authoritative
keystore copy, confirms absence, and records a durable `cleared` tombstone.
Ordinary filesystem and backend exceptions are normalized to the existing
plugin persistence error; `BaseException` process-control signals remain
visible. A partial revocation returns an error and preserves enough registry
state for deterministic retry or repair. It never reports success in `off`
mode.

## Recovery CLI

The existing `hermes secrets` command group gains two operations.

### `hermes secrets doctor`

`doctor` is strictly read-only. It reports, without printing secret values:

- configured mode and registered authorities;
- OS read/probe availability using only bounded, non-mutating checks;
- encrypted-store and authority-registry integrity;
- authority/backend mismatches and stale duplicate copies;
- POSIX permission or Windows ACL drift;
- abandoned temporary artifacts; and
- whether the container secrets path is persistent.

It does not create directories, files, keychain entries, or repair plans on
disk. Findings have stable identifiers and severity so tests and support
instructions can refer to them.

### `hermes secrets repair`

Without `--apply`, `repair` prints a deterministic plan and makes no changes.
`--apply` acquires the full profile transaction lock and can:

- restore permissions and ACLs;
- rebuild unambiguous authority metadata;
- remove a verified stale non-authoritative copy;
- resume or roll back an interrupted tier move;
- clean abandoned temporary artifacts; and
- rebuild a damaged tier from a verified healthy authoritative copy.

Tier moves use an explicit `--move-to os|file` option and the transactional
move protocol above. Ambiguous competing values fail closed.

Corrupt artifacts are atomically renamed into a timestamped quarantine below
the secrets directory before replacement. Quarantine is recoverable until the
operator explicitly prunes it.

A corrupt authority registry cannot prove that an absent known plugin key was
never registered: the lost entry may have been a `cleared` tombstone. Default
doctor/repair therefore reports each such key as ambiguous and refuses to
reconstruct authority, even when other surviving tier values are unambiguous.
The explicit `--reset-unrecoverable --apply --yes` recovery quarantines the
corrupt registry under the existing transaction lock, reconstructs surviving
unambiguous `os`/`file` entries, and records `cleared` for every absent known
key. The plan, findings, report, and quarantine manifest contain logical keys
and stable codes only, never secret values. Without `--yes`, a noninteractive
apply refuses before changing any file.

If the master key is lost and no healthy alternate copy exists, repair reports
the affected logical keys as unrecoverable. An explicit
`--reset-unrecoverable --apply` operation quarantines the unusable artifacts,
records cleared state, and initializes a clean store so credentials can be
re-entered. Interactive use requires confirmation; noninteractive use requires
an additional explicit confirmation flag. No command silently deletes the only
remaining copy.

## Child-process Secret Isolation

Startup ordering and final environment scrubbing remain compatible with legacy
secret-source precedence. Before every Bitwarden or command-source spawn,
including Bitwarden's Linux `ldd` check, the child environment is copied and
filtered with the exact `is_plugin_secret_key()` predicate. The process-level
scrub remains after source application as defense in depth.

Tests use real helper subprocesses and prove both that the child cannot observe
a plugin PAT and that the final process environment is scrubbed. Test patches
remain installed until every abandoned bounded worker has finished.

## Windows ACL Integration

A dependency-light Windows permission module owns current-user SID resolution
and restrictive DACL application. Callers use it after every create or atomic
replacement of:

- `.env`;
- the secrets directory;
- the encrypted master key;
- encrypted ciphertext;
- transaction lock files;
- authority metadata; and
- quarantine artifacts.

Directory ACLs include the inheritance and traversal rights required for the
current user. File ACLs grant only the required current-user access. ACL
failure is a typed persistence failure; code never treats `chmod` as an
equivalent Windows security boundary.

The startup `.env` sanitizer, ordinary configuration save, clear, migration,
and file-keystore replacement paths all invoke this shared boundary.

## Persistent Container Initialization

Container and mount detection move into a small shared module with no config or
keystore dependency cycle. A missing file-keystore master key may be created in
a container only when the effective secrets path is on proven persistent
storage. A distinct bind mount or named volume is affirmative evidence only
when the same parsed runtime evidence positively identifies Docker or Podman.
A process identified as containerized only by a union root is an ambiguous
runtime, as is any container not positively identified as Docker or Podman. In
Kubernetes and those ambiguous runtimes, a generic distinct non-memory
filesystem is `UNKNOWN`: disk-backed `emptyDir` and PVC-like mountinfo are not
durable proof. Operators may promote that evidence only by verifying the
deployment's storage class and retention policy and explicitly setting
`security.container_persistence_acknowledged: true` in the active profile's
`config.yaml`. The acknowledgement is read afresh for each inspection and is
never exposed as a `HERMES_*` environment variable.

Overlay, fuse-overlayfs, tmpfs, aufs, and unresolved ephemeral roots continue
to fail closed. If mount information cannot establish persistence, the error
points to a persistent mount, the operator acknowledgement where applicable,
or the explicit recovery/init workflow. An acknowledgement never overrides a
known memory or union filesystem. A fresh documented Docker/Podman `/opt/data`
volume must initialize successfully and survive restart.

## Strict Dry-run and Stable Errors

`hermes secrets migrate --dry-run` never resolves the active backend. It
reports the configured mode rather than an actual auto-selected tier. It
performs no OS set/get/delete probe, prompt-producing backend access, profile
write, or directory creation.

Ordinary clear catches and normalizes filesystem failures from open, temporary
file creation, flush, sync, atomic replacement, permission, and ACL operations.
The REST API continues returning the stable plugin persistence error rather
than a raw internal exception.

## Dependency Lock

Regenerate `uv.lock` with the repository interpreter so the root package
records `keyring`. Verify the result with the frozen lockfile check used by CI
and container builds.

## Test and Verification Strategy

Every implementation slice follows RED/GREEN TDD and uses only
`scripts/run_tests.sh`. Required regressions include:

- a real external-source helper cannot inherit a plugin PAT;
- registered OS authority survives timeout and restart without demotion;
- clear, update, mode change, and interrupted move cannot resurrect stale
  values;
- `off` mutations fail explicitly;
- transaction compensation at every injected boundary;
- `doctor` is byte-for-byte read-only;
- repair of ACL drift, corrupt metadata, stale copies, interrupted moves,
  abandoned temporaries, and a tier recoverable from a healthy copy;
- typed handling and explicit reset for unrecoverable key loss;
- Windows DACL reapplication after every real replacement path;
- successful initialization on a fresh persistent container volume and refusal
  on ephemeral storage;
- a migration dry-run with zero backend or filesystem side effects;
- stable clear errors for the filesystem-failure matrix;
- frozen lockfile verification;
- focused integration tests and the real startup environment regression; and
- the complete repository test suite.

After implementation, repeat the macOS CLI, Electron, and scheduler credential
verification because backend authority behavior changed. Windows, Linux, and
container manual rows remain explicitly outstanding unless those environments
are actually exercised. A final whole-branch security review must pass before
the feature branch is pushed.

## Delivery

Work is sequential. Each remediation task receives a fresh implementation
subagent, RED/GREEN evidence, an atomic commit, and a review checkpoint before
the next task. The feature branch is pushed only after all focused and complete
verification passes. It is not merged into `base` in this session.
