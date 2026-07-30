# Per-navigation browser profile selection

Date: 2026-07-26
Status: approved, not yet implemented
Supersedes: the session-wide profile routing shipped in `3a458156c`
Affected: `tools/browser_tool.py`, `tools/browser_session_manager.py`,
`tools/browser_profiles.py`, `tools/browser_session_registry.py`,
`docs/upstream-customizations/browser-profiles.yaml`

## Why this exists

`3a458156c` wired the agent's browser tools to launch the user's real installed
browser for `enrolled` profiles. An adversarial review
(`docs/reviews/2026-07-26-enrolled-browser-launch-adversarial-review.md`)
returned DO NOT SHIP with two CRITICAL findings, both reproduced independently:

- **EBL-001** — `acquire()` writes the process-global `os.environ["BROWSER_CDP_URL"]`
  via `_attach_cdp()`. An explicitly ephemeral task then resolves
  `_session_uses_enrolled_browser → False`, falls through to
  `_get_cdp_override()`, reads that global, and drives the corporate browser
  anyway. The value survives cleanup.
- **EBL-002** — with `browser.default_profile: enrolled`, every unbound session
  drove the corporate browser, so an attacker-controlled public page was loaded
  by a browser holding live SSO cookies and machine client certificates. The
  2026-07-20 design §5 forbids exactly this; the 2026-07-26 design asked for one
  browser covering internal and external without repealing it.

Both are authority-boundary failures. This design resolves them by deciding the
browser **per navigation** rather than per session.

## Scope

**In scope: browser-identity isolation.** Untrusted content never touches the
enrolled browser, its cookies, or its certificates.

**Explicitly out of scope: model-mediated exfiltration.** Per-navigation
selection does not stop a prompt-injected model from reading an internal page in
the enrolled browser and then navigating the ephemeral browser to a public
collector — the data travels through the model's context, not the browser. This
is a general agentic-browsing risk, is not made worse by this design, and is
recorded as a known residual risk rather than addressed here.

Also in scope, because the review found them and they block the use case or the
merge: EBL-003 through EBL-009.

## The routing rule

A single task may span two browsers, chosen per navigation. This is not a new
concept in this codebase: `_navigation_session_key(task_id, url)` already splits
one task across two backends by URL, returning `f"{task_id}::local"` so a cloud
session serves public URLs while a local Chromium sidecar serves private ones.
`_last_active_session_key` already makes `click`/`type`/`snapshot` follow the
browser the last navigation used, with a fail-closed ownership check for stale
bindings, and cleanup already reaps suffixed keys. This design reuses that
machinery rather than inventing a parallel one.

`_navigation_session_key` gains an enrolled branch:

```
if _get_cdp_override():        return task_id      # explicit /browser connect wins
if _is_camofox_mode():         return task_id      # camofox owns its session
if _session_trusts_url(task_id, url):
                               return f"{task_id}::enrolled"
...existing cloud / auto-local / hybrid logic unchanged...
```

`_session_trusts_url` is called with the **bare** `task_id`, which resolves
through `browser.default_profile` exactly as it does today. Routing performs no
filesystem probe: a per-navigation `os.path.exists` sweep would be wasteful and
would race the launch anyway. An unresolvable executable surfaces at acquire time
as a `ProfileError`, which is the existing, explicit failure path — not a silent
downgrade to the ephemeral browser.

Ordering is load-bearing:

- **After** the CDP-override and Camofox checks, so an explicit `/browser
  connect` and the Camofox backend keep owning their sessions exactly as today.
- **Before** the cloud/hybrid split, so an explicitly trusted origin reaches the
  corporate browser rather than a local sidecar. A trusted internal origin under
  a configured cloud provider matches both rules; enrolled wins, because
  reaching that origin with corporate credentials is the entire point.

The trust predicate is the existing `_session_trusts_url`. Routing and SSRF trust
therefore cannot disagree: an origin is either listed in the enrolled profile's
`trusted_origins` or it is not, and one predicate answers both questions. A
divergence between which browser *drives* and which profile *grants trust* would
be a serious defect, and sharing the predicate makes it unrepresentable.

`_is_local_sidecar_key` keeps its exact current meaning — "force local Chromium".
An enrolled key is never a local sidecar.

### What `browser.default_profile` now means

It no longer makes a session enrolled. It makes the enrolled browser *available*
for the origins that profile trusts. The bare task key is always ephemeral.

Consequences: turning the toggle off immediately stops new enrolled routing;
existing `::enrolled` sessions persist until cleanup. Scripted callers that
`bind()` a key explicitly — the confluence CLI, workflow `script` nodes — are
unaffected and keep driving the profile they bound.

## Forcing the SSRF guard on

This is the subtlest part of the design and the easiest thing to get wrong while
"fixing" EBL-001.

Today the enrolled session's SSRF guard is active only as a side effect of the
global: `_attach_cdp()` sets `BROWSER_CDP_URL`, so `_is_local_backend()` returns
`False`, so `_eval_ssrf_guard_active()` is `True`, so `_session_trusts_url`
scopes access to the listed origins. Remove the global and `_is_local_backend()`
returns `True` on an ordinary local install — the guard switches **off** and the
enrolled browser can reach any private origin, not only its trusted ones.

Naively removing the global therefore breaks origin-scoping and creates a worse
hole than EBL-001. The guard must be forced on for enrolled sessions.

Every guard site already reads the same shape and already has a session key in
scope (`nav_session_key`, `effective_task_id`, or `task_id`):

```
not _is_local_backend()
and not _is_local_sidecar_key(key)
and not _allow_private_urls()
```

Enrolled needs the inverse of the sidecar escape — one added disjunct per site:

```
(not _is_local_backend() or _is_enrolled_session_key(key))
and not _is_local_sidecar_key(key)
and not _allow_private_urls()
```

Sites: `_eval_ssrf_guard_active`, `browser_navigate`'s sensitive-query-param
check, its pre-navigation private-address block, its post-redirect block,
`browser_snapshot`'s current-URL recheck, `browser_vision`'s screenshot recheck,
and the private-page action guard. The ephemeral bare key keeps upstream
behaviour byte-for-byte. `_allow_private_urls()` remains the operator's blunt
global override.

`_is_enrolled_session_key` is a pure suffix predicate, mirroring
`_is_local_sidecar_key`. It must not consult config or launch anything.

## Endpoint ownership

`acquire()` gains `attach_global: bool = True`. The agent path passes `False`, so
it no longer mutates `os.environ`; scripted callers keep today's behaviour and
are not broken by the change.

The resolved endpoint goes into the session record —
`_active_sessions[key]["cdp_url"]` — which `_run_browser_command` already
consumes to build `--cdp <url>` for cloud providers. `_ensure_cdp_supervisor`
already prefers `_get_cdp_override()` and falls back to the session's own
`cdp_url`; with the global gone it selects the correct per-task endpoint with no
change, and becomes correct under concurrency rather than binding whichever task
acquired last.

## Acquire lifecycle

**Single-flight.** The current code releases the memo lock before calling
`acquire()`, so two threads can both miss and both launch; the review reproduced
two acquires and two endpoints for one key. Replace with a per-key lock held
across miss → acquire → publish, so exactly one acquire runs and the loser
observes the published result. This matters beyond duplication: `acquire()` runs
`_run_daemon_hygiene()`, which executes `close --all`, so a second concurrent
acquire can tear down the first session mid-navigation.

**Retain and release the handle.** `_session_cdp_url` currently discards the
returned `BrowserSession`, so `release()` never runs — but `acquire()` has
already called `registry.bind()`. The binding therefore survives cleanup, and the
task keeps internal-origin trust even after the operator turns
`browser.default_profile` off. Store the handle alongside the session record and
`release()` it exactly once during cleanup, which unbinds the registry.
`release()` is already idempotent.

**Failures are never cached** and never fall back to the bundled browser. That
invariant is unchanged from `3a458156c` and remains the point of the feature.

## The remaining review findings

**EBL-008 — trusted redirect (fix here).** The post-redirect guard checks only
`_is_safe_url(final_url)` and blanks the page, without consulting
`_session_trusts_url` as the pre-navigation guard does. A trusted internal
destination is therefore blocked, defeating the primary use case. Add the trust
check, mirroring the pre-navigation ordering, with the always-blocked
cloud-metadata floor evaluated first and never trusted.

Accepted behaviour change: routing is decided on the *initial* URL, so a public
URL that redirects into a trusted origin lands in the ephemeral browser and is
correctly blocked there — the ephemeral key trusts nothing. The real flow starts
at a trusted internal URL, and the browser follows CF Access/SSO redirects
internally without further routing decisions. Redirect re-routing is deliberately
not built now; it is recorded as a possible follow-up.

**EBL-004 — port collision.** `_ensure_enrolled_cdp` reuses any listener that
answers `/json/version`, so a second profile sharing the default port 9222 binds
its trust to a browser whose identity was never established. Reject duplicate
`cdp_port` across enrolled profiles at config load, and verify browser identity
from the `/json/version` payload before reusing an occupied port. This code
predates `3a458156c` but this design is what makes it reachable from the agent
path.

**EBL-006 — availability gate.** `check_browser_requirements`'s early return
skips the `agent-browser` CLI check, but the enrolled path still drives the
browser *through* agent-browser with `--cdp <url>`, so the CLI is required.
`resolve_executable` also accepts any existing path, so a mode-0644 file passes
the gate and the first acquire raises `PermissionError`. Validate the whole
chain without launching: CLI present, executable is a regular file with execute
permission where the platform has one, data directory usable, port valid.

**EBL-009 — dead endpoint.** The memo and `_active_sessions` have no liveness
notion, so a closed or crashed browser keeps being driven at a dead endpoint
while activity refreshes hold off the idle reaper. On a connection-class failure
for an enrolled key, evict the record and memo and permit one bounded
re-acquire.

**EBL-007 — the ledger.** The entry omits the new module state
(`_session_cdp_lock`, `_session_cdp_urls`) and every modified call-site owner
(`_get_session_info`, `_run_browser_command`, `_cleanup_single_browser_session`,
`check_browser_requirements`), and its `merge_guidance` explicitly instructs a
future merger to preserve the process-global side effect that EBL-001 proves is
the defect. Rewrite it to name the state and all owners, and to describe the
per-session contract. A ledger that passes its checker while omitting a
load-bearing symbol is worse than no ledger, because the entire mitigation for
this file's upstream churn rests on it.

## Testing

The review's central criticism of the current tests is correct: every launch test
replaces `acquire()` with a spy, so none exercises daemon hygiene, process
launch, CDP readiness, `_attach_cdp`, registry binding, `release()`, port
collision, or cross-task state. The single highest-risk untested path — two real
task IDs concurrently acquiring and using mixed profiles through the actual
manager — contains EBL-001, EBL-003, and EBL-005 together.

Required, beneath the process/CDP boundary rather than above it:

1. **Cross-task isolation.** Two concurrent task IDs, one enrolled and one
   explicitly ephemeral, different endpoints, with barriers around acquire.
   Assert each drives its own endpoint, and that this still holds after either
   task is cleaned up. This is EBL-001's regression test and it must fail if the
   global is reintroduced.
2. **Single-flight.** Barrier-driven concurrent calls for one key: exactly one
   acquire, one endpoint, one hygiene call.
3. **Lifecycle.** Real acquire with process launch mocked below CDP, then
   cleanup, then toggle-off, then reuse of the same key: no binding, no
   reacquire, no residual trust, idempotent release.
4. **Guard forcing.** The SSRF guard stays active for an enrolled key on a local
   backend. This test must fail if the added disjunct is dropped — otherwise
   removing the global silently disables origin-scoping with no other symptom.
5. **Routing.** Trusted origin → enrolled key; public origin → bare key;
   untrusted private origin → bare key and blocked; `/browser connect` and
   Camofox suppress enrolled routing; enrolled outranks `::local`.
6. **Redirect parity.** Public login redirecting to a trusted origin succeeds in
   an enrolled session; redirect to an unlisted private origin fails; redirect to
   cloud metadata fails even under a trusted enrolled session.
7. **Gate.** Non-executable file, missing CLI, unusable data dir, invalid port —
   each withholds the tools.

Every existing browser SSRF and hybrid-routing test must pass **unedited**. If
one needs editing, this design altered a boundary it claimed not to touch.

All runs use `scripts/run_tests.sh`, not `pytest` directly. The prior session's
"28 pre-existing failures" figure was an artefact of direct `pytest` without the
subprocess-isolation plugin; under the canonical runner those files pass, and the
real baseline is three timing-sensitive failures common to both refs.

## What deliberately does not change

- **Toggle off is byte-for-byte today's behaviour** — no `default_profile`, no
  enrolled routing, the existing throwaway path.
- **`BROWSER_CDP_URL` / `browser.cdp_url` semantics and precedence**, so
  `/browser connect` is unaffected.
- **The always-blocked cloud-metadata floor**, first at every guard site, never
  trusted, under any profile.
- **Chrome-first candidate order** and the exclusion of the bundled Chrome for
  Testing, both shipped in `3a458156c` and unchallenged by the review.
- **The confluence-research skill**, which keeps its private launcher.

## Risks

- **`browser_tool.py` remains the most upstream-churned file in this stack.**
  This design adds a routing branch and seven one-line guard disjuncts to it.
  The disjuncts are the silent-revert risk: dropping one loses origin-scoping
  with no build error and no failing test unless test 4 exists. That test is the
  mitigation, and the ledger must name it.
- **Two browsers per task is a visible behaviour change.** With `headed: true`
  the operator may see a second window appear when the agent first reaches a
  trusted origin.
- **Nothing here is verified on hardware.** Everything in the review's residual-
  risk list still stands, and a managed corporate Windows machine remains the
  only thing that can confirm real certificate and SSO behaviour.

## Verification on hardware

Unchanged from the prior design, plus two additions the split makes necessary:

- A public page and a trusted internal page in the same task must demonstrably
  land in **different** browsers, and the public one must hold no corporate
  cookies.
- Concurrent mixed sessions must not cross-drive, before or after either is
  cleaned up.
