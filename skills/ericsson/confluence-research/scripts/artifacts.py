"""Artifact layout, frontmatter and the incremental-sync manifest.

Layout is deliberately STABLE (not run-timestamped):

    <root>/confluence/<SPACE>/
    ├── INDEX.md            inventory table, regenerated each sync
    ├── SUMMARY.md          model-written synthesis (never machine-overwritten)
    ├── .manifest.json      page_id -> {version, sha256, path}
    ├── pages/<slug>-<id>.md
    └── attachments/<id>/<filename>

Timestamped run directories were the first instinct but defeat the point: with
stable paths a re-sync is a `git diff` of what changed in the space, and
unchanged pages cost zero fetches. The manifest carries history instead of the
directory name. This deviates from opportunity-visuals' "never overwrite a run
directory" -- that skill is one-shot; this maintains a mirror. SUMMARY.md is
the one file we never machine-write.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_NAME = ".manifest.json"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(title: str, limit: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", (title or "untitled").lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return (s[:limit].rstrip("-") or "untitled")


def content_sha(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:16]


class SpaceStore:
    def __init__(self, root: Path, space_key: str) -> None:
        self.base = Path(root) / "confluence" / (space_key or "UNSCOPED")
        self.pages = self.base / "pages"
        self.attachments = self.base / "attachments"
        self.manifest_path = self.base / MANIFEST_NAME

    def ensure(self) -> None:
        self.pages.mkdir(parents=True, exist_ok=True)
        self.attachments.mkdir(parents=True, exist_ok=True)

    # ── manifest ─────────────────────────────────────────────────────────────

    def load_manifest(self) -> dict:
        if not self.manifest_path.exists():
            return {"version": 1, "pages": {}}
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            data.setdefault("pages", {})
            return data
        except Exception:
            return {"version": 1, "pages": {}}

    def save_manifest(self, manifest: dict) -> None:
        manifest["updated_at"] = utcnow()
        self.manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def needs_fetch(manifest: dict, item: dict) -> bool:
        """True when the remote version exceeds what we have on disk.

        The whole incremental story: enumeration returns version numbers
        cheaply, so a re-sync of a 400-page space costs one CQL walk and only
        as many fetches as pages that actually changed.
        """
        rec = (manifest.get("pages") or {}).get(str(item["id"]))
        if not rec:
            return True
        return int(item.get("version") or 0) > int(rec.get("version") or 0)

    # ── writing ──────────────────────────────────────────────────────────────

    def page_path(self, page_id: str, title: str) -> Path:
        return self.pages / f"{slugify(title)}-{page_id}.md"

    def write_page(self, record: dict, markdown: str) -> Path:
        path = self.page_path(record["id"], record["title"])
        path.write_text(_frontmatter(record) + "\n" + markdown, encoding="utf-8")
        return path

    def write_attachment(self, page_id: str, filename: str, b64: str) -> Path:
        d = self.attachments / str(page_id)
        d.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w.\-]+", "_", filename) or "attachment.bin"
        path = d / safe
        path.write_bytes(base64.b64decode(b64))
        return path

    # ── index ────────────────────────────────────────────────────────────────

    def write_index(self, space_key: str, items: list[dict], stats: dict) -> Path:
        lines = [
            f"# Confluence mirror — {space_key}",
            "",
            f"_Last synced {utcnow()} · {len(items)} pages_",
            "",
            f"- fetched this run: **{stats.get('fetched', 0)}**",
            f"- unchanged (skipped): **{stats.get('skipped', 0)}**",
            f"- attachments downloaded: **{stats.get('attachments', 0)}**",
            f"- warnings: **{len(stats.get('warnings', []))}**",
            "",
            "| Page | Ver | Updated | Local |",
            "| --- | --- | --- | --- |",
        ]
        for it in sorted(items, key=lambda x: (x.get("title") or "").lower()):
            local = it.get("local_path") or ""
            try:
                rel = Path(local).relative_to(self.base) if local else ""
            except ValueError:
                rel = local
            title = (it.get("title") or "").replace("|", "\\|")
            link = f"[{title}]({it.get('url')})" if it.get("url") else title
            lines.append(
                f"| {link} | {it.get('version', '')} | "
                f"{(it.get('version_when') or '')[:10]} | "
                f"{f'[md]({rel})' if rel else '—'} |")
        warnings = stats.get("warnings") or []
        if warnings:
            lines += ["", "## Warnings", ""] + [f"- {w}" for w in warnings]
        path = self.base / "INDEX.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


def _yaml_scalar(v) -> str:
    if v is None:
        return '""'
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _frontmatter(rec: dict) -> str:
    """YAML frontmatter. `version` + `content_sha256` make re-sync cheap."""
    lines = ["---", "source: confluence"]
    for key in ("id", "title", "space_key", "space_name", "type", "status",
                "version", "version_by", "version_when", "url"):
        lines.append(f"{key}: {_yaml_scalar(rec.get(key, ''))}")
    lines.append(f"captured_at: {_yaml_scalar(rec.get('captured_at') or utcnow())}")
    lines.append(f"content_sha256: {_yaml_scalar(rec.get('content_sha256', ''))}")

    labels = rec.get("labels") or []
    lines.append("labels: [" + ", ".join(_yaml_scalar(x) for x in labels) + "]")

    ancestors = rec.get("ancestors") or []
    if ancestors:
        lines.append("ancestors:")
        for a in ancestors:
            lines.append(f"  - id: {_yaml_scalar(a.get('id', ''))}")
            lines.append(f"    title: {_yaml_scalar(a.get('title', ''))}")
    else:
        lines.append("ancestors: []")

    atts = rec.get("attachments") or []
    if atts:
        lines.append("attachments:")
        for a in atts:
            lines.append(f"  - title: {_yaml_scalar(a.get('title', ''))}")
            lines.append(f"    media_type: {_yaml_scalar(a.get('media_type', ''))}")
            lines.append(f"    file_size: {_yaml_scalar(a.get('file_size', 0))}")
            lines.append(f"    local: {_yaml_scalar(a.get('local', ''))}")
    else:
        lines.append("attachments: []")

    lines.append("---")
    return "\n".join(lines) + "\n"
