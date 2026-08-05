# Gateway autostart and dispatcher visibility — design

**Date:** 2026-08-05
**Branch:** `base` (brand-agnostic; merges to every brand)
**Status:** approved design, pending implementation plan

## Problem

A LOOP24 v5.2.1 user created a kanban task through the desktop UI. It landed in
Triage and never moved. Nothing in the product said why. Recovering took an hour
of CLI archaeology across four separate gates, none of which were discoverable:

1. The kanban DB sidecar was briefly unwritable (a one-off; not addressed here).
2. **The messaging gateway had never run on that machine.** The kanban
   dispatcher — the thing that sweeps Triage, promotes `todo → ready`, and
   spawns workers — exists *only* inside `gateway/run.py`. The desktop backend
   starts plugin routes and an in-process cron scheduler but never the
   dispatcher, so a desktop-only install has a board that looks alive and is
   structurally inert.
3. `kanban.default_assignee` ships as `""`, so `ready` tasks are never claimed.
4. No web search provider was configured, so the task could not have satisfied
   its own prompt.

The root cause of (2) is a gate asking the wrong question. `install.ps1`'s
`Start-GatewayIfConfigured` starts the gateway only when a **messaging** token
is present:

```powershell
foreach ($var in @("TELEGRAM_BOT_TOKEN","DISCORD_BOT_TOKEN","SLACK_BOT_TOKEN","SLACK_APP_TOKEN","WHATSAPP_ENABLED")) { ... }
if (-not $hasMessaging) { return }
```

But the gateway also hosts kanban dispatch and cron, and says so itself at boot:

```
WARNING gateway.run: No messaging platforms enabled.
INFO    gateway.run: Gateway will continue running for cron job execution.
```

The process knows it is useful without messaging. The code that decides whether
to start it does not. Configure kanban, skip messaging, and you get no
dispatcher — permanently, and silently.

The target users are non-technical. They will create a task, see nothing
happen, and have no path to the cause.

## Scope

Three changes. Boot persistence is explicitly **out of scope** (that remains a
Scheduled Task decision).

Deliberately **not** built, having been superseded by what already exists:

- Tray supervision of the gateway. The desktop already spawns it detached, and
  the only gap the tray would close is boot persistence.
- New start/stop endpoints and UI controls. `POST /api/gateway/restart` and the
  Gateway settings panel already do this.
- A cross-owner conflict model. `gateway/run.py` already refuses to double-run
  ("Another gateway instance (PID …) started during our startup. Exiting to
  avoid double-running") and the dispatcher holds a singleton lock.

## 1. Start the gateway with the desktop backend

**Site:** the FastAPI lifespan startup in `hermes_cli/web_server.py`
(`_lifespan`, ~line 362). One-shot, fail-safe: any exception is logged and
swallowed, never blocking backend startup.

**Action:** reuse `_spawn_gateway_restart` — already detached
(`windows_detach_flags()` / `start_new_session=True`), already guarded against
concurrent restarts via `_ACTION_PROCS`.

**Guards — all four required:**

| Guard | Why |
|---|---|
| Backend is desktop-spawned (`HERMES_DESKTOP=1`) | A server `hermes dashboard` relies on its own gateway; this behavior is desktop-only and must not change that deployment. The lifespan already uses this exact env check to gate the desktop cron ticker |
| Gateway not already running (`resolve_gateway_liveness`) | Idempotent across backend restarts |
| `_HERMES_GATEWAY` absent from the environment | The backend runs *inside* the gateway in some deployments; without this it would spawn itself. `_spawn_hermes_action` already scrubs this var for the same reason (#52470) |
| `gateway.autostart_with_desktop` is not `false` | New key in `hermes_cli/config_defaults.py` under the existing `gateway` block, default `true`, so the behavior can be turned off |

**Default-on is deliberate.** The gateway hosts kanban dispatch and cron, both
enabled by default; the honest condition is "does this install have automation
enabled", which is effectively always. The accepted consequence is that opening
the desktop app can start agent workers on `ready` tasks without an explicit
click. That is the intended product behavior: a non-technical user who creates a
task should not need to know a second process exists.

**Not touched:** `Start-GatewayIfConfigured` in `install.ps1` / `install.sh`
stays messaging-only. With the desktop covering the desktop case, widening the
installer gate adds no value for this problem and would put the change inside
the python-isolation ledger's blast radius for no reason.

**Idle cost:** one gateway process with a 60s dispatcher tick. Workers spawn
only when tasks are `ready` *and* assigned.

## 2. Say when the board is inert

**Frontend — shell-level banner.** The SDK kanban plugin's UI ships as a
prebuilt bundle (`plugins/kanban/dashboard/dist/index.js`) with no source in
this repo, so a banner cannot be added inside the plugin's board. It renders in
the desktop shell above the `/kanban` workspace pane, which covers **both** the
contributed plugin board and the built-in fallback board
(`apps/desktop/src/app/kanban/index.tsx`).

Condition: `statusSnapshot.gateway_running === false`. That field is already
returned by `/api/status` and already typed in
`apps/desktop/src/types/hermes.ts` (`gateway_running`, `gateway_pid`,
`gateway_state`), so no new endpoint or polling is needed.

Copy states the consequence and the remedy: nothing will pick these cards up,
and here is where to start it — linking to the Gateway settings panel. Product
names stay out of the string so the brand transform has nothing to rewrite.

**Backend — widen the create-time warning.** `plugins/kanban/dashboard/plugin_api.py`
emits a dispatcher-presence warning only for `ready` + assigned tasks:

> Only emit for ready+assigned tasks; triage/todo are expected to wait

Auto-decompose invalidated that. With `kanban.auto_decompose` defaulting true, a
Triage card *does* depend on a running dispatcher, so a stalled card is
indistinguishable from a queued one — and the one signal that would have
disclosed it is suppressed for exactly the column where it was needed. Extend
the warning to `triage` and `todo`.

## 3. Three things named "gateway", three distinct states

The footer chip fuses two states and omits the third. `gatewayState === 'open'`
(the desktop↔backend websocket) plus `inferenceStatus.ready` (the inference
gateway at `127.0.0.1:18080`) render as one "Gateway ready" label — which is
what told the user everything was healthy while the gateway they needed was
down.

| Row | Source | Today |
|---|---|---|
| Backend link | `gatewayState` | fused into one chip |
| Inference | `inferenceStatus?.ready` | fused into one chip |
| Automation (dispatcher + cron) | `statusSnapshot.gateway_running` | **absent** |

Sites: `apps/desktop/src/app/shell/hooks/use-statusbar-items.tsx` (the chip) and
`apps/desktop/src/app/shell/gateway-menu-panel.tsx` (the panel, which already
receives `statusSnapshot`). New copy in `apps/desktop/src/i18n/en.ts`; other
locales fall back to English, and the locale allowlist ships `en` only.

## Testing

| Change | Test |
|---|---|
| Autostart decision | Unit tests over the four cases: already running → no spawn; `_HERMES_GATEWAY` set → no spawn; config disabled → no spawn; otherwise → spawn |
| Autostart is fail-safe | A raising liveness probe must not break backend startup |
| Inert-board banner | Renders when `gateway_running` is false, absent when true |
| Create-time warning | Present for `triage`/`todo`, not only `ready`+assigned |
| Three-state chip | Each state independently reflected |

## Merge durability

All the touched files are shared with upstream: `web_server.py`,
`use-statusbar-items.tsx`, `gateway-menu-panel.tsx`, `plugin_api.py`, plus the
shell route wrapper. An upstream rewrite drops any of these with no conflict and
no build error, and — as with the node-deps stage — the failure is silent: the
board simply goes back to looking fine while doing nothing.

This gets a ledger entry under `docs/upstream-customizations/`, in the shape
used by `windows-npm-toolchain.yaml`: owned symbols, the named guard tests,
merge guidance (UNION, never `--theirs`), and a removal condition. The banner
test is the load-bearing one — it is the only mechanism that can detect the
regression.

`plugin_api.py` is already governed by `workflow-orchestration.yaml` as a
Bucket-1 UNION seam; the warning-widening is an additive change inside that
existing contract.

## Out of scope, recorded

- Boot persistence (Scheduled Task; user has explicitly deprioritized it).
- `kanban.default_assignee` defaulting to `""` — a real friction point with the
  same "silently dead-ends" character, but a separate decision about whether
  unassigned work should auto-route.
- Web search provider not configured — configuration, not a defect.
- The `kanban.db-shm` permission failure and its Unix-only remediation text on a
  Windows-only failure path. Worth fixing; unreproduced, so not specified here.
