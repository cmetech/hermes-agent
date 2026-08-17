"""Normalized, bounded Jira read operations."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

if __package__:
    from ._common.envelope import UNTRUSTED_CONTENT_WARNING, result_envelope
    from ._common.guardrails import require_explicit_intent
    from .models import JiraError
else:
    from _common.envelope import UNTRUSTED_CONTENT_WARNING, result_envelope
    from _common.guardrails import require_explicit_intent
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
WRITABLE_FIELDS = frozenset(
    {"summary", "description", "priority", "duedate", "labels", "environment"}
)
_DEFAULT_SEARCH_FIELDS = tuple(sorted(SAFE_FIELDS))
_GITLAB_URL = re.compile(
    r"https?://[^\s|\]>)\"',]*gitlab[^\s|\]>)\"',]*", re.I
)
_ISSUE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}-[1-9][0-9]{0,19}$")
_PROJECT_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,60}$")
_NUMERIC_ID = re.compile(r"^[0-9]{1,19}$")
_CUSTOM_FIELD = re.compile(r"^customfield_[0-9]{1,19}$")
_MAX_ADF_CHARS = 100_000
_MAX_ADF_NODES = 10_000
_MAX_ADF_DEPTH = 32
_MAX_EMAIL_LEN = 320
_MAX_WRITABLE_FIELDS = 20
_MAX_UPDATE_JSON_BYTES = 65_536
_MAX_UPDATE_JSON_DEPTH = 32
_MAX_UPDATE_JSON_NODES = 10_000
_LABEL = re.compile(r"^[^\s]{1,255}$")
_MAX_LABELS = 50


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


def _bounded_json_value(value: Any) -> Any:
    """Normalize a finite JSON value for a Jira field update.

    Jira field values intentionally retain their native shape: a priority may
    be an object, labels a list, and custom fields can have bounded nested
    structures.  Normalizing mappings makes that contract explicit while
    refusing Python-only values before anything reaches a transport.
    """

    nodes = 0

    def normalize(item: Any, depth: int) -> Any:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_UPDATE_JSON_NODES or depth > _MAX_UPDATE_JSON_DEPTH:
            raise JiraError("invalid_input")
        if item is None or type(item) in {str, int, bool}:
            return item
        if type(item) is float:
            if not math.isfinite(item):
                raise JiraError("invalid_input")
            return item
        if type(item) is dict:
            normalized: dict[str, Any] = {}
            for key, nested in item.items():
                if type(key) is not str:
                    raise JiraError("invalid_input")
                normalized[key] = normalize(nested, depth + 1)
            return normalized
        if type(item) is list:
            return [normalize(nested, depth + 1) for nested in item]
        raise JiraError("invalid_input")

    normalized = normalize(value, 0)
    try:
        encoded = json.dumps(
            {"fields": normalized},
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise JiraError("invalid_input") from None
    if len(encoded) > _MAX_UPDATE_JSON_BYTES:
        raise JiraError("invalid_input")
    return normalized


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

    def _status_name(self, key: str) -> str | None:
        """Read one issue status solely to reconcile an ambiguous write."""
        payload = self.client.rest_json_v2("GET", f"issue/{key}")
        if not isinstance(payload, Mapping):
            return None
        fields = payload.get("fields")
        if not isinstance(fields, Mapping):
            return None
        status = fields.get("status")
        return _name(status) if isinstance(status, Mapping) else None

    def _assignee_matches(self, key: str, desired: str | None) -> bool:
        """Read an assignee once to reconcile an ambiguous assignment write."""
        payload = self.client.rest_json_resolved_version("GET", f"issue/{key}")
        if not isinstance(payload, Mapping):
            return False
        fields = payload.get("fields")
        if not isinstance(fields, Mapping):
            return False
        assignee = fields.get("assignee")
        if desired is None:
            return assignee is None
        if not isinstance(assignee, Mapping):
            return False
        return desired in {
            _bounded_string(assignee.get("name"), 255),
            _bounded_string(assignee.get("accountId"), 255),
        }

    @staticmethod
    def _adf(text: str) -> dict[str, Any]:
        """Wrap plain text as one Atlassian Document Format paragraph."""
        if not text:
            return {"type": "doc", "version": 1, "content": []}
        return {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": text}],
                }
            ],
        }

    def create_issue(
        self,
        project: str,
        issue_type: str,
        summary: str,
        *,
        description: str | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Create one Jira issue with exactly one version-aware POST.

        A create has no idempotency key.  In particular, a later search could
        not distinguish this request's issue from a similarly filed issue, so
        an ambiguous create is deliberately neither reconciled nor retried.
        """
        if type(project) is not str or _PROJECT_KEY.fullmatch(project) is None:
            raise JiraError("invalid_input")
        if type(issue_type) is not str or not issue_type.strip() or len(issue_type) > 255:
            raise JiraError("invalid_input")
        if type(summary) is not str or not summary.strip() or len(summary) > 255:
            raise JiraError("invalid_input")
        if description is not None and (
            type(description) is not str or len(description) > 32_000
        ):
            raise JiraError("invalid_input")
        if type(dry_run) is not bool or type(confirm) is not bool:
            raise JiraError("invalid_input")
        if dry_run and confirm:
            raise JiraError("invalid_input")
        if not dry_run and not confirm:
            raise JiraError("confirmation_required")

        execute = require_explicit_intent(
            dry_run=dry_run,
            confirm=confirm,
            action=f"a new issue in Jira project {project}",
        )
        if not execute:
            return {
                "ok": True,
                "dry_run": True,
                "key": None,
                "project": project,
                "issue_type": issue_type,
                "summary": summary,
            }

        base_fields: dict[str, Any] = {
            "project": {"key": project},
            "issuetype": {"name": issue_type},
            "summary": summary,
        }
        v2_fields = dict(base_fields)
        v3_fields = dict(base_fields)
        if description is not None:
            v2_fields["description"] = description
            v3_fields["description"] = self._adf(description)
        payload = self.client.rest_json_versioned_mutation(
            "POST",
            "issue",
            json_body_by_version={
                "3": {"fields": v3_fields},
                "2": {"fields": v2_fields},
            },
        )
        if not isinstance(payload, Mapping):
            raise JiraError("invalid_remote_data")
        created_key = _bounded_string(payload.get("key"), 128)
        if created_key is None or _ISSUE_KEY.fullmatch(created_key) is None:
            raise JiraError("invalid_remote_data")
        return {
            "ok": True,
            "dry_run": False,
            "key": self._redact(created_key) or "",
            "project": project,
            "issue_type": issue_type,
            "summary": summary,
        }

    def assign_issue(
        self,
        key: str,
        assignee: str | None,
        *,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Assign an issue, or unassign it with ``assignee=None``.

        Cloud accepts an account ID and Data Center accepts a username.  The
        client chooses their body only after a read-only version resolution,
        so this operation always makes exactly one PUT.
        """
        if type(key) is not str or _ISSUE_KEY.fullmatch(key) is None:
            raise JiraError("invalid_input")
        if assignee is not None and (
            type(assignee) is not str or not assignee or len(assignee) > 255
        ):
            raise JiraError("invalid_input")
        if type(dry_run) is not bool or type(confirm) is not bool:
            raise JiraError("invalid_input")
        if dry_run and confirm:
            raise JiraError("invalid_input")
        if not dry_run and not confirm:
            raise JiraError("confirmation_required")

        execute = require_explicit_intent(
            dry_run=dry_run, confirm=confirm, action=f"Jira issue {key}"
        )
        if not execute:
            return {
                "ok": True,
                "dry_run": True,
                "issue_key": key,
                "assignee": assignee,
                "reconciled": False,
            }

        try:
            self.client.rest_json_versioned_mutation(
                "PUT",
                f"issue/{key}/assignee",
                json_body_by_version={
                    "3": {"accountId": assignee},
                    "2": {"name": assignee},
                },
            )
        except JiraError as exc:
            if exc.category != "write_ambiguous":
                raise
            try:
                reconciled = self._assignee_matches(key, assignee)
            except JiraError:
                raise exc from None
            if not reconciled:
                raise
            return {
                "ok": True,
                "dry_run": False,
                "issue_key": key,
                "assignee": assignee,
                "reconciled": True,
            }
        return {
            "ok": True,
            "dry_run": False,
            "issue_key": key,
            "assignee": assignee,
            "reconciled": False,
        }

    def update_fields(
        self,
        key: str,
        fields: Any,
        *,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Set a bounded allowlist of fields on an issue.

        Custom fields must use their ``customfield_<numeric id>`` identifier,
        obtained through :meth:`list_fields`; security, reporter and project
        remain deliberately unavailable to tool callers.
        """
        if type(key) is not str or _ISSUE_KEY.fullmatch(key) is None:
            raise JiraError("invalid_input")
        if type(fields) is not dict or not fields:
            raise JiraError("invalid_input")
        if len(fields) > _MAX_WRITABLE_FIELDS:
            raise JiraError("invalid_input")
        for name in fields:
            if (
                type(name) is not str
                or (
                    name not in WRITABLE_FIELDS
                    and _CUSTOM_FIELD.fullmatch(name) is None
                )
            ):
                raise JiraError("invalid_input")
        if type(dry_run) is not bool or type(confirm) is not bool:
            raise JiraError("invalid_input")
        if dry_run and confirm:
            raise JiraError("invalid_input")
        if not dry_run and not confirm:
            raise JiraError("confirmation_required")

        payload = _bounded_json_value(fields)
        execute = require_explicit_intent(
            dry_run=dry_run, confirm=confirm, action=f"Jira issue {key}"
        )
        if not execute:
            return {
                "ok": True,
                "dry_run": True,
                "issue_key": key,
                "fields": payload,
                "reconciled": False,
            }

        body = {"fields": payload}
        self.client.rest_json_versioned_mutation(
            "PUT",
            f"issue/{key}",
            json_body_by_version={"3": body, "2": body},
        )
        return {
            "ok": True,
            "dry_run": False,
            "issue_key": key,
            "fields": payload,
            "reconciled": False,
        }

    def _labels_match(
        self, key: str, operation: str, requested: list[str]
    ) -> bool:
        """Read issue labels once to reconcile an ambiguous label mutation."""
        payload = self.client.rest_json_resolved_version("GET", f"issue/{key}")
        if type(payload) is not dict:
            return False
        fields = payload.get("fields")
        if type(fields) is not dict:
            return False
        remote_labels = fields.get("labels")
        if type(remote_labels) is not list or any(
            type(label) is not str for label in remote_labels
        ):
            return False
        if operation == "add":
            return all(label in remote_labels for label in requested)
        return all(label not in remote_labels for label in requested)

    def manage_labels(
        self,
        key: str,
        operation: str,
        labels: Any,
        *,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Add or remove a bounded list of labels on an issue.

        The REST version is resolved before this mutation, never after it;
        this preserves the exactly-one-PUT contract in both Jira Cloud and
        Data Center auto mode.
        """
        if type(key) is not str or _ISSUE_KEY.fullmatch(key) is None:
            raise JiraError("invalid_input")
        if type(operation) is not str or operation not in {"add", "remove"}:
            raise JiraError("invalid_input")
        if (
            type(labels) is not list
            or not labels
            or len(labels) > _MAX_LABELS
            or any(
                type(label) is not str or _LABEL.fullmatch(label) is None
                for label in labels
            )
        ):
            raise JiraError("invalid_input")
        if type(dry_run) is not bool or type(confirm) is not bool:
            raise JiraError("invalid_input")
        if dry_run and confirm:
            raise JiraError("invalid_input")
        if not dry_run and not confirm:
            raise JiraError("confirmation_required")

        requested = list(labels)
        execute = require_explicit_intent(
            dry_run=dry_run, confirm=confirm, action=f"Jira issue {key}"
        )
        if not execute:
            return {
                "ok": True,
                "dry_run": True,
                "issue_key": key,
                "operation": operation,
                "labels": requested,
                "reconciled": False,
            }

        body = {
            "update": {"labels": [{operation: label} for label in requested]}
        }
        try:
            self.client.rest_json_versioned_mutation(
                "PUT",
                f"issue/{key}",
                json_body_by_version={"3": body, "2": body},
            )
        except JiraError as exc:
            if exc.category != "write_ambiguous":
                raise
            try:
                reconciled = self._labels_match(key, operation, requested)
            except JiraError:
                raise exc from None
            if not reconciled:
                raise
            return {
                "ok": True,
                "dry_run": False,
                "issue_key": key,
                "operation": operation,
                "labels": requested,
                "reconciled": True,
            }

        return {
            "ok": True,
            "dry_run": False,
            "issue_key": key,
            "operation": operation,
            "labels": requested,
            "reconciled": False,
        }

    def transition_issue(
        self,
        key: str,
        transition_id: str,
        *,
        dry_run: bool = False,
        confirm: bool = False,
        expected_status: str | None = None,
    ) -> dict[str, Any]:
        """Move an issue through one explicitly selected workflow transition.

        The caller obtains the workflow-specific ``transition_id`` from
        ``list_transitions``.  A POST is never retried.  Only an ambiguous
        write with a caller-provided target status gets one bounded read for
        reconciliation.
        """
        if type(key) is not str or _ISSUE_KEY.fullmatch(key) is None:
            raise JiraError("invalid_input")
        if (
            type(transition_id) is not str
            or _NUMERIC_ID.fullmatch(transition_id) is None
        ):
            raise JiraError("invalid_input")
        if expected_status is not None and (
            type(expected_status) is not str or len(expected_status) > 255
        ):
            raise JiraError("invalid_input")
        # Keep the connector's public error boundary at JiraError.  The shared
        # guardrail has the same strict validation, but raises its generic
        # ConnectorError type; validate at this boundary before invoking it.
        if type(dry_run) is not bool or type(confirm) is not bool:
            raise JiraError("invalid_input")
        if dry_run and confirm:
            raise JiraError("invalid_input")
        if not dry_run and not confirm:
            raise JiraError("confirmation_required")

        execute = require_explicit_intent(
            dry_run=dry_run, confirm=confirm, action=f"Jira issue {key}"
        )
        if not execute:
            return {
                "ok": True,
                "dry_run": True,
                "issue_key": key,
                "transition_id": transition_id,
                "reconciled": False,
            }

        try:
            self.client.rest_json_v2_mutation(
                "POST",
                f"issue/{key}/transitions",
                json_body={"transition": {"id": transition_id}},
            )
        except JiraError as exc:
            if exc.category != "write_ambiguous" or expected_status is None:
                raise
            try:
                reconciled_status = self._status_name(key)
            except JiraError:
                raise exc from None
            if reconciled_status != expected_status:
                raise
            return {
                "ok": True,
                "dry_run": False,
                "issue_key": key,
                "transition_id": transition_id,
                "reconciled": True,
            }

        return {
            "ok": True,
            "dry_run": False,
            "issue_key": key,
            "transition_id": transition_id,
            "reconciled": False,
        }

    def list_fields(
        self, *, custom_only: bool = False, max_results: int = 100
    ) -> dict[str, Any]:
        """List Jira fields so custom field IDs can be resolved by name.

        Without this an agent cannot map customfield_10234 to "Story Points",
        so every custom-field interaction has to be hardcoded per Jira
        deployment.
        """
        if type(custom_only) is not bool:
            raise JiraError("invalid_input")
        if type(max_results) is not int or not 1 <= max_results <= 200:
            raise JiraError("invalid_input")
        payload = self.client.rest_json("GET", "field")
        if not isinstance(payload, list):
            raise JiraError("invalid_remote_data")
        fields: list[dict[str, Any]] = []
        for entry in payload:
            if not isinstance(entry, Mapping):
                continue
            identifier = _bounded_string(entry.get("id"), 255)
            if not identifier:
                continue
            is_custom = entry.get("custom")
            if type(is_custom) is not bool:
                raise JiraError("invalid_remote_data")
            if custom_only and not is_custom:
                continue
            fields.append(
                {
                    "id": self._redact(identifier),
                    "name": self._redact(_bounded_string(entry.get("name"), 255))
                    or "",
                    "custom": is_custom,
                }
            )
        total = len(fields)
        truncated = total > max_results
        return result_envelope(
            fields[:max_results],
            total=total,
            truncated=truncated,
            hint=(
                "More fields exist. Raise max_results, or pass custom_only "
                "to narrow the list."
                if truncated
                else None
            ),
        )

    def _named_entries(self, raw: Any, *, extra: tuple[str, ...] = ()) -> list:
        """Normalize a bounded Jira list of named metadata objects."""
        if not isinstance(raw, list):
            return []
        entries = []
        for item in raw[:200]:
            if not isinstance(item, Mapping):
                continue
            identifier = _bounded_string(item.get("id"), 128)
            if not identifier:
                continue
            entry = {
                "id": self._redact(identifier) or "",
                "name": self._redact(_bounded_string(item.get("name"), 255)) or "",
            }
            for flag in extra:
                if flag in item and type(item[flag]) is not bool:
                    raise JiraError("invalid_remote_data")
                entry[flag] = item.get(flag, False)
            entries.append(entry)
        return entries

    def get_project(self, key: str) -> dict[str, Any]:
        """Fetch the project metadata needed to compose a valid issue."""
        if not isinstance(key, str) or _PROJECT_KEY.fullmatch(key) is None:
            raise JiraError("invalid_input")
        payload = self.client.rest_json("GET", f"project/{key}")
        if not isinstance(payload, Mapping):
            raise JiraError("invalid_remote_data")
        if "archived" in payload and type(payload["archived"]) is not bool:
            raise JiraError("invalid_remote_data")
        return {
            "key": self._redact(_bounded_string(payload.get("key"), 128)) or "",
            "name": self._redact(_bounded_string(payload.get("name"), 255)) or "",
            "id": self._redact(_bounded_string(payload.get("id"), 128)) or "",
            "project_type": self._redact(
                _bounded_string(payload.get("projectTypeKey"), 64)
            )
            or "",
            "archived": payload.get("archived", False),
            "issue_types": self._named_entries(
                payload.get("issueTypes"), extra=("subtask",)
            ),
            "components": self._named_entries(payload.get("components")),
            "versions": self._named_entries(
                payload.get("versions"), extra=("released",)
            ),
        }

    def list_transitions(self, key: str) -> dict[str, Any]:
        """List the workflow transitions currently available on an issue.

        Transition IDs are workflow-specific and cannot be guessed, so this
        is a hard prerequisite for jira_transition_issue.
        """
        if not isinstance(key, str) or _ISSUE_KEY.fullmatch(key) is None:
            raise JiraError("invalid_input")
        payload = self.client.rest_json("GET", f"issue/{key}/transitions")
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("transitions"), list
        ):
            raise JiraError("invalid_remote_data")
        transitions = []
        total = 0
        for item in payload["transitions"]:
            if not isinstance(item, Mapping):
                continue
            identifier = _bounded_string(item.get("id"), 128)
            if not identifier:
                continue
            total += 1
            if len(transitions) >= 200:
                continue
            target = item.get("to")
            transitions.append(
                {
                    "id": self._redact(identifier) or "",
                    "name": self._redact(_bounded_string(item.get("name"), 255))
                    or "",
                    "to_status": self._redact(
                        _name(target) if isinstance(target, Mapping) else None
                    )
                    or "",
                }
            )
        truncated = total > len(transitions)
        return result_envelope(
            transitions,
            total=total,
            truncated=truncated,
            hint=(
                "More valid transitions exist. This result contains the first 200."
                if truncated
                else None
            ),
        )

    def list_link_types(self) -> dict[str, Any]:
        """List every configured Jira issue-link type, bounded to 200 items.

        The type names and both directional phrasings are deployment-specific,
        so callers must discover them rather than guess before linking issues.
        A malformed response is refused as remote data rather than exposing a
        partial or misleading set of link semantics.
        """
        payload = self.client.rest_json("GET", "issueLinkType")
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("issueLinkTypes"), list
        ):
            raise JiraError("invalid_remote_data")

        normalized: list[dict[str, str]] = []
        for item in payload["issueLinkTypes"]:
            if not isinstance(item, Mapping):
                raise JiraError("invalid_remote_data")
            identifier = _bounded_string(item.get("id"), 128)
            name = _bounded_string(item.get("name"), 255)
            inward = _bounded_string(item.get("inward"), 255)
            outward = _bounded_string(item.get("outward"), 255)
            if not identifier or not name or not inward or not outward:
                raise JiraError("invalid_remote_data")
            normalized.append(
                {
                    "id": self._redact(identifier) or "",
                    "name": self._redact(name) or "",
                    "inward": self._redact(inward) or "",
                    "outward": self._redact(outward) or "",
                }
            )

        total = len(normalized)
        truncated = total > 200
        return result_envelope(
            normalized[:200],
            total=total,
            truncated=truncated,
            hint=(
                "More valid link types exist. This result contains the first 200."
                if truncated
                else None
            ),
        )

    def link_issues(
        self,
        inward: str,
        outward: str,
        link_type: str,
        *,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Create one directional link between two Jira issues.

        This write is intentionally neither retried nor reconciled after an
        ambiguous result. An existing identical link makes a later read
        non-probative: it cannot show whether this invocation created it.
        """
        if (
            type(inward) is not str
            or _ISSUE_KEY.fullmatch(inward) is None
            or type(outward) is not str
            or _ISSUE_KEY.fullmatch(outward) is None
            or inward == outward
        ):
            raise JiraError("invalid_input")
        if type(link_type) is not str or not link_type.strip() or len(link_type) > 255:
            raise JiraError("invalid_input")
        if type(dry_run) is not bool or type(confirm) is not bool:
            raise JiraError("invalid_input")
        if dry_run and confirm:
            raise JiraError("invalid_input")
        if not dry_run and not confirm:
            raise JiraError("confirmation_required")

        execute = require_explicit_intent(
            dry_run=dry_run,
            confirm=confirm,
            action=f"a Jira link from {inward} to {outward}",
        )
        result = {
            "ok": True,
            "dry_run": not execute,
            "inward": inward,
            "outward": outward,
            "link_type": link_type,
        }
        if not execute:
            return result

        body = {
            "type": {"name": link_type},
            "inwardIssue": {"key": inward},
            "outwardIssue": {"key": outward},
        }
        self.client.rest_json_versioned_mutation(
            "POST",
            "issueLink",
            json_body_by_version={"3": body, "2": body},
            empty_success_statuses=frozenset({201, 204}),
        )
        return result

    def search_assignable_users(
        self, project: str, query: str = "", *, max_results: int = 25
    ) -> dict[str, Any]:
        """Find users who can actually be assigned issues in one project.

        Assignability is a per-project permission, not a global user list, so
        a name from elsewhere in Jira may still be rejected on assignment.
        """
        if not isinstance(project, str) or _PROJECT_KEY.fullmatch(project) is None:
            raise JiraError("invalid_input")
        if not isinstance(query, str) or len(query) > 255:
            raise JiraError("invalid_input")
        if type(max_results) is not int or not 1 <= max_results <= 100:
            raise JiraError("invalid_input")
        payload = self.client.rest_json(
            "GET",
            "user/assignable/search",
            params={
                "project": project,
                "username": query,
                "maxResults": max_results,
            },
        )
        if not isinstance(payload, list):
            raise JiraError("invalid_remote_data")

        users: list[dict[str, str]] = []
        for item in payload:
            if not isinstance(item, Mapping):
                continue
            if "active" in item and type(item["active"]) is not bool:
                raise JiraError("invalid_remote_data")
            if item.get("active", True) is False:
                continue
            name = _bounded_string(item.get("name"), 255)
            if not name:
                continue
            user = {
                "name": self._redact(name) or "",
                "display_name": self._redact(
                    _bounded_string(item.get("displayName"), 255)
                )
                or "",
            }
            email = _bounded_string(item.get("emailAddress"), _MAX_EMAIL_LEN)
            if email:
                user["email"] = self._redact(email) or ""
            users.append(user)

        remote_may_be_truncated = len(payload) >= max_results
        truncated = remote_may_be_truncated or len(users) > max_results
        return result_envelope(
            users[:max_results],
            total=None if remote_may_be_truncated else len(users),
            truncated=truncated,
            hint=(
                "Jira may have more assignable users. Refine query to narrow "
                "the result."
                if truncated
                else None
            ),
        )

    def _normalize_issue(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict) or not isinstance(raw.get("fields"), dict):
            raise JiraError("invalid_remote_data")
        key = _bounded_string(raw.get("key"), 128)
        if not key or _ISSUE_KEY.fullmatch(key) is None:
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
        projected_key = self._redact(key) or ""
        return {
            "key": projected_key,
            "summary": summary,
            "status": status,
            "status_category": status_category,
            "priority": self._redact(_name(fields.get("priority"))),
            "issue_type": self._redact(_name(fields.get("issuetype"))),
            "labels": [self._redact(label) or "" for label in labels],
            "updated": self._redact(_bounded_string(fields.get("updated"), 128)),
            "created": self._redact(_bounded_string(fields.get("created"), 128)),
            "description": description,
            "environment": environment,
            "problem_summary": sentence,
            "gitlab_urls": extract_gitlab_urls(description, environment, summary),
            "issue_url": self._redact(f"{self.client.auth.origin}/browse/{key}"),
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
        deadline = self.client.operation_deadline()

        selected: list[dict[str, Any]] = []
        start_at = 0
        remote_total = None
        truncated = False
        fully_scanned = False
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
                deadline=deadline,
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
                raise JiraError("invalid_remote_data")
            raw_issues = payload["issues"]
            raw_total = payload.get("total", start_at + len(raw_issues))
            if type(raw_total) is not int or raw_total < 0:
                raise JiraError("invalid_remote_data")
            remote_total = raw_total
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
                        fully_scanned = start_at + consumed >= remote_total
                        truncated = not fully_scanned
                        break
            start_at += len(raw_issues)
            if len(selected) >= max_results:
                break
            if not raw_issues or start_at >= remote_total:
                fully_scanned = True
                break
        else:
            truncated = remote_total is None or start_at < remote_total
        if not has_filters:
            result_total = remote_total
        elif fully_scanned:
            result_total = len(selected)
        else:
            result_total = None
        return result_envelope(
            selected[:max_results],
            total=result_total,
            truncated=truncated,
            hint=(
                "More Jira issues remain unscanned. Narrow the JQL or filters "
                "before treating this filtered result as complete."
                if truncated and has_filters
                else (
                    "More issues match this JQL. Raise max_results or narrow "
                    "the query."
                )
                if truncated
                else None
            ),
            untrusted=True,
        )

    def my_tickets(self, max_results: int | None = None) -> dict[str, Any]:
        limit = self.client.auth.default_max_results if max_results is None else max_results
        return self.search_issues(
            jql=MY_TICKETS_JQL,
            max_results=limit,
            fields=["summary", "status", "priority", "updated", "description"],
        )

    def get_issue(self, key: str) -> dict[str, Any]:
        if not isinstance(key, str) or _ISSUE_KEY.fullmatch(key) is None:
            raise JiraError("invalid_input")
        deadline = self.client.operation_deadline()
        payload = self.client.rest_json(
            "GET",
            f"issue/{key}",
            params={"fields": ",".join(sorted(DETAIL_FIELDS))},
            deadline=deadline,
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
                    "created": self._redact(
                        _bounded_string(comment.get("created"), 128)
                    ),
                }
            )
        normalized["comments"] = comments
        normalized["content_warning"] = UNTRUSTED_CONTENT_WARNING
        return normalized

    def _find_comment(
        self,
        key: str,
        body: str,
        *,
        deadline: float,
        resolved_version: bool = False,
    ) -> str | None:
        read = (
            self.client.rest_json_resolved_version
            if resolved_version
            else self.client.rest_json
        )
        payload = read(
            "GET",
            f"issue/{key}/comment",
            params={"maxResults": 100, "orderBy": "-created"},
            deadline=deadline,
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
            if comment_id is None or _NUMERIC_ID.fullmatch(comment_id) is None:
                raise JiraError("invalid_remote_data")
            if adf_to_text(comment.get("body")).strip() == target:
                return self._redact(comment_id)
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
            type(key) is not str
            or _ISSUE_KEY.fullmatch(key) is None
            or type(body) is not str
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
        deadline = self.client.operation_deadline()
        existing = self._find_comment(key, body, deadline=deadline)
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
        original_failure = None
        try:
            payload = self.client.rest_json_versioned_mutation(
                "POST",
                f"issue/{key}/comment",
                json_body_by_version={"3": v3_body, "2": v2_body},
                deadline=deadline,
            )
        except JiraError as exc:
            if exc.category not in {"conflict", "write_ambiguous"}:
                raise
            original_failure = exc.category
        if original_failure is None:
            if isinstance(payload, dict):
                comment_id = _bounded_string(payload.get("id"), 128)
                if comment_id and _NUMERIC_ID.fullmatch(comment_id) is not None:
                    return self._comment_result(
                        self._redact(comment_id),
                        created=True,
                        duplicate=False,
                        reconciled=False,
                        dry_run=False,
                    )
            original_failure = "write_ambiguous"

        reconciled = None
        try:
            reconciled = self._find_comment(
                key, body, deadline=deadline, resolved_version=True
            )
        except JiraError:
            pass
        if reconciled is not None:
            return self._comment_result(
                reconciled,
                created=False,
                duplicate=True,
                reconciled=True,
                dry_run=False,
            )
        raise JiraError(original_failure) from None
