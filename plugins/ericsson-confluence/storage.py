"""Confluence storage format (XHTML) -> Markdown.

Ported verbatim from
``skills/ericsson/confluence-research/scripts/storage_to_md.py`` into the
Ericsson Confluence connector.

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


# ── Markdown -> storage format ───────────────────────────────────────────────
#
# Deliberately small: headings, paragraphs, lists, fenced code and inline
# links. Everything else degrades to escaped text, which is the safe
# direction. Every text node passes through html.escape, so markup a caller
# writes becomes visible characters rather than page structure -- a model
# cannot inject <ac:structured-macro> into the wiki through this path.

from html import escape as _escape

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# Matched against the RAW line: group 1 is the indent, and discarding it
# before matching is exactly how nesting gets lost.
_MD_LIST = re.compile(r"^([ \t]*)([-*]|\d+[.)])\s+(.*)$")
_MD_FENCE = re.compile(r"^```([A-Za-z0-9_+-]*)\s*$")
_MD_LINK = re.compile(r"\[([^\]]{1,512})\]\(([^)\s]{1,2048})\)")
_SAFE_LINK_SCHEME = re.compile(r"^(?:https?://|/(?![\\/]))", re.IGNORECASE)


def _inline(text: str) -> str:
    """Escape one line, then re-introduce only links we consider safe."""
    escaped = _escape(text, quote=True)

    def _link(match: "re.Match[str]") -> str:
        # The label and href were escaped with the rest of the line, so
        # unescape only for the scheme check and re-emit escaped.
        label, href = match.group(1), match.group(2)
        raw_href = href.replace("&amp;", "&")
        if not _SAFE_LINK_SCHEME.match(raw_href):
            # javascript:, data:, and anything else become plain text.
            return label
        return f'<a href="{href}">{label}</a>'

    return _MD_LINK.sub(_link, escaped)


def _cdata(text: str) -> str:
    """Wrap text in CDATA, splitting any literal ']]>' that would close it."""
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def markdown_to_storage(markdown: str) -> str:
    """Convert a bounded Markdown subset to Confluence storage format."""
    if not isinstance(markdown, str) or not markdown:
        return ""
    out: list[str] = []
    lines = markdown.replace("\r\n", "\n").split("\n")
    index = 0

    list_stack: list[str] = []    # 'ul' | 'ol' per open level, outermost first
    indent_stack: list[int] = []  # indent column that opened each level
    li_open = False               # the innermost <li> is still unclosed

    def _close_li() -> None:
        nonlocal li_open
        if li_open:
            out.append("</li>")
            li_open = False

    def _pop_level() -> None:
        """Close the innermost list. Its parent's <li> is then unclosed."""
        nonlocal li_open
        _close_li()
        out.append(f"</{list_stack.pop()}>")
        indent_stack.pop()
        # A nested list lives inside its parent's <li>, so that <li> is
        # still open once the nested list closes.
        li_open = bool(list_stack)

    def _close_list(to_indent: int | None = None) -> None:
        """Close levels deeper than to_indent; None closes all of them."""
        while indent_stack and (to_indent is None or indent_stack[-1] > to_indent):
            _pop_level()
        if to_indent is None:
            _close_li()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        fence = _MD_FENCE.match(stripped)
        if fence:
            _close_list()
            language = fence.group(1)
            body: list[str] = []
            index += 1
            while index < len(lines) and not _MD_FENCE.match(lines[index].strip()):
                body.append(lines[index])
                index += 1
            index += 1  # consume the closing fence
            parameter = (
                f'<ac:parameter ac:name="language">{_escape(language)}'
                f"</ac:parameter>"
                if language
                else ""
            )
            out.append(
                '<ac:structured-macro ac:name="code">'
                f"{parameter}"
                f"<ac:plain-text-body>{_cdata(chr(10).join(body))}"
                "</ac:plain-text-body></ac:structured-macro>"
            )
            continue

        if not stripped:
            _close_list()
            index += 1
            continue

        heading = _MD_HEADING.match(stripped)
        if heading:
            _close_list()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        # Matched against `line`, not `stripped`: the indent is the nesting.
        listed = _MD_LIST.match(line)
        if listed:
            indent = len(listed.group(1).expandtabs(2))
            kind = "ul" if listed.group(2) in ("-", "*") else "ol"
            content = listed.group(3)

            if not indent_stack or indent > indent_stack[-1]:
                # Open a level. When nesting, the parent <li> stays open so
                # the new list is emitted inside it, as XHTML requires.
                out.append(f"<{kind}>")
                list_stack.append(kind)
                indent_stack.append(indent)
                li_open = False
            else:
                _close_list(indent)
                _close_li()
                if list_stack and list_stack[-1] != kind:
                    # Marker type changed at this level: swap the container.
                    out.append(f"</{list_stack.pop()}>")
                    indent_stack.pop()
                    out.append(f"<{kind}>")
                    list_stack.append(kind)
                    indent_stack.append(indent)
            out.append(f"<li>{_inline(content)}")
            li_open = True
            index += 1
            continue

        _close_list()
        out.append(f"<p>{_inline(stripped)}</p>")
        index += 1

    _close_list()
    return "".join(out)
