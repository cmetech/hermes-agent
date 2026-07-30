# Consolidated browser automation: drive the real browser, not the throwaway one

Date: 2026-07-26
Status: approved in principle, not yet implemented
Affected: `tools/browser_tool.py`, `tools/browser_profiles.py`,
`tools/browser_session_manager.py` (caller only), `capabilities/ericsson.json`

## Goal

One browser capability that drives the user's **real, installed browser** for
both internal enterprise sites and external sites:

1. Control internal enterprise sites — view, fill forms, return content.
2. Control external sites (unchanged from today).
3. Absorb the confluence-research skill's browser logic into the shared tool, so
   the skill's private launcher becomes a backup that is no longer used.
4. Prefer **Chrome**, fall back to **Edge**.
5. Enable/disable internal trust and edit the trusted-origin list (already
   shipped — Settings → Safety).

## Current state

Goals 3 and 5 are built. Goal 2 works. Goals 1 and 4 do not, and goal 3's
consolidation was prepared but never adopted.

**The trust half is wired; the browser half is not.** `tools/browser_tool.py`
references the profile system in exactly one place — `session_trusts_url`
(line 3502), the SSRF decision. Across the whole repo, `browser_session_manager`
is referenced only by its own module, one docstring, and its own tests:
**nothing calls `acquire()`**. Plan Task 9 closed *binding*, not *launching*.

The consequence is worse than a missing feature. With
`browser.default_profile: enrolled`, an unbound agent session takes the
default-profile fallback and is granted internal-origin trust — while still
driving agent-browser's bundled Chrome for Testing, which has none of the
corporate certificates or SSO. The guard opens the door and the wrong browser
walks through it. That is also why runbook Step 4 reads as an mTLS failure: it
is the wrong browser, not a broken connection.

**Measured on the affected laptop (2026-07-26):**

```
_find_agent_browser     'C:\Users\ecorell\AppData\Roaming\npm\agent-browser.CMD'
_get_cdp_override       ''
_chromium_installed     False
check_browser_requirements >>>  False
```

So the browser tools are currently withheld from every session — silently, with
no error — because agent-browser's bundled Chromium was never downloaded. The
enrolled path would never have used it.

## Design

### 1. Browser preference: Chrome first, Edge fallback

`_enrolled_candidates()` (`browser_profiles.py`) lists **Edge only** on Windows,
and Edge before Chrome on macOS/Linux. The original reasoning was that Edge is
the browser enrolled with the OS certificate store on a managed Windows device.

Confirmed with the user 2026-07-26: Chrome reaches internal sites on the target
fleet, so Chrome is policy-managed there. The order flips and Windows gains real
Chrome paths.

Unchanged and load-bearing: agent-browser's bundled **Chrome for Testing** stays
excluded. It is a different binary, not merely a different profile, and it is the
one browser that cannot present machine certificates. The exclusion is the point
of the candidate list, not an incidental detail.

A user can still pin a specific binary via the profile's `executable` field;
`auto` is what consults this list.

### 2. The wiring seam

`acquire()` already anticipates this caller:

> `session_key` lets a caller bind the session under its own key — the agent
> passes its `task_id` so the trust seam in `browser_tool` matches.

Add one delegating helper to `browser_tool.py`, mirroring how `_session_trusts_url`
was introduced:

```
_session_cdp_url(session_key) ->
    resolve the profile for this session (explicit bind, else browser.default_profile)
    if enrolled:  acquire(profile, session_key=session_key).cdp_url
    else:         _get_cdp_override()          # today's behaviour, untouched
```

Call sites currently reading `_get_cdp_override()` for **launch decisions** read
this instead. `_get_cdp_override()` keeps its own meaning (env var, then
`browser.cdp_url`) and remains the fallback, so `/browser connect` and a
statically configured CDP endpoint behave exactly as now.

**Memoize per session — this is load-bearing.** `acquire()` runs
`_run_daemon_hygiene()`, which executes `close --all`. Calling it per tool
invocation would tear the browser down between `navigate` and `click`. The result
is cached per `session_key` and reused for the life of the session; a failed
acquire is not cached, so a transient launch failure can recover.

**Failure is explicit, never silent.** `acquire()` raises `ProfileError` for an
unresolvable enrolled browser and deliberately never falls back to the unmanaged
bundled Chrome. That behaviour is preserved: an enrolled session that cannot
start its browser reports why, rather than quietly driving a browser that will
be refused by internal sites. Silent fallback is the failure mode this whole
design exists to prevent.

### 3. The availability gate — after the wiring, not before

`check_browser_requirements()` returns False when `_chromium_installed()` is
False, which withholds every browser tool. Once the wiring exists, an enrolled
session no longer needs bundled Chromium, so the gate gains an early return
alongside the existing `_get_cdp_override()` one: true when
`browser.default_profile` names an **enrolled** profile whose executable actually
resolves.

**Order matters.** Making this change first — as was nearly done — would
advertise tools that then launch agent-browser with no Chromium and hang until
the command timeout (`browser_tool.py:4842` warns of exactly this). The gate may
only relax once there is a real browser behind it.

Scoped to *would actually work*, mirroring the Termux branch's reasoning about
never advertising tools that fail on first use: it requires the toggle on **and**
the executable to resolve. Toggle off means the throwaway browser, which
genuinely does need Chromium, and the gate stays as-is.

### 4. What deliberately does not change

- **External browsing.** Toggle off ⇒ unbound sessions resolve to no profile ⇒
  the existing throwaway path, byte-for-byte. The existing browser SSRF and
  hybrid-routing suites must pass **without edits**.
- **The always-blocked cloud-metadata floor**, at every guard site.
- **`browser.cdp_url` / `BROWSER_CDP_URL`** semantics and precedence.
- **The confluence skill.** It keeps its private launcher as a backup. It is not
  switched to the shared path in this change; consolidation means the shared tool
  gains the capability, not that the skill is rewritten. Retiring it stays where
  the earlier plan left it: not before `/browser` is trusted for internal *and*
  external.

## What the user will experience

The agent drives Chrome with its **own profile directory**
(`$HERMES_HOME/browser-profiles/enrolled`), not the user's everyday profile.
Attaching to a running default-profile Chrome is not possible, and hijacking a
live session would be wrong even if it were.

So the first internal page opens a **visible Chrome window** requiring one
interactive sign-in, which then persists — that is what `headed: true` is for.
Client certificates need no sign-in: on Windows they come from the machine
certificate store, which any installed Chrome uses regardless of profile. That is
precisely what Chrome for Testing cannot do.

## Risks

- **`browser_tool.py` is the most upstream-churned file in this stack** (v0.19.0
  changed 188 lines). Mitigated by keeping the change to one delegating helper
  plus call-site swaps — the shape already proven by `_session_trusts_url` — and
  by a ledger entry.
- **Chrome managed on the tester's laptop may not mean managed fleet-wide.** If
  that proves false, the candidate order becomes a config knob rather than a flip.
  Worth confirming via `chrome://policy` before rollout.
- **Session lifetime.** A long-lived enrolled browser holds a corporate SSO
  session. Existing inactivity handling applies; no new lifetime policy here.

## Testing

- `_session_cdp_url` returns the acquired CDP URL for an enrolled session, and
  `_get_cdp_override()`'s value otherwise.
- The acquire result is memoized per session: N tool calls ⇒ one `acquire()`.
- A failed acquire is not cached and surfaces as an error, never as a silent
  fallback to the bundled browser.
- Candidate order puts Chrome before Edge on all three platforms, Windows
  includes real Chrome paths, and Chrome for Testing appears nowhere.
- The gate reports available for a resolvable enrolled profile with no bundled
  Chromium, and still unavailable when the toggle is off and Chromium is missing.
- The existing browser SSRF/hybrid-routing suites pass **unedited**.

## Verification on hardware

Tests cannot prove this; the corporate machine has to.

- A natural-language request opens a **visible Chrome window**, sign-in completes,
  and the agent reports the real page title.
- The agent summarises internal page content, and fills a form
  (`browser_type` + `browser_click`) without hitting a guard.
- An external site still works with the toggle off.
- Eval `fetch` to a non-trusted origin under `enrolled` is denied; cloud-metadata
  is denied even under a trusted enrolled session.

## Out of scope

- Retiring or rewriting the confluence-research skill.
- The `/browser --profile` per-call flag.
- Desktop `computer_use` work.
- The absent-browser-tool symptom itself, which this fixes as a consequence
  rather than by targeting it.
