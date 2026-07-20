# Design — Persistent enrolled-browser session (Option B)

**Date:** 2026-07-20
**Status:** Proposal. Nothing implemented.
**Target branch:** `base` (brand-agnostic capability — golden rule: additive,
upstream-mergeable). Never a brand branch.
**Relationship to the confluence skill:** ADDITIVE. The `confluence-research`
skill stays exactly as shipped. This is the shared substrate it (and future
internal-site skills) will *optionally* consume once proven. We keep both until
the browser tool is confident in all scenarios.

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
      cdp_port: 9222
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
  are flagged medium/higher conflict risk in the surface table, so keep the
  edits minimal and union-resolvable.
- No brand emitter touched → `generate <brand> --check` stays 8/8.
- config additive (`browser.profiles`); absent → today's behaviour exactly.

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
2. Does the supervisor's raw-CDP dialog handling need per-profile awareness, or
   is it profile-agnostic? (Likely agnostic — it keys on cdp_url.)
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
