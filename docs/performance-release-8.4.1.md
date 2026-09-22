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
- A test-only brand CLI smoke correction uses `hermes.exe` on Windows, retains
  `hermes` on POSIX, pins imports to the tested checkout and checks CLI exit.
- Two generator fixtures now seed their neutral baseline in a temporary tree
  and avoid a fixed upstream TOML section order while retaining byte-exact
  round trips. No generator implementation changed.

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

- Installer/cache/update gate: 128 passed, zero failed, 12 platform skips across
  12 files, canonical runner with one worker and no retries (277.1 seconds).
- Brand Python contracts: 34 passed after correcting the Windows launcher
  fixture. The final checkout-pinned real CLI suite separately passed all 14.
- New bytecode/update/updater-fixture manifests passed strict committed-tree
  checks; neutral branding passed. No workflow runtime or agent-loop delta
  was carried from the repair branch.
- Fresh scoped review found no Critical or Important implementation defects.
- Generator unit tests initially ran against neutral base (94 passed, 37
  failed); those tests assume live OTTO overlays. Their generated brand stamp
  was neutralized and verified byte-identical to HEAD. OTTO-tree verification
  remains required; this initial run is not a passing generator gate.
- Existing Windows downloader scripts query `/releases`, including prereleases,
  and choose the first matching brand/architecture EXE. They do not use the
  strictly stable `/releases/latest` endpoint. No installer changes were made.
- Desktop focused verification: 132 Electron connection/backend tests, 159
  remote/generation tests, 384 renderer tests and 53 native Node tests passed
  (728 total); three opt-in live-remote tests skipped. Desktop renderer,
  Electron and E2E TypeScript checks passed. Dependency install used Node
  22.23.1 and the committed lockfile without version changes.
- The additional strict Desktop manifest scan reproduces the previously
  documented native Windows parser `invalid_json` defect (see native-validation
  plan). It is not a Desktop test failure. Its repair remains in the deferred
  worktree; this release does not claim that scanner or the full workflow merge
  gate passes. Brand runtime equality to tested base is checked directly.
- OTTO's first generator run passed 129 tests and exposed the two fixture
  assumptions above. The isolated neutral fixture passes on base; the complete
  branded rerun passed all 131 tests after those test-only corrections.
  Both descriptor checks passed, as did 34 Python brand tests per brand and
  LOOP24's 53 native Node tests. Both local production builds passed.
- The final test-manifest entry declares its fixture symbols. Full native
  TypeScript/JavaScript symbol attestation remains subject to the documented
  Windows parser limitation; earlier strict Python-only entries passed.

## Integrated and published source

Forward-only atomic push completed on 2026-09-22:

| Branch | Source commit |
| --- | --- |
| base | `39bc34f59a0bee526de6522e6a5bb8e60c01ec43` |
| otto | `7d68edf9b337ed68e381bc974b90072842e32244` |
| loop24 | `79a9d242cb5acf5a7fb55f36bc317846c71bc918` |

Both brands contain that base. Workflow/Kanban runtime and new bytecode/updater
modules are byte-identical to base. OTTO's local build ran at `7c7b3de75c`, with
only the three-line manifest-anchor correction added before the final source
pin; CI builds the exact final pin. LOOP24's local build used its final pin.

Production v8.4.1 runs dispatched with `prerelease=false`:

- OTTO: https://github.com/cmetech/otto/actions/runs/35693226214
- LOOP24: https://github.com/cmetech/loop24/actions/runs/35693228746

Both exact runs completed successfully. Production releases are public,
non-draft and non-prerelease:

- [OTTO v8.4.1](https://github.com/cmetech/otto/releases/tag/v8.4.1), published
  2026-09-22 06:10:24 UTC.
- [LOOP24 v8.4.1](https://github.com/cmetech/loop24/releases/tag/v8.4.1), published
  2026-09-22 06:10:37 UTC.

Each release has seven uploaded assets: Windows x64 EXE/MSI, macOS arm64
DMG/ZIP, plus three blockmaps. Verified nonzero sizes, API SHA-256 digests,
exact source pins in release bodies and successful native build jobs.
No Linux or Intel macOS installer is claimed. Builds remain unsigned.

The existing downloader selection was reproduced read-only from each release
API and selects its `*-8.4.1-win-x64.exe`. Install the chosen brand with:

```powershell
irm https://raw.githubusercontent.com/cmetech/otto/main/install.ps1 | iex
# Or LOOP24:
irm https://raw.githubusercontent.com/cmetech/loop24/main/install.ps1 | iex
```

Manual packaged Windows UAT and native macOS/Linux runtime UAT remain pending.
Next Windows checks: install/update, first boot preparation, repeated cold
connections, page navigation, retry after failure, profile switching and resume.
Do not interpret successful packaging as measured startup-speed acceptance.
The release receipt is a documentation-only follow-up on base; the table above
records the immutable base/brand commits used for the release, not receipt HEAD.
