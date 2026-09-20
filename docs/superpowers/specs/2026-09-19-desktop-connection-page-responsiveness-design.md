# Desktop Connection Lifecycle and Page Responsiveness Design

Date: 2026-09-19
Status: Approved by the user on 2026-09-19
Target branch: `base`

## Intent

Hermes Desktop must remain usable while a backend is starting, reconnecting, or
changing profiles. A healthy backend that starts slowly on Windows must not be
reported as timed out merely because the renderer used a shorter deadline than
Electron. Fast starts on macOS and Linux must still complete immediately, and a
genuinely wedged launch must still end in a bounded, actionable failure.

Page navigation must also acknowledge the user's action immediately. A lazy
route must not show a blank pane while its bundle loads, and a slow secondary
request must not hide an otherwise usable page. Warm revisits should paint
cached, profile-correct data while refreshing in the background.

This design changes only the Electron Desktop application. It does not change
the Hermes agent core, Python gateway protocol, CLI, TUI, prompt construction,
tool schemas, or session semantics.

## Evidence and Root Cause

The affected Windows laptop has 64 GB of memory and a healthy NVMe drive. The
Desktop application used roughly 650 MB during diagnosis, so resource pressure
does not explain the delay.

Observed local backend startup times ranged from approximately 14 to 53 seconds.
Electron already allows up to 90 seconds for the Python backend to announce its
port, followed by a bounded readiness check. The renderer independently gives
an initial connection 45 seconds and several profile/reconnect call sites only
20 seconds. Every `getConnection()` and `getConnectionFor()` IPC handler calls
an `ensure` path and may cold-start a local or remote profile backend. The
renderer therefore cannot correctly classify those calls as quick lookups.

When the renderer timeout wins, `withTimeout()` rejects without cancelling the
underlying IPC operation. Electron continues starting the backend, which can
become healthy shortly after the UI has already shown a failure. Restarting the
app merely retries the same work and can make recovery appear random.

The Windows-specific duration is consistent with cold Python imports and
real-time antivirus inspection of a large dependency tree. It exposes a
cross-platform ownership bug; the correction must not be a Windows-only branch.

Page loading has two adjacent sources of perceived delay:

- workspace and overlay lazy routes currently use `Suspense fallback={null}`;
  the user's click can therefore produce an empty pane;
- `ModelSettings` waits for model identity, provider options, auxiliary models,
  and MoA models as one `Promise.all` barrier before it publishes any result.

The Capabilities page already uses profile-scoped React Query keys and parallel
queries. The design preserves that architecture instead of introducing a
second cache.

## Decisions

### 1. Electron owns connection lifecycle and deadlines

Electron is authoritative for processes, ports, remote tunnels, and backend
health. It will become the only layer that decides whether a connection attempt
is still valid.

A small Electron lifecycle coordinator will expose two operations over a
normalized connection scope `{ connectionId, profile }`:

- `inspectConnection(scope)` returns the current lifecycle snapshot without
  spawning, probing, retrying, or otherwise changing state;
- `ensureConnection(scope)` starts or joins the one authoritative attempt and
  resolves only with a usable descriptor or a typed terminal failure.

The scope normalizer will preserve the existing meanings of the primary
connection, the local registry source, named profiles, remote/cloud sources,
and SSH-backed sources. It will use the existing `BackendDialClaims`, connection
generation fencing, backend pools, remote revalidation, and deletion guards.
It is an ownership layer over those mechanisms, not a replacement for them.

`inspectConnection` is for presentation and diagnostics only. A cached `ready`
snapshot is not permission to perform work against a potentially stale remote.
Any operation that needs a usable backend calls `ensureConnection`, which keeps
the current route-specific liveness checks.

### 2. The lifecycle contract is explicit and typed

Lifecycle snapshots use a discriminated state:

```ts
type ConnectionLifecycleState = 'absent' | 'starting' | 'ready' | 'failed'
type ConnectionLifecyclePhase = 'resolve' | 'launch' | 'port' | 'health' | 'remote'

interface ConnectionLifecycleSnapshot {
  attemptId: number | null
  elapsedMs: number
  phase: ConnectionLifecyclePhase | null
  scope: { connectionId: string | null; profile: string }
  state: ConnectionLifecycleState
  connection?: HermesConnection
  error?: ConnectionLifecycleError
}
```

The phase union maps to resolving the runtime/route, launching, waiting for a
port, checking health, and connecting a remote transport. Existing human
boot-progress copy remains the source of localized initial-boot messages.

Terminal failures cross IPC as data rather than relying on Electron to preserve
custom JavaScript error properties. The error record includes a stable code,
attempt and scope identity, phase, elapsed time, retryability, and a safe human
message. Expected codes cover launch failure, port-announcement timeout,
health timeout, remote unreachable, authentication required, missing
connection/profile, and lifecycle invalidation. The preload-only emergency
watchdog reports a distinct IPC timeout.

Logs include the scope, attempt ID, phase, elapsed time, and failure code. They
must not include tokens, authorization headers, secrets, or a raw environment.

### 3. One deadline policy is shared by main and preload

Existing port-announcement, health, remote, SSH, and preflight bounds remain
bounded and retain their current cold-start tolerance. A pure Electron policy
module will compose those stage budgets into the maximum valid lifecycle budget.

The preload bridge applies one emergency watchdog to detect a main-process IPC
that never settles. Its duration is derived from the same policy module and is
strictly greater than every valid Electron attempt plus a delivery margin. It
is not a second backend-start deadline.

Fast paths do not wait for any budget: a ready local backend, healthy remote, or
already-running profile resolves as soon as its existing checks finish.

Renderer `withTimeout()` remains available for operations that are genuinely
renderer-owned and short, such as a bounded WebSocket ticket mint. It will no
longer wrap an operation that can start a backend.

### 4. Existing public bridge calls remain compatibility aliases

The preload bridge will add the explicit lifecycle operations. Existing
`getConnection`, `getConnectionFor`, and `revalidateConnection` calls remain as
narrow compatibility aliases while internal Desktop callers migrate to the new
contract. The aliases delegate to the authoritative lifecycle coordinator; they
do not retain independent timing behavior.

This protects auxiliary windows and downstream contributions compiled against
the older bridge shape without forcing the renderer to keep using an ambiguous
API. No new Python RPC or shared model tool is introduced.

### 5. Renderer connection work is scoped and coalesced

The renderer will maintain a small lifecycle cache keyed by normalized
connection/profile scope. It stores snapshots and shared in-flight ensure
promises, not backend authority. Electron still provides the source of truth.

Initial boot, secondary gateways, plugin REST routing, voice playback, and
profile/source activation will use one renderer connection client. This removes
the scattered 20/45-second wrappers and ensures every path observes the same
typed result.

The existing two-phase switch behavior is preserved:

1. The requested profile/source becomes a pending target.
2. The current foreground gateway, session, and draft remain usable.
3. The target backend and socket open without publishing foreground identity.
4. The target profile, connection descriptor, and active gateway publish in one
   guarded commit only after readiness.
5. Failure leaves the previous foreground active and offers retry.

Existing hover prewarming and background profile gateways remain in place.
Prewarming calls the same coalesced ensure path, so hovering and then clicking
cannot create duplicate processes.

The target gateway's successful ensure result becomes the descriptor used for
activation. The renderer will not issue a second descriptor-only `getConnection`
call in parallel. Attempt IDs, scope identity, and the existing switch token
prevent an abandoned or late result from changing the foreground.

Leaving a page or changing the pending target revokes that caller's publication
rights; it does not cancel a shared Electron launch that another window or
consumer may still need. Explicit connection reconfiguration, profile deletion,
and application shutdown continue to invalidate generations and own teardown.

### 6. Lifecycle progress is observable, not deadline-extending

Electron will publish discrete lifecycle transitions keyed by scope. These are
state changes, not heartbeats, and they never extend a deadline.

The primary cold-start path continues to feed the existing Desktop boot-progress
surface. Named-profile and source switches use the existing pending-target and
non-modal shell states. A healthy post-boot reconnect must not resurrect the
full-screen cold-boot overlay.

Retry always begins or joins a current authoritative attempt. It never launches
an untracked duplicate process. Permanent failures such as a deleted profile or
registry entry remain fail-stop rather than entering an infinite reconnect loop.

## Page Responsiveness

### Visible lazy-route fallback

Workspace routes and conditional overlay routes will use a shared page-loading
fallback instead of `null`. The fallback uses the existing declarative `Loader`
inside a lightweight page-shaped frame, preserves the surrounding shell, has an
accessible localized label, and does not take focus.

The fallback must not introduce JavaScript frame loops. The existing
`desktop-loader` invariant remains authoritative.

### Intent-driven route prefetch

Core lazy route imports will be defined as reusable loader functions. Navigation
rows call a small `prefetchDesktopRoute(path)` helper on keyboard focus or after
the existing short pointer dwell. Repeated calls share the module promise.

No eager prefetch runs while the initial backend is cold-starting. Once the
gateway is usable, a browser-idle callback prefetches only Settings and
Capabilities. It yields to user input and does not load every route or plugin
contribution.

Contributed routes without an explicit prefetch capability remain lazy. This
change does not create a new plugin manifest or public extension API.

### Profile-correct stale-while-refresh data

Existing React Query data remains keyed by connection/profile scope. Returning
to a previously loaded scope paints its successful cached data immediately and
refreshes it in the background according to the shared query policy. Opening a
different scope shows a loading skeleton; it must never use the prior scope as
placeholder data.

Refresh failures preserve the last successful same-scope data and expose a
non-destructive retry state. Mutations keep their existing optimistic-write and
authoritative-refresh behavior.

The Capabilities page already follows this model for skills and toolsets. Its
scope keys, refetch timing, independent query states, mutations, and explicit
refresh behavior will be retained.

### Progressive Model Settings

`ModelSettings` will stop treating all four requests as one page-wide result.
The main model and provider catalog form the minimum usable model-selection
section and load together. Auxiliary assignments and MoA configuration load
independently into their own skeleton/error boundaries.

A failure in an optional section does not erase a successfully loaded main
model selector. Profile epochs and save generations continue to fence late
responses. On a profile change, the component shows cached data only when it
belongs to the new scope; it never retains another profile's draft or model
values.

### No universal keep-alive framework

This work will not add a generic page-retention framework or keep every heavy
route mounted. Existing stateful surfaces that already persist, including live
gateways and the terminal host, remain persistent. Bundle prefetch, visible
fallbacks, scoped query caching, and progressive section rendering address the
measured delays without imposing permanent memory and subscription costs on
every page.

## Performance Evidence

Local marks will measure:

- connection ensure start to ready/failure, including phase durations;
- profile/source selection to atomic activation;
- route intent to first visible fallback or page frame;
- route intent to settled page content.

Each connection attempt writes one terminal duration summary to Desktop logs.
Route measurements are logged only when first visible content takes at least
500 ms. No outbound analytics, third-party telemetry, stable user identifier,
or attribution tag is added.

## Testing and Verification

Implementation follows RED/GREEN TDD.

### Electron lifecycle tests

- A simulated local cold start that exceeds 45 seconds but remains inside the
  Electron policy succeeds.
- A named-profile cold start that exceeds 20 seconds succeeds.
- A ready backend resolves immediately; increasing the maximum does not add a
  delay on macOS or Linux.
- Port-announcement, health, remote, and IPC hangs end at their authoritative
  bounded failure with the correct typed code.
- Concurrent ensures for one normalized scope share one dial and attempt ID.
- Inspection never starts, probes, retries, or mutates a backend.
- A compatibility alias delegates to the same attempt and deadline policy.
- Generation invalidation prevents an obsolete completion from publishing.
- Error serialization excludes credentials and preserves actionable metadata.

The lifecycle coordinator will be extracted behind injectable clocks and
ensure/probe functions so these are ordinary deterministic unit tests rather
than tests that import the entire Electron main process.

### Renderer and switching tests

- Initial boot, secondary gateway, profile activation, plugin API, and voice
  routing use the shared client without a renderer backend-start timeout.
- Multiple renderer callers share the same ensure promise while Electron also
  remains the ultimate coalescing authority across windows.
- Switching away revokes publication from a late attempt.
- A failed target leaves the current foreground profile, connection, session,
  and draft intact.
- A warm profile switch does not restart its backend.
- Hover prewarm followed by activation creates one attempt.
- Deleted profiles/connections remain permanent failures with no retry loop.
- Current remote revalidation and authentication recovery behavior is retained.

### Page tests

- Every core lazy workspace and overlay route paints the shared fallback rather
  than a blank pane.
- Route focus/hover prefetches once, navigation reuses the module promise, and
  cold boot suppresses idle prefetch.
- Same-scope cached data paints during refresh; different-scope data never does.
- Main model/provider success renders even when auxiliary or MoA loading fails.
- Late profile-A responses cannot publish after switching to profile B.
- Reduced-motion and no-`requestAnimationFrame` loader invariants continue to
  pass.

### Cross-platform and live verification

- Run the Desktop unit, typecheck, lint, and build suites.
- Run the repository's Desktop test wrapper for `win32`, `darwin`, and `linux`
  platform contracts.
- Run the packaged/startup lifecycle tests that cover Windows process and path
  behavior.
- On the affected Windows laptop, record at least three cold launches and one
  cold named-profile activation. No healthy launch inside Electron's deadline
  may produce a renderer timeout or require an app restart.
- Verify a warm restart, warm profile switch, Settings revisit, and Capabilities
  revisit have no artificial wait.
- macOS and Linux CI must pass before integration. Where hardware timing cannot
  be reproduced locally, deterministic fake-clock tests prove the shared policy
  and CI proves platform packaging/build behavior.

No CLI/TUI behavior test is expected to change because their source and
transport are outside the diff. The final scoped-diff review must confirm that
shared Python agent and TUI files were not modified.

## Upstream-Customization Capture

The implementation lands as reviewable commits on a feature branch based on
`origin/base`, then merges into `base`. `main` remains sync-only.

The existing `desktop-bootstrap-aware-connection-deadline` entry in
`docs/upstream-customizations/fork-runtime.yaml` will be replaced by a broader
`desktop-electron-owned-connection-lifecycle` entry. It will declare every
shared upstream-owned Electron/preload/renderer file, exact owned symbols,
focused tests, `overlap_policy: any_owned_file`, merge guidance, and a removal
condition based on behavioral equivalence.

Page-loading changes will receive a separate
`docs/upstream-customizations/desktop-page-responsiveness.yaml` manifest and an
index entry in the ledger README. Keeping the connection invariant and page UX
in separate entries lets `$otto-upstream-merge` classify one as equivalent
without accidentally removing the other.

The feature-diff checker must cover both manifests before integration. Their
`last_verified_upstream` baseline remains the immutable upstream commit already
merged into `base`; normal feature work does not advance upstream baselines.

After verification, the feature branch is merged into `base`. A separate tested
`base` to `loop24` merge carries the change into the branded release branch.

## Non-Goals

- No Python import, bytecode, packaging, or antivirus optimization. Those belong
  to the separately reviewed shared-startup track.
- No changes to `run_agent.py`, `tui_gateway`, CLI, TUI, model providers,
  prompts, tool schemas, or session storage.
- No Windows-only timeout branch and no user-facing timeout environment variable.
- No unbounded retries, progress heartbeats, or deadline extension from progress
  events.
- No duplicate page-data cache beside React Query.
- No eager loading of every route, plugin, or page at application startup.
- No generic keep-alive framework or broad page rewrite.
- No outbound telemetry.

## Success Criteria

The change is successful when a healthy slow backend is governed by one
Electron-owned lifecycle and cannot be rejected early by the renderer; a hung
backend still fails within a documented bound; profile/source changes preserve
the previous usable foreground until atomic activation; page navigation always
paints immediate feedback; warm same-scope revisits use safe cached data; and
the full behavior is recorded in the upstream-customization ledger for future
`$otto-upstream-merge` rehearsals.
