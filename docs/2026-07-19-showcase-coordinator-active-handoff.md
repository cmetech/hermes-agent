# Task: fix `workflow showcase run` — unrunnable whenever the product is running (v3.0.0 regression)

## Repository and branch policy
Repo: cmetech/hermes-agent. This defect is brand-agnostic runtime code, so the fix
lands on `base` FIRST, then merges to every brand discovered from `brands/*.json`
(currently `otto`, `loop24`), each re-stamped and gated with
`node scripts/brand/generate.mjs <brand> --check` (expect 8/8 OK).
Never author this fix directly on a brand branch.

Reference: `release/base-v3.0.0` (tag commit `b4f758d51`). Target release: v3.0.1.

## Symptom
On a normal installed product (LOOP24 desktop running), EVERY attempt to start the
bundled showcase is rejected at admission:

    $ loop24 workflow showcase run laptop-diagnostic \
        --symptom "A fictional Windows laptop starts slowly after an update." \
        --idempotency-key loop24-laptop-diag-001 --trigger-source cli --json
    {
      "ok": false,
      "error": {"code": "coordinator_active", "message": "coordinator_active", "retryable": false},
      "result": {"reason_code": "coordinator_active", "run_id": null, "status": "rejected"},
      "next_actions": []
    }

`run_id` is null, so no run row, no events, nothing in the desktop Workflows view.
Reproduced from the chat agent AND from a plain terminal; identical result.

Confirmed live coordinator lease at the time (read from
`%LOCALAPPDATA%\loop24\workflows\admission.sqlite3`, table `coordinator_lease`):
  host_kind='web', pid=14052 (the desktop backend), epoch=1, lease_seconds=30.0,
  heartbeat_at 2026-07-19T22:32:29Z, lease_expires_at 22:32:59Z
i.e. a healthy, actively heartbeating leader — the normal steady state.

## Root cause
1. `plugins/workflow/admission.py:23`
   `RunAdmissionRequest.execution_mode: Literal["foreground","background"] = "foreground"`
2. `plugins/workflow/showcase.py` `run_showcase()` (~L407-444) constructs
   `RunAdmissionRequest(...)` and NEVER passes `execution_mode`, so every showcase
   run requests foreground.
3. `plugins/workflow/store.py:2684` fences foreground against a live leader:

       if request.execution_mode == "foreground" and fresh_leader is not None:
           return RunAdmissionResult(None, "rejected", "coordinator_active")

4. The coordinator is a background service hosted by `web` and `gateway`
   (`plugins/workflow/__init__.py:39-43`, `hosts={"web","gateway"}`), started by
   `hermes_cli/web_server.py:303,338`. The desktop backend therefore ALWAYS holds a
   fresh lease while the app is open.

=> The showcase is structurally unrunnable whenever the product is running.

For contrast, the general path already does this correctly:
- `plugins/workflow/cli.py:1469-1470` — `workflow run` defaults to background and
  only demands a healthy coordinator for background.
- `plugins/workflow/gateway_command.py:132` — the `/workflow` chat command passes
  `execution_mode="background"` explicitly.
Only the showcase path was left behind.

## Why no test caught it
Every showcase test calls `run_showcase(...)` with NO coordinator lease held, so the
foreground default always succeeds:
- `tests/plugins/workflow/test_showcase_offline_e2e.py`, `test_showcase_catalog.py`,
  `test_showcase_*_e2e.py` — zero `CoordinatorStore` / `try_acquire` usage.
- `test_operator_e2e.py` and `test_desktop_api.py` DO use both, but the lease
  acquisition (`test_operator_e2e.py:134`, `test_desktop_api.py:112`) is in different
  tests than the `run_showcase` calls (`:33`, `:705`).
There is no test for "start a showcase while a healthy coordinator holds the lease" —
which is the only configuration real users have.

## Second-order defect the fix MUST address
`plugins/workflow/showcase.py::_advance_until_wait` calls `scheduler.advance(run_id)`
with NO execution fence, unlike `cli.py:1528-1540` (which passes owner_id/epoch) and
`cli.py:_continue_foreground_if_owned` (which claims a foreground lease and no-ops when
the coordinator is healthy). It is called by `run_showcase` (when not `--no-wait`),
`approve_showcase`, and `reject_showcase`. Simply flipping admission to background
would let these CLI calls execute a coordinator-owned run concurrently with the
coordinator. Fix admission and in-process advancement together.

## Required behavior
1. `run_showcase()` selects the execution mode the same way `cli.py:1469-1470` does:
   background when a coordinator lease is fresh, foreground otherwise. Do NOT weaken
   or bypass the fence in `store.py:2684`.
2. Background showcase runs must NOT be advanced in-process. Return the admitted
   status and let the coordinator execute; the operator polls
   `workflow showcase status <run_id>`. Foreground runs keep today's
   advance-until-gate behavior.
3. `approve_showcase` / `reject_showcase` must record the decision durably in both
   modes, and only advance in-process when the run is foreground-owned and no healthy
   coordinator exists — mirroring `_continue_foreground_if_owned` (`cli.py:1743-1749`).
4. `--no-wait` semantics: today, with no coordinator, `--no-wait` admits a run that
   nothing will ever advance (it sits at `pending`). Either reject that combination
   with a clear message or document it. State the choice made.
5. Error envelope parity: `plugins/workflow/cli.py:1999-2005` raises
   `WorkflowCommandError(..., exit_code=EXIT_ACTION_FAILED=8)` with
   `next_actions: []` and `message == reason_code`. The general path maps
   `coordinator_active` to `WorkflowConflict` (exit 5, `cli.py:1517`). Align the
   showcase path: conflict exit code, human-readable message, populated `next_actions`.

## Required tests (TDD — write failing tests first)
a. Hold a coordinator lease (`CoordinatorStore.try_acquire`, pattern at
   `test_operator_e2e.py:134-150`), then call
   `run_showcase("laptop-diagnostic", hermes_home=..., symptom=...)` and assert a real
   `run_id` is returned and `execution_mode == "background"`. This test MUST fail
   before the fix with `reason_code == "coordinator_active"`.
b. No coordinator: existing foreground behavior still reaches the `paused` approval
   gate (regression guard for `test_operator_e2e.py:31-56`).
c. Background run + `approve_showcase`: the CLI must not execute the run tail
   in-process while a coordinator is leader (see the `forbidden_advance` monkeypatch
   idiom at `test_operator_e2e.py:150-155`).
d. CLI-level: showcase rejection maps to the conflict exit code with non-empty
   `next_actions`.
Do NOT weaken `tests/plugins/workflow/test_coordinator.py:1337` or `:1381` — the
fence is intended behavior.

## Operator-contract / docs updates
- `skills/.../workflow-showcase/workflows/run-showcase.md` step 7 handles only
  `coordinator_unavailable`. Add `coordinator_active` so an agent stops and reports
  instead of probing (in the observed session the agent ran help probes and an
  ad-hoc python one-liner that the user had to deny).
- Keep the Claude Code dev copy under `.claude/skills/` byte-identical if one exists.

## Non-goals
- Do not add showcase endpoints to `plugins/workflow/dashboard/plugin_api.py`.
- Do not make showcase packages discoverable by `discover_workflows`.
- Do not change the coordinator's hosts or lease semantics.
- No branding/emitter files are involved; `generate <brand> --check` must stay 8/8.

## Class-level guards (REQUIRED — these are what stop a recurrence)
Tests (a)-(d) fix one bug. The defect class is *"production runtime configuration is
never exercised by the test suite, and an unsafe default silently selects it."*
Implement all five guards below.

### G1 (design fix, highest value): make the unsafe default impossible
`RunAdmissionRequest.execution_mode` defaults to `"foreground"` — the one value that
is illegal in the only configuration users run. There are exactly FOUR production
admission sites, and three already state the mode explicitly:
    plugins/workflow/api_admission.py:124   execution_mode="background"   OK
    plugins/workflow/cli.py:1490            execution_mode=execution_mode OK
    plugins/workflow/gateway_command.py:121 execution_mode="background"   OK
    plugins/workflow/showcase.py:430        (omitted -> foreground)       BUG
Remove the default and make `execution_mode` a required field. This is a one-line
signature change plus one call site, and it makes the entire class unrepresentable —
a future author cannot forget it, because construction fails.
If the field must keep a default for compatibility reasons, state why, and G2 becomes
mandatory rather than belt-and-braces.

### G2: call-site invariant (AST test, no runtime cost)
    # tests/plugins/workflow/test_admission_call_sites.py
    import ast
    from pathlib import Path

    PRODUCTION = Path(__file__).parents[3] / "plugins" / "workflow"

    def test_every_admission_site_states_execution_mode() -> None:
        offenders = []
        for path in PRODUCTION.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name != "RunAdmissionRequest":
                    continue
                if not any(kw.arg == "execution_mode" for kw in node.keywords):
                    offenders.append(f"{path.relative_to(PRODUCTION)}:{node.lineno}")
        assert not offenders, (
            "every admission site must state execution_mode explicitly; an implicit "
            "foreground request is fenced whenever a coordinator is live "
            f"(store.py:2684). Offenders: {offenders}"
        )

### G3: exercise BOTH deployment configurations (the core guard)
Add a parametrized fixture to `tests/plugins/workflow/conftest.py` and apply it to
every operator-facing entry-point test. `coordinator_leader` is the ONLY configuration
real users have; today zero showcase tests run in it.

    # tests/plugins/workflow/conftest.py  (additive)
    import os
    from datetime import datetime, timezone

    import pytest

    from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
    from plugins.workflow.store import RunStore

    @pytest.fixture(params=["no_coordinator", "coordinator_leader"])
    def deployment_configuration(request, tmp_path):
        """Run operator entry points in both deployment shapes.

        'coordinator_leader' models the shipped product: the desktop backend
        (host_kind='web') holds a fresh lease for as long as the app is open.
        """
        if request.param == "coordinator_leader":
            store = RunStore(tmp_path)
            acquired = CoordinatorStore(store.database).try_acquire(
                CoordinatorIdentity(
                    owner_id="test-web-leader",
                    host_kind="web",
                    host_instance_id="test-web",
                    pid=os.getpid(),
                    process_start_time=None,
                ),
                now=datetime.now(timezone.utc),
                lease_seconds=600,
            )
            assert acquired.is_leader
        return request.param

Note: the fixture takes no heartbeat thread, so `lease_seconds` must exceed the test's
wall time. `lease_is_fresh` compares monotonic elapsed against `lease_seconds`
(`lease_clock.py`), so 600s is a safe margin; do not lower it to 30.

The entry-point test that would have caught this bug on day one:

    def test_showcase_starts_in_every_deployment_configuration(
        tmp_path, deployment_configuration
    ) -> None:
        started = run_showcase(
            "laptop-diagnostic", hermes_home=tmp_path, symptom="fictional smoke test"
        )
        assert started["run_id"], (
            "showcase admission rejected in "
            f"{deployment_configuration}: {started.get('reason_code')}"
        )

Apply the same fixture to at least: `test_operator_e2e.py`,
`test_showcase_offline_e2e.py`, and the desktop API admission tests.

### G4: no unfenced execution of a coordinator-owned run
`RunScheduler.__init__` accepts `execution_fence` / `execution_owner_id` and both
default to `None` (`scheduler.py:198-263`), so an unfenced `advance()` is silently
legal. `showcase.py::_advance_until_wait` takes exactly that path.
Add a runtime guard in `RunScheduler.advance` — refuse to advance a run whose
`execution_mode` is `"background"` when no fence and no foreground claim is held —
and an AST test asserting every `scheduler.advance(` call site in `plugins/workflow`
either passes a fence/owner or is provably foreground-only. Audit existing callers
before enabling the guard; this may surface other latent races.

### G5: every rejection reason code has an operator route
`store.start_run` can reject with TEN distinct reason codes:
    admission_closed, storage_repair_required, idempotency_conflict,
    coordinator_active, start_rate_capacity, nonterminal_capacity,
    profile_storage_quota, overlap_forbidden, queued_capacity, executing_capacity
The showcase CLI collapses ALL of them to `EXIT_ACTION_FAILED` with
`message == reason_code` and `next_actions: []` (`cli.py:1999-2005`), which is why the
agent in the observed session had no legal next move and resorted to ad-hoc probing.
Add a coverage test that, for every reason code literal parsed out of `store.py`:
  1. a documented exit-code mapping exists in `operator_command_contract()`;
  2. a human-readable message and non-empty `next_actions` are produced;
  3. the code appears in the operator skill contract (`run-showcase.md`).
Failing this test is the signal that a new failure mode shipped without an operator
route — the meta-defect behind this whole incident.

## Verification
- Reproduce the failure before the fix using test (a) and the `coordinator_leader`
  parametrization of G3 — both must fail before, pass after.
- After the fix, with the LOOP24 desktop running, `loop24 workflow showcase run
  laptop-diagnostic --symptom "..." --idempotency-key <k> --json` returns a real
  `run_id`, the run appears in the desktop Workflows view, and it reaches the manual
  approval gate without the CLI approving anything.
- Report actual command output; do not claim a pass without it.

---

## Appendix — context for whoever picks this up

**Why no UI surface can start a showcase today.**
- `plugins/workflow/dashboard/plugin_api.py` has zero showcase endpoints, so the
  desktop Workflows page cannot create a showcase run.
- The `/workflow` chat command (`gateway_command.py:75-90`) exposes only
  `run | approve | reject`, and `run` resolves a *discovered catalog* name via
  `discover_workflows`, which scans only `<workdir>/.hermes/workflows` and
  `$HERMES_HOME/workflows` (`discovery.py:77-78`). Showcase packages live in
  `plugins/workflow/showcases/packages/...` and are never discovered.
- Therefore the only entry point is the `workflow showcase run` CLI subcommand,
  which is foreground-only — and invoking it from the product guarantees a
  coordinator exists. The showcase is self-defeating in its shipped configuration.

**Which CLI commands are actually affected.** Only foreground admission is fenced:
- Broken while the desktop runs: `workflow showcase run` (and `workflow run
  --foreground`, where rejection is correct).
- Requires the desktop running: `workflow run <name>` (background default,
  `cli.py:1469-1470`).
- Unaffected: `list`, `show`, `validate`, `doctor`, `trust`, `untrust`, `runs`,
  `status`, `events`, `showcase list|describe|preflight|status|report`, and the gate
  and lifecycle commands (`approve`, `reject`, `provide-input`, `retry`, `reconcile`,
  `resume`, `cancel`, `abandon`, `archive`, `restore`, `cleanup`).

**Operator workaround until v3.0.1 ships** (smoke test only — the resulting run is
foreground and unfenced, so it is not evidence the desktop path works):

    loop24 workflow --hermes-home "$env:LOCALAPPDATA\loop24-showcase" showcase run \
      laptop-diagnostic --symptom "..." --idempotency-key <k> --trigger-source cli --json

`--hermes-home` is a hidden flag on the `workflow` command (`cli.py:361-363`) that
must precede the subcommand. Do not pass `--no-wait` there: with no coordinator in
that home, nothing would advance the run.
