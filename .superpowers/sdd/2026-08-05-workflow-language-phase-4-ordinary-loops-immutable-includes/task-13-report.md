# Task 13 report: Defensive, distribution, and base verification gates

Status: **PASS_AFTER_FIX; SDD/CONTROLLER FOLLOW-UPS FIXED; PRE-ACTIVATION**

## Outcome

The deterministic pre-activation gate is complete. Mandatory defensive coverage,
branch-regression coverage, distribution/schema/merge/customization coverage, Ruff,
typechecking, pin-state validation, and diff integrity pass. The full Python and
Desktop repository-wide failures have been reproduced on exact `base`; no branch-only
failure remains.

This task also repaired four mandatory compatibility defects exposed by verification:
current v3 no longer inherits v4's 512-node closure cap, legacy YAML values are
digestible without weakening explicit v4, positional `NodeExecutionContext`
construction is preserved, and API admission freezes authenticated cached bytes only
after one final live-identity check.

Archon current remains normalizer v3. Functional adversarial review passed with
545/545 independent tests, a clean review diff, and no finding at any severity. The
bounded defensive security review's single, non-platform-blocked attempt initially
returned `FINDINGS` with one blocking Medium; strict RED/GREEN fixed it, and the same
reviewer's scoped re-review passed with no new Critical, High, or Medium finding. The
honest final security result is `PASS_AFTER_FIX`; activation remains excluded.

## Files changed

Production compatibility fixes:

- `plugins/workflow/compilation.py`
- `plugins/workflow/dependency_manifest.py`
- `plugins/workflow/executors/base.py`
- `plugins/workflow/trust.py`
- `plugins/workflow/api_admission.py`
- `plugins/workflow/catalog_api.py`

Defensive and stale-contract tests:

- `tests/plugins/workflow/test_phase4_defensive_invariants.py`
- `tests/plugins/workflow/test_catalog_api.py`
- `tests/plugins/workflow/test_desktop_api.py`
- `tests/plugins/workflow/test_phase3_execution_semantics.py`
- `tests/plugins/workflow/test_runner_binding.py`
- `tests/scripts/test_workflow_merge_gate.py`

Evidence:

- `docs/reviews/2026-08-05-workflow-language-phase-4-validation.md`
- this report

The parent explicitly expanded ownership from the original defensive-test/report files
to the nine branch-implicated focused files and their exact causally implicated
`plugins/workflow/**` paths. No unrelated core file, Desktop source, customization
manifest, user-facing configuration, or activation mapping was changed.
The security fix round further bounded ownership to catalog capture, its defensive
regressions, and these two evidence documents.

## TDD and repair evidence

1. Mandatory defensive baseline:
   - exact no-retry Step 1 command;
   - 154 passed / 1 failed in 33.0s;
   - the thousand-node projection failure showed v4's decoder bound leaking into
     current v3; exact `base` passed the case.
2. Versioned closure RED/GREEN:
   - a new relationship test proved current v3 projects 513 nodes while explicit v4
     rejects before node 513 materialization;
   - RED 0 passed / 1 failed; GREEN 1 passed / 0 failed;
   - original performance and public untrusted-manifest controls passed separately.
3. Legacy graph digest:
   - branch-only language/structured-output failures covered YAML binary,
     date/timestamp, and runtime-over-limit integer values;
   - non-v4 internal projection gained exact tagged forms;
   - language + structured output + defensive + dependency manifest gate passed 112.
4. Positional execution context:
   - the new loop callback moved to the end of the dataclass compatibility surface;
   - script executor + Phase 4 loops/interactions passed 114.
5. Authenticated admission snapshot:
   - Desktop mutation cases exposed live identity revalidation after the trust
     boundary;
   - the new authenticated-snapshot seal validates each live identity once, drops live
     identity authority, and keeps ordinary sealed-cache change rejection intact;
   - full Desktop API passed 157, and the ordinary cache control passed independently.
6. Intentional test contracts:
   - catalog fake compilation preserves invalid-name testing without forging a
     self-authenticating compilation;
   - showcase and compatibility wrappers accept the intentional compilation context;
   - runner cache assertions now verify package identity, manifest root identity,
     covered paths, and immutable sealed bytes instead of exact slots.
7. Merge inventory:
   - all eleven Phase 4 suites were added to the existing standard-suite opt-out list;
   - merge-gate unit file passed 49 without expanding release commands.
8. Combined branch-only GREEN:
   - exact nine-file no-retry command;
   - 480 passed / 0 failed in 69.6s.
9. Security finding RED/GREEN:
   - the reviewer confirmed that adjacent `.hermes.yaml` capture followed an external
     symlink, authenticating those bytes as root policy or included-child provenance;
   - root/child sidecar RED: 0 passed / 2 failed because neither case raised;
   - definition/sidecar replacement RED: 0 passed / 2 failed because neither case
     raised;
   - one shared contained-regular-file capture path now uses no-follow/reparse checks,
     before/open/after-read stable identity, atomic pair byte reservation, and a single
     descriptor read per source;
   - combined focused GREEN: 4 passed / 0 failed in 0.4s;
   - full catalog API: 77 passed; Phase 4 defensive/catalog/security boundary: 128
     passed; exact Step 1: 161 passed, all with retries disabled.
10. SDD Important admission identity race:
    - a real Desktop/API regression changed an authenticated command resource after
      assessment but before the final seal;
    - RED 0/1: HTTP 500 escaped instead of stable `workflow_package_changed` 409;
    - admission now translates only the final seal's `OSError`, leaving capacity,
      compatibility, and storage mappings untouched;
    - GREEN 1/1 asserts 409 and no run/staging residue; full Desktop API passed 158.
11. SDD Important fallback coverage gap:
    - forced fallback RED 0/2 showed the descriptor-only replacement hook never ran
      for either definition or sidecar;
    - the relationship now covers relative descriptor and absolute fallback opens,
      disables `O_NOFOLLOW` in the forced fallback case, tracks all opened descriptors
      to closure, and verifies external path/content redaction;
    - GREEN 4/4; no catalog production refactor was needed; full catalog remained 77.
12. Controller full-suite deferred-build harness race:
    - controller full Python after fix round 1 was 32,338 passed / 28 failed plus the
      known Anthropic collection error; one additional failure was branch-only;
    - TUI gateway whole-file RED was 516 passed / 1 failed while the exact concurrent
      write target passed 1/1 alone and exact `base` passed the whole file;
    - two profile-scoped agent-build tests waited for fake `_make_agent` `built`, then
      popped their sessions before `agent_ready` proved the daemon build had finished
      its final JSON emission;
    - both retain the `built` assertion and now await `agent_ready` before teardown;
      whole-file GREEN was 517/517 in 15.1s with retries disabled;
    - production TUI gateway behavior was unchanged, and no second full Python run was
      required for this bounded controller follow-up.

## Final deterministic verification

- Exact mandatory defensive Step 1 after SDD fix round 1, retries disabled: 5 files,
  163 passed / 0 failed in 33.2s.
- Post-fix full Python gate, retries disabled: 2,776 files discovered; 32,338 tests
  passed and 27 failed across 16 files in 673.0s, plus four collection errors in one
  additional file. Every failing test and collection case exactly matches the prior
  exact-base attribution set; no branch-only failure remains.
- Exact-base attribution over all initially implicated files: 26 files, 1,053 passed /
  27 failed in 82.1s, plus the same collection error.
- Desktop typecheck: passed.
- Desktop Vitest branch: 497 files passed / 1 failed / 1 skipped; 4,733 tests passed /
  3 failed / 2 skipped in 53.47s. Exact `base` has the same three failures and nine
  fewer passing tests.
- Desktop ESLint branch: 4 errors / 144 warnings. Exact `base` has the same four errors
  and 144 warnings.
- Exact distribution/schema/merge/customization Step 4: 7 files, 1,059 passed /
  0 failed in 98.9s.
- SDD focused/full gates: admission race 1/1, descriptor/fallback replacements 4/4,
  Desktop API 158/158, catalog API 77/77, and defensive/catalog/security 130/130.
- Ruff on every Task 13 Python edit: passed.
- Direct pin/schema probe: current Archon 3, latest/explicit 4, both generated schemas
  valid Draft 2020-12, explicit envelope 239,878 bytes.
- `git diff --check`: passed.

The full command/failure/exclusion record is in
`docs/reviews/2026-08-05-workflow-language-phase-4-validation.md`.

## Self-review

- Public v4 manifest decoding remains bounded at 512 expanded nodes. Only an internal
  already-compiled v1-v3 path receives a limit equal to its materialized origin count.
- Legacy scalar tags are selected from sealed profile/version metadata and do not
  become a permissive v4 fallback.
- The admission cache transition validates live identities before clearing them;
  subsequent snapshot copying can no longer reopen or depend on package roots.
- Catalog capture rejects external symlink/reparse components and replacement races
  for both definitions and adjacent sidecars without disclosing external paths. It
  reserves both observed sizes before reading either descriptor and preserves
  candidate ordering and existing limits.
- Existing `seal()` behavior is unchanged for callers that still require live identity
  revalidation.
- The test updates assert behavioral relationships and avoid enumeration/slot
  change-detectors.
- No normalizer default, tool schema, plugin boundary, environment variable, Desktop
  wire action, or prompt-caching behavior changed.
- No generated build output remains. Activation, push, merge, rebase, publication,
  and worktree cleanup remain excluded.

## Independent review results

- Functional adversarial review: **PASS** — 545 passed, 0 failed; clean review diff;
  no Critical, High, Medium, or Low finding.
- Bounded defensive security review: **PASS_AFTER_FIX** — one non-platform-blocked
  attempt initially found MEDIUM-1; strict RED/GREEN repaired it; the same reviewer
  passed the scoped fix re-review with no new Critical, High, or Medium finding.
  Mandatory Step 1 remained 161/161 and full catalog remained 77/77.

This is the durable Task 13 pre-activation handoff. Task 14 activation and all remote
or integration operations remain outside this task.
