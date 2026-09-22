# Desktop Page Responsiveness Implementation Plan

> **For the implementing agent:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` after the connection lifecycle plan is accepted. Use `superpowers:test-driven-development` for every behavior change and `superpowers:verification-before-completion` before any completion claim. Do not merge, push, or delete the worktree without separate user approval.

**Goal:** Make Desktop page navigation acknowledge intent immediately, warm likely routes without competing with cold boot, and let useful Model Settings content render without waiting for unrelated slow requests.

**Architecture:** Centralize built-in lazy-page loaders so navigation can prefetch the exact module React later consumes. Wrap lazy routes in a visible, accessible shared fallback and measure intent-to-visible/settled locally. Gate hover/focus and idle prefetch on an open gateway. Split Model Settings into independently published primary, auxiliary, and MoA request groups while preserving profile epochs and save generations. Existing profile-scoped React Query caches remain the data authority.

**Tech Stack:** React 19, React Router 8, Nanostores, TanStack Query 5, Vitest/Testing Library, existing declarative Hermes `Loader`. No new runtime dependency.

**Spec:** [Desktop Connection Lifecycle and Page Responsiveness Design](../specs/2026-09-19-desktop-connection-page-responsiveness-design.md), especially Page Responsiveness, Performance Evidence, Testing and Verification, and Upstream-Customization Capture.

## Global constraints

- Execute only after the connection lifecycle tasks are green so page measurements are not confused with a false connection timeout.
- Work in `C:\wt\hermes-desktop-connection` on the same feature branch until integration is explicitly approved.
- Run `npx`/`npm` commands from `apps/desktop`; run repository `python`/`git` commands from the worktree root.
- Desktop renderer only. Do not modify Python/core, CLI, TUI, gateway protocol, or shared prompt/session behavior.
- Reuse the existing declarative `Loader` through `PageLoader`; never add a requestAnimationFrame animation or literal prose spinner.
- Lazy loading must remain lazy. Prefetch warms module bytes; it must not mount the page, run page effects, or fetch page data before navigation.
- No route prefetch may compete with cold connection startup. Intent and idle warming are enabled only after `$gatewayState === 'open'`.
- Idle warming is deliberately limited to Settings and Capabilities. Do not create a universal keep-alive or preload every route.
- Cache/query keys remain profile/connection scoped. Cached data from another scope must never flash during navigation or switching.
- Preserve Capabilities query/refetch behavior unless a failing contract test proves a necessary correction.
- Preserve Model Settings `profileEpoch`, MoA save generation/timer, edit drafts, mutation ordering, and profile-switch clearing.
- Reuse `t.common.loading` for accessible fallbacks so no locale update is required. If implementation adds any new user-visible copy, update all project-required locales in the same task.
- Performance marks/logs are local only; no telemetry.

## File and interface map

| File | Responsibility |
| --- | --- |
| `apps/desktop/src/app/lazy-pages.tsx` | Memoized module loaders, exported lazy views, route-to-prefetch table. |
| `apps/desktop/src/app/lazy-pages.test.tsx` | Exact-once prefetch/import reuse and unknown/contributed route behavior. |
| `apps/desktop/src/app/route-load-boundary.tsx` | Shared visible `PageLoader` fallback plus route settled marker. |
| `apps/desktop/src/app/route-load-boundary.test.tsx` | Visible/accessibility/no-focus and settle timing tests. |
| `apps/desktop/src/app/hooks/use-route-prefetch.ts` | Open-gateway intent and idle prefetch scheduling/cancellation. |
| `apps/desktop/src/app/hooks/use-route-prefetch.test.ts` | Cold-boot exclusion, idle fallback, cancellation, exact route set. |
| `apps/desktop/src/app/contrib/surfaces.tsx`, `wiring.tsx`, `app/chat/route-tile.tsx` | Consume centralized lazy views and visible boundaries. |
| `apps/desktop/src/app/chat/sidebar/index.tsx` | Pointer/focus intent prefetch for route-backed navigation. |
| `apps/desktop/src/app/settings/model-settings.tsx`, `.test.tsx` | Progressive primary/auxiliary/MoA loading with existing race guards. |
| `apps/desktop/src/lib/desktop-performance.ts`, `.test.ts` | Local route intent/visible/settled marks and slow-only log policy. |
| `docs/upstream-customizations/desktop-page-responsiveness.yaml`, `README.md` | Separate merge ledger entry for page responsiveness behavior. |

Planned public helpers:

```ts
export function prefetchDesktopRoute(to: string): Promise<void> | null
export function useRoutePrefetchEnabled(): boolean
export function useIdleDesktopRoutePrefetch(enabled: boolean): void

export function noteDesktopRouteIntent(to: string): void
export function noteDesktopRouteVisible(to: string): void
export function noteDesktopRouteSettled(to: string): void
```

## Task 1 — Centralize lazy modules and prove prefetch reuse

**Files:** Create `src/app/lazy-pages.tsx` and `.test.tsx`; modify no route consumer yet.

- [ ] Write tests with injected/import-spied loaders proving: calling `prefetchDesktopRoute('/skills')` starts the Skills module once; rendering `SkillsView` reuses that same promise; query/hash suffixes normalize through `routePathname`; Settings resolves its overlay module; unknown and contributed/plugin routes return `null` and are not guessed.
- [ ] Add table coverage for all current built-in lazy views: Artifacts, Messaging, Skills, Workflows, Kanban, Agents, Command Center, Cron, Webhooks, Profiles, Settings, and Starmap.
- [ ] Run RED:

  ```powershell
  cd apps/desktop
  npx vitest run --project ui src/app/lazy-pages.test.tsx
  ```

- [ ] Implement one memoized loader per module and build each exported `lazy()` component from that loader. Build a table from route constants to loader functions; do not duplicate route strings.
- [ ] Keep contributed routes outside the built-in table; their owners control their bundle lifecycle.
- [ ] Run GREEN and `npm run typecheck`. Commit `perf(desktop): share lazy page loaders`.

## Task 2 — Replace blank Suspense states with a visible boundary

**Files:** Create `src/app/route-load-boundary.tsx` and `.test.tsx`; modify `src/app/contrib/surfaces.tsx`, `wiring.tsx`, and `src/app/chat/route-tile.tsx`.

- [ ] Write a deferred-lazy-component test that clicks/navigates, asserts a `role="status"` fallback with the localized common loading label is immediately visible, asserts it does not take focus, resolves the module, then asserts real content replaces it.
- [ ] Add coverage for full workspace page, overlay, and route-tile sizing variants. The fallback must occupy the owning surface without obscuring the old context before an overlay route is actually selected.
- [ ] Run RED:

  ```powershell
  npx vitest run --project ui src/app/route-load-boundary.test.tsx src/app/contrib/surfaces.test.tsx
  ```

- [ ] Implement `RouteLoadBoundary({ route, variant, children })` with `<Suspense fallback={<PageLoader label={t.common.loading} ... />}>`. Put a no-DOM settled marker inside the Suspense content so settlement is reported only after the lazy child can commit.
- [ ] Replace every `fallback={null}` used for the built-in full pages and overlays in the three consumer files. Import lazy views only from `lazy-pages.tsx`.
- [ ] Do not change unrelated embed/code-highlighter Suspense fallbacks and do not wrap contributed routes in a new module-prefetch contract.
- [ ] Run GREEN plus focused routing tests:

  ```powershell
  npx vitest run --project ui src/app/route-load-boundary.test.tsx src/app/contrib/surfaces.test.tsx src/app/contrib/wiring-routing.test.ts src/app/chat/session-tile-owner-route.test.ts
  ```

- [ ] Run `rg -n "lazy\(|Suspense fallback=\{null\}" src/app/contrib/surfaces.tsx src/app/contrib/wiring.tsx src/app/chat/route-tile.tsx` and account for every result. Commit `fix(desktop): show page loading progress`.

## Task 3 — Prefetch from intent and only after connection readiness

**Files:** Create `src/app/hooks/use-route-prefetch.ts` and `.test.ts`; modify `src/app/chat/sidebar/index.tsx` and `src/app/contrib/wiring.tsx`; update sidebar/wiring tests.

- [ ] Write scheduler tests proving no module loads while gateway state is `starting`, `reconnecting`, `closed`, or `error`; switching to `open` schedules exactly Settings and Capabilities during idle; cleanup cancels pending work; a no-`requestIdleCallback` environment uses a cancellable `setTimeout` fallback.
- [ ] Write sidebar interaction tests proving `pointerenter` and keyboard `focus` prefetch a route only when open, repeated intent is idempotent, and click/navigation behavior is unchanged.
- [ ] Run RED on the new and closest sidebar/wiring tests.
- [ ] Implement the hook with injected/testable scheduler helpers. Idle callback runs one small route table, not component mounts or API requests.
- [ ] In `ChatSidebar`, call `prefetchDesktopRoute(item.route)` from `onPointerEnter` and `onFocus` when the gateway is open. Preserve all existing click, context-menu, accessibility, and split-pane behavior.
- [ ] In `ContribWiring`, call `useIdleDesktopRoutePrefetch(gatewayState === 'open')`. Do not schedule before the initial gateway opens and do not reschedule already-loaded modules on reconnect churn.
- [ ] Run focused GREEN and `npm run typecheck`. Commit `perf(desktop): prefetch likely pages after gateway ready`.

## Task 4 — Render Model Settings progressively

**Files:** Modify `src/app/settings/model-settings.tsx` and `.test.tsx`.

- [ ] Add deferred-promise tests proving the primary model/provider controls render as soon as `getGlobalModelInfo` + `getGlobalModelOptions` resolve even while auxiliary or MoA remains pending.
- [ ] Add the inverse tests: slow/failing main data does not publish an incomplete main selector; auxiliary failure affects only its section; MoA failure affects only its section; successful siblings remain interactive.
- [ ] Add race tests: switch profile while each of the three groups is pending; no prior-profile group may publish. Add a late prior-profile MoA autosave/error assertion alongside the existing generation test.
- [ ] Add refresh tests proving catalog refresh preserves current drafts, all three groups start concurrently, and each section updates independently without breaking `refresh()` callers that await complete settlement.
- [ ] Run RED:

  ```powershell
  npx vitest run --project ui src/app/settings/model-settings.test.tsx
  ```

- [ ] Split the monolithic `loading`/`error` state into primary, auxiliary, and MoA state. `refreshPrimary` awaits only info + options; `refreshAuxiliary` and `refreshMoa` run independently; `refresh` starts all three before awaiting `Promise.allSettled`.
- [ ] Keep `profileEpoch` checks before every state write. Keep `moaSaveGeneration`, `moaRef`, and timer cleanup unchanged in authority. On profile switch, clear all three sections before starting the new profile's concurrent group requests.
- [ ] Show `ModelSettingsSkeleton` only while the primary section has no publishable data. Give auxiliary and MoA their own lightweight existing `Skeleton`/`PageLoader` state and local error rendering; do not block the primary controls.
- [ ] Keep mutations and post-save refresh semantics. A failure in one read group must not erase last valid same-profile data from another group.
- [ ] Run GREEN, then settings/profile scope regressions:

  ```powershell
  npx vitest run --project ui src/app/settings/model-settings.test.tsx src/app/settings/profile-scope.test.tsx src/store/settings-scope.test.ts
  npm run typecheck
  ```

- [ ] Self-review every setter under the profile epoch and every debounced MoA save under both epoch and generation. Commit `perf(desktop): stream model settings sections`.

## Task 5 — Preserve profile-correct cached page data

**Files:** Prefer tests only in existing Capabilities/Settings suites; modify production query code only if a test exposes a real contract violation.

- [ ] Add or strengthen behavior tests proving a warm revisit in the same `{connectionId, profile}` scope paints cached Skills/Capabilities data while refetching, and switching scope never paints the previous scope's rows.
- [ ] Verify query keys include all identity dimensions already required by the API route. Do not snapshot query-key array lengths or literal enumeration counts; assert relationships between scopes.
- [ ] Verify the existing Capabilities refetch cadence and error behavior are unchanged. Do not introduce another cache above TanStack Query.
- [ ] Run:

  ```powershell
  npx vitest run --project ui src/app/skills/index.test.tsx src/app/settings/profile-scope.test.tsx src/store/settings-scope.test.ts
  ```

- [ ] If tests already pass with no production change, record that evidence and make no speculative refactor. If they fail, implement only the smallest profile/connection-key correction and commit it with the failing test as `fix(desktop): scope page caches to connection`.

## Task 6 — Add local route performance evidence

**Files:** Create `src/lib/desktop-performance.ts` and `.test.ts`; modify route boundary, sidebar/navigation seams, and optionally the existing opt-in perf harness adapter.

- [ ] Write pure tests with an injected monotonic clock and logger: intent starts a route measurement; fallback commit records visible; content commit records settled; superseded navigation cannot close the newer measurement; only durations at or above 500 ms emit one local log.
- [ ] Implement marks/measures with normalized route path only. Never include query strings, profile names, connection IDs, URLs, tokens, or page data in the log.
- [ ] Wire pointer/focus/click navigation intent and `RouteLoadBoundary` visible/settled callbacks. Avoid a render loop: measurements must not use React state.
- [ ] If the existing `VITE_PERF_PROBE` harness consumes marks cleanly, expose the named measures there without making production logging depend on the opt-in harness. Do not add network export.
- [ ] Run focused tests and manually inspect a slow lazy import in dev tools. Commit `perf(desktop): measure route readiness locally`.

## Task 7 — Capture the page contract in the upstream ledger

**Files:** Create `docs/upstream-customizations/desktop-page-responsiveness.yaml`; modify `docs/upstream-customizations/README.md`.

- [ ] Add one manifest with `overlap_policy: any_owned_file`, the exact lazy/fallback/prefetch/progressive-settings files, owned symbols, focused tests, and expected commit subjects.
- [ ] State merge guidance: preserve visible fallback, boot-gated prefetch, profile-scoped cache safety, and independent Model Settings publication. Do not treat a cosmetic spinner alone as upstream equivalence.
- [ ] Reference `desktop-loader.yaml` as a dependency whose declarative no-rAF loader remains authoritative; do not duplicate or alter that ledger entry.
- [ ] Set `last_verified_upstream: 29112bef099274229cadff79cdff7bf7b99c4b77` and add the new manifest to the README index.
- [ ] Run:

  ```powershell
  cd C:\wt\hermes-desktop-connection
  python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/desktop-page-responsiveness.yaml --diff 56d05c885f..HEAD
  git diff --check
  ```

- [ ] Compare manifest-owned files to the actual diff and commit `docs(upstream): capture desktop page responsiveness`.

## Task 8 — Full verification and UX acceptance

- [ ] Run from `apps/desktop`:

  ```powershell
  npm run typecheck
  npm run lint
  npm run test:ui
  npm run test:desktop:platforms
  npm run build
  ```

- [ ] On the affected Windows laptop, cold-launch the app three times. During boot, confirm no idle/hover prefetch starts before gateway open.
- [ ] Cold-open Capabilities and Settings. Confirm the click produces an immediate visible loader, the page replaces it, no loader freezes the renderer, and route timing appears only when the route takes at least 500 ms.
- [ ] Revisit both pages and confirm module load is warm and same-scope cached content paints without a blank pane. Switch profiles/sources and confirm no previous-scope data flashes.
- [ ] Throttle the auxiliary and MoA model calls independently. Confirm main model/provider controls become usable first and each delayed section recovers independently.
- [ ] Require macOS and Linux CI to pass before integration. The renderer behavior has no OS branch, but packaged cross-platform results remain required.
- [ ] Use `superpowers:verification-before-completion`; review the final diff against the approved spec and both plan documents. Do not merge to `base` until the user reviews evidence and explicitly approves integration.

## Review focus

- Navigation never leaves a blank lazy-route pane.
- Prefetch cannot compete with backend cold start and is restricted to user intent plus Settings/Capabilities idle warming.
- Prefetch reuses React's eventual import promise and never mounts or fetches page data.
- Same-scope stale-while-refresh is fast; cross-scope stale data is impossible.
- Slow auxiliary/MoA calls cannot block the main Model Settings controls.
- Existing declarative loader remains no-rAF and responsive on the affected Windows laptop.
- Changes remain Desktop-only and add no telemetry or new runtime dependency.
