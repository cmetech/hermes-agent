# Plan — `confluence-research` skill

**Date:** 2026-07-19
**Status:** Draft. Nothing landed in the repo. Reference implementation was in
scratchpad and has been cleared — rebuild from this plan when ready.
**Target branch:** `base` (shared capability content — see the placement
invariant in `CLAUDE.md`). Must NOT be created on a brand or feature branch.

---

## 1. Context

Loop24 has a working Confluence fetcher driven from Langflow:
- `loop_24/custom_components/ericsson_parsers/confluence_fetcher.py` — Langflow node.
- `loop_24/utils/confluence_page.py` — the worker.

Goal: bring this into the agent as a skill, invoked conversationally, output
summarized to a local Markdown mirror. No Langflow required.

## 2. Core technique (proven correct, keep it)

REST calls are issued as injected JS with `credentials: "same-origin"` from
inside a signed-in browser tab, so Cloudflare Access, SSO and mTLS are handled
by the browser. No cookie/cert handling in Python.

**VERIFIED 2026-07-19 in the real Ericsson environment** against
`https://eteamspace.internal.ericsson.com/rest/api` (Server/DC):
- DevTools console same-origin `fetch` to `/rest/api/space?limit=1` → 200 + JSON.
- **agent-browser** attached to corporate Edge (`--cdp 9222`) ran the same
  fetch and returned the authenticated JSON. **CF Access + SSO + mTLS all
  carried in attach mode.** The mTLS concern is resolved: yes, it works.

## 3. Engine decision: agent-browser vs Playwright (Python)

Both drive the *authenticated corporate Edge* and both work. The auth is the
browser's job; the engine just injects the fetch. Key facts from testing:

| | agent-browser | Python/Playwright worker |
|---|---|---|
| Already in tree | ✅ `package.json:36` (`agent-browser@^0.26.0`) | needs `playwright` pip dep |
| Cross-platform | ✅ 7 prebuilt binaries incl. win32-x64 | ✗ hardcoded Edge path (fixable) |
| Attach to corp Edge + mTLS | ✅ **verified working** | ✅ proven in loop24 |
| Process model | client-**daemon**; each call re-attaches | one long-lived process, held `page` |
| Robustness observed | **hung until full restart** (wedged daemon) | proven stable in loop24 |
| Reaches REST from Python | shell out + parse JSON | in-process `page.evaluate` |

### The Windows hang — important operational finding
During testing, `agent-browser --cdp eval` (both `--stdin` and direct-arg)
**hung** on Windows. `open` worked; `eval` wedged. Root cause: agent-browser's
background daemon was left half-attached by Ctrl+C'd runs. A clean relaunch of
everything fixed it. Two lessons:
1. This hang is in agent-browser's **CLI/daemon layer** — a layer the Python
   worker does not have (`connect_over_cdp` once, hold `page`, call
   `page.evaluate` in-process). The Python path cannot hit this specific
   failure.
2. `eval --stdin` piping is unreliable in PowerShell. If agent-browser is
   used, prefer `eval -b <base64>` and manage the daemon explicitly
   (`close --all` on start, bounded timeouts, reset stale sessions).

### Decision
Build **one implementation with a swappable session/eval seam**; support both
engines behind it. Everything else is engine-agnostic and written once.

```
confluence_api.py  (injected JS)      ─┐
storage_to_md.py   (XHTML → Markdown)  ├─ shared, engine-agnostic
artifacts.py       (tree/manifest)     │
confluence.py      (CQL walk, orchestr)┘
        │  Backend interface:
        │     eval_in_page(host_url, js) -> dict
        │     is_authenticated(api_base) -> bool
        ├── PlaywrightBackend    → page.evaluate      (default; proven, robust)
        └── AgentBrowserBackend  → agent-browser eval -b … --json  (cross-platform)
```

Only the ~40-line session layer is doubled. Injected JS, REST logic, Markdown
conversion, artifacts are shared.

**Engine selection:** `--engine {auto,playwright,agent-browser}`, plus a
`config.yaml` override.
- `auto` probe: is the chosen engine present and can it reach an authenticated
  session? Pick the first that passes.
- **Fallback rule:** fall back automatically only on **capability** failure
  (binary/dep absent, engine wedged). On **auth** failure (not signed in) do
  NOT fall back — report "run signin"; both engines fail identically and
  silent fallback would mask the real signal.

**Recommended default: Playwright/Python primary, agent-browser secondary.**
Rationale: the Python path is proven in loop24 and its process model avoided
the one failure we actually hit (daemon wedge on Windows). agent-browser is the
better long-term answer (already a dep, cross-platform, no pip Playwright) once
its daemon lifecycle is handled robustly in the backend. The seam lets us flip
the default later without touching the shared code. *(This reverses the earlier
lean toward agent-browser-default — the Windows hang is why.)*

## 4. Gaps in the existing worker (close these)

1. **No enumeration** — single page only; no CQL / space-wide / subtree. *Biggest gap.*
2. **Attachments listed, never downloaded** (`confluence_page.py:324`).
3. **Plain text, not Markdown** (`html_to_text`, `:293`) — flattens `body.view`.
4. **Windows-only** — hardcoded `EDGE_EXE` (`:49`). (Free with agent-browser.)
5. **No incremental re-run** — fetches `version`, never diffs on it.
6. Minor: `history` in EXPAND unused; dead `_browser_connect` (`:176`).

## 5. Design decisions

- **Convert `body.storage`, not `body.view`** — storage is clean, stable XHTML
  with macros intact; view is theme-rendered and lossy.
- **Stable paths, not run-timestamped dirs** — so re-sync is a `git diff` and
  unchanged pages cost zero fetches. `.manifest.json` carries history.
  Deliberately deviates from opportunity-visuals' "never overwrite a run dir"
  (that skill is one-shot; this maintains a mirror). `SUMMARY.md` never
  machine-overwritten.
- **Attachments via in-page base64** (Playwright backend) — Playwright's
  `context.request` does NOT carry the mTLS client cert; staying in-page does.
  agent-browser backend can use its native `download`. Size cap + list-only
  default either way.
- **Read-only subcommands before write** — `probe → enumerate → sync` mirrors
  opportunity-visuals' `inspect → analyze → prepare`.

## 6. Structure

```
skills/ericsson/confluence-research/
├── SKILL.md
├── requirements.txt              # playwright>=1.40 (Playwright backend only)
├── scripts/
│   ├── confluence.py             # CLI: signin|probe|enumerate|sync|fetch
│   ├── backends.py               # Backend interface + Playwright + agent-browser
│   ├── confluence_api.py         # injected JS, REST shapes, CQL walk
│   ├── storage_to_md.py          # storage XHTML → Markdown
│   └── artifacts.py              # tree layout, frontmatter, manifest
└── references/
    └── rest-api-notes.md         # Cloud vs DC, CQL cookbook, daemon lifecycle
```

Artifact layout:
```
$HERMES_HOME/research/confluence/<SPACE>/
├── INDEX.md          inventory, regenerated each sync
├── SUMMARY.md        model-written, never machine-overwritten
├── .manifest.json    page_id → {version, sha256, path}
├── pages/<slug>-<id>.md
└── attachments/<id>/<filename>
```
Frontmatter carries `version` + `content_sha256` → cheap incremental re-sync.

## 7. Known implementation notes (from the cleared reference impl)

- `storage_to_md.py` was written and **verified** on representative storage
  XHTML (headings, nested lists, `code` macro + CDATA + language, GFM tables,
  attachment image refs). Rebuild it; it's the highest-risk piece and it works.
- **Known bug to fix on rebuild:** `ac:task-list` rendered `completeVerify certs`
  instead of `- [x] Verify certs` — `<ac:task-status>` text leaked because it's
  never captured into `_pending_task_state`.
- On Windows + agent-browser: use `eval -b <base64>`, never `--stdin`
  (PowerShell stdin piping hangs). Manage the daemon (`close --all`, timeouts).

## 8. Open questions

1. ~~Cloud or Server/DC?~~ **Answered: Server/DC** (`/rest/api`, host
   `eteamspace.internal.ericsson.com`).
2. ~~Does the same-origin technique carry CF/SSO/mTLS here?~~ **Answered: yes.**
3. ~~Does Confluence expose a PAT?~~ **Answered: NO.** Confirmed 2026-07-19.
   There is no token path — the browser session cookie is the ONLY credential.
   This is *why* the browser approach exists; it is not a fallback, it is the
   whole mechanism. Removes any "headless token" alternative from the design.
4. **Unattended/headless mode — now the critical open question.** With no PAT,
   the only way to run without a human is a persisted enrolled-Edge session:
   sign in once (visible), flush cookies to a `--user-data-dir`/`--profile`,
   reuse headless until the cookie expires. The Python worker already does
   exactly this dance (`confluence_page.py` `--visible` → shutdown-to-flush →
   headless reuse) — a strong point in its favour. Still to test: how long the
   Ericsson session cookie lives, and whether headless reuse survives
   Conditional Access re-checks. Attach mode is proven; persisted-headless is
   the one to validate next.
5. **Session-expiry UX.** Because the cookie is the sole credential and it
   expires, the skill must detect expiry (the `probe` auth check already does)
   and prompt a graceful re-`signin` rather than failing opaquely. Design this
   in from the start, not as an afterthought.
6. **Scale** — largest space in scope (sizes CQL cap / concurrency need).

## 9. Landing procedure

1. Rebuild scripts from this plan; fix the `ac:task-list` bug; add references.
2. Verify end-to-end against a small real space (`probe → signin → enumerate →
   sync`), both engines.
3. Create on **`base`** (worktree, isolated from in-flight branches).
4. Add to `capabilities/ericsson.json` `skills[]`.
5. Merge `base` → every brand from `brands/*.json`; `generate <brand> --check`
   (8/8) per brand.
6. Consider `curation.skills.disabledByDefault` until first sign-in is done
   (gateway-toolcall-parity precedent).

No emitter touched; all files new → cannot conflict with upstream.

## 10. Verification criteria

- `INDEX.md` counts match the JSON from `sync`.
- Every `pages[].path` in the JSON exists on disk.
- One page's frontmatter `version` matches Confluence.
- A second `sync` with no upstream change reports `fetched: 0`.
- `storage_to_md` round-trips table + code macro + nested list + attachment
  image without loss (and the task-list bug is fixed).
- Both engines produce byte-identical Markdown for the same page.
