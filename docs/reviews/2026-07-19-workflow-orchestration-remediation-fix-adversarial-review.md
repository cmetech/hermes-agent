# Portable Workflow Orchestration Remediation-Fix Adversarial Review

**Date:** 2026-07-19
**Branch:** `feat/workflow-production-remediation` @ `aea2d9d95d0e8a43befa3a1b7288c3784f31d1ec` (Draft PR #3 → `main`)
**Reviewed against:** the 19-task findings-remediation plan (`cfcc51427`), the prior
adversarial re-review (2 Critical / 9 High / 13 Medium / 9 Low), and the branch's
remediation-verification report (2026-07-19).

## Verdict

**NOT READY to merge — one focused High remains; everything else is verified.**

Every one of the 33 prior findings (C-01, C-02, H-03..H-11, M-04..M-16,
L-01..L-09) plus the three release gates (M-01/M-02/M-03) was independently
re-verified **closed at HEAD** with behavioral evidence: the fixes are genuine,
the tests exercise real failure paths (real processes, real SQLite, real
middleware, real symlink attacks), the merge gate reproduces exactly
(635 passed / 1 skipped; installed-distribution 1; Desktop 17; tsc pass —
measured, exit 0), the gate script was strengthened rather than relaxed, the
four Windows portability fixes are authentic portability fixes (one was
strengthened), the v2.0.9 migration fixture is a genuine old-schema database
that current code cannot produce, and only docs landed after the final code
commit, so no gate evidence is stale.

However, this fresh pass **falsifies the branch's "no Critical or High merge
blocker" claim**: the newly built Gateway delivery surface (Task 15) has a
topology defect (NF-H1) under which chat-originated notifications are never
delivered in the standard desktop+gateway deployment. It is small and
contained, but it defeats the exact production promise H-01 was reopened to
deliver, so by the remediation plan's own completion condition the branch is
not yet mergeable. Four Mediums require a maintainer fix-or-accept decision;
the remaining findings are follow-up material.

## Method

Eight independent adversarial reviewers, one per remediation area
(T1–T3 security, T4–T5 idempotency/showcase, T6–T8 coordinator, T9–T12
bounds/Windows, T13–T14 services/notifications, T15–T16 new surfaces,
T17–T18 Desktop/CLI, T19 evidence audit), each instructed to refute the fix,
verify at HEAD (not just at the fix commit), and judge whether tests prove the
failure path. Roughly 350 targeted tests were executed across the reviews,
including a full reproduction of the no-argument merge gate. The one High
below was additionally re-verified line-by-line by the coordinating reviewer.

## Prior-finding closure summary

| Area | Findings | Verdict |
|---|---|---|
| T1 authorization (C-01) | 16/16 routes capability-gated; server-derived, narrowing-only scope; fail-closed; remote-non-admin vs local-admin cleanup test present | CLOSED |
| T2 evidence containment (C-02) | POSIX dir-fd/O_NOFOLLOW walk + triple identity check; real symlink-swap tests | CLOSED (Windows fallback untested — NF-L1) |
| T3 interaction identity (M-13) | Mandatory exact interaction id, in-transaction CAS, real multiprocess approve/reject race test | CLOSED |
| T4 idempotency (H-06/H-07/L-04) | Digest is purely semantic (verified by enumeration); spawn-based cross-process retry test; UNIQUE-constraint backstop; divergent-route retry defined and tested | CLOSED |
| T5 showcase (M-14/L-06) | Byte-identical trust-store assertion through a full offline showcase run | CLOSED |
| T6 fencing (H-03/H-04) | All dispatch-chain mutations fenced in-transaction; real mid-dispatch depose test across processes; `interrupt_active_claims` exact-fence match; monotonic+boot-id lease corroboration wired at every freshness decision | CLOSED (aux mutations residual — NF-M5, NF-L5) |
| T7 recovery windows (M-09/M-10/M-11) | Spawn-intent → uncertain → reconcile; PID+start-time identity on reclaim; digest-corroborated index repair, fail-closed | CLOSED |
| T8 foreground adoption (H-05) | Adoption epoch-fenced (H-03 not reintroduced), uncertain-effect reconcile, real-kill multiprocess tests | CLOSED (foreground-side gaps — NF-M2, NF-L6) |
| T9 journal reserve (M-08) | Per-claim reserve in the claim transaction; conditional consume; claim retained on exhaustion | CLOSED |
| T10 sweeps/stalls (M-04/M-05) | Durable keyset cursor inherited across elections; 205-run tail-visited test; approval waits structurally excluded from stall | CLOSED |
| T11 central admission (M-06/M-07) | Single runnable-entry path verified by exhaustive write-site enumeration; durable FIFO sequence; atomic last-slot | CLOSED (new head-of-line defect — NF-M3) |
| T12 Windows termination (M-12) | CREATE_SUSPENDED→assign→resume (no escape race); no breakaway; fail-closed on assignment failure; uncertainty surfaced, never assumed dead | CLOSED |
| T13 service generations (M-16) | Hung generation blocks replacement (availability-loss trade, never overlap); identity-checked callbacks; positive provider hot-reload usability test present | CLOSED |
| T14 notifications (M-15) | Durable always-advancing wrap cursor (wrap-boundary test disproves high-water-mark); prune preserves facts; bounded retry → visible dead-letter; benign-duplicate failure direction on both transports | CLOSED |
| T15 Gateway delivery (H-01/H-11) | Mechanism sound: token digest-stored, mint fail-closed to verified adapters, transport-level dedup crash-safe, gateway core workflow-free | **CLOSED-WITH-NEW-DEFECT (NF-H1, NF-M1, NF-M4)** |
| T16 API admission (H-02/H-10) | Background-only proven adversarially (monkeypatched `advance`); idempotency required; provenance fully server-derived; `channel="desktop"` literals gone; coordinator gate double-checked | CLOSED |
| T17 Desktop (H-08/H-09/L-07) | Actionable inbox traced click→HTTP; HMAC keyset cursor with pinned `observed_at`; 250-row no-gap test; server-derived actions only | CLOSED |
| T18 CLI contracts (L-01..L-05/L-08/L-09) | Envelopes on every command incl. subparser errors; typed taxonomy, sniffing removed; refcounted lock registry (eviction race hunted, closed) | CLOSED |
| T19 gates (M-01/M-02/M-03) | Native matrix counts reconcile exactly (132 = 127+5 = 130+2); gate reproduced locally; no dilution in the portability fixes; ledger commits match claims | CLOSED (gate-membership nuances — NF-M6) |

All five refinements demanded by the plan review have standing tests:
admin-principal scoping (found), cumulative v2.0.9 migration (found — CI matrix,
meta-test-enforced, though not in the local 635 selection), monotonic lease
corroboration (found and wired), provider-reload positive usability (found),
showcase-without-trust-write (found).

## New findings

### NF-H1 (High) — Gateway notification delivery starves when the web host holds the coordinator lease

The coordinator background service registers for both hosts
(`plugins/workflow/__init__.py:43`, `hosts={"web","gateway"}`) with
single-leader election, and delivery of `gateway:*` outbox rows runs only in
the leader's sweep, gated on `self.context.delivery_port is not None`
(`plugins/workflow/coordinator.py:277-284`). Only the gateway host ever binds
a port (`gateway/run.py:2828,7306`); `hermes_cli/web_server.py` starts the
web host with none. Election has no host priority and there is no non-leader
drain or handoff.

**Failure scenario (standard topology):** desktop backend (`hermes serve`) and
the gateway both run; the web host wins the lease (a startup-order coin flip —
and the typical boot order starts the backend first). A Telegram-originated
run completes; the web-leader coordinator executes the run correctly, but its
`delivery_port` is `None`, so the gateway outbox rows sit `pending`
indefinitely. The operator who started the run in chat is never notified —
even with every process healthy — until leadership happens to migrate or the
7-day capability TTL lapses and the row dead-letters. No test covers this
topology (the existing tests exercise each host in isolation).

This is the exact production promise H-01 was reopened to deliver, so it is
merge-blocking under the plan's own completion condition.

**Fix shape (contained):** let a delivery-capable non-leader drain
`gateway:*` outbox rows under the existing per-row lease/owner semantics (the
outbox already supports leased delivery, and drain does not touch run
execution, so single-executor invariants are unaffected); or give
port-bearing hosts election priority. Either stays inside branch-owned files.

### NF-M1 (Medium) — the "opaque" delivery capability leaks through two read APIs

The raw capability is stored in `TriggerProvenance.durable_record`
(`provenance.py:136-146`) and returned by `GET /runs/{run_id}` — the
sanitizer's secret-key regex does not match `return_route` — and
`GET /runs/{id}/evidence?kind=notifications` returns `transition_key` raw,
which embeds `…:gateway:<capability>`, bypassing the `gateway:opaque` masking
applied to `destination` in the same function (`notifications.py:874-892`).
Any `workflow:read` caller can harvest a live 7-day bearer token. Not
exploitable over HTTP today (no endpoint invokes the port), but it breaks the
opacity contract, and any future port consumer converts it into cross-chat
message injection. Fix: mask `return_route` in the sanitizer and hash the
capability inside `transition_key`.

### NF-M2 (Medium) — foreground dispatch is not transactionally fenced

Foreground execution calls `claim_node`/`mark_node_started`/`complete_node`
with `execution_fence=None`; foreground ownership is checked only at loop-top
(`scheduler.py:313-333,759`), and `claim_node`'s transaction does not verify
`execution_mode`/`foreground_owner` (`store.py:4386-4401`). A foreground owner
stalled past its lease (GC pause, laptop suspend) can resume mid-iteration
after background adoption and claim a ready node — including an outward one —
concurrently with the background leader. Two live executors on one run
violates the single-executor invariant the background side enforces
rigorously. Fix: verify foreground ownership in-transaction in `claim_node`
when no coordinator fence is supplied.

### NF-M3 (Medium) — global FIFO head-of-line starvation behind a paused-hold lane

`older_queued` in `_request_runnable_locked` (`store.py:3144-3148`) and
`try_promote_run` (`store.py:3220-3224`) is global, not per-lane. A run paused
on an approval gate with the default `pause_lane_policy='hold'` keeps its lane
held; a queued sibling then sits at the global queue head, and every other
workflow's queued run refuses to promote behind it while new starts queue
behind that. One un-actioned approval freezes all queued admission
profile-wide — the same operational symptom class as the original
stuck-at-10/11 incident, now via fairness rather than lost execution. No test
exercises an ineligible queue head with an eligible younger run from another
lane. Fix: skip globally-blocked-but-lane-ineligible heads during promotion,
or scope the FIFO check per lane.

### NF-M4 (Medium) — transient delivery-port exceptions permanently dead-letter notifications

`_deliver_gateway_notifications` maps any raised exception to
`terminal_fail(outcome_uncertain=True)` (`coordinator.py:224-236`) — including
a SQLite `busy_timeout` raised inside `deliver()` *before* the durable
`sending` receipt row exists, when retry would be provably safe. A 5-second DB
contention spike permanently kills a notification that was never attempted.
Fix: treat pre-`sending` failures as retryable (`fail()`), reserving
`terminal_fail` for genuinely uncertain post-send outcomes.

### NF-M5 (Medium) — key async regression tests are enforced by no green gate

The gateway reload-barrier tests, the provider hot-reload positive assertion,
and `tests/gateway/test_plugin_delivery.py` are absent from both the 635-test
merge gate and the CI workflow-portability matrix; they run only in the
repo-wide "Python tests" job, which is currently red for unrelated baseline
reasons — so regressions in the T13/T15 seams would be masked. (Related
nuance: the cumulative v2.0.9 migration test is CI-matrix-only, though its
matrix membership is now meta-test-enforced.) Fix: add these files to the
merge-gate selection (the local venvs need `pytest-asyncio`, already declared
in dev extras and installed in CI).

### Lower-severity findings (follow-up backlog)

- **NF-L1** — Windows evidence containment (`_read_fallback_contained_file`)
  loses `O_NOFOLLOW` and is detection-based; both adversarial symlink tests
  skip off-POSIX, so it has zero hostile coverage. Add a Windows CI case or a
  mocked-attributes unit test.
- **NF-L2** — attention endpoint sorts ascending and truncates at 100 with
  `next_cursor` always `None`: past 100 items the *newest* attention entries
  silently never appear (`plugin_api.py:860-871`).
- **NF-L3** — no *concurrent* cross-process idempotency race test (the
  100-way race is thread-only); serialization currently rests on
  flock + BEGIN IMMEDIATE, which a regression could remove unnoticed.
- **NF-L4** — legacy rows migrate into the CLI namespace, so a pre-upgrade
  chat/API intent retried post-upgrade through the new gateway/API paths mints
  a new run rather than joining (practical exposure minimal).
- **NF-L5** — auxiliary mutations reachable by a deposed leader remain
  unfenced: `try_promote_run`, `wake_due_retries`,
  `transition_pending_nodes`/`finalize_if_complete`, `expire_stale_claims`'
  interrupt branch, and `interrupt_for_host_pressure` (the latter can
  cross-epoch-interrupt a successor's run under disk pressure — disruption
  only, no duplicate effects, but it survives the H-04 fix).
- **NF-L6** — foreground leases lack monotonic corroboration
  (`store.py:3691-3695,3760-3767`); a backward wall-clock step extends a dead
  foreground owner's lease and re-creates the H-05 stuck-run symptom for the
  step's duration.
- **NF-L7** — concurrent plugin-reload race returns an unhandled 500 and
  skips config rollback (`web_server.py:174-197`); transient divergence,
  self-heals on the winner's discovery pass.
- **NF-L8** — run cleanup guards only *projected* undelivered notifications;
  an admin cleanup with a short `older_than` inside the 300 s repair window
  can quarantine an un-projected crash-gap terminal fact (`store.py:7602-7614`).
- **NF-L9** — `reconcile_journal` has no first-row progress guarantee: a
  journal larger than the passed byte budget wedges the repair cursor forever.
  Unreachable at the production call site today; one custom-budget caller away
  from a repair livelock (`notifications.py:444-446`).
- **NF-L10** — top-level CLI parser errors (`workflow bogus --json`) still
  emit plain argparse usage, exit 2, no JSON envelope (only subparser errors
  are enveloped); and the `OSError` handler passes `str(exc)` (may contain
  absolute paths) into machine output unsanitized.
- **NF-L11** — `plugin_return_routes`/`plugin_delivery_receipts` grow without
  bound (a capability is minted per authenticated command, even parse
  failures; no prune exists); and plugins receive the full port including
  `mint_return_route` rather than a deliver-only facade.
- **NF-L12** — `mint_return_route` rejects secondary-profile adapters
  (`plugin_delivery.py:132-134`), so `/workflow` on a multi-profile gateway
  always fails closed (functional gap, correct failure direction).
- **NF-L13** — `start_run` rejects at executing capacity where the central
  admission path would queue: the caller-visible predicate drift class M-06
  targeted, surviving at the ingress boundary.
- **Info** — Windows boot-id derivation (`int(psutil.boot_time())`) can churn
  under clock steps → spurious re-election (liveness only, safety preserved);
  delivery destination is a constant `"desktop"` so destination scoping is
  currently a no-op guard; Desktop receipt cache evicts past 256 un-acked
  receipts → duplicate toast (benign direction); the in-process gateway
  reload controller has no production caller; four shared media-tool files
  gained a `has_bound_background_service_host` retry gate (deliberate; noted
  for upstream-merge union care).

## Evidence-audit outcome (claims vs. reality)

The verification report's claims reproduce: gate output matches exactly, the
gate and its meta-tests were strengthened, the native-matrix arithmetic
reconciles per-platform, the migration fixture is authentic, ledger commits
match their rows, and the post-gate delta is docs-only. The single claim this
review overturns is the fresh-review verdict "no Critical or High merge
blocker" (NF-H1). The recorded unrelated baselines (Desktop lint, Windows
footguns in `ericsson-teams`, red repo-wide CI vs public `main`) were
confirmed as recorded, with the sharpening in NF-M5 that the red full-suite
job is currently the *only* runner of several branch-critical async tests.

## Required actions before merge

1. **Fix NF-H1** (delivery ownership topology) with a test that runs the
   web-leader + gateway-follower topology and asserts a gateway outbox row is
   delivered. Merge-blocking.
2. **Decide NF-M1..NF-M5**: fix (each is small and contained in branch-owned
   files) or explicitly accept with a recorded rationale. NF-M1 and NF-M4 sit
   on the same new surface as NF-H1 and are natural companions to that fix;
   NF-M2/NF-M3 are one-transaction-check fixes in `store.py`.
3. **Backlog NF-L1..NF-L13** as post-merge follow-ups; none is
   merge-blocking alone.

Scope confirmation: this review covers `feat/workflow-production-remediation`
only. The four contained fix branches (`fix/windows-index-materialization`,
`fix/fresh-capability-bootstrap`, `fix/windows-authenticated-resource-upgrade`,
`fix/workflow-production-review`) were verified as ancestors of HEAD;
`feat/workflow-operator-experience` (2 divergent commits, one violating the
background-only contract) is correctly excluded and should not be merged.
