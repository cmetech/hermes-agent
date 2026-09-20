# Windows Desktop validation evidence

## Local integration approval (2026-09-20)

The user explicitly approved merging into `base` and deferred macOS/Linux
testing until after Windows UAT. This supersedes the earlier pre-merge platform
gate in the plans; it does not claim those platforms have passed validation.
The documented Windows baseline suite failures and parser limitation remain open.

`base` was fast-forwarded from `56d05c885f2f11552ba369836b12f389b4fdbf11`
to feature tip `111149a30c76fa7307b7b872676b8a876bca8e82`, retaining all 21
feature commits and both customization ledger entries without conflicts.
No remote push, upstream synchronization, branded-branch regeneration, installed
application update, or release publication was performed.

The prepared short-path worktree was retained for Windows UAT and diagnostics.
Post-merge tests use its existing dependency installation after verifying that
its Git tree is identical to the merged `base` tree. The primary checkout stays
on `base`; only this integration receipt is amended after the test runs.

Post-merge verification passed:

- Nine focused Electron suites: 116 tests (lifecycle, primary startup, dial
  claims, remote liveness, generation, generation integration, SSH bootstrap,
  power-resume revalidation, and registry primary-profile scope).
- Five focused UI suites: 75 tests (renderer connection client, route prefetch,
  route loading boundary, Model Settings, and route performance).
- Combined feature ledger diff coverage and expected commit subjects against
  `56d05c885f..111149a30c`, plus `git diff --check`.

The full suite was not repeated for this conflict-free fast-forward; its earlier
Windows baseline failures remain documented below. These focused results are
not a claim that the entire suite or macOS/Linux passed.

## Scope and environment

Validation on the affected native Windows laptop, using Node 22.23.1 and
the production Electron renderer with the existing optional performance probe.
Windows reports 63.7 GiB physical memory and eight logical processors.

Worktree: `C:/wt/hermes-desktop-connection`.
Branch: `design/desktop-connection-lifecycle`, forked from `base` at
`56d05c885f2f11552ba369836b12f389b4fdbf11`.
Initial implementation reviewed at `9852d4b015a73f167b125f4921c3ed193d6df406`.
This document distinguishes that revision's measurements from later review fixes.

The source diff contains Desktop code/tests and documentation. No Python agent,
CLI, TUI, prompt, tool-schema, or session-storage source was changed.

## Native findings at the initial implementation revision

Read-only inspection of the installed app's `loop24/logs/desktop.log` confirms
slow startup outside the test harness:

| Existing installed-app start (UTC) | Resolve to ready | Spawn to ready | Port announcement to ready |
| --- | --- | --- | --- |
| 2026-09-17 12:41:39.886 | 62.634 s | 52.822 s | 0.137 s |
| 2026-09-17 12:42:56.456 | 33.375 s | 30.334 s | 0.115 s |
| 2026-09-17 16:03:36.330 | 55.833 s | 51.614 s | 0.175 s |
| 2026-09-19 20:28:02.014 | 18.587 s | 16.619 s | 0.077 s |

These historical runs used the installed app, not the feature branch. They show
that healthy launches can exceed the old shorter renderer deadline and that the
pre-listen phase dominates. They do not isolate import, filesystem scanning,
configuration, skill synchronization, or antivirus cost from one another.

The stock isolated harness did not connect in three 120-second checks. Reading
`getBootProgress()` and credential-filtered `getRecentLogs()` explained why:
each disposable home stopped at `bootstrap.choice`, waiting for installation.
The harness seeds `~/.hermes`, which does not exist on this branded Windows
installation. The neutral base build also correctly declines another brand's
CLI found on PATH. These runs are not evidence of a network or socket timeout.

The installed Co-worker runtime is v4.2.2 in the `loop24` home. Native acceptance
then used the existing process-local `HERMES_DESKTOP_HERMES_ROOT` developer
override to select that installed runtime while retaining disposable
`HERMES_HOME` and Electron user data. No live profile/configuration was copied or
edited, and no messages were submitted. This tests
the new Desktop against the installed runtime, not a rebuilt Python runtime.

| Check | Observed result |
| --- | --- |
| First valid local connection | Ready in 27,990 ms; no restart required |
| Second valid local connection | Ready in 24,669 ms; no restart required |
| First run backend spawn to port announcement | Approximately 27.9 seconds |
| First run port announcement to health-ready | Approximately 105 ms |
| Already-running local alias resolution | 3 ms and 4 ms respectively |
| First Capabilities navigation | Loading fallback at 38 ms, route content commit at 338 ms |
| First Messaging navigation | Loading fallback at 14 ms, route content commit at 310 ms |
| Model Settings | Primary, auxiliary, and MoA sections rendered successfully |

Route-content commit is not completion of every backend data request. Capabilities
may continue showing its data-loading state after the lazy route module commits.
Settings opens as an overlay; no complete end-to-end Settings timing was captured.

Repeated actual sidebar navigation worked, but did not add route measurements.
Review traced this to import-prefetch deduplication also suppressing subsequent
navigation-intent marks. This is included in the review correction wave.

The earlier stock `cold-start --spawn --prod --runs 3` renderer benchmark measured
median spawn-to-driver 5,781 ms and FCP 2,472 ms, and failed its committed baseline
comparison. Its first-run setup state and unmatched baseline environment prevent
using it as proof of an improvement or regression caused by these commits.
No controlled before/after laptop comparison has been completed.

A separate import trace observed work in `hermes_cli.web_server`, MCP libraries,
and configuration loading. It does not account for the entire pre-listen delay
and is not a complete startup profile. Its initial harness watched the descriptive
banner on stdout, while this runtime emits `HERMES_BACKEND_READY` on stdout and
the banner on stderr; therefore its 60-second observation limit is not a backend
readiness timing. The disposable backend also auto-started a gateway. Its PID
and `hermes_home` were verified against that test home's PID file before stopping
the two test gateway processes. Recursive temporary-directory cleanup was rejected
by automatic command review; `hermes-startup-trace-znZAHi` remains under Windows
Temp. No live installed gateway was stopped.

## Verification already run at the initial revision

- Typecheck passed.
- Lint passed with 202 pre-existing warnings and zero errors.
- Production build passed, including Electron bundles and native dependency staging.
- Full UI suite: 749 files / 9,200 tests passed; one test in
  `src/plugins/hermes-bots/cron-prompt.test.ts` failed because native Windows has
  no `sh` executable available to its `spawnSync('sh', ...)` call.
- Desktop platform suite: 136 files / 2,089 tests passed, 13 files / 51 tests failed,
  six skipped. Recorded failure categories include POSIX file modes, macOS path
  assumptions, SSH shell paths, symlink privileges, Git timing/CRLF, and Node test
  discovery. An earlier baseline had 13 failing files / 53 failing tests. This is
  not a fully green cross-platform acceptance result.
- Focused connection and page behavior suites passed before final review.
- Page manifest exactly covers its 16 changed source/test files for
  `5fb2c3f204..9852d4b015`; connection files are separately owned in `fork-runtime.yaml`.
  The native customization checker encounters its existing Windows parser
  `invalid_json` failure. A diagnostic run bypassing only Windows parser selection
  validated the page manifest; that is not a native checker pass.

## Review corrections and integration gates

Final review identified: primary/default cache identity; cached remote descriptors
bypassing health checks; preparation time consumed by the connection deadline;
timed-out underlying dial claims retained across retry; older optional Settings
reads overwriting a newer refresh; incomplete warm navigation measurements; and
auxiliary refresh hiding valid same-scope content. All seven are corrected in
`882a8aaab9`, with the native-discovered policy fix in `a8d86110a0`.

The existing installer/setup/update authority must retain its preparation semantics.
Only active connection work consumes the connection budget; ordinary progress must
not renew that budget. Remote descriptor reuse must retain existing dispatch health
checks. These are corrections to the implementation, not Python startup changes.

These requirements supersede two overly broad details in the original plan:
unconditional ready-descriptor reuse and a wall-clock attempt/watchdog that includes
interactive setup. Local reuse remains fast; operational remote validation remains
authoritative. Preparation pauses preserve the remaining active-work budget rather
than renewing it. The tradeoffs are a health-check cost for remote reuse and an
intentionally unbounded wait for the existing user/install/update authorities.

Original pre-integration gate (superseded by the approval above): obtain macOS/Linux CI and resolve or formally accept the
documented pre-existing full-suite/platform-check limitations. The correction
wave and local named-profile native acceptance are verified below.
No native macOS/Linux run or real remote OAuth/SSH run has been
performed here. Deferred optional-response and cross-scope behavior has unit-test
coverage, but no live throttled-backend acceptance was performed at this revision.

The feature is now merged locally into `base` as recorded above; branded branches
and the installed application have not been updated.

## Correction-wave native acceptance

Renderer production build passed (15,296 transformed modules); each native run
rebundled Electron main/preload from the corrected source. At correction commit
`882a8aaab9`, three launches reached an open gateway and visible editable composer:

| Run | Process spawn to observed open socket | Electron lifecycle to ready | Local ready alias |
| --- | --- | --- | --- |
| 1 | 22,142 ms | 19,418 ms | 3 ms |
| 2 | 51,698 ms | 45,143 ms | 2 ms |
| 3 | 38,081 ms | 35,868 ms | 3 ms |

These are uncontrolled laptop observations, not a speedup comparison. Source
verification and other host activity overlapped parts of the experiment.

Actual repeated sidebar navigation on run 3 now records every visit:

| Visit | Visible feedback/content | Route content committed |
| --- | --- | --- |
| Capabilities first | 37 ms | 335 ms |
| Messaging first | 27 ms | 336 ms |
| Capabilities warm | 22 ms | 22 ms |
| Messaging warm | 19 ms | 19 ms |
| Capabilities warm again | 21 ms | 21 ms |

The combined smoke initially failed on named-profile startup. The actual IPC
policy omitted `ipcDeliveryMarginMs`, although preload expected it. `NaN` became
an effectively immediate preparation inspection timeout. A regression using the
actual serialized policy reproduced the failure at 1 ms; commit `a8d86110a0`
supplies both typed policy fields and passes that regression. This is why unit checks alone did not
count as native acceptance.

Opening Model Settings also caused background provider resolution and the branded
runtime seeded an Outlook MCP connector into the otherwise disposable home.
Its logs reported provider fallback/payment errors, failed MCP connection and a
12.5-second Python event-loop stall. No chat prompt was submitted; the runs must
not be described as proving zero background provider/network activity. For the
repeat fixture, `auxiliary.free_only: true` and `mcp_servers.outlook.enabled: false`
are set in the generated test config. Real user configuration remains untouched.

The three retained homes are `hermes-perf-home-ybxxLp`,
`hermes-perf-home-dhOFmp`, and `hermes-perf-home-b9wosc` under Windows Temp.
The smoke harness tore down its Electron/backend processes; subsequent process
inspection found only the pre-existing user gateway before the next test launch.

### Successful repeat after the IPC policy correction

The repeated production smoke exited 0 against the corrected main/preload and
the same built renderer. Test home: `hermes-perf-home-O6ehdp` under Windows Temp.

- Gateway socket open observed 35,121 ms after Electron spawn; composer visible
  and editable. Authoritative lifecycle readiness was 31,080 ms.
- Backend spawn to port announcement: 30,926 ms; port announcement to
  health-ready: 133 ms. The remaining startup delay is predominantly before
  the backend starts listening, not the local socket handshake.
- First Capabilities/Messaging visits: visible feedback 44/24 ms and route
  content commits 339/329 ms. Warm visits: 19/21/23 ms.
- Model Settings: primary/provider, auxiliary and Mixture of Agents sections
  all rendered; no explicit prompt or model mutation was submitted.
- Named local profile cold ensure: 16,772 ms. Warm ensure: 1 ms, same generation.
  Named and default profiles had distinct backend URLs.
- Actual sidebar switch to named profile: 414 ms. Return to default: 115 ms.
  Both sockets opened and the original profile rail DOM node remained mounted.
- After harness teardown, process inspection showed only the two pre-existing
  user gateway Python processes; no test Electron/backend process remained.

### Final review and checks

The scoped reviewer approved `9852d4b015..a8d86110a0`: all seven findings
addressed, serialized-policy defect corrected, no remaining new defect identified.

- Correction native suites: 115 tests in nine files passed before the final
  policy amendment; the amended lifecycle suite then passed all 32 tests.
- Correction UI suites: 75 tests in five files passed.
- Full typecheck passed after the correction wave; final Electron typecheck
  passed after the policy amendment. Targeted lint and `git diff --check` passed.
- Main controller independently reran the final lifecycle suite: 32 passed.
- Main controller independently reran Model Settings: 52 passed.
- Production renderer and Electron bundle build passed; the final native
  rerun rebuilt Electron from the amended source.
- The repository checker's `validate_diff_coverage` passed for the union of the
  two feature entries against `56d05c885f..a8d86110a0`, including expected commit
  subjects. Selecting the entire runtime manifest also asks about unrelated
  older feature commit boundaries and is not the appropriate feature-range check.
  Full native symbol/parser validation still has the documented `invalid_json`
  baseline failure; coverage-only validation is not a substitute for that gate.

Remaining acceptance gaps: native macOS/Linux CI, real remote SSH/OAuth,
sleep/wake, deliberately failed live target recovery, and live independently
throttled optional Settings responses. Deterministic tests cover the related
deadline, remote-resolution, cancellation, and stale-response contracts; they
do not replace the missing native scenarios.

### Rulings retained from Superpowers execution

1. Setup/install/update preparation keeps its existing wait semantics outside
   the active connection budget. A wrong boundary can strand installation;
   composed tests cover the separation.
2. Remote health validation takes precedence over unconditional descriptor reuse.
   It costs a health check, but skipping it can strand a dead remote connection.
3. The native-reproduced missing policy field was corrected before handoff within
   the existing implementer/reviewer dispatches. The cost was one narrowly scoped
   correction/test commit; leaving the immediate timeout would violate the
   requested working implementation.

## Acceptance boundaries

- The native production smoke uses generated empty test homes, no copied secrets,
  and no submitted prompts. It is a connection/navigation test, not a realistic
  long-transcript or tool-streaming benchmark.
- A successful warm descriptor ensure proves backend reuse; a live sidebar
  profile switch additionally exercises socket activation and retained shell.
  Neither replaces remote SSH/OAuth or sleep/wake acceptance.
- Fake-clock tests cover healthy starts beyond the old deadlines, genuine hangs,
  retry ownership, and preparation waits. The native runs must not manufacture a
  four-minute installation on the user's machine merely to repeat that coverage.
- Cross-platform policy tests are useful regression evidence, not proof of native
  macOS/Linux packaging. Those CI gates remain mandatory before integration.
- Increasing the legitimate startup allowance prevents false failures. It is not
  evidence that Python imports or backend initialization became faster. A future
  startup optimization needs a controlled phase profile and before/after samples.
