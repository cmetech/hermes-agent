"""Confluence storage format (XHTML) -> Markdown.

Replaces the original worker's ``html_to_text()``, which flattened
``body.view`` into undifferentiated plain text -- losing headings, tables,
links, code blocks and list structure. That loss matters twice: it makes the
artifact worse to read, and a worse input to summarize, because the model can
no longer see document structure.

We convert ``body.storage`` rather than ``body.view``:
  * storage is clean, stable XHTML authored by Confluence itself
  * view is post-rendered, theme-dependent, full of layout wrappers
  * storage keeps macros as ``<ac:structured-macro>``, so code blocks and
    callouts survive as semantic units instead of styled divs

Stdlib only (HTMLParser). No lxml/bs4 dependency.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

_HEADINGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}
_CALLOUTS = {"info", "note", "warning", "tip", "panel"}

# Confluence task metadata whose text must never reach the output (dropped
# entirely). ``ac:task-status`` is handled separately -- its text IS captured,
# into the checkbox state, but must not leak into the body.
_TASK_META_DROP = {"ac:task-id", "ac:task-uuid"}


class _StorageToMarkdown(HTMLParser):
    def __init__(self, attachment_dir: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._attachment_dir = attachment_dir.rstrip("/")

        self._list_stack: list[str] = []          # 'ul' | 'ol'
        self._ol_counters: list[int] = []
        self._skip_depth = 0                       # inside <script>/<style>
        self._link_href: str | None = None
        self._link_buf: list[str] | None = None

        # table state
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._row_is_header = False
        self._header_done = False

        # macro state
        self._macro_stack: list[str] = []
        self._macro_param: str | None = None
        self._macro_params: dict[str, str] = {}
        self._in_plain_text = False
        self._plain_buf: list[str] = []

        # task state
        self._task_meta_drop = 0                   # inside task-id/uuid (drop text)
        self._in_task_status = False               # inside task-status (capture text)
        self._pending_task_state: str | None = None

    # ── helpers ──────────────────────────────────────────────────────────────

    def _emit(self, text: str) -> None:
        """Route text to whatever container is currently open."""
        if self._cell is not None:
            self._cell.append(text)
        elif self._link_buf is not None:
            self._link_buf.append(text)
        else:
            self.out.append(text)

    def _nl(self, n: int = 1) -> None:
        if self._cell is None:
            self.out.append("\n" * n)

    @staticmethod
    def _attr(attrs, name: str) -> str | None:
        for k, v in attrs:
            if k == name:
                return v
        return None

    def _list_prefix(self) -> str:
        indent = "  " * (len(self._list_stack) - 1) if self._list_stack else ""
        if self._list_stack and self._list_stack[-1] == "ol":
            self._ol_counters[-1] += 1
            return f"{indent}{self._ol_counters[-1]}. "
        return f"{indent}- "

    # ── tags ─────────────────────────────────────────────────────────────────

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        # --- Confluence macros -------------------------------------------------
        if tag == "ac:structured-macro":
            name = (self._attr(attrs, "ac:name") or "").lower()
            self._macro_stack.append(name)
            self._macro_params = {}
            return
        if tag == "ac:parameter":
            self._macro_param = self._attr(attrs, "ac:name") or ""
            return
        if tag in ("ac:plain-text-body", "ac:plain-text-link-body"):
            self._in_plain_text = True
            self._plain_buf = []
            return

        # --- Confluence tasks --------------------------------------------------
        if tag == "ac:task-list":
            self._list_stack.append("ul")
            self._ol_counters.append(0)
            if len(self._list_stack) == 1:
                self._nl(2)
            return
        if tag == "ac:task":
            self._pending_task_state = None
            return
        if tag in _TASK_META_DROP:
            self._task_meta_drop += 1
            return
        if tag == "ac:task-status":
            self._in_task_status = True
            self._pending_task_state = ""
            return
        if tag == "ac:task-body":
            self._nl(1)
            self.out.append(self._list_prefix())
            box = "x" if (self._pending_task_state or "").lower() == "complete" else " "
            self.out.append(f"[{box}] ")
            return

        # --- Confluence resource refs -----------------------------------------
        if tag == "ac:image":
            return
        if tag == "ri:attachment":
            fn = self._attr(attrs, "ri:filename") or ""
            if fn:
                path = f"{self._attachment_dir}/{fn}" if self._attachment_dir else fn
                self._emit(f"![{fn}]({path})")
            return
        if tag == "ri:page":
            title = self._attr(attrs, "ri:content-title") or ""
            if title:
                anchor = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                self._emit(f"[{title}](#{anchor})")
            return
        if tag == "ri:url":
            href = self._attr(attrs, "ri:value") or ""
            if href:
                self._emit(f"<{href}>")
            return

        # --- structural HTML ---------------------------------------------------
        if tag in _HEADINGS:
            self._nl(2)
            self.out.append(_HEADINGS[tag] + " ")
        elif tag == "p":
            self._nl(2)
        elif tag == "br":
            self._emit("  \n")
        elif tag in ("ul", "ol"):
            self._list_stack.append(tag)
            self._ol_counters.append(0)
            if len(self._list_stack) == 1:
                self._nl(2)
        elif tag == "li":
            self._nl(1)
            self.out.append(self._list_prefix())
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag in ("em", "i"):
            self._emit("*")
        elif tag in ("code", "tt"):
            self._emit("`")
        elif tag == "blockquote":
            self._nl(2)
            self.out.append("> ")
        elif tag == "hr":
            self._nl(2)
            self.out.append("---")
            self._nl(2)
        elif tag == "a":
            self._link_href = self._attr(attrs, "href")
            self._link_buf = []
        elif tag == "table":
            self._table = []
            self._header_done = False
        elif tag == "tr":
            self._row = []
            self._row_is_header = False
        elif tag in ("td", "th"):
            self._cell = []
            if tag == "th":
                self._row_is_header = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return

        # --- macros ------------------------------------------------------------
        if tag == "ac:parameter":
            self._macro_param = None
            return
        if tag in ("ac:plain-text-body", "ac:plain-text-link-body"):
            self._in_plain_text = False
            body = "".join(self._plain_buf).strip("\n")
            macro = self._macro_stack[-1] if self._macro_stack else ""
            if macro in ("code", "noformat"):
                lang = self._macro_params.get("language", "")
                self._nl(2)
                self.out.append(f"```{lang}\n{body}\n```")
                self._nl(2)
            else:
                self._emit(body)
            self._plain_buf = []
            return
        if tag == "ac:structured-macro":
            if self._macro_stack:
                self._macro_stack.pop()
            return

        # --- tasks -------------------------------------------------------------
        if tag == "ac:task-list":
            if self._list_stack:
                self._list_stack.pop()
                self._ol_counters.pop()
            if not self._list_stack:
                self._nl(2)
            return
        if tag == "ac:task":
            return
        if tag in _TASK_META_DROP:
            self._task_meta_drop = max(0, self._task_meta_drop - 1)
            return
        if tag == "ac:task-status":
            self._in_task_status = False
            return
        if tag == "ac:task-body":
            return

        # --- structural --------------------------------------------------------
        if tag in _HEADINGS:
            self._nl(2)
        elif tag == "p":
            self._nl(2)
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
                self._ol_counters.pop()
            if not self._list_stack:
                self._nl(2)
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag in ("em", "i"):
            self._emit("*")
        elif tag in ("code", "tt"):
            self._emit("`")
        elif tag == "blockquote":
            self._nl(2)
        elif tag == "a":
            text = "".join(self._link_buf or []).strip()
            href = self._link_href or ""
            self._link_buf, self._link_href = None, None
            if href and text:
                self._emit(f"[{text}]({href})")
            elif text:
                self._emit(text)
        elif tag in ("td", "th"):
            cell = re.sub(r"\s+", " ", "".join(self._cell or []).strip())
            self._cell = None
            if self._row is not None:
                self._row.append(cell.replace("|", "\\|"))
        elif tag == "tr":
            if self._table is not None and self._row:
                self._table.append(self._row)
                if self._row_is_header and not self._header_done:
                    self._table.append(["---"] * len(self._row))
                    self._header_done = True
            self._row = None
        elif tag == "table":
            self._flush_table()

    def _flush_table(self) -> None:
        if not self._table:
            self._table = None
            return
        rows = self._table
        # GFM needs a delimiter row; synthesize one if the table had no <th>.
        if not self._header_done and rows:
            rows.insert(1, ["---"] * len(rows[0]))
        self._nl(2)
        for r in rows:
            self.out.append("| " + " | ".join(r) + " |\n")
        self._nl(1)
        self._table = None
        self._header_done = False

    # ── data ─────────────────────────────────────────────────────────────────

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._task_meta_drop:
            return
        if self._in_task_status:
            self._pending_task_state = (self._pending_task_state or "") + data.strip()
            return
        if self._in_plain_text:
            self._plain_buf.append(data)
            return
        if self._macro_param is not None:
            self._macro_params[self._macro_param] = data.strip()
            return
        if self._macro_stack and self._macro_stack[-1] not in _CALLOUTS \
                and self._macro_stack[-1] not in ("", "code", "noformat"):
            # Unknown macro wrapper: keep its text, drop the wrapper.
            pass
        if data.strip() or (self._cell is None and data == " "):
            self._emit(data)

    def unknown_decl(self, data):
        """Confluence wraps code-macro bodies in CDATA."""
        if data.startswith("CDATA["):
            payload = data[6:]
            if self._in_plain_text:
                self._plain_buf.append(payload)
            else:
                self._emit(payload)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in ("br", "hr", "ri:attachment", "ri:page", "ri:url", "img"):
            self.handle_endtag(tag)

    def result(self) -> str:
        md = "".join(self.out)
        md = unescape(md)
        md = re.sub(r"[ \t]+\n", "\n", md)
        md = re.sub(r"\n{3,}", "\n\n", md)
        return md.strip() + "\n"


def storage_to_markdown(storage_html: str, attachment_dir: str = "") -> str:
    """Convert Confluence storage XHTML to Markdown. Never raises."""
    if not storage_html:
        return ""
    parser = _StorageToMarkdown(attachment_dir=attachment_dir)
    try:
        parser.feed(storage_html)
        parser.close()
    except Exception:
        # Partial output beats no output; the caller records a warning.
        pass
    return parser.result()


if __name__ == "__main__":
    import sys
    print(storage_to_markdown(sys.stdin.read(), attachment_dir=""))
