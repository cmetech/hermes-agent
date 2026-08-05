# Gateway Autostart and Dispatcher Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A desktop user who creates a kanban task gets it worked without knowing a second process exists — and when something is genuinely not running, the product says so instead of looking idle.

**Architecture:** Three independent changes on `base`. The backend starts the messaging gateway from its FastAPI lifespan when it is desktop-spawned and nothing else has (the gateway hosts the kanban dispatcher and cron). The kanban route grows a banner driven by `gateway_running`, which `/api/status` already returns. The footer's fused "gateway" chip splits into the three distinct things that name refers to.

**Tech Stack:** Python 3.11 / FastAPI / pytest on the backend; React 19 / TypeScript / `@tanstack/react-query` / vitest + Testing Library on the desktop.

## Global Constraints

- Branch is `base` (brand-neutral). Never hardcode a brand name (`OTTO`, `LOOP24`) in code, copy, or tests.
- New user-facing copy must contain no product name, so the build-time brand transform has nothing to rewrite.
- A new i18n key must be added in THREE places or the build fails `tsc`: declared in `apps/desktop/src/i18n/types.ts` (an explicit `Translations` interface), then defined in `en.ts` AND `zh.ts` — both declare `: Translations` and must satisfy every required key. Do NOT add it to `ar.ts`, `ja.ts`, or `zh-hant.ts`: those use `defineLocale()` and fall back to English by design. Follow the existing `operations.kanbanUnavailable` key as the precedent.
- `npm run test:ui` does NOT typecheck. Any task touching TypeScript must also pass `cd apps/desktop && npx tsc --noEmit -p tsconfig.json`.
- Every touched file is shared with upstream. Changes are additive; never restructure surrounding code.
- Python tests run with `./venv/bin/python -m pytest`. Desktop UI tests run with `npm run test:ui --prefix apps/desktop`.
- Do not modify `scripts/install.ps1` or `scripts/install.sh` (out of scope, and inside the python-isolation ledger's blast radius).
- Do not modify `plugins/kanban/dashboard/dist/*` — a prebuilt bundle with no source in this repo.

---

### Task 1: Gateway autostart for desktop-spawned backends

**Files:**
- Modify: `hermes_cli/config_defaults.py` (the existing `"gateway"` block, ~line 2454)
- Modify: `hermes_cli/web_server.py` (new helpers near `_spawn_gateway_restart` ~line 4108; call site in `_lifespan` ~line 402)
- Test: `tests/hermes_cli/test_gateway_autostart.py` (create)

**Interfaces:**
- Consumes: `_spawn_gateway_restart(profile=None)` (existing, spawns detached), `gateway.status.resolve_gateway_liveness(use_cache=False) -> GatewayLiveness` (has `.running`), `hermes_cli.config.load_config() -> dict`
- Produces: `_should_autostart_gateway(*, is_desktop: bool, in_gateway: bool, autostart_enabled: bool, gateway_running: bool) -> bool` and `_maybe_autostart_gateway() -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/hermes_cli/test_gateway_autostart.py`:

```python
"""The desktop backend must start the gateway when nothing else will.

The gateway hosts the kanban dispatcher and cron. Its only autostart path
(install.ps1's Start-GatewayIfConfigured) fires solely when a MESSAGING token
is present, so a desktop install that uses kanban but no messaging never gets a
dispatcher -- the board looks alive and is structurally inert, permanently and
silently. These tests pin the four guards that decide whether to start one.
"""

from __future__ import annotations

import pytest

from hermes_cli.web_server import _maybe_autostart_gateway, _should_autostart_gateway


class TestShouldAutostartGateway:
    def test_starts_when_desktop_and_nothing_is_running(self):
        assert _should_autostart_gateway(
            is_desktop=True,
            in_gateway=False,
            autostart_enabled=True,
            gateway_running=False,
        )

    def test_no_start_when_a_gateway_is_already_running(self):
        """Idempotent across backend restarts; the gateway also refuses to
        double-run, but we must not spawn a process just to have it exit."""
        assert not _should_autostart_gateway(
            is_desktop=True,
            in_gateway=False,
            autostart_enabled=True,
            gateway_running=True,
        )

    def test_no_start_when_we_are_the_gateway(self):
        """The backend runs INSIDE the gateway in some deployments. Without
        this guard it would spawn itself (the same hazard _spawn_hermes_action
        already scrubs _HERMES_GATEWAY for)."""
        assert not _should_autostart_gateway(
            is_desktop=True,
            in_gateway=True,
            autostart_enabled=True,
            gateway_running=False,
        )

    def test_no_start_when_disabled_by_config(self):
        assert not _should_autostart_gateway(
            is_desktop=True,
            in_gateway=False,
            autostart_enabled=False,
            gateway_running=False,
        )

    def test_no_start_for_a_server_dashboard(self):
        """`hermes dashboard` on a server relies on its own gateway; this
        change must not alter that deployment."""
        assert not _should_autostart_gateway(
            is_desktop=False,
            in_gateway=False,
            autostart_enabled=True,
            gateway_running=False,
        )


class TestMaybeAutostartGateway:
    def _patch(self, monkeypatch, *, running: bool, cfg: dict, desktop: str = "1"):
        monkeypatch.setenv("HERMES_DESKTOP", desktop)
        monkeypatch.delenv("_HERMES_GATEWAY", raising=False)
        monkeypatch.setattr(
            "hermes_cli.config.load_config", lambda *a, **k: cfg
        )
        monkeypatch.setattr(
            "gateway.status.resolve_gateway_liveness",
            lambda *a, **k: type("L", (), {"running": running, "pid": None})(),
        )

    def test_spawns_when_no_gateway_is_running(self, monkeypatch):
        spawned = []
        self._patch(monkeypatch, running=False, cfg={})
        monkeypatch.setattr(
            "hermes_cli.web_server._spawn_gateway_restart",
            lambda *a, **k: spawned.append(True) or (None, False),
        )
        assert _maybe_autostart_gateway() is True
        assert spawned == [True]

    def test_does_not_spawn_when_config_disables_it(self, monkeypatch):
        spawned = []
        self._patch(
            monkeypatch,
            running=False,
            cfg={"gateway": {"autostart_with_desktop": False}},
        )
        monkeypatch.setattr(
            "hermes_cli.web_server._spawn_gateway_restart",
            lambda *a, **k: spawned.append(True) or (None, False),
        )
        assert _maybe_autostart_gateway() is False
        assert spawned == []

    def test_is_fail_safe(self, monkeypatch):
        """A broken probe must never stop the backend from starting."""
        monkeypatch.setenv("HERMES_DESKTOP", "1")
        monkeypatch.delenv("_HERMES_GATEWAY", raising=False)

        def boom(*a, **k):
            raise RuntimeError("probe exploded")

        monkeypatch.setattr("gateway.status.resolve_gateway_liveness", boom)
        assert _maybe_autostart_gateway() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/hermes_cli/test_gateway_autostart.py -q`
Expected: FAIL — `ImportError: cannot import name '_maybe_autostart_gateway'`

- [ ] **Step 3: Add the config key**

In `hermes_cli/config_defaults.py`, inside the existing `"gateway": {` block, after `"delivery_ledger": True,`:

```python
        # Start the messaging gateway alongside a desktop-spawned backend.
        # The gateway hosts the kanban dispatcher and cron, both enabled by
        # default, so a desktop install with no messaging configured would
        # otherwise never get a dispatcher: the installer's
        # Start-GatewayIfConfigured fires only when a messaging token exists,
        # leaving the board permanently and silently inert. Set false to keep
        # gateway lifecycle fully manual.
        "autostart_with_desktop": True,
```

- [ ] **Step 4: Write the helpers**

In `hermes_cli/web_server.py`, immediately after `_spawn_gateway_restart` (~line 4127):

```python
def _should_autostart_gateway(
    *,
    is_desktop: bool,
    in_gateway: bool,
    autostart_enabled: bool,
    gateway_running: bool,
) -> bool:
    """Decide whether this backend should spawn a gateway.

    Pure, so the four guards are testable without a process, a config file or
    a live probe. Every guard is load-bearing:

    * ``is_desktop`` -- a server ``hermes dashboard`` relies on its own
      gateway; this behaviour is desktop-only.
    * ``in_gateway`` -- the backend runs INSIDE the gateway in some
      deployments and would otherwise spawn itself (#52470).
    * ``autostart_enabled`` -- ``gateway.autostart_with_desktop``.
    * ``gateway_running`` -- idempotent across backend restarts.
    """
    if not is_desktop:
        return False
    if in_gateway:
        return False
    if not autostart_enabled:
        return False
    return not gateway_running


def _maybe_autostart_gateway() -> bool:
    """Start the gateway for a desktop backend when nothing else has.

    Fail-safe by construction: the desktop must start even if the probe, the
    config read or the spawn fails. Returns True when a spawn was issued (a
    test seam; the lifespan ignores the result).
    """
    try:
        from gateway.status import resolve_gateway_liveness
        from hermes_cli.config import load_config

        cfg = load_config()
        gateway_cfg = cfg.get("gateway") if isinstance(cfg.get("gateway"), dict) else {}
        liveness = resolve_gateway_liveness(use_cache=False)
        if not _should_autostart_gateway(
            is_desktop=os.getenv("HERMES_DESKTOP") == "1",
            in_gateway=os.getenv("_HERMES_GATEWAY") is not None,
            autostart_enabled=bool(gateway_cfg.get("autostart_with_desktop", True)),
            gateway_running=bool(getattr(liveness, "running", False)),
        ):
            return False
        _spawn_gateway_restart()
        _log.info(
            "gateway autostart: no gateway running; started one so kanban "
            "dispatch and cron are available to the desktop"
        )
        return True
    except Exception:
        _log.exception("gateway autostart failed; desktop backend continues")
        return False
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/hermes_cli/test_gateway_autostart.py -q`
Expected: PASS (8 tests)

- [ ] **Step 6: Wire it into the lifespan**

In `hermes_cli/web_server.py::_lifespan`, immediately after the desktop-cron block that ends with `cron_thread.start()` (~line 410):

```python
    # The gateway hosts the kanban dispatcher and cron. Its only other
    # autostart path fires solely when a MESSAGING platform is configured, so
    # a desktop install that uses kanban and no messaging would never get a
    # dispatcher and the board would sit inert with no signal. Runs on a
    # thread (the liveness probe does file I/O and may issue an HTTP health
    # check) so backend startup is never blocked; mirrors the cron ticker.
    if os.getenv("HERMES_DESKTOP") == "1":
        threading.Thread(
            target=_maybe_autostart_gateway,
            daemon=True,
            name="gateway-autostart",
        ).start()
```

- [ ] **Step 7: Verify nothing else broke**

Run: `./venv/bin/python -m pytest tests/hermes_cli/test_gateway_autostart.py tests/hermes_cli/test_web_server.py tests/cron/test_scheduler_provider.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add hermes_cli/web_server.py hermes_cli/config_defaults.py tests/hermes_cli/test_gateway_autostart.py
git commit -m "fix(desktop): start the gateway so kanban dispatch actually runs"
```

---

### Task 2: Warn on triage/todo, not only ready

**Files:**
- Modify: `plugins/kanban/dashboard/plugin_api.py` (~line 765, the create endpoint's dispatcher-presence warning)
- Test: `tests/hermes_cli/test_kanban_dispatcher_warning.py` (create)

**Interfaces:**
- Produces: `_task_needs_dispatcher(status: str, assignee: str | None) -> bool` in `plugins/kanban/dashboard/plugin_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/hermes_cli/test_kanban_dispatcher_warning.py`:

```python
"""Which columns depend on a running dispatcher.

The create endpoint warned only for ready+assigned tasks, on the reasoning
that "triage/todo are expected to wait". Auto-decompose (kanban.auto_decompose,
default True) made that false: the dispatcher tick is what sweeps Triage, so a
triage card with no dispatcher is stalled, not queued -- and this warning was
the only thing that could have said so.
"""

from __future__ import annotations

import pytest

from plugins.kanban.dashboard.plugin_api import _task_needs_dispatcher


@pytest.mark.parametrize("status", ["triage", "todo"])
def test_triage_and_todo_need_a_dispatcher(status):
    assert _task_needs_dispatcher(status, None)


def test_ready_with_an_assignee_needs_a_dispatcher():
    assert _task_needs_dispatcher("ready", "default")


def test_ready_without_an_assignee_does_not():
    """Unassigned ready tasks are skipped by the dispatcher regardless, so a
    dispatcher warning would misdirect: the missing piece is the assignee."""
    assert not _task_needs_dispatcher("ready", None)


@pytest.mark.parametrize("status", ["running", "blocked", "review", "done"])
def test_terminal_and_in_flight_states_do_not(status):
    assert not _task_needs_dispatcher(status, "default")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/hermes_cli/test_kanban_dispatcher_warning.py -q`
Expected: FAIL — `ImportError: cannot import name '_task_needs_dispatcher'`

- [ ] **Step 3: Add the predicate**

In `plugins/kanban/dashboard/plugin_api.py`, above the create endpoint:

```python
def _task_needs_dispatcher(status: str, assignee: Optional[str]) -> bool:
    """True when this task cannot progress without a running dispatcher.

    ``triage``/``todo`` are included because auto-decompose
    (``kanban.auto_decompose``, default True) runs on the dispatcher tick --
    so those columns depend on it exactly as ``ready`` does. They were
    excluded on the assumption that they "are expected to wait", which
    predates auto-decompose and made a stalled card indistinguishable from a
    queued one. An unassigned ``ready`` task is excluded on purpose: the
    dispatcher skips it whatever its own state, so the missing piece is the
    assignee, not the dispatcher.
    """
    if status in ("triage", "todo"):
        return True
    return status == "ready" and bool(assignee)
```

- [ ] **Step 4: Use it at the warning site**

In the same file, replace the existing condition (~line 769):

```python
        if task and task.status == "ready" and task.assignee:
```

with:

```python
        if task and _task_needs_dispatcher(task.status, task.assignee):
```

Leave the body of the block (the `_check_dispatcher_presence` probe and the `body["warning"]` assignment) exactly as it is.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/hermes_cli/test_kanban_dispatcher_warning.py tests/hermes_cli/test_kanban_core_functionality.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add plugins/kanban/dashboard/plugin_api.py tests/hermes_cli/test_kanban_dispatcher_warning.py
git commit -m "fix(kanban): warn about a missing dispatcher on triage and todo too"
```

---

### Task 3: The dispatcher banner component

**Files:**
- Create: `apps/desktop/src/app/kanban/dispatcher-banner.tsx`
- Create: `apps/desktop/src/app/kanban/dispatcher-banner.test.tsx`
- Modify: `apps/desktop/src/i18n/en.ts` (the `operations` block — `kanban` is at ~line 2111, `kanbanUnavailable` at ~line 2187)

**Interfaces:**
- Consumes: `getStatus()` from `@/hermes` returning `StatusResponse` with `gateway_running: boolean`; `useI18n()` from `@/i18n`
- Produces: `export function DispatcherBanner(): JSX.Element | null`

- [ ] **Step 1: Add the copy**

In `apps/desktop/src/i18n/en.ts`, in the `operations` block next to `kanbanUnavailable`:

```ts
    dispatcherOffline: 'No dispatcher is running, so these cards will not be picked up. Start the local gateway in Settings to work them.',
```

- [ ] **Step 2: Write the failing test**

Create `apps/desktop/src/app/kanban/dispatcher-banner.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { DispatcherBanner } from './dispatcher-banner'

const getStatus = vi.fn()

vi.mock('@/hermes', () => ({
  getApiRequestProfile: () => 'default',
  getStatus: () => getStatus()
}))

function renderBanner() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    <QueryClientProvider client={client}>
      <DispatcherBanner />
    </QueryClientProvider>
  )
}

describe('DispatcherBanner', () => {
  it('warns when no dispatcher is running', async () => {
    getStatus.mockResolvedValue({ gateway_running: false })
    renderBanner()
    await waitFor(() => expect(screen.getByRole('status')).toBeTruthy())
  })

  it('stays silent when the dispatcher is running', async () => {
    getStatus.mockResolvedValue({ gateway_running: true })
    renderBanner()
    await waitFor(() => expect(getStatus).toHaveBeenCalled())
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('stays silent while status is still unknown', async () => {
    // A banner that flashes on every mount before the first response would
    // train users to ignore it.
    getStatus.mockReturnValue(new Promise(() => {}))
    renderBanner()
    expect(screen.queryByRole('status')).toBeNull()
  })
})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npm run test:ui --prefix apps/desktop -- src/app/kanban/dispatcher-banner.test.tsx`
Expected: FAIL — cannot resolve `./dispatcher-banner`

- [ ] **Step 4: Write the component**

Create `apps/desktop/src/app/kanban/dispatcher-banner.tsx`:

```tsx
import { useQuery } from '@tanstack/react-query'

import { getApiRequestProfile, getStatus } from '@/hermes'
import { useI18n } from '@/i18n'

/**
 * Say when the board is inert.
 *
 * The dispatcher that sweeps Triage, promotes todo -> ready and spawns workers
 * lives only inside the gateway process. With no gateway running, every card
 * sits exactly where it was created and the board looks identical to a healthy
 * idle one -- the failure this banner exists to make visible.
 *
 * Renders only on an explicit `false`: while the first request is in flight
 * `gateway_running` is undefined, and flashing a warning on every mount would
 * teach users to ignore it.
 */
export function DispatcherBanner() {
  const { t } = useI18n()
  const profile = getApiRequestProfile() ?? 'default'

  const status = useQuery({
    queryFn: () => getStatus(),
    queryKey: ['dispatcher-presence', profile],
    refetchInterval: () => (document.visibilityState === 'visible' ? 60_000 : false)
  })

  if (status.data?.gateway_running !== false) {
    return null
  }

  return (
    <div
      className="mb-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400"
      role="status"
    >
      {t.operations.dispatcherOffline}
    </div>
  )
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npm run test:ui --prefix apps/desktop -- src/app/kanban/dispatcher-banner.test.tsx`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/src/app/kanban/dispatcher-banner.tsx apps/desktop/src/app/kanban/dispatcher-banner.test.tsx apps/desktop/src/i18n/en.ts
git commit -m "feat(kanban): add a banner for a board with no dispatcher"
```

---

### Task 4: Show the banner on both kanban boards

**Files:**
- Modify: `apps/desktop/src/app/contrib/surfaces.tsx` (`ChatRoutesSurface`, ~lines 192 and 203-209)
- Test: `apps/desktop/src/app/contrib/kanban-banner.test.tsx` (create)

**Interfaces:**
- Consumes: `DispatcherBanner` from `../kanban/dispatcher-banner`; `KANBAN_ROUTE` from `../routes` (already imported in this file)

**Context:** the SDK kanban plugin's UI is a prebuilt bundle (`plugins/kanban/dashboard/dist/index.js`) with no source here, so the banner cannot live inside its board. Both the contributed board and the built-in fallback render through `ChatRoutesSurface`, which is why one wiring point covers both.

- [ ] **Step 1: Write the failing test**

Create `apps/desktop/src/app/contrib/kanban-banner.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../kanban/dispatcher-banner', () => ({
  DispatcherBanner: () => <div data-testid="dispatcher-banner" />
}))

import { KanbanRouteContent } from './surfaces'

describe('kanban route content', () => {
  it('puts the dispatcher banner above the board', () => {
    render(<KanbanRouteContent>{<div data-testid="board" />}</KanbanRouteContent>)
    expect(screen.getByTestId('dispatcher-banner')).toBeTruthy()
    expect(screen.getByTestId('board')).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:ui --prefix apps/desktop -- src/app/contrib/kanban-banner.test.tsx`
Expected: FAIL — `KanbanRouteContent` is not exported

- [ ] **Step 3: Add the wrapper**

In `apps/desktop/src/app/contrib/surfaces.tsx`, add the import next to the existing route imports:

```tsx
import { DispatcherBanner } from '../kanban/dispatcher-banner'
```

and add this exported helper above `ChatRoutesSurface`:

```tsx
/** Kanban board content with the inert-board warning above it.
 *
 *  Exported so one wrapper serves BOTH boards: the contributed SDK plugin
 *  page (a prebuilt bundle we cannot edit) and the built-in fallback. */
export function KanbanRouteContent({ children }: { children: ReactNode }) {
  return (
    <>
      <DispatcherBanner />
      {children}
    </>
  )
}
```

- [ ] **Step 4: Use it on the built-in route**

Replace (~line 192):

```tsx
      {!kanbanContributed && <Route element={page(<KanbanView />)} path="kanban" />}
```

with:

```tsx
      {!kanbanContributed && (
        <Route element={page(<KanbanRouteContent><KanbanView /></KanbanRouteContent>)} path="kanban" />
      )}
```

- [ ] **Step 5: Use it on the contributed route**

Replace the contributed-routes map (~lines 203-209):

```tsx
      {routeContributions.map(route => (
        <Route
          element={page(<ContribBoundary id={route.key}>{route.render()}</ContribBoundary>)}
          key={route.key}
          path={route.path.slice(1)}
        />
      ))}
```

with:

```tsx
      {routeContributions.map(route => {
        const content = <ContribBoundary id={route.key}>{route.render()}</ContribBoundary>

        return (
          <Route
            element={page(
              route.path === KANBAN_ROUTE
                ? <KanbanRouteContent>{content}</KanbanRouteContent>
                : content
            )}
            key={route.key}
            path={route.path.slice(1)}
          />
        )
      })}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `npm run test:ui --prefix apps/desktop -- src/app/contrib/ src/app/routes.test.ts`
Expected: PASS, including the pre-existing `kanban-yield.test.tsx` (the yield contract must be untouched)

- [ ] **Step 7: Commit**

```bash
git add apps/desktop/src/app/contrib/surfaces.tsx apps/desktop/src/app/contrib/kanban-banner.test.tsx
git commit -m "feat(kanban): show the dispatcher banner on both boards"
```

---

### Task 5: Split the fused gateway chip into three states

**Files:**
- Modify: `apps/desktop/src/app/shell/hooks/use-statusbar-items.tsx` (~lines 251-268)
- Modify: `apps/desktop/src/app/shell/gateway-menu-panel.tsx` (already receives `statusSnapshot`)
- Modify: `apps/desktop/src/i18n/en.ts` (next to `gatewayReady`, ~line 2697)
- Test: `apps/desktop/src/app/shell/gateway-states.test.tsx` (create)

**Interfaces:**
- Consumes: `statusSnapshot.gateway_running` (already typed in `apps/desktop/src/types/hermes.ts`), `gatewayState`, `inferenceStatus`
- Produces: `export function gatewayAutomationLabel(gatewayRunning: boolean | undefined, copy: { automationRunning: string; automationStopped: string; automationUnknown: string }): string` in `use-statusbar-items.tsx`

**Context:** the chip fuses the desktop↔backend websocket (`gatewayState`) with the inference gateway (`inferenceStatus.ready`) and has no concept of the messaging gateway. That is what reported "Gateway ready" while the gateway the board needed was down.

- [ ] **Step 1: Add the copy — in three files, or `tsc` fails**

First declare the keys in `apps/desktop/src/i18n/types.ts`, in the same block that declares `gatewayReady`:

```ts
      automation: string
      automationRunning: string
      automationStopped: string
      automationUnknown: string
```

Then define them in `apps/desktop/src/i18n/en.ts`, next to `gatewayReady`:

```ts
      automation: 'Automation',
      automationRunning: 'running',
      automationStopped: 'stopped',
      automationUnknown: 'unknown',
```

Then define them in `apps/desktop/src/i18n/zh.ts`, in the same block as its `gatewayReady`:

```ts
      automation: '自动化',
      automationRunning: '运行中',
      automationStopped: '已停止',
      automationUnknown: '未知',
```

Do NOT add these to `ar.ts`, `ja.ts`, or `zh-hant.ts` — those are partial locales using `defineLocale()` and fall back to English by design.

- [ ] **Step 2: Write the failing test**

Create `apps/desktop/src/app/shell/gateway-states.test.tsx`:

```tsx
import { describe, expect, it } from 'vitest'

import { gatewayAutomationLabel } from './hooks/use-statusbar-items'

const copy = {
  automationRunning: 'running',
  automationStopped: 'stopped',
  automationUnknown: 'unknown'
}

describe('gatewayAutomationLabel', () => {
  it('reports stopped when the messaging gateway is down', () => {
    // The case that misled a real user: the chip said "ready" from the
    // websocket + inference legs while this was false.
    expect(gatewayAutomationLabel(false, copy)).toBe('stopped')
  })

  it('reports running when it is up', () => {
    expect(gatewayAutomationLabel(true, copy)).toBe('running')
  })

  it('reports unknown before the first status response', () => {
    expect(gatewayAutomationLabel(undefined, copy)).toBe('unknown')
  })
})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npm run test:ui --prefix apps/desktop -- src/app/shell/gateway-states.test.tsx`
Expected: FAIL — `gatewayAutomationLabel` is not exported

- [ ] **Step 4: Add the helper**

In `apps/desktop/src/app/shell/hooks/use-statusbar-items.tsx`, above `useStatusbarItems`:

```tsx
/** Label for the messaging gateway — the process that hosts kanban dispatch
 *  and cron. Distinct from the desktop<->backend websocket and from the
 *  inference gateway, both of which the chip already reports. `undefined`
 *  means no status response yet, which must not read as "stopped". */
export function gatewayAutomationLabel(
  gatewayRunning: boolean | undefined,
  copy: { automationRunning: string; automationStopped: string; automationUnknown: string }
): string {
  if (gatewayRunning === undefined) {
    return copy.automationUnknown
  }

  return gatewayRunning ? copy.automationRunning : copy.automationStopped
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npm run test:ui --prefix apps/desktop -- src/app/shell/gateway-states.test.tsx`
Expected: PASS (3 tests)

- [ ] **Step 6: Render the third state in the panel**

In `apps/desktop/src/app/shell/gateway-menu-panel.tsx`, next to the existing gateway/inference rows, add a row using the helper:

```tsx
        <div className="flex items-center justify-between gap-2">
          <span>{t.commandCenter.automation}</span>
          <span className={statusSnapshot?.gateway_running === false ? 'text-amber-600' : undefined}>
            {gatewayAutomationLabel(statusSnapshot?.gateway_running, t.commandCenter)}
          </span>
        </div>
```

Add the import:

```tsx
import { gatewayAutomationLabel } from './hooks/use-statusbar-items'
```

- [ ] **Step 7: Run the shell tests**

Run: `npm run test:ui --prefix apps/desktop -- src/app/shell/`
Expected: PASS, including the pre-existing `statusbar-visibility.test.tsx` pin test

- [ ] **Step 8: Commit**

```bash
git add apps/desktop/src/app/shell/hooks/use-statusbar-items.tsx apps/desktop/src/app/shell/gateway-menu-panel.tsx apps/desktop/src/app/shell/gateway-states.test.tsx apps/desktop/src/i18n/en.ts
git commit -m "fix(desktop): report the automation gateway separately from the other two"
```

---

### Task 6: Ledger entry and full verification

**Files:**
- Create: `docs/upstream-customizations/gateway-autostart.yaml`

**Context:** every file touched in Tasks 1-5 is shared with upstream, and each change fails silently if reverted — the board simply goes back to looking fine while doing nothing. The ledger is what makes a merge catch it.

- [ ] **Step 1: Write the ledger entry**

Create `docs/upstream-customizations/gateway-autostart.yaml`:

```yaml
schema_version: 1
feature: gateway-autostart
# The desktop must start the process that does the work.
#
# A LOOP24 user created a kanban task in the UI. It landed in Triage and never
# moved; nothing in the product said why. The dispatcher that sweeps Triage,
# promotes todo -> ready and spawns workers exists only inside gateway/run.py,
# and the gateway's autostart is gated on MESSAGING tokens
# (Start-GatewayIfConfigured) -- while the gateway also hosts kanban dispatch
# and cron, and says so at boot:
#
#     WARNING gateway.run: No messaging platforms enabled.
#     INFO    gateway.run: Gateway will continue running for cron job execution.
#
# The process knows it is useful without messaging; the code deciding whether
# to start it does not. Configure kanban, skip messaging, and the board is
# permanently and silently inert.
upstream_changes:
- id: desktop-backend-gateway-autostart
  change_class: capability-generic
  owner: downstream-edge-capability
  files:
  - hermes_cli/web_server.py
  - hermes_cli/config_defaults.py
  - tests/hermes_cli/test_gateway_autostart.py
  owned_symbols:
  - _should_autostart_gateway
  - _maybe_autostart_gateway
  tests:
  - tests/hermes_cli/test_gateway_autostart.py
  expected_commit_subject: 'fix(desktop): start the gateway so kanban dispatch actually runs'
  upstream_candidate: true
  merge_guidance: >-
    UNION on merge, never --theirs. Four guards, each load-bearing: desktop-only
    (a server `hermes dashboard` owns its own gateway), not-in-gateway (the
    backend runs INSIDE the gateway in some deployments and would spawn itself
    -- the same hazard _spawn_hermes_action scrubs _HERMES_GATEWAY for),
    config-disabled, and already-running. Dropping any one produces no build
    error and no failing test outside the named file.

    The spawn MUST stay routed through _spawn_gateway_restart: it is already
    detached (windows_detach_flags / start_new_session) and already reuses an
    in-flight restart. A hand-rolled Popen would tie the gateway's lifetime to
    the desktop backend and reintroduce the bug from the other direction.

    Default-on is deliberate and was agreed with the product owner: the target
    users are non-technical and will create a task, see nothing happen, and
    have no path to the cause. Do not "make it opt-in for safety" -- that
    restores the original defect.
  removal_condition: >-
    Remove when upstream starts the gateway for desktop installs on its own --
    i.e. when a desktop backend with kanban enabled and no messaging configured
    gets a dispatcher without this hook. Keep the tests.
  last_verified_upstream: 36cb5ae5530a75def7df3195e49b7a4aa2add482

- id: dispatcher-presence-visibility
  change_class: capability-generic
  owner: downstream-edge-capability
  files:
  - plugins/kanban/dashboard/plugin_api.py
  - apps/desktop/src/app/kanban/dispatcher-banner.tsx
  - apps/desktop/src/app/kanban/dispatcher-banner.test.tsx
  - apps/desktop/src/app/contrib/surfaces.tsx
  - apps/desktop/src/app/contrib/kanban-banner.test.tsx
  - apps/desktop/src/app/shell/hooks/use-statusbar-items.tsx
  - apps/desktop/src/app/shell/gateway-menu-panel.tsx
  - apps/desktop/src/app/shell/gateway-states.test.tsx
  owned_symbols:
  - _task_needs_dispatcher
  - DispatcherBanner
  - KanbanRouteContent
  - gatewayAutomationLabel
  tests:
  - tests/hermes_cli/test_kanban_dispatcher_warning.py
  - apps/desktop/src/app/kanban/dispatcher-banner.test.tsx
  - apps/desktop/src/app/contrib/kanban-banner.test.tsx
  - apps/desktop/src/app/shell/gateway-states.test.tsx
  expected_commit_subject: 'feat(kanban): show the dispatcher banner on both boards'
  upstream_candidate: true
  merge_guidance: >-
    UNION on merge, never --theirs. THE FAILURE MODE IS A SILENT REVERT OF A
    SILENCE FIX: dropping any of this restores a board that looks healthy while
    nothing runs. There is no build error, no type error, and no failing test
    outside the named files.

    KanbanRouteContent must wrap BOTH kanban routes. The SDK kanban plugin's UI
    is a prebuilt bundle (plugins/kanban/dashboard/dist/index.js) with no source
    in this repo, so the banner cannot be moved inside the board; the shell
    wrapper is the only site that covers the contributed page. An upstream
    refactor of the contributed-routes map will drop the KANBAN_ROUTE branch
    with no conflict -- re-verify against kanban-banner.test.tsx by name.

    _task_needs_dispatcher deliberately includes triage/todo and deliberately
    excludes unassigned ready. Upstream's comment "triage/todo are expected to
    wait" predates auto-decompose (kanban.auto_decompose, default True) and is
    no longer true; do not restore it. plugin_api.py is already a Bucket-1
    UNION seam under workflow-orchestration.yaml -- this is additive inside
    that contract.

    The banner renders only on an explicit false, never on undefined. A banner
    that flashes on every mount trains users to ignore it, which costs the
    signal this exists to provide.
  removal_condition: >-
    Remove when upstream's own kanban surfaces disclose dispatcher absence --
    i.e. when a board with no gateway running says so without this code.
  last_verified_upstream: 36cb5ae5530a75def7df3195e49b7a4aa2add482
```

- [ ] **Step 2: Validate the ledger**

Run: `./venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/gateway-autostart.yaml`
Expected: exit 0, no output

- [ ] **Step 3: Run the full affected test surface**

```bash
./venv/bin/python -m pytest tests/hermes_cli/test_gateway_autostart.py tests/hermes_cli/test_kanban_dispatcher_warning.py tests/hermes_cli/test_kanban_core_functionality.py tests/hermes_cli/test_web_server.py -q
npm run test:ui --prefix apps/desktop -- src/app/kanban/ src/app/contrib/ src/app/shell/ src/app/routes.test.ts
cd apps/desktop && npx tsc --noEmit -p tsconfig.json && cd -
```
Expected: PASS, and `tsc` reports no errors.

The `tsc` leg is not redundant: `npm run test:ui` does not typecheck, so a missing i18n type declaration passes every test and still breaks the build.

- [ ] **Step 4: Confirm base is still brand-neutral**

Run: `node scripts/brand/check-neutral.mjs`
Expected: `brand-neutral: OK — 'base' carries no brand stamp.`

- [ ] **Step 5: Commit**

```bash
git add docs/upstream-customizations/gateway-autostart.yaml
git commit -m "docs(ledger): record the gateway autostart and dispatcher visibility surface"
```

---

## Merge and release

After all six tasks pass on `base`:

```bash
BRANDS=$(ls brands/*.json | xargs -n1 basename | sed 's/\.json$//' | grep -vE '^(_|schema$)')
for BR in $BRANDS; do
  git checkout $BR && git merge base
  node scripts/brand/generate.mjs $BR --check   # GATE: 8/8 OK
done
```

Then push `base` and every brand forward-only, and end on `otto` with a clean tree.

Do **not** cut a release as part of this plan. Releases are a separate, explicit decision by the product owner, and the paired-brand rule applies when one is made.
