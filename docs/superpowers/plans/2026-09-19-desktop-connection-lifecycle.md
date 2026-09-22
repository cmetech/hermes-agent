# Desktop Connection Lifecycle Implementation Plan

> **For the implementing agent:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task in the current isolated worktree. Use `superpowers:test-driven-development` for every behavior change and `superpowers:verification-before-completion` before any completion claim. Do not merge, push, or delete the worktree without separate user approval.

**Goal:** Make Desktop connection startup, reconnect, and profile/source switching wait for Electron's authoritative bounded lifecycle instead of failing on shorter renderer timers.

**Architecture:** Add a typed, scope-keyed connection lifecycle coordinator in Electron. Every Desktop-owned dial joins that coordinator, while side-effect-free inspection reports its current snapshot. Electron owns stage and whole-attempt deadlines; preload adds only a strictly larger emergency IPC watchdog. A small renderer client coalesces and caches descriptors but never owns backend-start deadlines. Existing gateway/profile activation, generation fencing, and two-phase switching remain authoritative.

**Tech Stack:** Electron 41, TypeScript 6, React 19, Nanostores, Vitest fake timers, existing Hermes WebSocket/JSON-RPC transport. No new runtime dependency and no Python change.

**Spec:** [Desktop Connection Lifecycle and Page Responsiveness Design](../specs/2026-09-19-desktop-connection-page-responsiveness-design.md), especially Decisions 1–6, Performance Evidence, Testing and Verification, and Upstream-Customization Capture.

## Global constraints

- Work only in `C:\wt\hermes-desktop-connection` on `design/desktop-connection-lifecycle`, based on `base`.
- Run `npx`/`npm` commands from `apps/desktop`; run repository `python`/`git` commands from the worktree root.
- Desktop-only: do not modify the Python agent core, gateway protocol, CLI, TUI, prompts, model/tool schemas, or session semantics.
- Electron owns processes, ports, SSH/remote setup, health, retries, and terminal failure. The renderer may present progress but must not invent a shorter backend-start deadline.
- Preserve `BackendDialClaims`, connection-generation fencing, deletion/update guards, backend pools, boot-progress behavior, remote revalidation, and two-phase profile/source activation.
- `inspectConnection` is strictly read-only: no spawn, probe, retry, ticket mint, cache invalidation, or lifecycle extension.
- One normalized `{ connectionId, profile }` scope has at most one authoritative in-flight ensure. A lifecycle event never renews a deadline.
- Fast cached paths settle immediately. Genuine hangs remain bounded by Electron's policy. Preload's watchdog is derived from the same policy and is strictly larger than any permitted Electron path.
- Typed error payloads cross IPC as data. Do not depend on Electron preserving custom properties on a rejected `ipcRenderer.invoke()` error.
- Do not add a user-facing environment variable. Continue honoring the existing port-announcement override through the shared policy.
- Keep `getConnection`, `getConnectionFor`, and `revalidateConnection` as compatibility doors; implement them through the new authority.
- Preserve profile/source identity in all cache keys. Never display or publish a descriptor from another scope.
- Logs remain local and credential-free. Do not add telemetry.
- Each implementation task follows RED → minimal GREEN → focused regression → self-review → commit. Stop on an architectural contradiction rather than weakening an invariant.

## File and interface map

New names below are planned interfaces.

| File | Responsibility |
| --- | --- |
| `apps/desktop/electron/connection-lifecycle-policy.ts` | Pure stage/attempt/preload watchdog policy shared by main and preload. |
| `apps/desktop/electron/connection-lifecycle.ts` | Scope normalization, snapshots, typed errors, coalesced attempts, inspect/ensure/invalidate, transition logging. |
| `apps/desktop/electron/connection-lifecycle.test.ts` | Fake-clock lifecycle, coalescing, scoping, deadline, error, and platform-contract tests. |
| `apps/desktop/electron/backend-ready.ts`, `backend-health.ts`, `gateway-ws-probe.ts`, `ssh-connection.ts` | Export existing timeout constants needed by the policy; behavior otherwise unchanged. |
| `apps/desktop/electron/main.ts` | Bind the coordinator to existing local/registry resolve paths; publish IPC handlers/events; route renderer-facing and native feature dials through one authority. |
| `apps/desktop/electron/preload.ts` | Expose ensure/inspect/lifecycle subscription and enforce only the emergency IPC watchdog. |
| `apps/desktop/src/global.d.ts` | Public typed scope/snapshot/result/error bridge contract. |
| `apps/desktop/src/lib/desktop-connection.ts` | Renderer scope-keyed in-flight/ready cache, typed error conversion, invalidation, and compatibility feature detection. |
| `apps/desktop/src/lib/desktop-connection.test.ts` | Renderer coalescing, isolation, event, fallback, and stale-result tests. |
| `apps/desktop/src/app/gateway/hooks/use-gateway-boot.ts` | Initial boot/reconnect consumer; no renderer backend-start timer. |
| `apps/desktop/src/app/gateway/hooks/use-gateway-request.ts` | Request recovery through the shared renderer connection client. |
| `apps/desktop/src/store/gateway.ts`, `profile.ts`, `connections.ts` | Profile/source preparation and atomic activation using scope-correct shared descriptors. |
| `apps/desktop/src/api/plugins.ts`, `lib/voice-playback.ts`, Desktop workflow/bot connection consumers | Migrate all remaining direct bridge dials to the shared client. |
| `docs/upstream-customizations/fork-runtime.yaml`, `README.md` | Replace the bootstrap workaround ledger entry with the complete lifecycle contract. |

Public Electron lifecycle types:

```ts
interface DesktopConnectionScope {
  connectionId: null | string
  profile: string
}

type DesktopConnectionPhase = 'resolve' | 'launch' | 'port' | 'health' | 'remote'
type DesktopConnectionState = 'absent' | 'starting' | 'ready' | 'failed'

interface DesktopConnectionSnapshot {
  scope: DesktopConnectionScope
  attemptId: string | null
  state: DesktopConnectionState
  phase: DesktopConnectionPhase | null
  elapsedMs: number
  connection?: HermesConnection
  error?: DesktopConnectionErrorData
}

type DesktopConnectionErrorCode =
  | 'launch_failed'
  | 'port_timeout'
  | 'health_timeout'
  | 'remote_unreachable'
  | 'auth_required'
  | 'missing_connection'
  | 'missing_profile'
  | 'invalidated'
  | 'attempt_timeout'
  | 'ipc_timeout'
```

The IPC ensure result is a tagged data envelope, `{ ok: true, connection, snapshot } | { ok: false, error, snapshot }`. Preload's public `ensureConnection()` unwraps successful data and throws a renderer-visible `DesktopConnectionError` only after retaining the typed payload. `inspectConnection()` returns a snapshot directly.

## Task 1 — Lock the deadline and lifecycle contract with pure tests

**Files:** Create `apps/desktop/electron/connection-lifecycle-policy.ts`, `connection-lifecycle.ts`, and `connection-lifecycle.test.ts`; modify timeout modules only to export existing constants.

- [ ] Write policy tests first. Inject `win32`, `darwin`, and `linux` only as contract inputs; assert identical semantics, a cold local attempt budget that covers port announcement + readiness + WebSocket proof, a remote budget covering existing SSH/probe stages, and a preload watchdog greater than every Electron budget by a fixed delivery margin.
- [ ] Add a test proving the existing `HERMES_DESKTOP_PORT_ANNOUNCE_TIMEOUT_MS` override feeds both the Electron attempt bound and preload watchdog, without creating a second configuration source.
- [ ] Run RED:

  ```powershell
  cd apps/desktop
  npx vitest run --project electron electron/connection-lifecycle.test.ts
  ```

  Expected: the new modules/interfaces do not exist.

- [ ] Implement `resolveConnectionLifecyclePolicy(env)` as a pure function. Import the exported existing stage constants; do not duplicate magic timeout numbers. Include a whole-attempt deadline and a larger preload delivery margin.
- [ ] In `connection-lifecycle.ts`, implement `normalizeDesktopConnectionScope`, `desktopConnectionScopeKey`, typed snapshot/error helpers, and `ConnectionLifecycleCoordinator`. Constructor dependencies must inject `clock`, `setTimer`, `clearTimer`, `dial(scope, reporter)`, `classifyError`, `log`, and `publish` so tests never launch a process.
- [ ] Make `ensure(scope)` publish `absent → starting → ready|failed`, coalesce concurrent callers by normalized key, return cached ready descriptors synchronously through `Promise.resolve`, and reject/return a typed `attempt_timeout` at the Electron whole-attempt deadline.
- [ ] Make `inspect(scope)` return a copy of current state without invoking any dependency. Make `invalidate(scope)` fence late settlement by attempt ID and publish `invalidated` only to the matching scope.
- [ ] Add fake-timer tests for: 53-second healthy Windows launch; a launch longer than the old 20/45-second renderer limits; a genuine never-settling dial; same-scope coalescing; cross-profile and cross-connection independence; cached fast path; late success after invalidation; transition ordering; and exactly one credential-free terminal log per attempt.
- [ ] Run GREEN and existing timeout regressions:

  ```powershell
  npx vitest run --project electron electron/connection-lifecycle.test.ts electron/backend-ready.test.ts electron/backend-health.test.ts electron/backend-dial-claim.test.ts electron/backend-connection-state.test.ts electron/ssh-connection.test.ts
  ```

- [ ] Self-review for timer cleanup on every terminal path, no state mutation in inspect, and no OS-specific behavior branch. Commit `feat(desktop): define authoritative connection lifecycle`.

## Task 2 — Put Electron main and preload behind the authority

**Files:** Modify `apps/desktop/electron/main.ts`, `preload.ts`, `apps/desktop/src/global.d.ts`; extend `connection-lifecycle.test.ts`; modify/add the closest existing Electron integration tests.

- [ ] Add failing tests for the bridge contract: `inspectConnection` cannot call the dial; two `ensureConnection` IPC calls for one scope share one dial; local and registry scopes normalize correctly; failures cross as typed envelopes; preload watchdog fires only after the Electron attempt bound; a fast cached ensure does not wait for a timer.
- [ ] Add a test that a lifecycle subscriber receives discrete changed snapshots only—no heartbeat—and that unsubscribing removes the listener.
- [ ] Run the focused tests and observe RED.
- [ ] Instantiate one process-wide coordinator in `main.ts`. Its dial adapter must retain `BackendDialClaims` and route primary/profile requests to `ensureBackend`, registry requests to `ensureRegistryBackend`, then apply existing generation assertions and header stripping before publication.
- [ ] Thread a narrow optional `ConnectionAttemptReporter` through the existing resolution/spawn boundaries so snapshots can report `resolve`, `launch`, `port`, `health`, or `remote`. Do not duplicate the boot state machine. Convert the primary eager `startHermes()` kick to the same coordinator ensure so renderer calls join the already-running authoritative attempt.
- [ ] Add `hermes:connection:ensure`, `hermes:connection:inspect`, and `hermes:connection:lifecycle` IPC. Send tagged ensure envelopes and sanitized snapshots. Never send proxy headers, tokens beyond the already-approved descriptor contract, raw child output, or unbounded exception objects.
- [ ] Make existing `hermes:connection` and `hermes:connection:for` handlers delegate to the coordinator and preserve their return shape. Make renderer-facing WebSocket URL mint paths obtain their descriptor through the coordinator before minting.
- [ ] Route native feature entry points that can independently dial the same Desktop scope—terminal, preview/plugin routing, session windows, roster/profile enumeration, and resume rebuilds—through a single `ensureDesktopConnection` adapter. Keep recursive resolver calls internal to the raw backend functions to avoid coordinator self-deadlock.
- [ ] In preload, expose `ensureConnection(scope)`, `inspectConnection(scope)`, and `onConnectionLifecycle(callback)`. Wrap ensure and connection-derived WS URL/revalidation IPC in `invokeWithConnectionWatchdog`, using only the policy's larger preload bound; convert a watchdog expiry into typed `ipc_timeout`.
- [ ] Extend `global.d.ts` with the exact types and retain legacy bridge declarations. Do not use `any` at the public boundary.
- [ ] Run focused GREEN:

  ```powershell
  npx vitest run --project electron electron/connection-lifecycle.test.ts electron/primary-backend-startup.test.ts electron/connection-generation-integration.test.ts electron/connection-generation-routing.test.ts electron/backend-start-failure.test.ts
  npm run typecheck
  ```

- [ ] Inspect `rg -n "backendDialClaims\.run|ensureBackend\(|ensureRegistryBackend\(" electron/main.ts` and account for every non-recursive call as coordinator-owned or document why it is internal. Commit `fix(desktop): make Electron own connection lifecycle`.

## Task 3 — Add the renderer connection client and remove premature timers

**Files:** Create `apps/desktop/src/lib/desktop-connection.ts` and `.test.ts`; modify `src/lib/with-timeout.ts`, gateway boot/request hooks, gateway/profile stores, and their focused tests.

- [ ] Write renderer-client tests for normalized scope keys, same-scope promise sharing, ready-cache reuse, lifecycle-event invalidation, cross-profile/source isolation, attempt-ID stale-result fencing, typed error preservation, and legacy bridge fallback.
- [ ] Add regression tests to `use-gateway-boot.test.tsx` proving a healthy ensure resolving after 45 seconds succeeds and a typed Electron terminal failure reaches the boot overlay without waiting for a renderer timeout.
- [ ] Replace the existing gateway-store timeout test expectation with the new contract: a 20+ second cold profile ensure remains pending/owned, then succeeds; a typed Electron timeout rejects and releases `entry.connectPromise` so retry works.
- [ ] Run RED:

  ```powershell
  npx vitest run --project ui src/lib/desktop-connection.test.ts src/app/gateway/hooks/use-gateway-boot.test.tsx src/store/gateway.test.ts
  ```

- [ ] Implement the renderer client. `ensureDesktopConnection(scope)` calls the new bridge when present, coalesces by normalized key, caches only ready descriptors, and drops failed/invalidation entries. `inspectDesktopConnection(scope)` never falls back to legacy ensure. Subscribe once to lifecycle events and fence updates by attempt ID.
- [ ] Migrate `use-gateway-boot.ts` initial boot, retry, wake recovery, bootstrap handoff, and fallback-profile warmup to the client. Delete the renderer-side bootstrap-deadline renewal workaround that the Electron policy supersedes.
- [ ] Migrate `use-gateway-request.ts`, `store/gateway.ts`, and `store/profile.ts`. Remove `withTimeout` around connection ensure/inspect/revalidate and any WS URL IPC now guarded by preload. Retain unrelated model-request and transport-connect bounds.
- [ ] Preserve two-phase switching: prepare target while old profile/source remains active; activate only after target gateway is open; use the descriptor from the shared scoped ensure; retain activation epoch/abort fencing; never publish a late/superseded target.
- [ ] Replace `sharedPrimaryRoute`/`isAttachedSharedRemote` duplicate direct dials with the descriptor returned by the same shared ensure. Preserve the conservative shared-remote failure behavior and existing `sharedPrimary`/`sharedRemote` tests.
- [ ] Remove `BACKEND_BOOT_WAIT_TIMEOUT_MS` from `with-timeout.ts`. Keep `RECONNECT_ATTEMPT_TIMEOUT_MS` only for operations that cannot cold-start a backend, and rewrite its comment accordingly.
- [ ] Run focused GREEN:

  ```powershell
  npx vitest run --project ui src/lib/desktop-connection.test.ts src/app/gateway/hooks/use-gateway-boot.test.tsx src/app/gateway/hooks/use-gateway-request.test.ts src/store/gateway.test.ts src/store/gateway-shared-remote.test.ts src/store/gateway-connection-lifecycle.test.ts src/store/profile.test.ts src/store/profile-switch-failure.test.ts src/store/profile-select-source.test.ts
  ```

- [ ] Search for remaining direct dials and classify them before commit:

  ```powershell
  rg -n "getConnection\(|getConnectionFor\(|revalidateConnection\(" src
  rg -n "BACKEND_BOOT_WAIT_TIMEOUT_MS|DESCRIPTOR_LOOKUP_TIMEOUT_MS" src
  ```

  Expected: only the renderer client, type declarations, compatibility tests, or explicitly documented non-starting calls remain. Commit `fix(desktop): join scoped connection attempts in renderer`.

## Task 4 — Migrate the remaining Desktop consumers

**Files:** Modify `src/api/plugins.ts`, `src/lib/voice-playback.ts`, `src/app/workflows/marketplace/supervisor-provider.tsx`, `src/plugins/hermes-bots/create-dialog.tsx`, and every remaining product call site found by the Task 3 audit; update colocated tests.

- [ ] For each call site, first add/adjust a test that supplies a delayed ensure beyond 20 seconds and proves the feature waits for Electron, or supplies a typed terminal error and proves the feature releases its local ownership state.
- [ ] Migrate plugin sockets, voice playback, marketplace supervision, and bot creation to `ensureDesktopConnection`. Use the exact connection/profile scope already owned by each feature; do not substitute the active route for an explicit background route.
- [ ] Keep feature-specific request/audio/operation timeouts after a descriptor is ready. Remove only the timers that can race backend startup or Electron-owned revalidation.
- [ ] Verify no call site caches an unscoped descriptor and no late promise can write after its profile/source generation changes.
- [ ] Run all affected tests, then:

  ```powershell
  rg -n "getConnection\(|getConnectionFor\(|revalidateConnection\(" src
  npx vitest run --project ui src/api/plugins.test.ts src/lib/voice-playback.test.ts src/plugin-socket-scope.test.ts
  ```

  Add exact workflow/bot test paths discovered during implementation to the command. Commit `fix(desktop): route feature dials through connection authority`.

## Task 5 — Capture diagnostics and upstream-merge ownership

**Files:** Modify lifecycle modules/tests, `docs/upstream-customizations/fork-runtime.yaml`, and `docs/upstream-customizations/README.md`.

- [ ] Add a test that each attempt logs one terminal structured line with attempt ID, normalized non-secret scope, final phase/state, elapsed milliseconds, and error code; ensure transition events themselves do not spam logs.
- [ ] Add renderer `performance.mark`/`measure` points for initial connection and profile/source activation. Keep marks local; no upload, identifier, URL, token, or raw error.
- [ ] Replace ledger entry `desktop-bootstrap-aware-connection-deadline` with `desktop-electron-owned-connection-lifecycle`. List every owned lifecycle/policy/client symbol and focused test, and describe removal only when upstream has one Electron-owned scoped lifecycle with equivalent slow-start, bounded-hang, and switching tests.
- [ ] Keep `last_verified_upstream: 29112bef099274229cadff79cdff7bf7b99c4b77`; this feature work does not advance the upstream merge baseline.
- [ ] Run the manifest diff check against the branch base:

  ```powershell
  cd C:\wt\hermes-desktop-connection
  python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/fork-runtime.yaml --diff 56d05c885f..HEAD
  ```

- [ ] Run `git diff --check` and inspect the ledger file list against `git diff --name-only 56d05c885f..HEAD`. Commit `docs(upstream): capture desktop connection lifecycle`.

## Task 6 — Full verification and live Windows acceptance

- [ ] Run Desktop static and full unit verification from `apps/desktop`:

  ```powershell
  npm run typecheck
  npm run lint
  npm run test:desktop:platforms
  npm run test:ui
  npm run build
  ```

- [ ] Run the repository customization checker for every modified manifest and `git diff --check`.
- [ ] Run at least three packaged or production-mode cold launches on this Windows laptop. For each, record port-announcement time, health-ready time, WS-open time, and first usable composer time. Confirm no renderer timeout occurs while Electron still reports a healthy starting attempt.
- [ ] Cold-start one named local profile and, if configured, one real remote/SSH source. Confirm old context remains usable until target activation and retry succeeds after a deliberately failed target.
- [ ] Verify fast warm launch/reconnect still resolves immediately and sleep/wake recovery still works.
- [ ] Require the repository's macOS and Linux CI jobs to pass before integration. Unit tests inject `win32`, `darwin`, and `linux` to prove policy parity, but do not claim a Windows host emulates packaged macOS/Linux behavior.
- [ ] Use `superpowers:verification-before-completion`; compare the final diff to the approved spec and this plan. Do not merge to `base` until the user reviews the evidence and explicitly approves integration.

## Review focus

- A healthy 53-second Windows cold start cannot lose to a renderer 20/45-second timer.
- A never-settling dial still terminates under Electron's bounded attempt policy and releases ownership for retry.
- Inspection has no side effects and lifecycle events never extend deadlines.
- Every profile/source descriptor and cache entry is scope-correct and generation-fenced.
- Existing macOS/Linux fast paths and sleep/wake behavior are preserved without platform-specific production branches.
- No Python/core, CLI, TUI, prompt, tool, or model behavior changed.
- The upstream ledger replaces—not stacks on—the old bootstrap-only workaround.
