# Confluence REST + engine notes

Reference for the `confluence-research` skill. The mechanism: injected
same-origin `fetch()` inside an authenticated browser tab. The browser carries
Cloudflare Access, SSO and mTLS; Python never does HTTP.

## Deployment shapes

| | Server / Data Center | Cloud |
|---|---|---|
| REST root | `https://HOST/rest/api` | `https://HOST/wiki/rest/api` |
| URL marker | no `/wiki/` in path | `/wiki/` in path |
| Page URL | `/pages/<id>/Title` or `/display/SPACE/Title` | `/wiki/spaces/SPACE/pages/<id>/Title` |

`derive_api_base()` picks the root from the URL; `probe` reports which.

**Ericsson (`eteamspace.internal.ericsson.com`) is Server/DC** — `/rest/api`.
Verified 2026-07-19: DevTools and agent-browser both returned authenticated
JSON from `/rest/api/space?limit=1`.

## Endpoints used

- Auth probe: `GET /space?limit=1`
- CQL search: `GET /content/search?cql=<cql>&limit=<n>&expand=version,space,ancestors`
  - paginate by following `_links.next` verbatim (opaque on Cloud)
- Page body: `GET /content/<id>?expand=body.storage,version,space,ancestors,metadata.labels,history.lastUpdated`
- Attachment list: `GET /content/<id>/child/attachment?expand=version&limit=200`
- Attachment bytes: `GET <_links.download>` (relative to origin), in-page → base64

## CQL cookbook

| Goal | CQL |
|---|---|
| Whole space (pages) | `space = "KEY" AND type = page ORDER BY title` |
| Space + blog posts | `space = "KEY" AND type in (page, blogpost) ORDER BY title` |
| A page's subtree | `ancestor = <id> AND type = page ORDER BY title` |
| Recently changed | `space = "KEY" AND lastmodified >= now("-7d")` |
| By label | `space = "KEY" AND label = "runbook"` |

Pass raw CQL with `--cql` to override `--space`/`--descendants-of`.

## Why `body.storage`, not `body.view`

Storage is clean, stable XHTML authored by Confluence, with macros intact
(`<ac:structured-macro>` for code/callouts, `<ac:task-list>` for tasks). View
is theme-rendered, wrapper-heavy and lossy. `storage_to_md.py` converts storage.

## Engines

Both drive the enrolled corporate browser over CDP (port 9333 by default,
`CONFLUENCE_CDP_PORT` to change). The skill launches Edge itself, or reuses an
Edge you already started with `--remote-debugging-port=9333`. It is deliberately
NOT 9222: that is `/browser connect`'s default port, and an enrolled browser
sitting there can be adopted as the process-global CDP endpoint for every page.

- **playwright** — `connect_over_cdp` once, hold `page`, `page.evaluate(fn)`
  (auto-invokes the `async () => {}` string). No daemon. Default.
- **agent-browser** — `agent-browser --cdp 9333 eval -b <base64>`; the JS is
  wrapped as `(fn)()` and base64-encoded. **Never `--stdin`** — PowerShell
  stdin piping to the CLI hangs. The backend runs `close --all` on start to
  clear a wedged daemon (the Windows failure mode observed in testing).

## mTLS / SSO gotchas

- Only the **enrolled** browser (corporate Edge) presents the mTLS client cert
  silently and passes device-compliance SSO. A downloaded Chrome for Testing
  will fail — that is why `resolve_browser()` prefers Edge and why the skill
  never uses agent-browser's bundled Chrome.
- `credentials: "same-origin"` only carries cookies when the fetch runs from a
  page already on that origin, so every subcommand navigates to the target host
  before evaluating.
- The session cookie is the sole credential and expires. `probe` /
  `is_authenticated` detect it; re-run `signin`.

## Unattended runs (open item)

`signin` launches visible Edge, waits for login, then shuts Edge down so cookies
flush to the profile dir; later `sync` relaunches headless against the same
profile. Whether headless reuse survives Conditional Access re-checks, and how
long the Ericsson cookie lives, are not yet measured — validate before relying
on scheduled syncs. Attach mode (a human keeps Edge open) is proven.
