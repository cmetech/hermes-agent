"""Bounded, redacted Confluence operations."""

from __future__ import annotations

import re
from typing import Any, Mapping

if __package__:
    from ._common.envelope import UNTRUSTED_CONTENT_WARNING, result_envelope
    from ._common.guardrails import require_explicit_intent
    from .models import ConfluenceError
    from .storage import markdown_to_storage, storage_to_markdown
else:
    from _common.envelope import UNTRUSTED_CONTENT_WARNING, result_envelope
    from _common.guardrails import require_explicit_intent
    from models import ConfluenceError
    from storage import markdown_to_storage, storage_to_markdown


EXPAND_PAGE = "body.storage,version,space,ancestors,metadata.labels,history.lastUpdated"
EXPAND_LIST = "version,space,ancestors"

_CONTENT_ID = re.compile(r"^[0-9]{1,19}$")
_SPACE_KEY = re.compile(r"^[A-Za-z0-9._~-]{1,255}$")
_SPACE_TYPES = {"global", "personal"}
_MAX_BODY_CHARS = 100_000
_MAX_CQL_CHARS = 4096
_MAX_TITLE_CHARS = 255
_MAX_WRITE_BODY_CHARS = 65_536


def _bounded_string(value: Any, maximum: int) -> str | None:
    return value[:maximum] if isinstance(value, str) else None


class ConfluenceOperations:
    def __init__(self, client, *, max_pages: int = 10) -> None:
        if type(max_pages) is not int or not 1 <= max_pages <= 10:
            raise ConfluenceError("invalid_configuration")
        self.client = client
        self.max_pages = max_pages
        self.base = client.path_prefix.rstrip("/")

    def _redact(self, value: str | None) -> str | None:
        """Strip the configured token out of remote text."""
        if value is None:
            return None
        authorization = getattr(self.client.auth, "authorization", "")
        candidates = [authorization]
        if isinstance(authorization, str) and " " in authorization:
            candidates.append(authorization.split(" ", 1)[1])
        for secret in candidates:
            if isinstance(secret, str) and len(secret) >= 4:
                value = value.replace(secret, "<redacted>")
        return value

    @staticmethod
    def _content_id(value: Any) -> str:
        if not isinstance(value, str) or _CONTENT_ID.fullmatch(value) is None:
            raise ConfluenceError("invalid_input")
        return value

    @staticmethod
    def _space_key(value: Any) -> str:
        if not isinstance(value, str) or _SPACE_KEY.fullmatch(value) is None:
            raise ConfluenceError("invalid_input")
        return value

    @staticmethod
    def _title(value: Any) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > _MAX_TITLE_CHARS
        ):
            raise ConfluenceError("invalid_input")
        return value

    @staticmethod
    def _body_storage(markdown: Any) -> str:
        """Convert caller Markdown to storage format, escaping all text."""
        if not isinstance(markdown, str) or len(markdown) > _MAX_WRITE_BODY_CHARS:
            raise ConfluenceError("invalid_input")
        return markdown_to_storage(markdown)

    @staticmethod
    def _mapping(payload: Any) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            raise ConfluenceError("invalid_remote_data")
        return payload

    def _storage_value(self, payload: Mapping[str, Any]) -> str:
        body = payload.get("body")
        if not isinstance(body, Mapping):
            return ""
        storage = body.get("storage")
        if not isinstance(storage, Mapping):
            return ""
        return _bounded_string(storage.get("value"), _MAX_BODY_CHARS) or ""

    @staticmethod
    def _version(payload: Mapping[str, Any]) -> int | None:
        version = payload.get("version")
        if isinstance(version, Mapping) and type(version.get("number")) is int:
            return version["number"]
        return None

    def _markdown(self, storage_value: str, *, max_chars: int) -> tuple[str, bool]:
        full = self._redact(storage_to_markdown(storage_value)) or ""
        if len(full) <= max_chars:
            return full, False
        return full[:max_chars], True

    def _content_summary(self, row: Mapping[str, Any]) -> dict[str, Any]:
        space = row.get("space")
        return {
            "id": _bounded_string(row.get("id"), 64) or "",
            "title": self._redact(_bounded_string(row.get("title"), 512)) or "",
            "type": _bounded_string(row.get("type"), 64) or "",
            "space_key": self._redact(_bounded_string(space.get("key"), 255)) if isinstance(space, Mapping) else None,
        }

    def _paged(self, path: str, params: dict[str, Any], max_results: int) -> tuple[list[Mapping[str, Any]], int | None, bool]:
        rows: list[Mapping[str, Any]] = []
        total: int | None = None
        start = 0
        page_size = min(max_results, 100)
        pages_fetched = 0
        last_results: list[Any] = []
        for _ in range(self.max_pages):
            pages_fetched += 1
            payload = self._mapping(self.client.get_json(path, params={**params, "start": start, "limit": page_size}))
            results = payload.get("results")
            if not isinstance(results, list):
                raise ConfluenceError("invalid_remote_data")
            last_results = results
            if type(payload.get("totalSize")) is int:
                total = payload["totalSize"]
            rows.extend(row for row in results if isinstance(row, Mapping))
            if not results:
                break
            if len(rows) >= max_results or (
                len(results) < page_size and (total is None or total <= len(rows))
            ):
                break
            start += len(results)
        reached_page_cap = (
            pages_fetched == self.max_pages
            and len(rows) < max_results
            and len(last_results) >= page_size
        )
        truncated = (
            len(rows) > max_results
            or (total is not None and total > len(rows))
            or reached_page_cap
        )
        return rows[:max_results], total, truncated

    def search(self, cql: str, *, max_results: int = 25) -> dict[str, Any]:
        """Search content with CQL.

        Raw CQL is exposed deliberately: it is the whole value of Confluence
        search, and the configured token carries the user's own permissions,
        so a query cannot reach content the user could not already read.
        Enumeration uses EXPAND_LIST -- bodies are fetched deliberately via
        confluence_get_page rather than dragged along with every hit.
        """
        if (
            not isinstance(cql, str)
            or not cql.strip()
            or len(cql) > _MAX_CQL_CHARS
        ):
            raise ConfluenceError("invalid_input")
        if type(max_results) is not int or not 1 <= max_results <= 100:
            raise ConfluenceError("invalid_input")
        rows, total, truncated = self._paged(
            f"{self.base}/content/search",
            {"cql": cql, "expand": EXPAND_LIST},
            max_results,
        )
        return result_envelope(
            [self._content_summary(row) for row in rows],
            total=total,
            truncated=truncated,
            hint=(
                "More content matches this CQL. Raise max_results or narrow "
                "the query." if truncated else None
            ),
            untrusted=True,
        )

    def list_spaces(
        self, *, space_type: str | None = None, max_results: int = 25
    ) -> dict[str, Any]:
        """List spaces the token can see."""
        if space_type is not None and (
            not isinstance(space_type, str) or space_type not in _SPACE_TYPES
        ):
            raise ConfluenceError("invalid_input")
        if type(max_results) is not int or not 1 <= max_results <= 100:
            raise ConfluenceError("invalid_input")
        params: dict[str, Any] = {}
        if space_type is not None:
            params["type"] = space_type
        rows, total, truncated = self._paged(
            f"{self.base}/space", params, max_results
        )
        spaces = [
            {
                "key": self._redact(_bounded_string(row.get("key"), 255)) or "",
                "name": self._redact(_bounded_string(row.get("name"), 512)) or "",
                "type": _bounded_string(row.get("type"), 64) or "",
            }
            for row in rows
        ]
        return result_envelope(
            spaces,
            total=total,
            truncated=truncated,
            hint="More spaces exist. Raise max_results." if truncated else None,
        )

    def list_children(
        self, content_id: str, *, max_results: int = 25
    ) -> dict[str, Any]:
        """List one page's direct child pages."""
        content_id = self._content_id(content_id)
        if type(max_results) is not int or not 1 <= max_results <= 100:
            raise ConfluenceError("invalid_input")
        rows, total, truncated = self._paged(
            f"{self.base}/content/{content_id}/child/page",
            {"expand": EXPAND_LIST},
            max_results,
        )
        return result_envelope(
            [self._content_summary(row) for row in rows],
            total=total,
            truncated=truncated,
            hint="More child pages exist. Raise max_results." if truncated else None,
            untrusted=True,
        )

    def list_comments(
        self, content_id: str, *, max_results: int = 25
    ) -> dict[str, Any]:
        """List comments on a page, with bodies rendered as Markdown."""
        content_id = self._content_id(content_id)
        if type(max_results) is not int or not 1 <= max_results <= 100:
            raise ConfluenceError("invalid_input")
        rows, total, truncated = self._paged(
            f"{self.base}/content/{content_id}/child/comment",
            {"expand": "body.storage,version"},
            max_results,
        )
        comments = []
        for row in rows:
            version = row.get("version")
            author = None
            created = None
            if isinstance(version, Mapping):
                by = version.get("by")
                if isinstance(by, Mapping):
                    author = self._redact(
                        _bounded_string(by.get("displayName"), 255)
                    )
                created = _bounded_string(version.get("when"), 64)
            markdown, _truncated = self._markdown(
                self._storage_value(row), max_chars=_MAX_BODY_CHARS
            )
            markdown = markdown.rstrip()
            comments.append(
                {
                    "id": _bounded_string(row.get("id"), 64) or "",
                    "author": author,
                    "created": created,
                    "markdown": markdown,
                }
            )
        return result_envelope(
            comments,
            total=total,
            truncated=truncated,
            hint="More comments exist. Raise max_results." if truncated else None,
            untrusted=True,
        )

    def get_page(self, content_id: str) -> dict[str, Any]:
        content_id = self._content_id(content_id)
        payload = self._mapping(self.client.get_json(f"{self.base}/content/{content_id}", params={"expand": EXPAND_PAGE}))
        space = payload.get("space")
        breadcrumb = []
        ancestors = payload.get("ancestors")
        if isinstance(ancestors, list):
            for ancestor in ancestors[:20]:
                if isinstance(ancestor, Mapping):
                    title = self._redact(_bounded_string(ancestor.get("title"), 255))
                    if title:
                        breadcrumb.append(title)
        markdown, _ = self._markdown(self._storage_value(payload), max_chars=_MAX_BODY_CHARS)
        return {
            "id": _bounded_string(payload.get("id"), 64) or content_id,
            "title": self._redact(_bounded_string(payload.get("title"), 512)) or "",
            "type": _bounded_string(payload.get("type"), 64) or "",
            "version": self._version(payload),
            "space_key": self._redact(_bounded_string(space.get("key"), 255)) if isinstance(space, Mapping) else None,
            "breadcrumb": breadcrumb,
            "markdown": markdown,
            "content_warning": UNTRUSTED_CONTENT_WARNING,
        }

    def get_page_body(self, content_id: str, *, raw_storage: bool = False, max_chars: int = 32_000) -> dict[str, Any]:
        content_id = self._content_id(content_id)
        if type(raw_storage) is not bool or type(max_chars) is not int or not 1 <= max_chars <= _MAX_BODY_CHARS:
            raise ConfluenceError("invalid_input")
        payload = self._mapping(self.client.get_json(f"{self.base}/content/{content_id}", params={"expand": "body.storage,version"}))
        storage_value = self._storage_value(payload)
        markdown, truncated = self._markdown(storage_value, max_chars=max_chars)
        result: dict[str, Any] = {
            "id": content_id,
            "version": self._version(payload),
            "markdown": markdown,
            "truncated": truncated,
            "content_warning": UNTRUSTED_CONTENT_WARNING,
        }
        if truncated:
            result["hint"] = "The page body was truncated. Raise max_chars to read more."
        if raw_storage:
            result["raw_storage"] = self._redact(storage_value) or ""
        return result

    def create_page(
        self,
        space_key: str,
        title: str,
        markdown: str,
        *,
        parent_id: str | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Create one page from Markdown without retrying ambiguous writes."""
        space_key = self._space_key(space_key)
        title = self._title(title)
        storage_value = self._body_storage(markdown)
        if parent_id is not None:
            parent_id = self._content_id(parent_id)
        if type(dry_run) is not bool or type(confirm) is not bool:
            raise ConfluenceError("invalid_input")
        if dry_run and confirm:
            raise ConfluenceError("invalid_input")
        if not dry_run and not confirm:
            raise ConfluenceError("confirmation_required")

        execute = require_explicit_intent(
            dry_run=dry_run,
            confirm=confirm,
            action=f"a new page '{title}' in space {space_key}",
        )
        payload: dict[str, Any] = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {
                "storage": {"value": storage_value, "representation": "storage"}
            },
        }
        if parent_id is not None:
            payload["ancestors"] = [{"id": parent_id}]
        if not execute:
            return {
                "ok": True,
                "dry_run": True,
                "id": None,
                "space_key": space_key,
                "title": title,
                "parent_id": parent_id,
            }
        response = self._mapping(
            self.client.request_json("POST", f"{self.base}/content", json_body=payload)
        )
        created_id = _bounded_string(response.get("id"), 64)
        if not created_id:
            raise ConfluenceError("invalid_remote_data")
        return {
            "ok": True,
            "dry_run": False,
            "id": created_id,
            "space_key": space_key,
            "title": title,
            "parent_id": parent_id,
        }

    def add_comment(
        self,
        content_id: str,
        markdown: str,
        *,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Add one comment to a page, from Markdown."""
        content_id = self._content_id(content_id)
        if not isinstance(markdown, str) or not markdown.strip():
            raise ConfluenceError("invalid_input")
        storage_value = self._body_storage(markdown)
        if type(dry_run) is not bool or type(confirm) is not bool:
            raise ConfluenceError("invalid_input")
        if dry_run and confirm:
            raise ConfluenceError("invalid_input")
        if not dry_run and not confirm:
            raise ConfluenceError("confirmation_required")

        execute = require_explicit_intent(
            dry_run=dry_run, confirm=confirm, action=f"Confluence page {content_id}"
        )
        if not execute:
            return {
                "ok": True,
                "dry_run": True,
                "id": None,
                "content_id": content_id,
                "markdown": markdown,
            }
        payload = {
            "type": "comment",
            "container": {"id": content_id, "type": "page"},
            "body": {
                "storage": {"value": storage_value, "representation": "storage"}
            },
        }
        response = self._mapping(
            self.client.request_json(
                "POST", f"{self.base}/content", json_body=payload
            )
        )
        comment_id = _bounded_string(response.get("id"), 64)
        if not comment_id:
            raise ConfluenceError("invalid_remote_data")
        return {
            "ok": True,
            "dry_run": False,
            "id": comment_id,
            "content_id": content_id,
            "markdown": markdown,
        }

    def update_page(
        self,
        content_id: str,
        *,
        title: str | None = None,
        markdown: str | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Edit a page's title, body, or both with optimistic concurrency."""
        content_id = self._content_id(content_id)
        if title is None and markdown is None:
            raise ConfluenceError("invalid_input")
        if title is not None:
            title = self._title(title)
        new_storage = self._body_storage(markdown) if markdown is not None else None
        if type(dry_run) is not bool or type(confirm) is not bool:
            raise ConfluenceError("invalid_input")
        if dry_run and confirm:
            raise ConfluenceError("invalid_input")
        if not dry_run and not confirm:
            raise ConfluenceError("confirmation_required")

        execute = require_explicit_intent(
            dry_run=dry_run,
            confirm=confirm,
            action=f"Confluence page {content_id}",
        )

        current = self._mapping(
            self.client.get_json(
                f"{self.base}/content/{content_id}",
                params={"expand": "body.storage,version"},
            )
        )
        current_version = self._version(current)
        if current_version is None:
            raise ConfluenceError("invalid_remote_data")
        if title is None:
            next_title = current.get("title")
            if (
                not isinstance(next_title, str)
                or not next_title.strip()
                or len(next_title) > _MAX_TITLE_CHARS
            ):
                raise ConfluenceError("invalid_remote_data")
        else:
            next_title = title
        if new_storage is None:
            body = current.get("body")
            storage = body.get("storage") if isinstance(body, Mapping) else None
            next_storage = storage.get("value") if isinstance(storage, Mapping) else None
            if not isinstance(next_storage, str):
                raise ConfluenceError("invalid_remote_data")
        else:
            next_storage = new_storage

        if not execute:
            return {
                "ok": True,
                "dry_run": True,
                "id": content_id,
                "current_version": current_version,
                "title": next_title,
            }

        payload = {
            "id": content_id,
            "type": _bounded_string(current.get("type"), 64) or "page",
            "title": next_title,
            "version": {"number": current_version + 1},
            "body": {
                "storage": {"value": next_storage, "representation": "storage"}
            },
        }
        response = self._mapping(
            self.client.request_json(
                "PUT", f"{self.base}/content/{content_id}", json_body=payload
            )
        )
        response_version = self._version(response)
        if response_version is None:
            raise ConfluenceError("invalid_remote_data")
        return {
            "ok": True,
            "dry_run": False,
            "id": content_id,
            "version": response_version,
            "title": next_title,
        }
