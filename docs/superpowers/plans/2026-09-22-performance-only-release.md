# Performance-only v8.4.1 release plan

> Execute natively with superpowers:executing-plans; obtain one fresh scoped review before integration.

**Goal:** Merge the isolated performance release into `base` and publish OTTO and LOOP24 v8.4.1.

**Architecture:** Desktop reliability and page responsiveness already live at base
`199a16f353`. Carry only install/update bytecode preparation, bounded Windows ZIP
rename reliability, and the updater test-isolation fixture needed to verify these.
Preserve the entire unfinished `perf/install-bytecode` branch/worktree unchanged.

**Tech Stack:** Git, Python, PowerShell/Bash installers, Electron/React, existing branded GitHub release workflows.

**Spec:** User approval of performance-only integration and production v8.4.1 on
2026-09-22; behavioral contracts in `docs/python-bytecode-precompile.md`,
`docs/upstream-customizations/desktop-page-responsiveness.yaml`,
`docs/upstream-customizations/fork-runtime.yaml`, and
`docs/otto-desktop-release-install.md`.

## Global constraints

- Development main is `base`; literal `main` is untouched.
- No workflow runtime, marketplace, credential-storage, compression, deadline,
  media, or unrelated environment repair is included.
- Existing product code is integrated from reviewed commits, not reimplemented.
- Bytecode preparation happens at install/update, never ordinary launch.
- No live profile, application installation or gateway is modified during tests.
- Preserve upstream manifest baselines; this is not an upstream synchronization.
- User explicitly approved production v8.4.1 before manual Windows UAT; native
  Linux/macOS runtime checks and the full repository suite remain pending.
- Both discovered brands must pass scoped gates and build from pinned commits.
- Use existing releases-only repositories, never product tags in the source repo.
- Finish with the primary checkout on `base`.

## Review focus

- Partial commit extraction must not import unfinished workflow behavior.
- Old updater/new helper transitions must preserve cache invalidation and backfill.
- Windows retries must remain bounded and preserve rollback and POSIX behavior.
- Brand merges must preserve native assets and generated identities without
  overwriting shared source or altering neutral base.
- Stable releases must pin the tested source SHA and retain the deferred-test disclosure.

### Task 1: Isolate and verify the performance candidate

- [x] Create `release/performance-only` at base in `C:/wt/hermes-performance-release`.
- [x] Cherry-pick `17c44a5501` (bytecode preparation and its manifest/tests).
- [x] Carry only updater production/test/manifest hunks from `c5ec546acd` and
  updater test isolation from `bc52fc43f5`; retain provenance in commit messages.
- [x] Add a test-isolation manifest entry and scoped release receipt.
- [x] Run the canonical Python installer/cache/update suites, relevant Desktop
  lifecycle/page suites, typecheck, neutral-brand and generator tests. Expected:
  all scoped tests pass; actual non-native skips remain explicit.
- [x] Review the candidate against base. Python-only manifests passed strict
  committed-tree checks. Full non-Python attestation remains blocked by the
  documented native Windows parser defect; do not claim that gate passes.

### Task 2: Integrate base and regenerate every brand

- [x] Fast-forward clean `base` to the reviewed candidate.
- [x] Discover brands from `brands/*.json`, excluding schema and fixtures.
- [x] Merge exact tested base into each brand; regenerate owned overlays,
  preserve binary brand assets and shared-source changes, then commit.
- [x] Run generator checks, relevant branded runtime checks and Desktop build
  gates. Verify base ancestry in each final brand SHA.
- [x] Push only the tested base and brand refs, forward-only.

### Task 3: Publish and verify v8.4.1

- [x] Inspect both existing release workflows: explicit `--publish never` in
  Electron Builder, explicit release upload, exact source SHA and stamp branch.
- [x] Confirm v8.4.1 does not already exist; dispatch both with prerelease=false.
- [x] Monitor exact runs to completion; verify release body commit and full
  Windows/macOS installer assets. No Linux artifact/runtime claim.
- [x] Record results/deferred failures and installer links, with stable `irm`
  distribution behavior verified from the release-repository installer.
- [x] Verify primary checkout is clean and on base; retain repair worktree.

Completed production publication: see `docs/performance-release-8.4.1.md` for
immutable source pins, exact successful runs, assets and deferred UAT/tooling.
