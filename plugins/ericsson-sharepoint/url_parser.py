"""Strict SharePoint URL parsing without network or redirect behavior."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Collection
from urllib.parse import unquote_to_bytes, urlsplit, urlunsplit


_UI_TOKEN = re.compile(r":[A-Za-z]:\Z")
_UI_MODES = frozenset("rsguwxpbfivtloa")
_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_MAX_URL_LENGTH = 8192
_MAX_SEGMENTS = 256
_MAX_SEGMENT_LENGTH = 1024


class SharePointURLParseError(ValueError):
    """Raised when a URL cannot identify an authorized SharePoint target."""


@dataclass(frozen=True, slots=True)
class ParsedSharePointURL:
    kind: str
    host: str
    site_path: str = ""
    library: str = ""
    item_path: str = ""
    sharing_url: str = ""


def _decode_segment(raw: str) -> str:
    if _PERCENT_ESCAPE.search(raw):
        raise SharePointURLParseError("URL contains malformed percent escaping")
    try:
        decoded = unquote_to_bytes(raw).decode("utf-8", "strict")
    except UnicodeError:
        raise SharePointURLParseError("URL path is not valid UTF-8") from None
    if (
        not decoded
        or len(decoded) > _MAX_SEGMENT_LENGTH
        or decoded in {".", ".."}
        or "/" in decoded
        or "\\" in decoded
        or any(ord(character) < 32 or ord(character) == 127 for character in decoded)
    ):
        raise SharePointURLParseError("URL contains an unsafe path segment")
    return decoded


def parse_sharepoint_url(
    url: str,
    *,
    allowed_hosts: Collection[str],
) -> ParsedSharePointURL:
    """Parse a tenant-constrained URL into path or Graph sharing identity."""
    if (
        not isinstance(url, str)
        or not url
        or len(url) > _MAX_URL_LENGTH
        or "\\" in url
        or any(character.isspace() for character in url)
    ):
        raise SharePointURLParseError("SharePoint URL is invalid")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise SharePointURLParseError("SharePoint URL authority is invalid") from None
    host = (parts.hostname or "").lower().rstrip(".")
    allowed = {str(value).strip().lower().rstrip(".") for value in allowed_hosts}
    if (
        parts.scheme.lower() != "https"
        or not host
        or host not in allowed
        or parts.username is not None
        or parts.password is not None
        or port not in {None, 443}
        or parts.fragment
    ):
        raise SharePointURLParseError("SharePoint URL is outside configured tenant authority")

    raw_segments = [segment for segment in parts.path.split("/") if segment]
    if len(raw_segments) > _MAX_SEGMENTS:
        raise SharePointURLParseError("SharePoint URL has too many path segments")
    segments = [_decode_segment(segment) for segment in raw_segments]

    had_ui_prefix = bool(segments and _UI_TOKEN.fullmatch(segments[0]))
    if had_ui_prefix:
        segments = segments[1:]
        if segments and len(segments[0]) == 1 and segments[0].lower() in _UI_MODES:
            segments = segments[1:]
        if not segments or segments[0].lower() not in {"sites", "teams"}:
            canonical = urlunsplit(("https", host, parts.path, parts.query, ""))
            return ParsedSharePointURL(
                kind="sharing", host=host, sharing_url=canonical
            )

    site_path = ""
    if segments and segments[0].lower() in {"sites", "teams"}:
        if len(segments) < 2:
            raise SharePointURLParseError("SharePoint site path is incomplete")
        site_path = f"{segments[0]}/{segments[1]}"
        segments = segments[2:]
    library = segments[0] if segments else ""
    item_path = "/".join(segments[1:]) if len(segments) > 1 else ""
    return ParsedSharePointURL(
        kind="path",
        host=host,
        site_path=site_path,
        library=library,
        item_path=item_path,
    )


__all__ = [
    "ParsedSharePointURL",
    "SharePointURLParseError",
    "parse_sharepoint_url",
]

