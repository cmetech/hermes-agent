# Workflow Catalog + Desktop Trigger verification

- Date: 2026-07-20
- Branch: `feat/workflow-catalog-desktop-trigger`
- Target: `base`
- Dependency: remediation merge `9879acba85e0820d9bd1397c1fefe65e31fbcd3a`

## Result

Tasks 1–8 are implemented as one commit each. Task 9 contains this evidence,
the operator documentation update, and reproducible UAT fixtures. The feature
uses the existing authenticated workflow admission endpoint; HTTP requests
only persist and wake work, while the coordinator executes in the background.

The source branch is intentionally unpushed. Nothing was merged, pushed,
tagged, released, deployed, or published by this work. Completion authorizes
maintainer review for a PR targeting `base` only.

## Commits and TDD evidence

| Task                    | Commit                                                                                  | RED evidence                                                                                                                                                                                              | GREEN evidence                                                                                                                                                                                               |
| ----------------------- | --------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1 — catalog endpoint    | `00cc2a66aa2fc417bc3cb01cc657381c287d4fb2`                                              | Catalog and gate selection: 12 passed / 7 expected route failures. Later REDs covered bounds, unsafe names/enums, budget drift, UTF overflow, superscript devices, and case-fold collisions.              | Final catalog selection includes 47 cases plus its pinned gate meta-test; exact-head full gate: 716/1 Python, installed 1, Desktop 17, TypeScript clean.                                                     |
| 2 — detail/preflight    | `c9f5cc8811c2247b6e3ebd1088d59d33a9eb8c39`                                              | Initial detail contract: 8 expected 404 failures. Later review loops exposed mutation, redaction, bounds, WAL consistency, same-metadata recycling, and 513-node Catalog misclassification.               | Shared projection qualifier and detail file green; exact-head full gate: 742/1 Python, installed 1, Desktop 17, TypeScript clean.                                                                            |
| 3 — Desktop provenance  | `c6dabb806a156129782624e5b524304b8f6b240b`                                              | Replayed after the real local-session contradiction: 24 passed / 3 expected provenance failures. Forged-name RED returned 404 for unsafe names instead of validating them as 422.                         | Focused forged-name/resource/digest selection: 3/3; exact-head full gate: 742/1 Python, Desktop 17.                                                                                                          |
| 4 — Desktop bridge      | `e381379dfbbdc381650db4aa982ed465de438ddb`                                              | Structured channel contract: 0 passed / 2 expected failures. Adversarial dead-branch guard: 1 passed / 1 expected failure.                                                                                | Workflow UI: 63/63; Electron structured transport: 7/7; bridge: 11/11; exact-head full gate: 742/1 Python, Desktop 17.                                                                                       |
| 5 — catalog datagrid    | `91079e9bf136a98469962e09f56ab91fefc13235`                                              | Valid retained pre-implementation test state: 14 passed / 7 expected catalog failures.                                                                                                                    | Catalog: 24/24; workflow UI: 74/74; exact-head full gate: 742/1 Python, Desktop 17.                                                                                                                          |
| 6 — Review & Run        | `dee55f2136beb556f72ba1f83ab1edb0af119535`                                              | Original UI: 16/16 expected failures. Adversarial optional-input RED: both selected cases failed (two-state boolean and explicit empty/false transmission).                                               | UI: 17/17; focused remediation: 3/3; exact-head full gate: 743/1 Python, installed 1, Desktop 34, TypeScript clean.                                                                                          |
| 7 — View modal          | `8c66939e14b5e7abc96314ec94e681c46529c253`                                              | View/topology: 25 passed / 2 expected failures. Focus RED failed because View → Review restored focus to the document body instead of the catalog trigger.                                                | Focus regression: 1/1; workflow UI: 110/110; exact-head full gate: 743/1 Python, Desktop 51.                                                                                                                 |
| 8 — real middleware E2E | `7e2e9fea0005f0e5515bb73cbcbb2ce7bc003bb1`                                              | Initial E2E exposed D1. Later REDs covered resource races/parity, projection admission, UTF overflow, superscript devices, and semantic/generated filename collisions.                                    | Resource/digest/race/projection/name-set parity green; native portability: 208/3; exact-head full gate: 745/1 Python, installed 1, Desktop 51, TypeScript clean.                                             |
| 9 — verification        | This document's commit, subject `docs(workflow): Task 9 verify catalog desktop trigger` | Documentation/UAT task: the first isolation probe used a differently resolved profile trust store and was rejected as evidence; the run was repeated with the Desktop root and real trust record aligned. | Reproducible fixtures parsed successfully; approval topology rendered Mermaid, omitted fixture returned `topology_mermaid_too_many_nodes`; real Electron walkthrough passed. Final gates are recorded below. |

Tasks 3–7 were genuinely replayed after correcting D1. Each replay reran its
behavioral RED, task-scoped GREEN, full merge gate, and fresh review. That
first replay retained stable Task 4–7 patch IDs; the later adversarial fixes
intentionally changed Tasks 4 and 6 before the nine-head replay below.

After the first adversarial findings, Tasks 1, 4, 6, and 8 were rewritten. The
second adversarial review then found three Important boundary gaps and one
Minor focus gap; Tasks 1, 3, 7, and 8 were rewritten again. A third review
found a trusted-resource race, non-portable Windows input names, and
cross-entry catalog budget drift; Tasks 1 and 8 were rewritten once more. A
fourth review found definition-projection admission drift and generated
filename component overflow; Tasks 1, 2, and 8 were rewritten. A fifth review
found superscript Windows devices and case-insensitive input-name collisions;
Tasks 1 and 8 were rewritten. All nine exact commit objects were
replay-validated in a separate detached
worktree. The earlier replay correctly failed Task 1 with 26 import failures
because its input-name predicate depended on a helper introduced only by Task
2; the historical dependency was corrected before the successful replays. The
final per-head Python/Desktop counts were 716/17, 742/17, 742/17, 742/17,
742/17, 743/34, 743/51, 745/51, and 745/51; every installed-distribution test
passed and every TypeScript check exited 0.

## Final gates and baseline reconciliation

The mandatory remediation pre-rewrite gate and every exact-head detached replay
of `scripts/test_workflow_merge_gate.sh` exited 0. The final implementation
selection at Tasks 8 and 9 was:

- Python: **745 passed / 1 skipped** (detached Task 9 run: 60.38 seconds);
- installed-distribution integration: **1 passed** (3.66 seconds);
- Desktop merge-gate selection: **51 passed across 9 files**;
- renderer TypeScript: exit 0;
- emitted tested Task 8 SHA:
  `7e2e9fea0005f0e5515bb73cbcbb2ce7bc003bb1`; the Task 9 documentation
  replay also emitted its exact detached HEAD and exited 0.

The broader `npm run test:workflow-ui` selection passed **110/110 across 15
files**. The complete local native portability selection from the three-OS CI
job passed **208/208 runnable tests with 3 skipped**. `npm run typecheck`
passed both renderer and Electron projects.
Scoped ESLint reported **0 errors** across all touched Desktop files. It found
one non-blocking padding warning in `review-run-dialog.tsx`. The known-baseline
`electron/main.ts` still reports its unrelated whole-file baseline (12 errors /
95 warnings), but none falls on a feature-changed line. The customization
checker, fixture semantic validation, Markdown formatting check, and
`git diff --check` all exited 0.

The approved baseline is 668 passed / 1 skipped Python, installed-distribution
1, Desktop merge-gate 17, and TypeScript exit 0.

Python reconciliation:

| Change                                                              | Gate delta |
| ------------------------------------------------------------------- | ---------: |
| Task 1: 47 catalog API cases + 1 pinned gate-selection meta-test    |        +48 |
| Task 2: 25 detail/catalog cases + 1 pinned gate-selection meta-test |        +26 |
| Task 6: 1 pinned Desktop Review & Run gate-selection meta-test      |         +1 |
| Task 8: 1 real-middleware E2E + 1 pinned gate-selection meta-test   |         +2 |
| Total                                                               |    **+77** |

Task 3 added provenance and digest cases to existing task-scoped files that are
outside the focused merge-gate selection, so they add zero to the gate count.
Tasks 4, 5, and 7 likewise add no Python tests to that selection.

Desktop merge-gate reconciliation is 17 + 17 Task 6 Review & Run cases + 17
Task 7 View/topology cases = 51. The broader `test:workflow-ui` selection
reconstructs from 53 before Task 4 to 110: +10 bridge/catalog integration, +11
datagrid, +17 Review & Run, and +19 View/index/topology tests, including the
new View → Review focus regression.

The new catalog, detail, and real-middleware files are pure API tests and are
correctly in the merge gate. Admission boundary regressions were added to the
existing `test_desktop_api.py`; that whole file remains in the pinned three-OS
native portability matrix. The final three additions there cover cross-entry
parity, deletion and symlink-swap races, and projection-bound admission,
reconciling the matrix from 204/3 to 208/3. They are not duplicated in the
focused merge gate.

## D1 evidence

`_verified_operator` derives `trigger_source="desktop"` for both verified
OAuth Desktop sessions and the standard loopback session-token
`local_admin_authenticated` principal. Bearer-token principals remain
`api`. Caller headers and request bodies cannot choose provenance.

Fixed digest assertions prove byte stability for CLI, chat, cron, remote-token,
legacy API, and the deterministic local-Desktop namespace. The local Desktop
digest is
`e83faa3f8e0a03c54110fd8a660c4609240a4e5242807512df88515b680aa4c6`.

Exactly one pre-existing provenance assertion changed intentionally:

- `test_post_runs_api_admission_returns_before_blocking_advance` changed its
  local-admin persisted source expectation from `api` to `desktop`.

That test still asserts `local_admin_claim`, actor
`profile-local-dashboard`, source instance `api:local-admin`, no return
route, and no request-time execution. It also now corroborates the legacy
`trigger == "desktop"` projection. No coverage was weakened.

## Fixture-based Desktop UAT

This was explicitly a fixture-based walkthrough, **not** the bundled showcase.
The committed fixtures live under
`tests/plugins/workflow/fixtures/desktop-catalog-trigger/workflows`.

The real Electron renderer was launched against an isolated Desktop user-data
directory, Hermes root, and project directory. UI controls were driven through
Electron's Chrome DevTools Protocol; renderer, preload, Electron main,
authenticated bridge, real headless backend, real REST routes, SQLite stores,
trust store, coordinator, bash executor, and approval mutation were not mocked.

1. Installed both committed fixture packages into the isolated project's real
   `.hermes/workflows/` discovery location.
2. Ran real CLI doctor and trust commands. The approval fixture digest was
   `34b39a4513937984e26d7073ecac5a5be31c0f03400ced8df1f026ec82b9b6f4`;
   `hermes workflow trust ... --digest ... --json` returned `trusted`.
3. Opened **Workflows**. The real catalog listed
   `desktop-approval-uat` as trusted, no-input, Project source, with enabled
   View and Run actions.
4. Selected **View**. Diagram rendered the three-node topology:
   `prepare-review -> operator-approval -> finish-after-approval`.
5. Toggled to **Definition**. The displayed element was a non-editable `pre`;
   all three node bodies were `[REDACTED]`. Selected **Copy definition** and
   verified the OS clipboard parsed as JSON with the fixture name and all
   redacted node values.
6. Selected **Run** from the View modal. **Review & Run** showed the real trust
   verdict, package/risk digests, execution environment, and shell-node risk.
7. Selected **Start workflow**, exercising the authenticated
   `POST /runs` wrapper. Desktop switched to **Active board** and showed the
   run as `running`, source `desktop`.
8. The coordinator executed the first bash node. The run moved to **Needs
   attention**, status `paused`, progress `1/3`, with
   `workflow approval · Approve` and the fixture's approval message.
9. Opened the run from the Attention inbox and selected **Approve** through the
   real run mutation endpoint.
10. The coordinator resumed the run. The selected run left Active and the
    Attention inbox, then showed `succeeded` and `3/3` in Completed.

Durable verification for run
`0fdff318d87543aa82d649abe26f7577` reported:

- status `succeeded`;
- trigger and provenance source `desktop`;
- assurance `local_admin_claim`;
- all three nodes `succeeded`;
- one `interaction_approved`, one `run_paused`, and one `run_succeeded`
  event.

For the fallback, selected **View** on the committed 101-node
`desktop-topology-omitted-uat` project fixture. The real detail response
omitted Mermaid with `topology_mermaid_too_many_nodes`; Desktop displayed
“Diagram omitted because the workflow is too large — showing outline.” and the
bounded `outline-001 -> outline-002 ...` text topology.

## Known v1 limitations

- **L-A — Bundled showcases are not visible in the Workflows tab.** The catalog
  scans project/profile workflows only, not bundled showcase package resources.
  The built-in “try me” content is CLI-only in v1.
- **L-B — The bundled approval-gate showcase is not runnable from the UI.**
  `laptop-diagnostic` requires legacy file/text inputs outside the v1
  flat-input contract, so it is correctly classified unsupported and
  Run-disabled.

Neither limitation was changed under UAT pressure.

## Plan deviations and rationale

- The original base wording named `main`; repository policy and maintainer
  amendment require `origin/base` and a future PR targeting `base`.
- Task 2 cited invalid `--topology ... --json` combinations. Parity instead
  compares REST topology Mermaid/text/warnings and redacted definition values
  with the flat `workflow show NAME --json` envelope. Existing CLI rejection
  remains unchanged.
- The plan assumed typed bridge errors already survived IPC. They did not.
  Task 4 added an additive serializable structured channel used only by the
  three workflow wrappers; legacy throwing callers remain byte-unchanged.
  Error normalization stays in the renderer and supports FastAPI `detail`
  plus plugin `error` envelopes without message parsing. The older OAuth
  `Error.statusCode` attachment is likely a latent non-serializing path.
- The plan's “22 existing callers” inventory was stale. The replay found 117
  current legacy `api(...)` expressions and preserved all 117 byte-for-byte.
- Task 3 initially covered only the OAuth-session principal. The real Task 8
  middleware E2E proved the standard local session-token principal still
  persisted `api`. Task 3 was rewritten and Tasks 4–7 genuinely replayed so
  local-admin now derives `desktop`.
- Task 5's final amended isolated test diff imported a production module absent
  before Task 5 and failed collection. The retained original pre-implementation
  Task 5 test state supplied the valid 14-pass/7-fail behavioral RED; the final
  production patch remained byte-identical.
- The plan named the bundled showcase for manual UAT. The maintainer confirmed
  that was an error and explicitly authorized the committed Desktop-compatible
  approval fixture. L-A/L-B above preserve the deliberate v1 boundaries.

## Verification method note

The first attempt to combine autosquash with `git rebase --exec` exposed a Git
test-environment hazard: nested repository fixtures inherited the rebase's
exported `GIT_DIR`, so unrelated Git tests attached to the outer rebase. The
run was stopped, the preserved feature ref restored the exact committed tree,
and the test-written local `core.bare`, `core.autocrlf`, and test identity
values were removed. The maintainer checkout retained its pre-existing status.
The safe replay then used a separate detached worktree with no rebase
environment and passed the full gate at all nine exact commit objects.

The hardening rewrites intentionally repeated that detached replay. An earlier
Task 1 boundary run failed 26 catalog cases because
`projection_key_is_secret` belonged to Task 2 at that historical point. The
predicate was rewritten to use the already-existing shared
`sanitize_projection`/`sanitize_text` contract, the focused selection passed
8/8, and the historical dependency was removed. That rewrite then added the
portable-name and request-work regressions and completed a nine-head replay at
708/1 through 736/1. The projection/component rewrite completed another at
709/1 through 738/1, and the final Windows namespace rewrite passed 716/1
through 745/1. This failure is part of the evidence: it prevented a commit that
only worked after a future task.

## Review and release boundary

The first whole-branch adversarial review reported **0 Critical, 4 Important,
and 1 Minor** findings. All were reproduced and remediated under TDD:

1. admission now reuses the bounded, failure-isolating catalog resolver, so
   corrupt/duplicate neighbors do not block a valid row and requested invalid
   or capacity entries return typed 422/503 envelopes;
2. catalog and detail use one 4 MiB-capped, shape-validated, lock-free trust
   snapshot per request, including valid non-object JSON fail-closed behavior;
3. enum inputs without exactly one bounded, usable choice list are classified
   unsupported before Catalog, View, or Review can expose a half-form;
4. untouched optional inputs are omitted, while optional booleans expose an
   explicit unset/on/off control and preserve explicitly selected false; and
5. the two unreachable structured-response fallbacks were removed, leaving one
   shared collector branch per authenticated transport.

The first remediated rereview reported **0 Critical, 3 Important, and 1 Minor**
additional findings. All were likewise reproduced and remediated:

1. admission now applies the catalog's fixed executable-resource budget and
   carries the same cached bytes through immutable snapshot creation; oversized
   per-file and aggregate packages return typed retryable 503, and a snapshot
   digest mismatch is discarded with typed 409 before run persistence;
2. one Desktop input-name predicate enforces storage-safe, sanitizer-stable,
   non-redacted names in catalog classification and POST validation, so forged
   slash, backslash, ANSI/control, or projection-secret keys fail with 422;
3. enum choices are string-only in v1, preventing Python/JSON/JavaScript number
   coercion from collapsing distinct options into duplicate Select values; and
4. View captures its stable catalog trigger and passes it through Review, so
   closing after View → Run restores meaningful focus rather than targeting a
   detached modal button.

The next rereview reported **0 Critical, 3 Important, and 0 Minor** findings.
All three were reproduced against that reviewed commit and remediated under
TDD:

1. admission now seals the exact validated resource cache before trust
   authorization and snapshotting; deletion or a symlink swap after digest
   verification cannot trigger a disk fallback or copy untrusted bytes;
2. the shared Desktop input-name contract rejects Windows device names,
   forbidden characters, control characters, and trailing dots/spaces in both
   catalog classification and admission/store validation; and
3. catalog rows each receive the same per-package resource budget used by
   detail/admission, while a separate explicit request-work bound returns a
   truthful partial list with `truncated=true` instead of misclassifying later
   packages.

The decisive RED evidence was: 10 failed / 6 passed for the new portable-name
catalog cases; a store test that failed to reject a non-portable name; an E2E
row classified `capacity` even though detail and admission accepted it; and
two resource mutations that respectively returned 500 or snapshotted
`UNTRUSTED_EXTERNAL_RESOURCE`. The global-work RED also returned
`truncated=false`. Focused GREEN selections passed 19/19 catalog name cases,
6/6 API cases, the direct store case, both race mutations, and the bounded
partial-list assertion. A wider affected Python selection passed 122/122.

The following rereview reported **0 Critical, 2 Important, and 0 Minor**
findings. Both were reproduced and remediated under TDD:

1. a trusted 513-node package was Catalog-invalid and Detail-capacity but a
   direct POST still returned 202. Task 2 now owns one shared bounded
   definition-projection qualifier for list/detail/admission, and Task 8's
   resolver applies it before any persistence; and
2. the portable-name predicate counted characters rather than the generated
   filename component. A 64-emoji name reached storage and could raise
   `OSError`. Task 1 now bounds `name + ".txt"` to 255 UTF-8 bytes and 255
   UTF-16 code units, while Task 8 pins POST and direct-store rejection.

The decisive RED selections were 2 failures in Catalog (the oversized UTF
name was exposed and projection exhaustion was mislabeled), 2 failures in POST
(the name fell through to 404 and the 513-node workflow returned 202), and 1
direct-store failure. Focused GREEN selections passed 18/18 Catalog cases,
2/2 POST cases, and 1/1 store case; the wider affected selection passed
114/114. The resulting exact-head replay passed 709/1 through 738/1, and the
native portability matrix passed 208/3.

The next rereview reported **0 Critical, 2 Important, and 0 Minor** findings.
Both Windows namespace gaps were reproduced and remediated under TDD:

1. the shared device-name expression now rejects `COM¹`, `COM²`, `COM³`,
   `LPT¹`, `LPT²`, and `LPT³`, case-insensitively and with extensions, matching
   Windows' documented reserved aliases; and
2. a shared collection-level portability check now rejects names that collide
   under case folding. Catalog classifies such a form unsupported, POST returns
   422, and Store rejects the combined file/text input set before writing any
   snapshot bytes.

The decisive RED selections were 7 Catalog failures (six superscript aliases
plus `Mode`/`mode`), 1 POST failure, and 1 Store failure. Focused GREEN passed
24/24 Catalog cases, 1/1 POST, and 1/1 Store; the wider affected selection
passed 121/121. The resulting exact-head replay passed 716/1 through 745/1;
the native matrix remained 208/3.

The following rereview reported **0 Critical, 1 Important, and 0 Minor**
finding: Store compared semantic input names, not the actual generated target
components, so file input `report.txt` and text value `report` both wrote
`inputs/report.txt` and left a stale manifest digest. The direct-store RED
failed 1/1. Task 8 now validates case-folded raw file targets together with
`.txt`-suffixed text targets before either channel writes. Focused GREEN passed
1/1, the affected selection remained 121/121, and the rewritten Task 8 and 9
gates both passed 745/1 with installed-distribution 1, Desktop 51, and
TypeScript exit 0.

The decisive fresh rereview reported **0 Critical, 0 Important, and 0 Minor**
findings. It verified exact and case-variant mixed-channel target collisions are
rejected before copying or writing, failed validation removes staging, and all
prior projection, resource-budget, sealed-cache, Windows-device, component
bound, and Catalog/Detail/POST parity remediations remain intact. Its focused
accumulated boundary selection passed 31/31, `git diff --check` passed, and all
nine tasks were rescanned without edits.

## Four-reviewer disposition and CF-2 remediation

The final four-reviewer report at
`docs/reviews/2026-07-20-workflow-catalog-desktop-trigger-adversarial-review.md`
gave a **READY FOR MERGE** verdict with no Critical, High, or Important finding.
It identified CF-2 as a release-blocking branding Minor: the empty-catalog Docs
link used the lowercase upstream Nous Research host, which the Desktop build
transform cannot rebrand.

CF-2 was remediated in a separate post-review commit so the nine required task
commits and their replay evidence remain intact. The link now targets the
cmetech-owned `base` source for the committed operator page:
`https://github.com/cmetech/hermes-agent/blob/base/website/docs/user-guide/features/workflows.md`.
The brand descriptors and releases-only OTTO/LOOP24 repositories expose no
separate documentation host, so one fork-owned page is the durable destination
for both release brands and does not depend on build-time name substitution.

Strict TDD evidence for CF-2:

- RED: the focused `index.test.tsx` run failed 1 and passed 25 because the
  renderer still emitted the upstream Nous URL;
- GREEN: the focused file passed 26/26;
- `npm run test:workflow-ui` passed 110/110 across 15 files;
- renderer `npx tsc --noEmit` exited 0;
- scoped ESLint on `catalog.tsx` and `index.test.tsx` reported zero errors; and
- the full merge gate passed 745/1 Python, installed-distribution 1, Desktop
  51/51 across 9 files, and TypeScript exit 0. This assertion replacement adds
  no test-count delta, so the existing +77 Python and +34 Desktop reconciliation
  remains exact.

Nothing was merged, pushed, tagged, released, deployed, or published. No pull
request was opened. The branch is left solely for maintainer merge review.
