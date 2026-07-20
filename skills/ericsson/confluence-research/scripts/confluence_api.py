"""Confluence REST calls as injected-JS builders + orchestration.

Every request runs as an async JS function issued with
``credentials: "same-origin"`` inside the authenticated page, so the browser
attaches the Cloudflare Access cookie, the SSO session and any mTLS client
certificate. Nothing here does HTTP from Python -- that is the whole point,
and it is why this survives corporate auth that a plain requests/httpx client
cannot. Verified 2026-07-19 against eteamspace.internal.ericsson.com.

Engine-agnostic: each builder returns a string of the form
``async () => { ... }``. A Backend (see backends.py) evaluates it and returns
the resolved value. The Playwright backend passes it to ``page.evaluate``
(auto-invoked); the agent-browser backend wraps it as ``(<fn>)()``.

Original ``_PREAMBLE`` / page-info / by-id / by-title fetchers are from
loop_24/utils/confluence_page.py, kept close to verbatim. New: CQL enumeration
(space-wide + descendants) with cursor pagination, and attachment download.
"""

from __future__ import annotations

from urllib.parse import quote, urlparse

# Full expand for a single page fetch.
EXPAND_PAGE = "body.storage,version,space,ancestors,metadata.labels,history.lastUpdated"
# Lightweight expand for enumeration -- only enough to decide if a page changed.
EXPAND_LIST = "version,space,ancestors"

_PREAMBLE = """
  const _H = { "Accept": "application/json" };
  async function _get(url) {
    const r = await fetch(url, { headers: _H, credentials: "same-origin" });
    if (!r.ok) throw new Error(url + " => " + r.status + " " + r.statusText);
    return r.json();
  }
"""

# Resolve page id / space / title from whatever the current tab shows.
# Four fallbacks, because Confluence exposes this differently across
# Cloud, Server/DC and the legacy /display/ URL form.
PAGE_INFO_JS = """
async () => {
  const loc = window.location;
  const path = loc.pathname;
  let pageId = null, spaceKey = null, title = null;

  let m = path.match(/\\/pages\\/(\\d+)/) || (loc.search.match(/[?&]pageId=(\\d+)/));
  if (m) pageId = m[1];

  try {
    if (window.AJS && AJS.params) {
      pageId   = pageId   || AJS.params.pageId   || null;
      spaceKey = spaceKey || AJS.params.spaceKey || null;
      title    = title    || AJS.params.pageTitle || AJS.params.contentTitle || null;
    }
  } catch (e) {}

  const meta = (n) => {
    const el = document.querySelector('meta[name="' + n + '"]');
    return el ? el.getAttribute('content') : null;
  };
  pageId   = pageId   || meta('ajs-page-id')   || meta('confluence-page-id');
  spaceKey = spaceKey || meta('ajs-space-key');

  if (!spaceKey || !title) {
    const dm = path.match(/\\/display\\/([^/]+)\\/([^/?#]+)/);
    if (dm) {
      spaceKey = spaceKey || decodeURIComponent(dm[1]);
      title    = title    || decodeURIComponent(dm[2].replace(/\\+/g, ' '));
    }
  }
  return { pageId, spaceKey, title, href: loc.href, origin: loc.origin };
}
"""


def derive_api_base(url: str, override: str | None = None) -> str:
    """Cloud lives under /wiki/rest/api; Server/DC under /rest/api."""
    if override:
        return override.rstrip("/")
    parts = urlparse(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    path = parts.path.rstrip("/")
    if "/wiki/" in parts.path or path.endswith("/wiki"):
        return f"{origin}/wiki/rest/api"
    return f"{origin}/rest/api"


def probe_js(api_base: str) -> str:
    """Cheap auth liveness check. Also reveals Cloud vs DC by what it returns."""
    return f"""
async () => {{
  {_PREAMBLE}
  try {{
    const d = await _get("{api_base}/space?limit=1");
    return {{ ok: true, size: (d.results || []).length }};
  }} catch (e) {{
    return {{ ok: false, error: String(e) }};
  }}
}}
"""


def cql_for_space(space_key: str, include_blogposts: bool = False) -> str:
    types = "type in (page, blogpost)" if include_blogposts else "type = page"
    return f'space = "{space_key}" AND {types} ORDER BY title'


def cql_for_descendants(page_id: str) -> str:
    return f"ancestor = {page_id} AND type = page ORDER BY title"


def _cql_page_js(api_base: str, cql: str, cursor: str | None, limit: int) -> str:
    """One page of a CQL search.

    Confluence returns ``_links.next`` for the following page; we hand it back
    rather than computing offsets -- that is what DC expects and what Cloud
    requires (its cursor is opaque).
    """
    if cursor:
        target = f'new URL({_js_str(cursor)}, location.origin).href'
    else:
        q = f"{api_base}/content/search?cql={quote(cql)}&limit={limit}&expand={EXPAND_LIST}"
        target = _js_str(q)
    return f"""
async () => {{
  {_PREAMBLE}
  const d = await _get({target});
  return {{
    results: (d.results || []).map(r => ({{
      id: r.id,
      title: r.title,
      type: r.type,
      status: r.status,
      space_key: (r.space || {{}}).key || "",
      version: ((r.version || {{}}).number) || 0,
      version_when: ((r.version || {{}}).when) || "",
      version_by: (((r.version || {{}}).by) || {{}}).displayName || "",
      ancestors: (r.ancestors || []).map(a => ({{ id: a.id, title: a.title }})),
      webui: ((r._links || {{}}).webui) || ""
    }})),
    next: ((d._links || {{}}).next) || null,
    base: ((d._links || {{}}).base) || location.origin
  }};
}}
"""


def fetch_by_id_js(api_base: str, page_id: str) -> str:
    return f"""
async () => {{
  {_PREAMBLE}
  return await _get("{api_base}/content/{page_id}?expand={EXPAND_PAGE}");
}}
"""


def fetch_by_title_js(api_base: str, space: str, title: str) -> str:
    q = (f"{api_base}/content?spaceKey={quote(space)}&title={quote(title)}"
         f"&expand={EXPAND_PAGE}&limit=1")
    return f"""
async () => {{
  {_PREAMBLE}
  const d = await _get({_js_str(q)});
  return (d.results && d.results[0]) || null;
}}
"""


def attachments_js(api_base: str, page_id: str) -> str:
    return f"""
async () => {{
  {_PREAMBLE}
  const d = await _get("{api_base}/content/{page_id}/child/attachment?expand=version&limit=200");
  return (d.results || []).map(a => ({{
    id: a.id,
    title: a.title || "",
    media_type: ((a.extensions || {{}}).mediaType) || "",
    file_size: ((a.extensions || {{}}).fileSize) || 0,
    comment: ((a.extensions || {{}}).comment) || "",
    download: ((a._links || {{}}).download) || "",
    version: ((a.version || {{}}).number) || 0
  }}));
}}
"""


def download_js(download_url: str, max_bytes: int) -> str:
    """Pull attachment bytes as base64, in-page.

    Why base64 through eval rather than an out-of-page HTTP client: an
    out-of-page request uses a different network stack that does NOT carry the
    browser's mTLS client certificate. Staying in-page keeps the guarantee
    that anything the user can download, we can. Cost is ~33% transfer
    overhead, which is why max_bytes exists.
    """
    return f"""
async () => {{
  const url = new URL({_js_str(download_url)}, location.origin).href;
  const r = await fetch(url, {{ credentials: "same-origin" }});
  if (!r.ok) throw new Error("download => " + r.status);
  const buf = await r.arrayBuffer();
  if (buf.byteLength > {max_bytes}) {{
    return {{ skipped: true, size: buf.byteLength }};
  }}
  let bin = "";
  const bytes = new Uint8Array(buf);
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) {{
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  }}
  return {{ skipped: false, size: buf.byteLength, b64: btoa(bin) }};
}}
"""


def _js_str(s: str) -> str:
    """Encode a Python string as a safe JS double-quoted literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ── orchestration (engine-agnostic; `backend` implements eval()) ─────────────

def is_authenticated(backend, api_base: str) -> bool:
    try:
        res = backend.eval(probe_js(api_base))
        return bool(res and res.get("ok"))
    except Exception:
        return False


def page_info(backend) -> dict:
    try:
        return backend.eval(PAGE_INFO_JS) or {}
    except Exception:
        return {}


def enumerate_content(backend, api_base: str, cql: str, limit: int = 100,
                      max_pages: int = 5000) -> list[dict]:
    """Walk a CQL result set to completion. Returns a flat inventory.

    The capability the original worker lacked entirely: it could only take one
    URL/id or a hand-built CSV. CQL gets a whole space, or a subtree, in a
    handful of round trips.
    """
    out: list[dict] = []
    cursor: str | None = None
    while True:
        chunk = backend.eval(_cql_page_js(api_base, cql, cursor, limit))
        results = (chunk or {}).get("results") or []
        base = (chunk or {}).get("base") or ""
        for r in results:
            r["url"] = (base + r["webui"]) if r.get("webui") else ""
        out.extend(results)
        cursor = (chunk or {}).get("next")
        if not cursor or not results or len(out) >= max_pages:
            break
    return out


def fetch_page_by_id(backend, api_base: str, page_id: str) -> dict | None:
    return backend.eval(fetch_by_id_js(api_base, str(page_id)))


def fetch_page_by_title(backend, api_base: str, space: str, title: str) -> dict | None:
    return backend.eval(fetch_by_title_js(api_base, space, title))


def fetch_attachments(backend, api_base: str, page_id: str) -> list[dict]:
    return backend.eval(attachments_js(api_base, str(page_id))) or []


def download_attachment(backend, download_url: str, max_bytes: int) -> dict:
    return backend.eval(download_js(download_url, max_bytes)) or {}
