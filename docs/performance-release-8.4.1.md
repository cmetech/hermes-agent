# OTTO / LOOP24 v8.4.1 performance release

## Approved scope

On 2026-09-22 the user approved a performance-only integration into `base`,
excluding unfinished workflow/marketplace repairs. They then explicitly
requested production v8.4.1 rather than an RC, acknowledging that they are the
only current user and will test the release afterward.

Included:

- Desktop connection ownership, deadlines, retry fencing and profile isolation,
  already integrated in `199a16f353` with the page-responsiveness work.
- Shared lazy imports, visible navigation feedback, ready-gated prefetch,
  scoped caching and progressively loaded Model Settings.
- Install/update-only Python bytecode preparation from `17c44a5501`.
- Bounded Windows ZIP-update rename retries from `c5ec546acd`, without unrelated
  production repairs from intermediate commits.
- Updater-only test isolation from `bc52fc43f5`; no environment/runtime patch
  from that mixed commit is included.

The new Python helper benefits Desktop, CLI and TUI installation/startup; it
does not change the core agent loop, prompt caching, tool schemas or sessions.
Missing/unwritable bytecode remains warning-only. A first update executed by
an older updater may need a subsequent update to backfill preparation; the
new installer prepares it immediately. See `python-bytecode-precompile.md`.

## Explicitly deferred, not waived as passing

The unfinished `perf/install-bytecode` worktree at
`C:/wt/hermes-desktop-connection` retains the later validation/runtime repairs,
including all uncommitted native workflow changes. It is not merged wholesale.
The 399 marketplace-transaction failures were observed there, not established
as 399 regressions in this isolated release. They remain a separate investigation.

The repository's full suite is not green/complete. Historical baseline failures
include LSP shell-output diagnostics and Codex test SQLite cleanup; later repair
work also found compression/deadline, media/path and native workflow defects.
None is claimed fixed by this performance release. Its gates are the selected
installer/update/cache and Desktop lifecycle/navigation contracts, plus branded
generation/build checks. Any new failure in those gates must be resolved or
explicitly surfaced, not silently skipped.

Manual packaged Windows UAT, controlled before/after startup measurements,
native macOS/Linux runtime UAT, remote SSH/OAuth and sleep/wake acceptance remain
pending. Native CI installer builds do not certify those runtime scenarios.
Precompilation reduces compilation work, not all backend initialization costs.

## Distribution contract

Both products build from pinned source commits descended from the same tested
base. Existing `cmetech/otto` and `cmetech/loop24` release workflows own stable
v8.4.1 publication. No product tag/release is created in `cmetech/hermes-agent`.
No live local installation/profile is replaced by this work. Source and test
ownership are recorded in the existing Desktop and new bytecode/update/test
manifests, with upstream baselines unchanged.

## Current evidence

Candidate verification and publication are in progress. Exact commits, scoped
test results, build run IDs and release assets will be recorded after completion.
