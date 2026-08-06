# Workflow language Phase 4 validation and activation

Date: 2026-08-06

Status: **PASS_AFTER_FIX; FEATURE-BRANCH ACTIVATION COMPLETE; BASE INTEGRATION
NOT PERFORMED**

This document records both the historical Task 13 pre-activation validation and
the completed Task 14 feature-branch activation. New/default
`archon-2026-07` admissions now use normalizer v4, `hermes-legacy` remains v2,
and explicit or sealed v1-v3 compatibility remains supported. No push, merge,
rebase, publication, `base` integration, or worktree cleanup was performed.

## Result summary

- Phase 4 is active on the feature branch at activation commit
  `7bf55d5d680faea8d82474c1d3e2a3dd8f69a096`; current Archon selects v4,
  current legacy selects v2, and sealed readers support v1-v4.
- The exact final Phase 4 gate passes 225/225, the activation migration set
  passes 536/536, and the fresh installed-wheel integration passes 1/1.
- The final full no-retry run has only the exact 27 baseline failures across 16
  files plus the same four Anthropic setup errors. Its 32,343 passes and 688.4s
  wall time are explicitly reconstructed/measured as described below, not
  presented as a captured runner-summary line.
- The historical Task 13 defensive, distribution, Desktop, functional-review,
  and bounded security conclusions remain valid and are retained below.
- Activation is complete only on the feature branch. No `base` integration or
  remote/release operation was performed.

## Historical Task 13 pre-activation evidence

The following Task 13 sections preserve the evidence captured while Archon
still selected v3 and v4 required explicit selection. Their pre-activation
statements are historical observations, superseded for current/default
admission by the Task 14 activation evidence later in this document.

- The mandatory defensive gate passes with file retries disabled: 5 files, 163
  passed, 0 failed in 33.2s.
- The repaired branch-only regression set passes with file retries disabled: 9 files,
  480 passed, 0 failed in 69.6s.
- The distribution/schema/merge/customization gate passes: 7 files, 1,059 passed,
  0 failed in 98.9s.
- Desktop typechecking passes. The three Vitest failures and four ESLint errors are
  reproduced exactly on clean `base`; the branch adds nine passing Desktop tests and
  no lint diagnostic.
- The post-fix full Python suite has no branch-only failure: 32,338 tests passed, 27
  failed across 16 files, and four collection errors occurred in one additional file;
  all failing cases reproduce on exact `base`.
- Ruff on every Python file changed during Task 13 and `git diff --check` pass.
- Functional adversarial review: **PASS** with 545/545 independent tests, a clean
  review diff, and no Critical, High, Medium, or Low findings.
- Bounded security review: **PASS_AFTER_FIX**. Its single, non-platform-blocked attempt
  found one blocking Medium catalog-sidecar containment issue; strict RED/GREEN fixed
  it, and the same reviewer's scoped re-review passed with no new Critical, High, or
  Medium finding.

### Mandatory defensive invariants

Command:

```text
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_security_boundaries.py tests/plugins/workflow/test_performance_bounds.py tests/plugins/workflow/test_approval_races.py tests/plugins/workflow/test_crash_recovery.py
```

Initial result: 5 files, 154 passed, 1 failed in 33.0s, with retries disabled. The
failure was `test_thousand_node_projection_is_bounded_and_disables_mermaid`: the
Phase 4 512-node closure decoder had leaked into current v3 compilation. The same test
passed on exact `base`, establishing a branch regression rather than a pre-existing
failure.

The defensive module now additionally proves:

- current v3 can compile/project a 513-node root while explicit v4 rejects before
  materializing node 513;
- same-name root and dependency resources retain distinct package and snapshot
  origins;
- an admitted composed run completes after both source roots are removed and a
  restarted scheduler makes no open attempt beneath the removed dependency root;
- the ordinary sealed-cache identity check remains fail-closed when a source changes;
- root-active and included-child sidecars cannot authenticate bytes through an
  external symlink, and definition/sidecar replacement during capture fails closed;
- existing containment, digest, stale-action, replay, bounds, and redaction cases stay
  green.

Final result after SDD fix round 1: 5 files, 163 passed, 0 failed in 33.2s, with
retries disabled. Per-file
results were 29 security-boundary tests, 33 performance-bound tests, 3 approval-race
tests, 74 crash-recovery tests, and 24 Phase 4 defensive-invariant tests.

### Bounded security review finding and fix

The independent bounded review completed with result **`FINDINGS`** and one blocking
Medium finding. Catalog enumeration already excludes static workflow-definition
symlinks with `DirEntry.is_file(follow_symlinks=False)`, but capture subsequently used
`Path.is_file()`, `stat()`, and `read_bytes()` for an adjacent `.hermes.yaml`. Those
operations followed an external symlink and allowed the external bytes to become
active root policy or authenticated included-child provenance. The same read path
also left a replacement window between size accounting and byte capture.

Strict relationship TDD recorded both bug classes before production changes:

- external root/child sidecars: 2 selected, 0 passed, 2 failed because neither case
  raised;
- definition/sidecar replacement at descriptor open: 2 selected, 0 passed, 2 failed
  because neither case raised;
- combined GREEN after the shared capture fix: 4 selected, 4 passed, 0 failed in
  0.4s.

Catalog definition and optional sidecar capture now share a contained-regular-file
path. On descriptor-relative platforms it opens the catalog root, every directory,
and the file with no-follow semantics; on Windows/fallback platforms it rejects
symlink/reparse components around the open. Both paths compare the before, opened,
after-open, descriptor-after-read, and path-after-read identities. The definition and
sidecar sizes are reserved atomically before either descriptor is read; each source is
read once into the immutable catalog snapshot. Errors remain generic and do not expose
external or absolute paths. Candidate ordering and fixed byte limits are unchanged.

Post-fix evidence includes the full catalog API file (77 passed), the exact Task 13
Step 1 suite (161 passed), and the combined Phase 4 defensive/catalog/security-boundary
gate (128 passed), all with retries disabled. The same reviewer then performed the
scoped fix re-review and returned **PASS** with no new Critical, High, or Medium
finding. The final bounded-review result is therefore **PASS_AFTER_FIX**. The platform
gate was not triggered, and no second independent security attempt was used.

### SDD fix round 1

The post-convergence SDD review reported two independent Important findings. Both were
reproduced before production/test-harness changes and fixed without activating v4.

1. Final API admission identity errors were untranslated. A real Desktop/API
   regression mutated an authenticated command resource after risk assessment but
   before `seal_authenticated_snapshot()`. RED was 0 passed / 1 failed because the
   endpoint returned HTTP 500 instead of stable 409. Admission now translates only
   the final seal's `OSError` to non-retryable `workflow_package_changed` 409;
   capacity, compatibility, and storage error handling are unchanged. GREEN was 1/1,
   and the relationship asserts no persisted run or staging snapshot.
2. The catalog replacement regression intercepted only descriptor-relative
   `os.open(..., dir_fd=...)`, leaving the Windows/fallback path unexercised. Forced
   fallback RED was 0 passed / 2 failed because definition and sidecar replacements
   did not trigger the hook. The test now covers descriptor-relative and absolute
   fallback candidates, forces fallback without `O_NOFOLLOW`, tracks descriptor
   closure, and asserts external paths/content stay redacted. GREEN was 4/4 across
   definition/sidecar and both read modes. No catalog production seam was required.

Post-fix-round evidence: full Desktop API 158/158, full catalog API 77/77, exact
mandatory Step 1 163/163, and defensive/catalog/security-boundary 130/130, all with
retries disabled. Step 4 remained 1,059/1,059. Ruff passed the three Python files
touched in the round.

### Controller full-suite follow-up (fix round 2)

The controller's full Python run after SDD fix round 1 reported 32,338 passed and 28
failed, plus the known Anthropic collection error. One failure was additional and
branch-only: `test_write_json_serializes_concurrent_writes`. The complete TUI gateway
server file was 516 passed / 1 failed, the exact write test passed 1/1 alone, and the
complete file passed on exact `base`.

The two preceding profile-scoped deferred-build tests waited only for a `built` event
set inside their fake `_make_agent`; they removed the session before its `agent_ready`
event proved the daemon build thread had completed its final JSON emission. That
thread could cross monkeypatch teardown and write a ninth partial line into the next
test's `_ChunkyStdout`. Both tests now preserve their `built` assertions and also
require `agent_ready` before popping the session. The no-retry complete-file GREEN was
517 passed, 0 failed in 15.1s. This was a test-harness synchronization fix only; no TUI
gateway production behavior changed. A second full Python run was not required for
this bounded controller follow-up.

### Mandatory defects repaired during the gate

The full and focused gates exposed compatibility defects caused by the staged Phase 4
implementation. Each was narrowed against current branch behavior and, where the
premise was not self-evidently branch-local, exact `base`.

1. Current v1-v3 packages were incorrectly decoded through the v4 512-expanded-node
   limit. Internal compilation now applies the new bound only to Phase 4 semantics;
   the public manifest decoder remains strictly bounded.
2. The new package-graph digest could not project legacy YAML `bytes`, date/timestamp,
   or Python integers beyond the runtime decimal-conversion guard. Only trusted
   v1-v3 compiled values receive exact tagged projections; explicit v4 remains strict.
3. The new optional `NodeExecutionContext.record_loop_decision` field had shifted
   existing positional arguments. It now sits at the end of the compatibility
   surface.
4. API admission revalidated live source identity after trust and thereby rejected
   copying already-authenticated cached bytes if the source disappeared at the
   admission boundary. Admission now performs one final identity validation, seals
   the authenticated snapshot authority, and copies from those immutable bytes.
   Ordinary cache sealing still rejects a changed live identity.
5. Catalog, Desktop, Phase 3, and runner-binding test doubles were updated to the
   intentional compilation/cache contracts rather than weakening production checks.
6. All eleven Phase 4 standard-suite files were added to the workflow merge-gate
   opt-out inventory with the existing standard-suite coverage reason. No release-gate
   command or upstream customization ownership was expanded.

Focused evidence included:

- v3/v4 closure regression RED: 1 failed; GREEN: 1 passed;
- graph-digest/language/defensive focused gate: 4 files, 112 passed;
- script executor plus loop interactions/loops: 3 files, 114 passed;
- full catalog API: 77 passed;
- full Desktop workflow API: 157 passed;
- full merge-gate unit file: 49 passed;
- all nine previously branch-implicated files together, retries disabled: 480 passed,
  0 failed in 69.6s.

### Full Python base gate

Command:

```text
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh
```

The post-fix runner discovered 2,776 test files and ran with 14 workers. File retries
were explicitly disabled, so there were no retry-based passes. Exact final result:
32,338 tests passed and 27 failed across 16 files in 673.0s, plus four collection
errors in `tests/agent/test_anthropic_output_field_leak.py`. Every failing test and
collection case exactly matched the earlier exact-base attribution run. No Phase 4 or
other branch-only failure remained. The filtered capture retained the discovery,
failing-file, failing-test, collection-error, and aggregate lines; no result is
estimated.

Exact-base attribution command selected all 26 files implicated by the first full
run. On clean `base` it produced 1,053 passed and 27 failed in 82.1s, with 16 failing
files and the same collection error. The final branch failures were:

- `tests/agent/test_credential_pool_routing.py` (1)
- `tests/hermes_cli/test_config_read_guard.py` (1)
- `tests/hermes_cli/test_plugins_hub_perf_guard.py` (1)
- `tests/hermes_cli/test_model_switch_custom_providers.py` (2)
- `tests/plugins/workflow/test_cli.py` (5)
- `tests/plugins/workflow/test_persistent_session_recovery.py` (1)
- `tests/test_install_macos_launcher.py` (1)
- `tests/test_install_sh_acp_launcher.py` (4)
- `tests/test_managed_runtime_resolution.py` (1)
- `tests/tools/test_approval.py` (1)
- `tests/tools/test_environment_bash_launch_contract.py` (1)
- `tests/tools/test_docker_environment.py` (1)
- `tests/tools/test_modal_snapshot_isolation.py` (2)
- `tests/tools/test_transcription_tools.py` (1)
- `tests/tools/test_wake_word.py` (1)
- `tests/tools/test_voice_mode.py` (3)

`tests/agent/test_anthropic_output_field_leak.py` stopped during collection because it
imported a stale managed-install `hermes_constants.py` that lacks
`get_process_hermes_home`. Exact `base` reproduces the same import error.

### Desktop gates and base attribution

Commands:

```text
cd apps/desktop && npm run typecheck
cd apps/desktop && npm test
cd apps/desktop && npm run lint
```

- Typecheck: passed all renderer, Electron, and E2E TypeScript projects.
- Branch Vitest: 497 files passed, 1 failed, 1 skipped; 4,733 tests passed,
  3 failed, 2 skipped; duration 53.47s. All failures are in
  `src/app/settings/model-settings.test.tsx`.
- Clean-base Vitest: the same file and same three cases failed; 497 files passed,
  1 failed, 1 skipped; 4,724 tests passed, 3 failed, 2 skipped; duration 51.07s.
  Thus all nine tests added by the branch pass.
- Branch ESLint: 148 diagnostics (4 errors, 144 warnings) in 11.99s.
- Clean-base ESLint: the exact same 148 diagnostics (same four files, rules, and
  locations modulo branch line movement) in 12.13s. The errors are the existing import
  sort in `contrib/surfaces.tsx` and reactive-ref rule in three workflow renderer
  files. Phase 4 adds no lint diagnostic.

Vitest emitted the existing Vite native-config-loader warning and jsdom canvas/React
`act(...)` warnings. No dependency or lockfile was changed.

### Distribution, schema, merge, and customization gate

Command:

```text
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_installed_distribution_e2e.py tests/plugins/workflow/test_showcase_distribution_e2e.py tests/scripts/test_check_upstream_customizations.py tests/scripts/test_workflow_merge_gate.py tests/scripts/test_workflow_upstream_merge.py tests/test_desktop_workflow_test_gate.py
```

Result: 7 files, 1,059 passed, 0 failed in 98.9s. This includes 628 language-schema,
49 workflow-merge-gate, 274 upstream-customization, 104 workflow-upstream-merge, 2
showcase-distribution, and 2 Desktop-workflow-gate tests. The integration-marked
fresh-installed-environment case in `test_installed_distribution_e2e.py` is excluded
by the repository's default marker policy; the required unmarked distribution file
completed normally.

### Historical static checks and pre-activation pin

Ruff command covered all twelve Python files modified during Task 13 and passed with
`All checks passed!`. `git diff --check` also passed.

The direct generated-contract/schema probe reported:

```text
CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]=3
LATEST_NORMALIZER_VERSION=4
current_contract_normalizer=3
explicit_contract_normalizer=4
explicit_contract_bytes=239878
draft_2020_12_schemas=valid
```

At the end of Task 13, Phase 4 remained pre-activation: ordinary Archon authoring
selected v3 and Task 14 had not yet been performed.

### Historical review status and exclusions

- Functional adversarial review: **PASS**. Independent evidence was 545 passed, 0
  failed with a clean review diff and no Critical, High, Medium, or Low finding. Scope
  covered normalizer inheritance, include premise/intent, closure determinism,
  snapshot resume, signal replay, client skew, and change scope.
- Defensive security review: **PASS_AFTER_FIX**. The single bounded attempt was not
  platform-blocked. It initially returned `FINDINGS` for one blocking Medium
  catalog-sidecar containment/TOCTOU issue. After strict RED/GREEN repair, the same
  reviewer's scoped re-review passed with no new Critical, High, or Medium finding;
  mandatory Step 1 remained 161/161 and full catalog remained 77/77.
- Phase 4 activation: excluded from Task 13 and not performed in that task.
- Push, merge, and publication: excluded and not performed.

Task 13 review convergence was complete as a pre-activation result. Task 14 and
all push, merge, rebase, publication, and cleanup operations were excluded from
that historical task.

## Task 14 activation evidence — 2026-08-06

Phase 4 language semantics are activated on
`feat/workflow-language-phase-4-ordinary-loops-immutable-includes` by commit
`7bf55d5d680faea8d82474c1d3e2a3dd8f69a096`. The one public selection authority now
maps Archon to normalizer v4 while legacy remains v2. Supported sealed readers remain
v1-v4, including explicit v3 resume. This is a feature-branch result only: no merge,
push, rebase, publication, `base` integration, or worktree cleanup was performed.

### Strict RED/GREEN activation sequence

1. The exact four-file activation RED was 758 passed / 8 failed. Every failure was a
   moving-current assertion that still observed Archon v3.
2. Changing only `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` to 4 exposed the
   expected stale contract/trust tests. The exact Step 3 activation command ultimately
   reached 874 passed / 5 failed; all five are the exact-base packaged-schema CLI
   failures documented above.
3. The generated compatibility-code section serialized to 15,714 bytes, exceeding
   its advertised 15,000-byte section ceiling. The authoritative ceiling is now
   16,000 bytes; the total 256,000-byte envelope, 4,000-byte reserve, and other section
   ceilings are unchanged.
4. The initial exact eleven-file Phase 4 no-retry gate was 224 passed / 1 failed. The
   failing defensive test incorrectly called moving current "v3"; its historical leg
   now explicitly selects v3 and retains the current-v4 rejection relationship. The
   final gate was 225 passed / 0 failed in 6.7s.
5. The first full no-retry run after activation was 32,302 passed / 66 failed in
   662.6s: the exact 27 base failures plus 39 activation-only failures across 15 files,
   with the known four Anthropic setup errors in one additional file. Those 15 files
   passed together after migration, 535/535 initially and 536/536 after the added
   serializer relationship.

### Production defects exposed by activation

- The Desktop/detail response model rejected valid normalizer v4 with an upper bound
  of 3. The exact `StrictInt`, lower bound, and closed response shape remain; the upper
  bound is now 4. The stale invalid-version test now uses unsupported v5.
- Phase 4 dependency identity initially used Python's ordinary decimal conversion and
  could not digest a valid 5,000-digit structured-schema integer already preserved by
  Phase 3. The bounded manifest encoder now delegates scalar leaves to the exact
  canonical encoder while enforcing the existing 8 MiB total envelope.
- A first correction made that encoder too strict for historical non-finite YAML graph
  values. Focused reproduction proved failure occurred during package-graph hashing,
  before language normalization. The workflow-scoped encoder now preserves the prior
  `NaN`, `Infinity`, and `-Infinity` byte representation, distinguishes those values
  from strings, enforces sorted string keys and bounded lists/tuples, and leaves the
  core structured-output encoder strict. Ordinary canonical bytes are unchanged.

Focused serializer/response evidence was 5/5 for non-finite values, ordinary byte
identity, collision distinction, and the 5,000-digit integer; the full language,
structured-output, and catalog API files were 148/148. A separate focused production
relationship set was 4/4. The fresh installed-wheel integration test was 1/1.

### Final gates and full-suite attribution

- Exact Step 3: 874 passed / 5 exact-base CLI failures in 15.9s.
- Exact eleven-file Phase 4 gate, retries disabled: 225 passed / 0 failed in 6.7s.
- Exact 15-file activation migration set, retries disabled: 536 passed / 0 failed in
  12.4s.
- Fresh installed-wheel integration: 1 passed / 0 failed in 8.9s.
- Ruff over all 27 activation Python files: `All checks passed!`.
- `git diff --check`: passed.

The second post-migration full run was 32,337 passed / 32 failed in 681.4s. Twenty-seven
failures were the exact base set. Three deterministic extras exposed the response and
manifest defects above; two process-heavy extras in `tests/test_tui_gateway_server.py`
and `tests/scripts/test_workflow_upstream_merge.py` passed immediate no-retry
whole-file reruns (517/517 and 104/104 respectively).

After the deterministic corrections, the final full no-retry run discovered 2,776
files with one additional relationship test and produced exactly the documented 27
failures across the same 16 base files, plus the same four Anthropic setup errors in
`tests/agent/test_anthropic_output_field_leak.py`. No activation-specific or transient
failure remained. The tool transport retained the final failure inventory but
truncated the middle runner-summary line, so the passing aggregate is explicitly
reconstructed rather than presented as directly captured: the previous complete run
had 32,369 total outcomes, the final run added one test, and 32,370 minus 27 yields
32,343 passing tests. Invocation/result timestamps give 688.4s command wall; no
runner-reported duration is invented.

The Task 13 functional review remains clean and its bounded defensive security result
remains **PASS_AFTER_FIX**. Activation did not introduce a new security finding or
weaken the repaired catalog-sidecar boundary.

## Conclusion

Phase 4 activation is complete and verified on the feature branch: new/default
Archon admissions use v4, legacy remains v2, and explicit or sealed v1-v3
snapshots remain compatible. The exact activation, migration, installed-wheel,
and baseline-attributed full-suite evidence above supports that conclusion.
Task 13's pre-activation observations remain preserved as historical evidence;
they no longer describe the current profile default. No `base` integration,
push, merge, rebase, publication, release, or worktree cleanup was performed.
