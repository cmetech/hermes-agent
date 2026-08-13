"""Normalized, bounded Jira read operations."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

if __package__:
    from .models import JiraError
else:
    from models import JiraError


MY_TICKETS_JQL = (
    "assignee = currentUser() AND resolution = Unresolved "
    "ORDER BY priority DESC, updated DESC"
)
SAFE_FIELDS = frozenset(
    {
        "summary",
        "status",
        "priority",
        "updated",
        "created",
        "description",
        "environment",
        "issuetype",
        "labels",
    }
)
DETAIL_FIELDS = SAFE_FIELDS | {"comment"}
_DEFAULT_SEARCH_FIELDS = tuple(sorted(SAFE_FIELDS))
_GITLAB_URL = re.compile(
    r"https?://[^\s|\]>)\"',]*gitlab[^\s|\]>)\"',]*", re.I
)
_ISSUE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}-[1-9][0-9]{0,19}$")
_MAX_ADF_CHARS = 100_000
_MAX_ADF_NODES = 10_000
_MAX_ADF_DEPTH = 32


def _safe_link(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return value


def adf_to_text(value: Any) -> str:
    """Flatten bounded Jira ADF while preserving common document structure."""

    if isinstance(value, str):
        return value[:_MAX_ADF_CHARS]
    if not isinstance(value, (dict, list)):
        return ""
    seen = 0
    remaining = _MAX_ADF_CHARS

    def leaf(text: str) -> str:
        nonlocal remaining
        if remaining <= 0:
            return ""
        selected = text[:remaining]
        remaining -= len(selected)
        return selected

    def render(node: Any, depth: int = 0) -> str:
        nonlocal seen
        seen += 1
        if seen > _MAX_ADF_NODES or depth > _MAX_ADF_DEPTH:
            return ""
        if isinstance(node, list):
            return "".join(render(item, depth + 1) for item in node)
        if not isinstance(node, dict):
            return ""
        node_type = node.get("type")
        content = node.get("content", [])
        if not isinstance(content, list):
            content = []
        if node_type == "text":
            text = node.get("text")
            if not isinstance(text, str):
                return ""
            text = leaf(text)
            for mark in node.get("marks", []):
                if not isinstance(mark, dict) or mark.get("type") != "link":
                    continue
                attrs = mark.get("attrs")
                href = _safe_link(attrs.get("href") if isinstance(attrs, dict) else None)
                if href:
                    return f"[{text}]({href})"
            return text
        if node_type == "mention":
            attrs = node.get("attrs")
            label = attrs.get("text") if isinstance(attrs, dict) else None
            if isinstance(label, str) and label.strip():
                return leaf("@" + label.strip().lstrip("@"))
            return ""
        if node_type == "hardBreak":
            return "\n"
        if node_type in {"doc", "blockquote", "panel"}:
            return "\n".join(
                part for part in (render(item, depth + 1) for item in content) if part
            )
        if node_type in {"paragraph", "heading", "listItem"}:
            return "".join(render(item, depth + 1) for item in content)
        if node_type in {"bulletList", "orderedList"}:
            items = []
            for index, item in enumerate(content, 1):
                rendered = render(item, depth + 1).strip()
                if rendered:
                    prefix = "- " if node_type == "bulletList" else f"{index}. "
                    items.append(prefix + rendered.replace("\n", "\n  "))
            return "\n".join(items)
        if node_type == "codeBlock":
            rendered = "".join(render(item, depth + 1) for item in content)
            return f"```\n{rendered}\n```" if rendered else ""
        if node_type == "table":
            return "\n".join(
                part for part in (render(item, depth + 1) for item in content) if part
            )
        if node_type == "tableRow":
            return " | ".join(
                part.strip()
                for part in (render(item, depth + 1) for item in content)
                if part.strip()
            )
        if node_type in {"tableCell", "tableHeader"}:
            return " ".join(
                part.strip()
                for part in (render(item, depth + 1) for item in content)
                if part.strip()
            )
        return "".join(render(item, depth + 1) for item in content)

    text = render(value)
    return text[:_MAX_ADF_CHARS].strip()


def extract_gitlab_urls(*values: Any) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = value if isinstance(value, str) else adf_to_text(value)
        for match in _GITLAB_URL.finditer(text):
            url = match.group(0).rstrip(".,;:!?)]}>\"'")
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def _bounded_string(value: Any, maximum: int = 4096) -> str | None:
    return value if isinstance(value, str) and len(value) <= maximum else None


def _name(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    return _bounded_string(value.get("name"), 256)


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 128:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _string_filter(values: Any) -> frozenset[str]:
    if values is None:
        return frozenset()
    if not isinstance(values, list) or len(values) > 20:
        raise JiraError("invalid_input")
    output = set()
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise JiraError("invalid_input")
        output.add(value.strip().casefold())
    return frozenset(output)


def _age_threshold(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 3650:
        raise JiraError("invalid_input")
    return value


class JiraOperations:
    def __init__(
        self,
        client,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        max_pages: int = 10,
    ) -> None:
        if type(max_pages) is not int or not 1 <= max_pages <= 10:
            raise JiraError("invalid_configuration")
        self.client = client
        self._now = now
        self.max_pages = max_pages

    def _redact(self, value: str | None) -> str | None:
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

    def _normalize_issue(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict) or not isinstance(raw.get("fields"), dict):
            raise JiraError("invalid_remote_data")
        key = _bounded_string(raw.get("key"), 128)
        if not key:
            raise JiraError("invalid_remote_data")
        fields = raw["fields"]
        labels = fields.get("labels", [])
        if not isinstance(labels, list) or len(labels) > 100 or any(
            not isinstance(label, str) or len(label) > 256 for label in labels
        ):
            raise JiraError("invalid_remote_data")
        description = self._redact(adf_to_text(fields.get("description"))) or ""
        environment = self._redact(adf_to_text(fields.get("environment"))) or ""
        summary = self._redact(_bounded_string(fields.get("summary"), 4096)) or ""
        status = self._redact(_name(fields.get("status")))
        status_value = fields.get("status")
        status_category = (
            self._redact(_name(status_value.get("statusCategory")))
            if isinstance(status_value, dict)
            else None
        )
        candidate = description or environment or summary
        compact = " ".join(candidate.split())
        sentence = re.split(r"(?<=[.!?])\s", compact, maxsplit=1)[0] if compact else ""
        if len(sentence) > 260:
            sentence = sentence[:257].rstrip() + "..."
        return {
            "key": key,
            "summary": summary,
            "status": status,
            "status_category": status_category,
            "priority": self._redact(_name(fields.get("priority"))),
            "issue_type": self._redact(_name(fields.get("issuetype"))),
            "labels": [self._redact(label) or "" for label in labels],
            "updated": _bounded_string(fields.get("updated"), 128),
            "created": _bounded_string(fields.get("created"), 128),
            "description": description,
            "environment": environment,
            "problem_summary": sentence,
            "gitlab_urls": extract_gitlab_urls(description, environment, summary),
            "issue_url": f"{self.client.auth.origin}/browse/{key}",
        }

    def _matches(
        self,
        issue: dict[str, Any],
        *,
        statuses: frozenset[str],
        issue_types: frozenset[str],
        priorities: frozenset[str],
        labels: frozenset[str],
        min_age_days: int | None,
        max_age_days: int | None,
    ) -> bool:
        def selected(field: str, allowed: frozenset[str]) -> bool:
            return not allowed or str(issue.get(field) or "").casefold() in allowed

        if not selected("status", statuses):
            return False
        if not selected("issue_type", issue_types):
            return False
        if not selected("priority", priorities):
            return False
        if labels and not {label.casefold() for label in issue["labels"]}.intersection(labels):
            return False
        if min_age_days is not None or max_age_days is not None:
            updated = _timestamp(issue.get("updated"))
            if updated is None:
                return False
            now = self._now()
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            age = (now.astimezone(timezone.utc) - updated).total_seconds() / 86400
            if min_age_days is not None and age < min_age_days:
                return False
            if max_age_days is not None and age > max_age_days:
                return False
        return True

    def search_issues(
        self,
        *,
        jql: str,
        max_results: int,
        fields: list[str] | None = None,
        statuses: list[str] | None = None,
        issue_types: list[str] | None = None,
        priorities: list[str] | None = None,
        labels: list[str] | None = None,
        min_age_days: int | None = None,
        max_age_days: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(jql, str) or not jql.strip() or len(jql) > 4096:
            raise JiraError("invalid_input")
        if type(max_results) is not int or not 1 <= max_results <= 100:
            raise JiraError("invalid_input")
        if fields is None:
            requested_fields = set(_DEFAULT_SEARCH_FIELDS)
        elif (
            not isinstance(fields, list)
            or not fields
            or len(fields) > len(SAFE_FIELDS)
            or any(field not in SAFE_FIELDS for field in fields)
        ):
            raise JiraError("invalid_input")
        else:
            requested_fields = set(fields)
        status_filter = _string_filter(statuses)
        type_filter = _string_filter(issue_types)
        priority_filter = _string_filter(priorities)
        label_filter = _string_filter(labels)
        minimum_age = _age_threshold(min_age_days)
        maximum_age = _age_threshold(max_age_days)
        if (
            minimum_age is not None
            and maximum_age is not None
            and minimum_age > maximum_age
        ):
            raise JiraError("invalid_input")
        if status_filter:
            requested_fields.add("status")
        if type_filter:
            requested_fields.add("issuetype")
        if priority_filter:
            requested_fields.add("priority")
        if label_filter:
            requested_fields.add("labels")
        if minimum_age is not None or maximum_age is not None:
            requested_fields.add("updated")
        has_filters = bool(
            status_filter
            or type_filter
            or priority_filter
            or label_filter
            or minimum_age is not None
            or maximum_age is not None
        )
        page_size = 100 if has_filters else max_results

        selected: list[dict[str, Any]] = []
        start_at = 0
        total = None
        truncated = False
        pages = 0
        while pages < self.max_pages:
            pages += 1
            payload = self.client.rest_json(
                "GET",
                "search",
                params={
                    "jql": jql.strip(),
                    "startAt": start_at,
                    "maxResults": page_size,
                    "fields": ",".join(sorted(requested_fields)),
                },
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
                raise JiraError("invalid_remote_data")
            raw_issues = payload["issues"]
            raw_total = payload.get("total", start_at + len(raw_issues))
            if type(raw_total) is not int or raw_total < 0:
                raise JiraError("invalid_remote_data")
            total = raw_total
            consumed = 0
            for raw in raw_issues:
                consumed += 1
                normalized = self._normalize_issue(raw)
                if self._matches(
                    normalized,
                    statuses=status_filter,
                    issue_types=type_filter,
                    priorities=priority_filter,
                    labels=label_filter,
                    min_age_days=minimum_age,
                    max_age_days=maximum_age,
                ):
                    selected.append(normalized)
                    if len(selected) >= max_results:
                        truncated = start_at + consumed < total
                        break
            start_at += len(raw_issues)
            if len(selected) >= max_results:
                break
            if not raw_issues or start_at >= total:
                break
        else:
            truncated = total is None or start_at < total
        return {
            "issues": selected[:max_results],
            "truncated": truncated,
            "warnings": ["results_truncated"] if truncated else [],
        }

    def my_tickets(self, max_results: int | None = None) -> list[dict[str, Any]]:
        limit = self.client.auth.default_max_results if max_results is None else max_results
        return self.search_issues(
            jql=MY_TICKETS_JQL,
            max_results=limit,
            fields=["summary", "status", "priority", "updated", "description"],
        )["issues"]

    def get_issue(self, key: str) -> dict[str, Any]:
        if not isinstance(key, str) or _ISSUE_KEY.fullmatch(key) is None:
            raise JiraError("invalid_input")
        payload = self.client.rest_json(
            "GET",
            f"issue/{key}",
            params={"fields": ",".join(sorted(DETAIL_FIELDS))},
        )
        normalized = self._normalize_issue(payload)
        fields = payload["fields"]
        comment_container = fields.get("comment") or {}
        if not isinstance(comment_container, dict) or not isinstance(
            comment_container.get("comments", []), list
        ):
            raise JiraError("invalid_remote_data")
        comments = []
        for comment in comment_container.get("comments", [])[-5:]:
            if not isinstance(comment, dict):
                raise JiraError("invalid_remote_data")
            author = comment.get("author") or {}
            if not isinstance(author, dict):
                raise JiraError("invalid_remote_data")
            comments.append(
                {
                    "author": self._redact(
                        _bounded_string(author.get("displayName"), 256)
                    ),
                    "body": self._redact(adf_to_text(comment.get("body"))) or "",
                    "created": _bounded_string(comment.get("created"), 128),
                }
            )
        normalized["comments"] = comments
        return normalized

    def _find_comment(self, key: str, body: str) -> str | None:
        payload = self.client.rest_json(
            "GET",
            f"issue/{key}/comment",
            params={"maxResults": 100, "orderBy": "-created"},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("comments"), list):
            raise JiraError("invalid_remote_data")
        remote_comments = payload["comments"]
        if len(remote_comments) > 100:
            raise JiraError("capacity")
        target = body.strip()
        for comment in remote_comments:
            if not isinstance(comment, dict):
                raise JiraError("invalid_remote_data")
            comment_id = _bounded_string(comment.get("id"), 128)
            if comment_id and adf_to_text(comment.get("body")).strip() == target:
                return comment_id
        return None

    @staticmethod
    def _comment_result(
        comment_id: str | None,
        *,
        created: bool,
        duplicate: bool,
        reconciled: bool,
        dry_run: bool,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "id": comment_id,
            "created": created,
            "duplicate": duplicate,
            "reconciled": reconciled,
            "dry_run": dry_run,
        }

    def add_comment(
        self, key: str, body: str, *, dry_run: bool = False
    ) -> dict[str, Any]:
        if (
            not isinstance(key, str)
            or _ISSUE_KEY.fullmatch(key) is None
            or not isinstance(body, str)
            or not body.strip()
            or len(body) > 32_000
            or type(dry_run) is not bool
        ):
            raise JiraError("invalid_input")
        if dry_run:
            return {
                **self._comment_result(
                    None,
                    created=False,
                    duplicate=False,
                    reconciled=False,
                    dry_run=True,
                ),
                "issue_key": key,
                "body": body,
            }
        existing = self._find_comment(key, body)
        if existing is not None:
            return self._comment_result(
                existing,
                created=False,
                duplicate=True,
                reconciled=False,
                dry_run=False,
            )
        v3_body = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": body}],
                    }
                ],
            }
        }
        v2_body = {"body": body}
        selected_body = v2_body if self.client.auth.rest_api_version == "2" else v3_body
        try:
            payload = self.client.rest_json(
                "POST",
                f"issue/{key}/comment",
                json_body=selected_body,
                json_body_by_version={"3": v3_body, "2": v2_body},
            )
        except JiraError as exc:
            if exc.category not in {"conflict", "write_ambiguous"}:
                raise
            reconciled = self._find_comment(key, body)
            if reconciled is None:
                raise
            return self._comment_result(
                reconciled,
                created=False,
                duplicate=True,
                reconciled=True,
                dry_run=False,
            )
        if not isinstance(payload, dict):
            raise JiraError("invalid_remote_data")
        comment_id = _bounded_string(payload.get("id"), 128)
        if not comment_id:
            raise JiraError("invalid_remote_data")
        return self._comment_result(
            comment_id,
            created=True,
            duplicate=False,
            reconciled=False,
            dry_run=False,
        )
