# Desktop loader: offload the animation off the JS main thread

Date: 2026-07-26
Status: approved, not yet implemented
Affected: `apps/desktop/src/components/ui/loader.tsx` (+ new sibling files)

## Problem

On a corporate Windows laptop (16 GB, managed image), the Co-worker desktop app
becomes fully unresponsive while the Capabilities page loads: clicks do nothing,
the page never renders, and the app must be killed. The same build on an
unmanaged 64 GB machine behaves normally.

Total process memory across gateway, tray and app was ~1 GB, so memory pressure
is not the cause.

## Evidence

`desktop.log` on the affected machine, repeating:

```
[hermes] [renderer] webContents became unresponsive
```

That is Electron's `unresponsive` event: the renderer's JavaScript main thread
is blocked. Bootstrap completed cleanly, the backend reached ready
(`HERMES_BACKEND_READY`), and `[python] ignoring inherited PYTHONPATH for this
session` confirms the backend env scrub is working — nothing in the backend path
is at fault.

Pausing the renderer in DevTools during the hang produced a call stack composed
entirely of `a` → `requestAnimationFrame`, inside the `Loader` component.

The backend was independently exonerated by timing every call
`GET /api/skills` makes, in a cold process:

```
0.16s  _find_all_skills          62
0.00s  load_usage                 0
0.00s  bundled_manifest_names    85
0.00s  hub_installed_names        0
0.02s  load_config               81
0.00s  _get_mcp_servers           2
1.27s  configurable_toolsets     25
```

~1.45s total. The data was never expensive to produce; the renderer never got
far enough to process it.

## Root cause

`Loader` (`loader.tsx:332-365`) runs a `requestAnimationFrame` loop that, every
frame:

1. Rebuilds the SVG path from `pathSteps + 1` points — 241 for the default, 221
   for `page-loader.tsx`, 181 for the terminal — each an evaluation of
   `config.point()` plus a `toFixed(2)`, joined into a string and assigned to
   `d`, forcing the SVG engine to re-parse the path.
2. Walks `config.particleCount` particles (78 for `rose-curve`), evaluating
   `config.point()` again per particle and issuing four `setAttribute` calls
   each.
3. Sets a rotation transform.

That is ~319 curve evaluations and ~314 DOM attribute writes per frame. On a
fast machine each frame completes well inside its budget and the thread idles
between frames. On the laptop each frame overruns, `rAF` re-queues immediately,
and the main thread never becomes idle — so input is never serviced.

The failure mode is self-perpetuating and maximally bad: the spinner shown
*while* the page loads is what prevents the page from loading.

### The redundancy

`particleFor` (`loader.tsx:537-548`) derives `opacity` and `radius` solely from
`fade`, which derives solely from `tailOffset = index / (particleCount - 1)`.
Both are therefore **constant for the life of the component**, yet both are
rewritten every frame. For `rose-curve` that is 156 of the ~314 per-frame writes
setting values to what they already held.

Only three things genuinely vary per frame: the rotation angle, the path shape
(via the `detailScale` pulse), and each particle's position along the curve.
Every one is a declarative animation expressed imperatively.

### Why the machines differ

The per-frame work is identical on both. What differs is how long a frame takes.
Contributing factors, largest first:

- **Software rasterization.** Corporate images commonly disable or blacklist GPU
  acceleration, and Chromium then rasterizes on the CPU. Re-rastering an
  animated SVG path every frame in software is far more expensive than on the
  GPU — plausibly an order of magnitude, not the 2-3× a CPU difference alone
  would give. Unconfirmed; tracked separately.
- **CPU and display.** A throttled laptop core, possibly driving a high-DPI
  panel at scaling.
- **Concurrent loaders.** Each mounted `Loader` runs its own independent rAF
  loop. On a fast machine loads finish before many accumulate.

The GPU question is tracked as its own task and does not block this work.

## Goals

- Zero per-frame JavaScript in the loader.
- A loader that **cannot** block the main thread, on any machine, under any
  rendering path. Declarative animations have no JS loop to starve the thread,
  so under software rendering the worst case degrades to visible jank rather
  than a frozen application.
- Honour `prefers-reduced-motion`.
- Survive upstream merges, detectably.

## Non-goals

- Preserving the breathing morph. Explicitly sacrificed (see below).
- Optimising `configurable_toolsets` (1.27s). Noted, not addressed here.
- Changing the 21 curve definitions or the three call sites' props.
- Diagnosing GPU acceleration or the duplicate backend start.

## Design

### Animation

Compute the path **once at mount**, with `detailScale` frozen at `0.76` — the
midpoint of the `0.52 … 1.0` range `detailScaleFor` produces
(`0.52 + ((sin(θ) + 1) / 2) * 0.48`, `loader.tsx:524-531`). The curve no longer
breathes. This is a deliberate, approved
simplification: it removes the only reason `d` must change over time, which is
what makes the rest fully declarative.

Two native animations then do all the work:

- **Rotation** — CSS `@keyframes` rotating the `<g>`, duration taken from
  `config.rotationDurationMs`, `linear`, `infinite`. Compositor-driven.
- **Trail** — the particles are kept. Each circle gets
  `offset-path: path("<computed d>")` with `offset-distance` animated 0% → 100%
  over `config.durationMs`, and a per-particle negative `animation-delay`
  derived from the existing `tailOffset * config.trailSpan` so the particles
  space into the same comet tail. Chromium animates `offset-distance` natively.
  `r` and `opacity` become static attributes, set once.

Result: the rendered output is a static `d`, one CSS rotation, and 78 CSS
`offset-distance` animations. No JavaScript runs per frame.

**Fallback**, if `offset-path` proves unreliable in the packaged Chromium: a
single stroked path using `stroke-dasharray` with a CSS-animated
`stroke-dashoffset`. One element, and visually a comet streak rather than
discrete dots. A further visual simplification, accepted in advance.

### Reduced motion

Under `@media (prefers-reduced-motion: reduce)`, both animations are disabled and
the curve renders statically. `role="status"` and the `Loading` label are
unaffected — only motion is removed. Some corporate Windows images set this
system-wide, so this may be the path the affected laptop takes anyway.

### File layout

| File | Change | Conflict risk |
|---|---|---|
| `apps/desktop/src/components/ui/loader-native.tsx` | **New.** The declarative implementation. | None — absent upstream |
| `apps/desktop/src/components/ui/loader.test.tsx` | **New.** The guard tests. | None — absent upstream |
| `apps/desktop/src/components/ui/loader.tsx` | Two minimal edits: `export` on `LOADER_CURVES`; `Loader` delegates to `loader-native`. | Low — two small lines |
| `docs/upstream-customizations/desktop-loader.yaml` | **New.** Ledger entry. | None |

`loader.tsx` currently carries **no** OTTO changes — its history is upstream only
(`51c68d4ab`, `8fe8c2d6c`). This work creates a new customization surface on a
shared upstream file, which is why the preservation strategy below is part of the
design rather than an afterthought.

## Surviving upstream merges

Three layers, because file-level tactics alone are insufficient.

**1. Minimal, greppable footprint.** The implementation lives in a new file.
New files are absent upstream and cannot conflict — the same property that has
kept `otto-presets.ts` and `brand-scope.ts` merge-free. The shared file keeps
only an `export` keyword and a delegation, so upstream may churn its internals
freely.

**2. A behavioural guard against silent reverts.** This is the load-bearing
layer. A merge that rewrites `loader.tsx` wholesale can drop the delegation with
**no conflict, no build error and no type error** — the app would compile, look
identical, and freeze again on slow machines. `loader.test.tsx` therefore renders
every one of the 21 `LOADER_TYPES` with `requestAnimationFrame` stubbed and
asserts it is never called. Being behavioural rather than textual, it holds
regardless of how upstream restructures the file.

**3. An in-repo ledger.** `docs/upstream-customizations/desktop-loader.yaml`,
validated by `scripts/check_upstream_customizations.py`, which the
`otto-upstream-merge` skill already runs. The ledger travels with the branch, so
it is visible to a merge performed from another checkout — unlike the
workspace-level surface table.

A row is also added to the workspace `CLAUDE.md` surface table and its
`AGENTS.md` twin, since that file lists `loader.tsx` as untouched today. The
ledger and the test are what enforce it; the table row is documentation.

## Testing

In `loader.test.tsx`:

- All 21 `LOADER_TYPES` render with `requestAnimationFrame` stubbed; assert it is
  never called.
- `r` and `opacity` are set once and never rewritten.
- The reduced-motion branch renders without animation and retains `role` and the
  accessible label.

## Verification on hardware

Tests do not prove the freeze is gone. On the affected laptop:

- Capabilities opens.
- `desktop.log` no longer logs `webContents became unresponsive`.
- Pausing in DevTools during a load lands somewhere ordinary, not in a rAF chain.
- Visual check of the spinner on both machines — this is a look change and needs
  eyes, not only green tests.

## Risks

- **`offset-path` behaviour in the packaged Chromium is unverified.** Mitigated
  by the `stroke-dashoffset` fallback, agreed in advance.
- **The look changes.** The breathing morph is gone; under the fallback the
  discrete dots become a streak. Accepted: correctness over decoration.
- **A future upstream rewrite of `loader.tsx`.** This is what layer 2 exists for.

## Rollout

Lands on `base`, then reaches users through the normal brand merge and release
path (tracked separately). Until then the affected laptop stays affected.
