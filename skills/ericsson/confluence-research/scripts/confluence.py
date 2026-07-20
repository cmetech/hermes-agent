#!/usr/bin/env python3
"""Confluence research CLI — authenticated-browser fetch, Markdown mirror.

Subcommands follow the read-only-before-write discipline of opportunity-visuals
(inspect -> analyze -> prepare):

    signin      one-time interactive login; persists the session
    probe       auth + deployment check (Cloud vs Server/DC). Read-only.
    enumerate   CQL inventory of a space/subtree. Read-only, no page bodies.
    sync        fetch changed pages -> Markdown + attachments + INDEX.md
    fetch       single page by URL/id -> JSON on stdout (writes nothing)

Engine: --engine {auto,playwright,agent-browser}. Both drive the enrolled
corporate browser over CDP; only the eval mechanism differs.

stdout is pure JSON; all diagnostics go to stderr, so any caller can parse it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import artifacts as A                       # noqa: E402
import confluence_api as api                # noqa: E402
from backends import log, select_backend, default_profile_dir  # noqa: E402
from storage_to_md import storage_to_markdown  # noqa: E402

DEFAULT_MAX_ATTACH_MB = 25


def _default_root() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "research"


def _emit(payload) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _fail(msg: str, code: int = 1):
    _emit({"ok": False, "error": msg})
    sys.exit(code)


def _record_from_content(c: dict, attachments: list[dict]) -> dict:
    space = c.get("space") or {}
    version = c.get("version") or {}
    labels = ((c.get("metadata") or {}).get("labels") or {}).get("results", [])
    links = c.get("_links") or {}
    base_ui = links.get("base") or ""
    webui = links.get("webui") or ""
    return {
        "id": c.get("id", ""),
        "title": c.get("title", ""),
        "type": c.get("type", ""),
        "status": c.get("status", ""),
        "space_key": space.get("key", ""),
        "space_name": space.get("name", ""),
        "version": version.get("number", 0),
        "version_by": (version.get("by") or {}).get("displayName", ""),
        "version_when": version.get("when", ""),
        "labels": [l.get("name", "") for l in labels],
        "ancestors": [{"id": a.get("id", ""), "title": a.get("title", "")}
                      for a in (c.get("ancestors") or [])],
        "url": (base_ui + webui) if webui else "",
        "storage_html": ((c.get("body") or {}).get("storage") or {}).get("value", ""),
        "attachments": attachments,
        "captured_at": A.utcnow(),
    }


def _make_backend(args, headless: bool):
    backend = select_backend(args.engine, default_profile_dir())
    backend.start(headless=headless)
    return backend


def _resolve_cql(args, backend, api_base: str) -> tuple[str, str]:
    if args.cql:
        return args.cql, (args.space or "")
    if args.descendants_of:
        return api.cql_for_descendants(args.descendants_of), (args.space or "")
    space = args.space or (api.page_info(backend) or {}).get("spaceKey") or ""
    if not space:
        _fail("Could not determine a space. Pass --space, --cql or --descendants-of.")
    return api.cql_for_space(space, args.include_blogposts), space


# ── subcommands ──────────────────────────────────────────────────────────────

def cmd_signin(args) -> None:
    api_base = api.derive_api_base(args.url, args.api_base)
    backend = _make_backend(args, headless=False)
    try:
        backend.navigate(args.url)
        if api.is_authenticated(backend, api_base):
            _emit({"ok": True, "already_signed_in": True, "api_base": api_base})
            return
        log("Sign in to Confluence in the browser window that opened ...")
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            if api.is_authenticated(backend, api_base):
                log("Signed in.")
                # Shut the browser down so cookies flush to disk before a later
                # headless run reuses the profile. Load-bearing.
                backend.shutdown()
                _emit({"ok": True, "already_signed_in": False, "api_base": api_base})
                return
            time.sleep(2)
        _fail(f"Sign-in not completed within {args.timeout}s.", 2)
    finally:
        backend.shutdown()


def cmd_probe(args) -> None:
    api_base = api.derive_api_base(args.url, args.api_base)
    backend = _make_backend(args, headless=True)
    try:
        backend.navigate(args.url)
        authed = api.is_authenticated(backend, api_base)
        info = api.page_info(backend)
        _emit({
            "ok": authed,
            "engine": backend.name,
            "authenticated": authed,
            "api_base": api_base,
            "deployment": "cloud" if "/wiki/rest/api" in api_base else "server_dc",
            "page_info": info,
            "hint": None if authed else "Run `confluence.py signin <url>` once.",
        })
        if not authed:
            sys.exit(3)
    finally:
        backend.shutdown()


def cmd_enumerate(args) -> None:
    api_base = api.derive_api_base(args.url, args.api_base)
    backend = _make_backend(args, headless=True)
    try:
        backend.navigate(args.url)
        if not api.is_authenticated(backend, api_base):
            _fail("Not signed in. Run `confluence.py signin <url>` once.", 3)
        cql, space = _resolve_cql(args, backend, api_base)
        log(f"CQL: {cql}")
        items = api.enumerate_content(backend, api_base, cql, limit=args.limit)
        log(f"{len(items)} item(s).")
        _emit({"ok": True, "engine": backend.name, "space_key": space,
               "cql": cql, "count": len(items), "items": items})
    finally:
        backend.shutdown()


def cmd_sync(args) -> None:
    api_base = api.derive_api_base(args.url, args.api_base)
    root = Path(args.out) if args.out else _default_root()
    max_bytes = int(args.max_attachment_mb * 1024 * 1024)

    backend = _make_backend(args, headless=True)
    try:
        backend.navigate(args.url)
        if not api.is_authenticated(backend, api_base):
            _fail("Not signed in. Run `confluence.py signin <url>` once.", 3)

        cql, space = _resolve_cql(args, backend, api_base)
        log(f"CQL: {cql}")
        items = api.enumerate_content(backend, api_base, cql, limit=args.limit)
        if args.max_pages:
            items = items[: args.max_pages]
        log(f"{len(items)} page(s) in scope.")

        space = space or (items[0].get("space_key") if items else "UNSCOPED")
        store = A.SpaceStore(root, space)
        store.ensure()
        manifest = store.load_manifest()
        stats = {"fetched": 0, "skipped": 0, "attachments": 0, "warnings": []}

        for n, item in enumerate(items, 1):
            pid = str(item["id"])
            title = item.get("title", "")

            if not args.force and not store.needs_fetch(manifest, item):
                stats["skipped"] += 1
                item["local_path"] = manifest["pages"].get(pid, {}).get("path", "")
                continue

            log(f"[{n}/{len(items)}] {title}")
            try:
                content = api.fetch_page_by_id(backend, api_base, pid)
            except Exception as e:
                stats["warnings"].append(f"{title} ({pid}): fetch failed — {e}")
                continue
            if not content:
                stats["warnings"].append(f"{title} ({pid}): not found / no access")
                continue

            atts: list[dict] = []
            if not args.no_attachments:
                try:
                    atts = api.fetch_attachments(backend, api_base, pid)
                except Exception as e:
                    stats["warnings"].append(f"{title} ({pid}): attachment list failed — {e}")

            record = _record_from_content(content, atts)
            markdown = storage_to_markdown(
                record["storage_html"], attachment_dir=f"../attachments/{pid}")
            if not markdown.strip():
                stats["warnings"].append(f"{title} ({pid}): empty body after conversion")

            if atts and args.download_attachments:
                for a in atts:
                    if not a.get("download"):
                        continue
                    try:
                        res = api.download_attachment(backend, a["download"], max_bytes)
                    except Exception as e:
                        stats["warnings"].append(f"{title}: {a.get('title')} download failed — {e}")
                        continue
                    if res.get("skipped"):
                        stats["warnings"].append(
                            f"{title}: {a.get('title')} skipped "
                            f"({res.get('size', 0) / 1e6:.1f} MB > {args.max_attachment_mb} MB)")
                        continue
                    p = store.write_attachment(pid, a.get("title", "file"), res["b64"])
                    a["local"] = str(p.relative_to(store.base))
                    stats["attachments"] += 1

            record["content_sha256"] = A.content_sha(markdown)
            record["attachments"] = atts
            out_path = store.write_page(record, markdown)
            item["local_path"] = str(out_path)
            manifest["pages"][pid] = {
                "version": record["version"],
                "sha256": record["content_sha256"],
                "path": str(out_path),
                "title": title,
                "synced_at": A.utcnow(),
            }
            stats["fetched"] += 1

        store.save_manifest(manifest)
        index = store.write_index(space, items, stats)
        _emit({
            "ok": True, "engine": backend.name, "space_key": space,
            "root": str(store.base), "index": str(index),
            "counts": {k: v for k, v in stats.items() if k != "warnings"},
            "warnings": stats["warnings"],
            "pages": [{"id": i["id"], "title": i.get("title", ""),
                       "version": i.get("version"), "path": i.get("local_path", "")}
                      for i in items],
        })
    finally:
        backend.shutdown()


def cmd_fetch(args) -> None:
    api_base = api.derive_api_base(args.url, args.api_base)
    backend = _make_backend(args, headless=True)
    try:
        backend.navigate(args.url)
        if not api.is_authenticated(backend, api_base):
            _fail("Not signed in. Run `confluence.py signin <url>` once.", 3)
        info = api.page_info(backend)
        pid = args.page_id or info.get("pageId")
        if pid:
            content = api.fetch_page_by_id(backend, api_base, str(pid))
        elif info.get("spaceKey") and info.get("title"):
            content = api.fetch_page_by_title(backend, api_base, info["spaceKey"], info["title"])
        else:
            _fail("Could not resolve a page id, or a space+title, from that URL.")
        if not content:
            _fail("Page not found or no access.")

        atts = [] if args.no_attachments else api.fetch_attachments(backend, api_base, content["id"])
        record = _record_from_content(content, atts)
        markdown = storage_to_markdown(record["storage_html"])
        record["content_sha256"] = A.content_sha(markdown)
        record["markdown"] = markdown
        if not args.keep_html:
            record.pop("storage_html", None)
        _emit({"ok": True, "engine": backend.name, "page": record})
    finally:
        backend.shutdown()


# ── argparse ─────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="confluence", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    # Shared options, accepted both before AND after the subcommand.
    eng = argparse.ArgumentParser(add_help=False)
    # SUPPRESS default so it only overrides the top-level value when actually
    # passed after the subcommand (else argparse would reset it to the default).
    eng.add_argument("--engine", choices=["auto", "playwright", "agent-browser"],
                     default=argparse.SUPPRESS,
                     help="Session/eval engine (default: auto).")
    p.add_argument("--engine", choices=["auto", "playwright", "agent-browser"],
                   default="auto", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("url", help="Any Confluence URL on the target host.")
        sp.add_argument("--api-base", help="Override REST root (odd Server/DC setups).")
        return sp

    sp = common(sub.add_parser("signin", help="One-time interactive sign-in.", parents=[eng]))
    sp.add_argument("--timeout", type=int, default=300)
    sp.set_defaults(func=cmd_signin)

    sp = common(sub.add_parser("probe", help="Auth + deployment check (read-only).", parents=[eng]))
    sp.set_defaults(func=cmd_probe)

    def scope(sp):
        sp.add_argument("--space", help="Space key. Defaults to the current tab's space.")
        sp.add_argument("--descendants-of", help="Page id: mirror that subtree only.")
        sp.add_argument("--cql", help="Raw CQL. Overrides --space/--descendants-of.")
        sp.add_argument("--include-blogposts", action="store_true")
        sp.add_argument("--limit", type=int, default=100, help="CQL page size.")
        return sp

    sp = scope(common(sub.add_parser("enumerate", help="Inventory only (read-only).", parents=[eng])))
    sp.set_defaults(func=cmd_enumerate)

    sp = scope(common(sub.add_parser("sync", help="Fetch changed pages -> Markdown.", parents=[eng])))
    sp.add_argument("--out", help="Artifact root. Default $HERMES_HOME/research.")
    sp.add_argument("--force", action="store_true", help="Refetch even if unchanged.")
    sp.add_argument("--max-pages", type=int, default=0, help="0 = no cap.")
    sp.add_argument("--no-attachments", action="store_true", help="Skip listing.")
    sp.add_argument("--download-attachments", action="store_true",
                    help="Also download bytes (default: list only).")
    sp.add_argument("--max-attachment-mb", type=float, default=DEFAULT_MAX_ATTACH_MB)
    sp.set_defaults(func=cmd_sync)

    sp = common(sub.add_parser("fetch", help="Single page -> JSON on stdout.", parents=[eng]))
    sp.add_argument("--page-id")
    sp.add_argument("--no-attachments", action="store_true")
    sp.add_argument("--keep-html", action="store_true", help="Include raw storage XHTML.")
    sp.set_defaults(func=cmd_fetch)
    return p


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except RuntimeError as e:
        _fail(str(e), 2)
    except KeyboardInterrupt:
        _fail("Interrupted.", 130)


if __name__ == "__main__":
    main()
