# Design — Persistent enrolled-browser session (Option B)

**Date:** 2026-07-20
**Status:** Approved with revisions — see §0 (Revision 2026-07-25). Nothing
implemented. Implementation plan:
`docs/superpowers/plans/2026-07-25-browser-session-manager.md`.
**Target branch:** `base` (brand-agnostic capability — golden rule: additive,
upstream-mergeable). Never a brand branch.
**Relationship to the confluence skill:** ADDITIVE. The `confluence-research`
skill stays exactly as shipped. This is the shared substrate it (and future
internal-site skills) will *optionally* consume once proven. We keep both until
the browser tool is confident in all scenarios.

---

## 0. Revision — 2026-07-25 (post upstream Hermes v0.19.0)

This design was written 2026-07-20. Upstream merge `d4966edba` (Hermes v0.19.0)
landed afterwards and changed **188 lines of `tools/browser_tool.py`** plus 52
lines of `skills/computer-use/SKILL.md`. Every code claim below was re-verified
on `base` on 2026-07-25. Results:

**Claims that HOLD (unchanged):**
- `browser_tool.py` drives agent-browser (115 refs); the 11 Playwright refs are
  Chromium install/cache plumbing only, no driver. §2's "converge on
  agent-browser" stands.
- `browser_supervisor.py` is still `"One supervisor per (task_id, cdp_url)
  pair"` and browser-identity-agnostic. §6's "supervisor unchanged" stands.
- `browser_tool.py` / `browser_supervisor.py` are still PURE UPSTREAM (zero OTTO
  hooks). §6a's mandatory-registration requirement stands and is now the
  higher-risk item, given the 188-line churn.

**§5 is SUPERSEDED — the eval security baseline inverted.** v0.19.0 added
`browser.restrict_evaluate` and made the sensitive-primitive denylist
**opt-in, OFF by default** (upstream's reasoning: it gated on primitive *names*
like `fetch`/`cookie`, blocking legitimate DOM extraction without preventing
real exfiltration). `browser.allow_unsafe_evaluate` now only overrides
`restrict_evaluate` back off. Therefore §5's premise — "`default`/ephemeral →
denylist ON (current safe behaviour, preserved)" — is **false as of v0.19.0**.
Per-profile trust is still a net improvement, but it would be *tightening* the
ephemeral default rather than preserving it. Treat that as a deliberate decision,
not a freebie.

**NEW BLOCKER — the CDP attach path trips the SSRF guard.** Egress is now gated
by:

```python
_eval_ssrf_guard_active = (not _is_local_backend()
                           and not _is_local_sidecar_key(key)
                           and not _allow_private_urls())
```

and `_is_local_backend()` **returns False on any CDP override** — deliberately,
so a model-driven navigate cannot reach internal services via an off-host
Chrome. This design attaches enrolled Edge over CDP (`cdp_port: 9333`), so an
internal Confluence host that resolves to a private IP would be **blocked**, for
eval-fetch *and* navigate/snapshot. The design's central technique does not work
as written on v0.19.0.

**Resolution (decided 2026-07-25): integrate with the SSRF guard at
per-session, origin-scoped granularity.** Upstream's hybrid-routing "local
sidecar" already exempts a session from the guard via a session-key suffix
(`_is_local_sidecar_key`, `_LOCAL_SUFFIX = "::local"`) — that is the correct
granularity to mirror. Trust is evaluated per (session, URL) against the
profile's `trusted_origins`, NOT as a blanket session exemption. The global
`browser.allow_private_urls` escape hatch is explicitly REJECTED as the
mechanism: it disables private-URL protection for every session, including
ephemeral external browsing.

**NEW, helpful:** v0.19.0 added `browser.headed` + `AGENT_BROWSER_HEADED`
(`_is_headed_mode()`), which partly covers §3's `sess.signin()` visible-launch
need. It is global config, so this design makes headed a **per-profile**
property; the enrolled launcher owns it (enrolled sessions attach via `--cdp`
and never reach the `--session` local launcher where `--headed` is appended).

**Desktop half — decided 2026-07-25: extend upstream `computer_use`, do NOT
fork.** The ultimate goal is two natural-language tools: browser page control
(`browser_*`) and desktop control (`computer_use`). Both already exist upstream;
`computer_use` is a registered toolset (`tools/computer_use/`, cua-driver
backend, `tools_config.py:120`) and is not brand-excluded today. v0.19.0
invested substantially in it (background-first verify→escalate ladder,
`delivery_mode`/`bring_to_front`, structured `effect`/`escalation`/`code`
verdicts). Accordingly
`docs/2026-07-23-hermes-computer-use-reliability-planning-prompt.md` — which
proposed a downstream-owned `cua_desktop` tool + `cua-desktop` skill with
upstream `computer_use` hidden for OTTO/LOOP24 — is **SUPERSEDED**. Forking
would permanently reimplement work upstream is doing well and would add a large
OTTO surface to govern, which is the same golden-rule tension as §6a one level
up. The desktop half is therefore out of scope for this design's plan and needs
only a thin downstream layer (skill guidance / curation) if anything.

---

## 1. Problem

There are two browser paths in the tree and they don't share lifecycle:

1. **OOB `/browser`** (`tools/browser_tool.py` + `tools/browser_supervisor.py`)
   drives **agent-browser CLI** for actions and a **raw-CDP supervisor** for
   dialogs/OOPIF. It can *attach* to a running browser over CDP
   (`/browser connect 9222`) and can eval in-page via
   `browser_cdp(Runtime.evaluate)` (which bypasses the `fetch` denylist that
   blocks `browser_console`). **But it cannot LAUNCH a specific enrolled
   browser, use a persistent user-data-dir, or hold a signin/session.** Its
   local provider launches agent-browser's bundled Chrome for Testing — the
   UNMANAGED browser that FAILS corporate mTLS/SSO.

2. **confluence-research skill** (`skills/.../scripts/backends.py`) reimplements
   the missing lifecycle: launch enrolled Edge with a dedicated profile,
   connect over CDP, signin, flush-to-disk, headless reuse. Default engine is
   **Playwright** (opposite of OOB), with an agent-browser backend as the
   cross-platform alternative.

The lifecycle logic in (2) is **generic** — nothing about launching enrolled
Edge with a persistent profile is Confluence-specific. Every internal
authenticated site (SharePoint, ServiceNow, internal dashboards, Jira UI) needs
the same thing. It should live in the shared browser subsystem, so there is one
consistent way to manage browsers.

## 2. Engine decision — converge on agent-browser

Verified 2026-07-20 from code:
- `tools/browser_tool.py` drives **agent-browser CLI** (Playwright only appears
  as Chromium install/cache plumbing).
- `tools/browser_supervisor.py` holds its **own raw CDP WebSocket**.
- OOB uses **no Playwright** as a driver.

Verified 2026-07-19 in the Ericsson env: **agent-browser attached to enrolled
Edge over CDP and issued authenticated same-origin fetch** (CF Access + SSO +
mTLS all carried).

Therefore the shared manager standardizes on **agent-browser** — it is already
the OOB driver, already a repo dependency, and already proven against corporate
Confluence. The skill's Playwright backend becomes fallback-only. This is what
makes "one consistent way" real rather than a third stack.

> Caveat that survives the convergence: agent-browser's client-daemon wedged on
> Windows during testing (`eval` hung until a full restart). The manager MUST
> own daemon hygiene (`close --all` on acquire, bounded timeouts, base64 eval
> never `--stdin`). This is a first-class requirement, not an afterthought.

## 3. Design: a named-profile session manager

New brand-agnostic module (NOT edits threaded through the big shared files):

    tools/browser_profiles.py        # NEW — pure profile registry + launcher
    tools/browser_session_manager.py # NEW — acquire/signin/eval/release over agent-browser

`tools/browser_tool.py` and the confluence backend both become thin CONSUMERS
of the manager. The two large shared upstream files
(`browser_tool.py`/`browser_supervisor.py`) get only minimal additive hooks —
never heavy new logic — to keep upstream merges clean.

### Profiles

A profile is a named browser identity. Config in `config.yaml`:

```yaml
browser:
  profiles:
    default:                       # EXTERNAL — unchanged OOB behaviour
      kind: ephemeral              # agent-browser bundled Chrome for Testing
      trusted_origins: []          # eval fetch/network stays DENYLISTED
    enrolled:                      # INTERNAL — the new path (opt-in)
      kind: enrolled
      executable: auto             # resolve enrolled Edge (OS cert store)
      user_data_dir: "${HERMES_HOME}/browser-profiles/enrolled"
      cdp_port: 9333            # NOT 9222 -- /browser connect owns that port
      trusted_origins:             # per-profile eval allowlist (see §5)
        - "https://eteamspace.internal.ericsson.com"
        - "https://*.internal.ericsson.com"
```

`kind: ephemeral` = today's disposable external browser. `kind: enrolled` =
launch the real corporate browser with a persistent profile.

### Manager API (importable — usable by tools AND scripts/workflows)

```python
from tools.browser_session_manager import acquire

sess = acquire(profile="enrolled", headless=True)   # launch/attach + daemon hygiene
sess.navigate(url)
sess.is_authenticated(probe_js)                      # generic auth probe hook
result = sess.eval(fn_js)                             # base64 IIFE via agent-browser
sess.signin(url, probe_js, timeout=300)              # visible -> wait -> flush-to-disk
sess.release()                                        # detach; profile persists
```

Because it's importable (not only agent-tool-shaped), a workflow `script` node
and the confluence CLI drive the same code the `/browser` command does.

## 4. How both use cases keep working

| | External (`default`) | Internal (`enrolled`) |
|---|---|---|
| Trigger | `/browser` default, as today | `/browser --profile enrolled`, or skill/script `acquire("enrolled")` |
| Browser | agent-browser Chrome for Testing | enrolled Edge, OS cert store |
| Profile | ephemeral | persistent user-data-dir, live SSO |
| Session mixing | never sees corporate cookies | never used for untrusted pages |
| Eval fetch/network | DENYLISTED (safe default) | allowed for `trusted_origins` only |

External is the untouched default path — the enhancement ADDS the enrolled
path, it does not replace anything. Isolation between them is a hard rule
(§5), riding on the supervisor's existing per-`(task_id, cdp_url)` isolation.

## 5. Security — per-profile eval policy (a net improvement)

> **SUPERSEDED by §0 (2026-07-25).** The baseline described in this section
> changed in upstream v0.19.0: the denylist is now opt-in
> (`browser.restrict_evaluate`, default OFF) and egress is gated by the SSRF
> guard instead. The per-profile/per-origin *intent* below is retained; the
> mechanism is now an origin-scoped hook into `_eval_ssrf_guard_active`'s
> predicates, not a replacement for `allow_unsafe_evaluate`. Read §0 first.

Today `browser.allow_unsafe_evaluate` is a single GLOBAL switch: all-or-nothing
`fetch`/network eval. This replaces it with per-profile / per-origin trust:

- **`default` / any ephemeral profile** → denylist ON. A hostile external page
  cannot run `fetch` exfiltration. (Current safe behaviour, preserved.)
- **`enrolled` profile, page origin ∈ `trusted_origins`** → `fetch` permitted —
  the sanctioned same-origin REST technique.
- **`enrolled` profile, origin ∉ `trusted_origins`** → denylist ON (a stray
  external tab in the enrolled browser is still protected).

Net: strictly safer than the global switch AND it enables the internal use
case. The global flag stays honoured for back-compat (maps to "all origins
trusted") but is deprecated in docs.

**Hard isolation rule:** an untrusted external site must NEVER be driven through
the `enrolled` profile — its live SSO/mTLS session would be exposed to a
malicious page via the same eval/fetch channel we rely on. The manager refuses
to attach an ephemeral/external navigation to an enrolled session and vice
versa (profile is fixed at `acquire`).

**Enrolled-browser rule:** the launcher MUST resolve the *enrolled* Edge
(OS cert store) and MUST NOT use agent-browser's bundled Chrome for the
enrolled profile — that distinction is the entire reason mTLS works.

## 6. Merge-safety (golden rule)

- New logic lives in NEW modules (`browser_profiles.py`,
  `browser_session_manager.py`) → cannot conflict with upstream.
- `browser_tool.py` / `browser_supervisor.py` get only small additive hooks
  (profile lookup at connect; route enrolled launches to the manager). These
  are currently PURE UPSTREAM (no OTTO rows today) → Option B is the FIRST OTTO
  edit to them, so registration is mandatory (see §6a).
- No brand emitter touched → `generate <brand> --check` stays 8/8.
- config additive (`browser.profiles`); absent → today's behaviour exactly.
- **Supervisor unchanged.** It is keyed by `(task_id, cdp_url)` and is browser
  -identity-agnostic (confirmed from the internals doc), so the enrolled launch
  sits BELOW it. Per-profile eval trust is enforced in the `browser_cdp` tool
  wrapper (maps `cdp_url → profile`), NOT in the supervisor.

## 6a. Required merge-governance artifacts (MANDATORY, same commit)

Without these, a future `main → base` merge silently reverts the hooks. All
three ship in the SAME commit as the code:

1. **Workspace `CLAUDE.md` surface-table rows** for the two edited shared files
   (`browser_tool.py` hooks, `browser_supervisor.py` if touched) AND the two new
   modules — nature of change, conflict risk, union-on-merge note.
2. **Paired `AGENTS.md`** updated byte-identically (`cmp CLAUDE.md AGENTS.md`).
3. **`otto-upstream-merge` skill silent-revert greps** — a grep per hook that
   proves the OTTO logic survived the merge (e.g. `git grep -c
   'browser_session_manager\|browser.profiles' -- tools/browser_tool.py`).

**Backfill owed NOW (pre-existing, not Option B):** the merged
`confluence-research` skill has no surface-table row. Add one for
`skills/ericsson/confluence-research/**` + the `capabilities/ericsson.json`
registration (low risk — new files + additive line — but the golden rule
requires it). Update `CLAUDE.md` + `AGENTS.md` together.

## 7. Migration path for the confluence skill

Staged; the skill keeps working throughout.

1. **Now (done):** skill self-contained, Playwright default + agent-browser
   backend. Ships and is testable independently.
2. **After the manager lands + is proven:** add a THIRD backend to
   `backends.py` — `SessionManagerBackend` that calls `acquire("enrolled")`.
   Make it the default when available; keep Playwright/agent-browser backends as
   fallback. One-line default flip, no rewrite (the seam exists for this).
3. **When confident in all scenarios:** consider retiring the skill's private
   Edge launcher in favour of the manager entirely. Not before — we keep both
   until `/browser` is trusted for internal + external.

## 8. Open questions

1. Enrolled-Edge headless reuse vs Conditional Access re-checks, and cookie
   lifetime — unmeasured (also open for the skill). Validate before unattended.
2. ~~Does the supervisor need per-profile awareness?~~ RESOLVED: no. The
   internals doc confirms it is keyed by `(task_id, cdp_url)` and is
   browser-identity-agnostic. No supervisor change; per-profile eval trust is
   enforced in the `browser_cdp` tool wrapper.
3. Windows daemon hygiene: is `close --all` on acquire sufficient, or does the
   manager need a health-probe + relaunch loop? Testing suggested the latter
   may be needed.
4. `--profile enrolled` surface on the `/browser` *command* (chat) — new flag
   vs config-default-only. Command-surface change touches shared files; scope
   carefully.

## 9. Verification criteria

- `/browser` external flow byte-unchanged (regression: existing browser tests
  pass untouched).
- `acquire("enrolled")` launches enrolled Edge, persists the profile, and a
  same-origin fetch to a `trusted_origins` host returns 200 in the Ericsson env.
- eval `fetch` to a NON-trusted origin under `enrolled` is DENIED.
- eval `fetch` under `default`/ephemeral is DENIED (unchanged safe default).
- confluence `SessionManagerBackend` produces byte-identical Markdown to the
  Playwright backend for the same page.
- `generate <brand> --check` 8/8 on otto and loop24; existing browser test
  suite green.
```
